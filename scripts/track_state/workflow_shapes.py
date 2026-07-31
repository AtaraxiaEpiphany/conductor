"""Workflow-shape registry — the third axis (the node SEQUENCE).

The task-type registry (:mod:`task_profiles`) says what each node *says*
(routing + workflow prose). The verify-mode registry
(:mod:`verify_mode_profiles`) says what each phase gate *means*. This module
says the node *sequence*: which dispatch agents a workflow runs, in what order,
its verify policy, and its stop condition. The conductor's fixed state machine
topology (planner → executor → checker) becomes *declared* here, so a project
ships a custom shape (e.g. ``research-first``) with zero plugin edits.

The registry resolves as **plugin baseline ⊕ project overlay**, exactly
mirroring :mod:`task_profiles`. The baseline is the JSON data file at
``templates/workflow/workflow-shapes.json``. A project may drop
``conductor/workflow/workflow-shapes.json`` to add a project-specific shape or
override a built-in one — opt-in by file presence (absent = plugin defaults,
zero behavior change). The overlay merges over the baseline: project shapes are
added, the project wins a conflicting shape, and a project ``default`` wins
per-key. Loading is **fail-open**: if the baseline is missing or unparseable,
we fall back to ``_FALLBACK`` (a verbatim copy of the pre-registry hardcoded
topology) and log loudly to stderr; a malformed overlay falls back to the
baseline alone — dispatch must never crash over a malformed registry.

**Shape is advisory today.** The shape declares intended topology but does NOT
reorder dispatch — the conductor runs the same action-driven planner→executor→
checker spine regardless of the resolved shape. The single dispatch-path
consumer is :func:`shape_allows` (``dispatch.py:1741``), and its result is
**never** used to block or reroute: when a dispatched action's agent is outside
the resolved shape's ``nodes``, the spine attaches an advisory ``shape_violation``
disclosure to the emitted leaf envelope (no-silent-caps) and the dispatch still
proceeds — a shape misconfiguration must never deadlock a track. The other
accessors (:func:`nodes_for`, :func:`verify_policy_for`, :func:`stop_condition_for`,
:func:`instruction_for`) are consumed **only** by ``registry-doc`` display, never
by dispatch ordering, wave.py, handoff.py, or any SubagentStart injection.
``instruction_for`` in particular is NOT injected into an orchestrator prompt
(contrast the task-type ``workflow`` field, which IS injected). So setting
``research-first`` surfaces ``shape_violation`` disclosures but does not run
``explorer`` first. Making the shape genuinely load-bearing would require code at
``_step_emit_dispatch`` / ``cmd_dispatch_next``; today it is a diagnostic, not a
gate.

Adding a shape after this module exists is a one-row registry edit: it is
automatically (a) resolvable via :func:`nodes_for`/:func:`verify_policy_for`,
(b) rendered by ``registry-doc --shape <name>``, (c) surfaced as an advisory
``shape_violation`` when dispatch drifts off-topology — all with **zero** Python
edits. (To make the new shape *drive* dispatch rather than merely diagnose it,
the change is code at the emit site, not a registry row.)
"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path


# --- fallback: verbatim copy of the pre-registry hardcoded topology ----------
# DO NOT edit this to change a shape — edit the registry JSON instead. This
# exists ONLY so a missing/malformed registry never crashes dispatch. If you
# find yourself changing a value here, you are changing the fail-open floor,
# not the real config.
_FALLBACK = {
    "default": {
        "nodes": ["spec-planner", "task-executor", "phase-checker"],
        "verify_policy": "checkpoint",
        "stop_condition": "all_nodes_done",
    },
    "shapes": {
        "default": {
            "nodes": ["spec-planner", "task-executor", "phase-checker"],
            "verify_policy": "checkpoint",
            "stop_condition": "all_nodes_done",
        },
    },
}


def _plugin_root() -> Path:
    """Resolve the plugin root, preferring ``$CLAUDE_PLUGIN_ROOT`` when it matches
    the ``__file__``-derived root (same ground-truth-first discipline as
    ``task_profiles._plugin_root`` / ``lib.env.get_plugin_root``). This module is
    at ``<plugin>/scripts/track_state/workflow_shapes.py``.
    """
    file_root = Path(__file__).resolve().parent.parent.parent
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        env_resolved = Path(env_root).resolve()
        if env_resolved == file_root:
            return env_resolved
    return file_root


def _plugin_registry_path() -> Path:
    """The always-present plugin baseline registry.

    ``<plugin>/templates/workflow/workflow-shapes.json``.
    """
    return _plugin_root() / "templates" / "workflow" / "workflow-shapes.json"


def _project_root() -> Path | None:
    """Resolve the *project* root (NOT the plugin root), or ``None`` when not in
    a real project tree. Mirrors ``task_profiles._project_root`` exactly (the
    same ladder is used by every overlay-aware registry so they agree on what
    "the project" is).

    1. ``$CLAUDE_PROJECT_DIR`` (Claude Code's authoritative injection) when set;
    2. else the cwd, **but only if** ``$cwd/conductor/tracks/`` is a dir — the
       repo's "this is a real project, not the plugin repo" signal;
    3. else ``None`` (no project, no overlay).

    Inlined (not an import of ``lib.env``): this module is imported transitively
    by the standalone hook scripts, and ``lib.env`` resolution can raise —
    inlining keeps the fail-open boundary tight.
    """
    env_proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_proj:
        return Path(env_proj).resolve()
    cwd = Path.cwd()
    if (cwd / "conductor" / "tracks").is_dir():
        return cwd
    return None


def _project_override_path() -> Path | None:
    """The project overlay registry candidate, or ``None`` when there is no
    project tree to overlay from.

    ``<project>/conductor/workflow/workflow-shapes.json`` — opt-in by file
    presence (absent file = plugin defaults, zero behavior change).
    """
    root = _project_root()
    if root is None:
        return None
    return root / "conductor" / "workflow" / "workflow-shapes.json"


def _load_baseline() -> dict:
    """Load the plugin baseline registry, fail-open to :data:`_FALLBACK`.

    This is the always-present floor: if the shipped registry is missing,
    unparseable, or structurally wrong, we use the hardcoded fallback so
    dispatch never crashes over the plugin's own registry.
    """
    cand = _plugin_registry_path()
    try:
        if cand.exists():
            data = json.loads(cand.read_text(encoding="utf-8"))
            shapes = data.get("shapes")
            default = data.get("default")
            if isinstance(shapes, dict) and isinstance(default, dict):
                return data
            print(
                f"WARNING: workflow-shapes registry at {cand} has invalid shape "
                f"(missing 'shapes'/'default'); using built-in fallback values.",
                file=sys.stderr,
            )
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: workflow-shapes registry at {cand} unreadable ({exc}); "
            f"using built-in fallback values.",
            file=sys.stderr,
        )
    return _FALLBACK


def _merge_overlay(baseline: dict) -> dict:
    """Shallow-merge a project overlay onto the baseline, if present.

    ``shapes``: project overlays baseline (project shapes added; project wins a
    conflicting shape). ``default``: project wins per-key if declared
    (``{**baseline_default, **overlay_default}``). The return shape is identical
    to the baseline's, so every consumer is overlay-blind — this merge is the
    single chokepoint that flows everywhere.

    Fail-open to *baseline alone* on any overlay read/shape error (the baseline
    is valid; a malformed project file must NOT drag dispatch down to
    :data:`_FALLBACK`).
    """
    overlay_path = _project_override_path()
    if overlay_path is None or not overlay_path.exists():
        return baseline
    try:
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: project workflow-shapes overlay at {overlay_path} "
            f"unreadable ({exc}); using plugin baseline alone.",
            file=sys.stderr,
        )
        return baseline
    if not isinstance(overlay, dict):
        print(
            f"WARNING: project workflow-shapes overlay at {overlay_path} has "
            f"invalid shape (not an object); using plugin baseline alone.",
            file=sys.stderr,
        )
        return baseline

    merged_shapes = dict(baseline.get("shapes", {}))
    overlay_shapes = overlay.get("shapes")
    if isinstance(overlay_shapes, dict):
        merged_shapes.update(overlay_shapes)

    merged_default = dict(baseline.get("default", {}))
    overlay_default = overlay.get("default")
    if isinstance(overlay_default, dict):
        merged_default.update(overlay_default)

    return {"default": merged_default, "shapes": merged_shapes}


@lru_cache(maxsize=1)
def _load() -> dict:
    """Load + cache the resolved registry (plugin baseline ⊕ project overlay).

    The baseline always loads (fail-open to :data:`_FALLBACK`); the project
    overlay, if present at ``<project>/conductor/workflow/workflow-shapes.json``,
    merges on top (project wins conflicts). Cached so the merge runs once.
    """
    baseline = _load_baseline()
    return _merge_overlay(baseline)


def _shape(name: str) -> dict:
    """The profile for a single shape name, falling back to the default shape."""
    data = _load()
    prof = data["shapes"].get(name)
    if prof is None:
        return data["default"]
    # Inherit any missing key from the default shape so a registry row only
    # has to state what it overrides.
    merged = dict(data["default"])
    merged.update(prof)
    return merged


# --- public API ----------------------------------------------------------------

def SHAPES_VOCAB() -> tuple[str, ...]:
    """The closed vocabulary of known workflow-shape names, in registry order.

    The drift-killer lint (``check-contract-registry-sync.py``) forbids a
    hand-maintained shapes table in the contract for the same reason it forbids
    tag/mode tables: a second home drifts. This is the single source.
    """
    return tuple(_load()["shapes"].keys())


def nodes_for(shape: str) -> tuple[str, ...]:
    """The ordered dispatch-agent node list a shape runs — the topology.

    This is the allowlist the dispatch spine consults: an action whose agent is
    not in this list (for the resolved shape) is refused. Returns a tuple for
    stable membership tests. Unknown shape → ``default`` shape's nodes (fail-
    open: a typo never blocks dispatch, it falls back to the standard loop).
    """
    return tuple(_shape(shape).get("nodes", ()))


def verify_policy_for(shape: str) -> str:
    """How a shape gates progress: ``checkpoint`` | ``none``.

    ``checkpoint`` (default) → the phase-checker stamps a checkpoint gate.
    ``none`` → no checkpoint gate (research/exploration shapes that produce no
    committable artifact). Mirrors :func:`task_profiles.route_for` as a
    shape-level routing primitive.
    """
    return _shape(shape).get("verify_policy", "checkpoint")


def stop_condition_for(shape: str) -> str:
    """What makes a shape DONE (``all_nodes_done`` by default)."""
    return _shape(shape).get("stop_condition", "all_nodes_done")


def instruction_for(shape: str) -> str:
    """The optional prompt-shaping prose for the orchestrator when this shape is
    active (mirrors task-type ``workflow`` + verify-mode ``protocol``). Absent
    (the common case) = ``""`` = the default §3.0 dispatch loop.
    """
    return _shape(shape).get("instruction", "")


def resolve_shape(track_state_field) -> str:
    """Resolve the active shape name for a track.

    Reads the optional ``workflow_shape`` field from ``track-state.json``
    (written by ``init-from-plan``; v1 always writes ``"default"``). Absent or
    unknown → ``"default"`` (fail-open: a track predating this field, or one
    with a typo, runs the standard loop rather than blocking).

    ``track_state_field`` is the raw value of that field (a str or None) — the
    caller is expected to pull it off the loaded state dict so this stays a pure
    function of its argument.
    """
    if isinstance(track_state_field, str) and track_state_field:
        if track_state_field in SHAPES_VOCAB():
            return track_state_field
        # Unknown shape name (typo, or a project shape the plugin baseline
        # doesn't know) — fail-open to default, but surface it so it's visible.
        print(
            f"WARNING: unknown workflow_shape {track_state_field!r} in "
            f"track-state.json; using 'default'.",
            file=sys.stderr,
        )
    return "default"
