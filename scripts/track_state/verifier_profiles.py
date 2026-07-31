"""Verifier registry — the fourth axis (the checkpoint VERIFIER set).

The task-type registry (:mod:`task_profiles`) says what each node *says*
(routing + workflow prose). The verify-mode registry (:mod:`verify_mode_profiles`)
says what each phase gate *means*. The workflow-shape registry
(:mod:`workflow_shapes`) says the node *sequence* — and THIS registry says which
**read-only checkpoint verifiers** a workflow fans out and the **assignment
field-set** each one needs. The conductor's hardcoded ``[ac-tracer, test-runner]``
checkpoint fan-out becomes *declared* here, so a project ships a custom verifier
(e.g. ``lint-runner``, ``e2e-runner``) with zero plugin edits beyond the agent
definition.

The registry resolves as **plugin baseline ⊕ project overlay**, exactly mirroring
:mod:`workflow_shapes`. The baseline is the JSON data file at
``templates/workflow/verifier-profiles.json``. A project may drop
``conductor/workflow/verifier-profiles.json`` to add a project-specific verifier
or override a built-in one — opt-in by file presence (absent = plugin defaults,
zero behavior change). The overlay merges over the baseline: project verifiers
are added and the project wins a conflicting key. Loading is **fail-open**: if
the baseline is missing or unparseable, we fall back to ``_FALLBACK`` (a verbatim
copy of the pre-registry hardcoded pair) and log loudly to stderr; a malformed
overlay falls back to the baseline alone — dispatch must never crash over a
malformed registry.

**The consumer is the dispatch fan-out.** ``dispatch.cmd_dispatch_next`` /
``dispatch._step_emit_dispatch_batch`` iterate the resolved **shape's** verifiers
(:func:`workflow_shapes.verifiers_for` — the third and fourth axes joined at the
checkpoint), and ``dispatch._build_verifier`` reads each verifier's ``field_set``
from here to emit its assignment envelope body (NOT a hardcoded
``if agent == "test-runner"`` branch). So adding a verifier is one row here +
the agent definition + naming it in a shape's ``verifiers`` list; the fan-out,
the envelope builder, and ``registry-doc`` all derive from it automatically.
"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path


# --- fallback: verbatim copy of the pre-registry hardcoded verifier pair -----
# DO NOT edit this to change a verifier — edit the registry JSON instead. This
# exists ONLY so a missing/malformed registry never crashes dispatch. If you
# find yourself changing a value here, you are changing the fail-open floor,
# not the real config.
_FALLBACK = {
    "ac-tracer": {
        "agent": "ac-tracer",
        "field_set": ["TRACK_DIR", "TRACK_ID"],
    },
    "test-runner": {
        "agent": "test-runner",
        "field_set": ["TRACK_DIR", "TRACK_ID", "PHASE_INDEX"],
    },
}

# The verifiers every shape's checkpoint fans out when the shape declares no
# `verifiers` field (the fail-open default — mirrors the pre-registry behavior
# of always fanning out both). workflow_shapes.verifiers_for falls back to this.
DEFAULT_VERIFIERS: tuple[str, ...] = ("ac-tracer", "test-runner")


def _plugin_root() -> Path:
    """Resolve the plugin root, preferring ``$CLAUDE_PLUGIN_ROOT`` when it matches
    the ``__file__``-derived root (same ground-truth-first discipline as
    ``task_profiles._plugin_root`` / ``workflow_shapes._plugin_root`` /
    ``lib.env.get_plugin_root``). This module is at
    ``<plugin>/scripts/track_state/verifier_profiles.py``.
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

    ``<plugin>/templates/workflow/verifier-profiles.json``.
    """
    return _plugin_root() / "templates" / "workflow" / "verifier-profiles.json"


def _project_root() -> Path | None:
    """Resolve the *project* root (NOT the plugin root), or ``None`` when not in
    a real project tree. Mirrors ``task_profiles._project_root`` /
    ``workflow_shapes._project_root`` exactly (the same ladder is used by every
    overlay-aware registry so they agree on what "the project" is).

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

    ``<project>/conductor/workflow/verifier-profiles.json`` — opt-in by file
    presence (absent file = plugin defaults, zero behavior change).
    """
    root = _project_root()
    if root is None:
        return None
    return root / "conductor" / "workflow" / "verifier-profiles.json"


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
            verifiers = data.get("verifiers")
            if isinstance(verifiers, dict) and verifiers:
                return data
            print(
                f"WARNING: verifier-profiles registry at {cand} has invalid shape "
                f"(missing/empty 'verifiers'); using built-in fallback values.",
                file=sys.stderr,
            )
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: verifier-profiles registry at {cand} unreadable ({exc}); "
            f"using built-in fallback values.",
            file=sys.stderr,
        )
    return {"verifiers": _FALLBACK}


def _merge_overlay(baseline: dict) -> dict:
    """Shallow-merge a project overlay onto the baseline, if present.

    ``verifiers``: project overlays baseline (project verifiers added; project
    wins a conflicting key). The return shape is identical to the baseline's,
    so every consumer is overlay-blind — this merge is the single chokepoint
    that flows everywhere.

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
            f"WARNING: project verifier-profiles overlay at {overlay_path} "
            f"unreadable ({exc}); using plugin baseline alone.",
            file=sys.stderr,
        )
        return baseline
    if not isinstance(overlay, dict):
        print(
            f"WARNING: project verifier-profiles overlay at {overlay_path} has "
            f"invalid shape (not an object); using plugin baseline alone.",
            file=sys.stderr,
        )
        return baseline

    merged = dict(baseline.get("verifiers", {}))
    overlay_verifiers = overlay.get("verifiers")
    if isinstance(overlay_verifiers, dict):
        merged.update(overlay_verifiers)

    return {"verifiers": merged}


@lru_cache(maxsize=1)
def _load() -> dict:
    """Load + cache the resolved registry (plugin baseline ⊕ project overlay).

    The baseline always loads (fail-open to :data:`_FALLBACK`); the project
    overlay, if present at ``<project>/conductor/workflow/verifier-profiles.json``,
    merges on top (project wins conflicts). Cached so the merge runs once.
    """
    baseline = _load_baseline()
    return _merge_overlay(baseline)


def _profile(name: str) -> dict:
    """The profile for a single verifier name, or ``{}`` when unknown.

    Unknown verifiers are fail-open (an empty profile), mirroring every other
    registry's posture — a typo never crashes the consumer.
    """
    return _load()["verifiers"].get(name, {})


# --- public API ----------------------------------------------------------------

def VERIFIER_VOCAB() -> tuple[str, ...]:
    """The closed vocabulary of known verifier names, in registry order.

    The drift-killer lint (``check-contract-registry-sync.py``) forbids a
    hand-maintained verifier table in the contract for the same reason it forbids
    tag/mode/shape tables: a second home drifts. This is the single source.
    """
    return tuple(_load()["verifiers"].keys())


def field_set_for(verifier: str) -> tuple[str, ...]:
    """The ordered assignment field-set a verifier's ``dispatch_batch`` envelope
    body carries (``TRACK_DIR`` / ``TRACK_ID`` / ``PHASE_INDEX``), from the
    registry.

    This is what makes :func:`dispatch._build_verifier` data-driven: instead of a
    hardcoded ``if agent == "test-runner": …PHASE_INDEX…`` branch, it reads this
    tuple and emits one ``KEY=value`` line per token. Unknown verifier → ``()``
    (the caller falls back to the ``TRACK_DIR``+``TRACK_ID`` floor). Returns a
    tuple (immutable) so callers can't corrupt the shared registry list.
    """
    raw = _profile(verifier).get("field_set")
    if not isinstance(raw, list):
        return ()
    return tuple(str(f) for f in raw)


def when_to_use_for(verifier: str) -> str:
    """The one-line 'what this verifier checks' hint, rendered by
    ``registry-doc --verifier <name>``. Absent/unknown → ``""``."""
    return str(_profile(verifier).get("when_to_use", ""))


def agent_for(verifier: str) -> str:
    """The dispatch agent name a verifier dispatches (the wave member's ``agent``
    slot). Defaults to the verifier name itself when the row omits ``agent``
    (the common case — the verifier key IS the agent name). Unknown → ``""``.
    """
    return str(_profile(verifier).get("agent", verifier))
