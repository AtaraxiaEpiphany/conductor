"""Task-type registry — the single source of truth for task-type semantics.

The tag *name* still lives in plan.md task names (e.g. ``[Migrate] Add X``) and
is re-extracted at every read via :func:`helpers.extract_tags`. This module holds
what the tag *means*: which dispatch category it routes to, whether it is exempt
from the TDD / coverage gates, a one-line ``when_to_use`` hint (injected into
spec-planner so its tag-decision guidance is data-driven), and — for tags whose
executor behavior diverges from default TDD — a ``workflow`` field (the
generalization of ``[Migrate]`` §4.M; injected into task-executor). It replaces
the hardcoded exemption sets that used to live in ``helpers.py``
(``_tag_exempt_from_tdd`` / ``_tag_exempt_from_coverage``) and the branch table in
``dispatch._classify_task``.

The registry resolves as **plugin baseline ⊕ project overlay**. The baseline is
the JSON data file shipped at ``templates/workflow/task-type-profiles.json``. A
project may drop ``conductor/workflow/task-type-profiles.json`` (alongside the
other workflow files setup scaffolds there) to add project-specific tags or
override a built-in tag's semantics — opt-in by file presence (absent = plugin
defaults, zero behavior change). The overlay merges over the baseline: project
tags are added, the project wins a conflicting tag, and a project ``default``
profile wins per-key. Loading is **fail-open**: if the baseline is missing or
unparseable, we fall back to ``_FALLBACK`` (a verbatim copy of the pre-registry
hardcoded values) and log loudly to stderr; a malformed overlay falls back to
the baseline alone — dispatch must never crash over a malformed registry. The
fallback is the emergency mirror that keeps the plugin working even if the data
file is deleted; it is deliberately a separate constant so a future editor can
see exactly what the registry is supposed to contain.

Adding a task type after this module exists is a one-line registry row: it is
automatically (a) recognized by ``extract_tags``/``strip_tags`` (they build
their regex from :data:`TAG_VOCAB`), (b) routed correctly by
:func:`route_for`, (c) given the right exemptions by
:func:`is_tdd_exempt`/:func:`is_coverage_exempt`, (d) surfaced to spec-planner
with its ``when_to_use`` hint via :func:`when_to_use_for`, and — if it carries
a ``workflow`` — (e) injected into task-executor via :func:`workflow_for`; all
with **zero** Python edits.
"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path


# --- fallback: verbatim copy of the pre-registry hardcoded values ----------------
# DO NOT edit this to change a tag's behavior — edit the registry JSON instead.
# This exists ONLY so a missing/malformed registry never crashes dispatch. If you
# find yourself changing a value here, you are changing the fail-open floor, not
# the real config.
_FALLBACK = {
    "default": {"route": "executor", "tdd_exempt": False, "coverage_exempt": False},
    "tags": {
        "Explore": {"route": "explore", "tdd_exempt": True, "coverage_exempt": False,
                    "when_to_use": "Investigation/analysis that produces NO code or file change."},
        "Docs":    {"route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Markdown/docs ONLY — no code touched at all."},
        "Config":  {"route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Edits .env/.yaml/.json config with NO business logic."},
        "Chore":   {"route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Dependencies, tooling, CI/CD, build scripts with NO feature code."},
        "Manual":  {"route": "manual",   "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Requires a HUMAN — UI walkthrough, cross-browser check, staging deploy."},
        "Migrate": {"route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Framework/version migration, package rename, or major-dep bump where an EXISTING test suite is the safety net.",
                    "workflow": "The existing suite's red state is the START, green is the GOAL. No Step 3 (Red); Step 4 (Green) is the whole task; commit fix(migrate): …; no Step 6 coverage gate."},
    },
}


def _plugin_root() -> Path:
    """Resolve the plugin root, preferring ``$CLAUDE_PLUGIN_ROOT`` when it matches
    the ``__file__``-derived root (same ground-truth-first discipline as
    ``lib.env.get_plugin_root``). This module is at
    ``<plugin>/scripts/track_state/task_profiles.py``.
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

    ``<plugin>/templates/workflow/task-type-profiles.json``. This is the only
    baseline candidate — the prior ``<plugin>/conductor/workflow/`` candidate was
    dead weight (that dir does not exist in the plugin tree and setup never
    populates it).
    """
    return _plugin_root() / "templates" / "workflow" / "task-type-profiles.json"


def _project_root() -> Path | None:
    """Resolve the *project* root (NOT the plugin root), or ``None`` when not in
    a real project tree.

    Same ladder as :func:`lib.env.resolve_data_dir` / ``infer_project_dir_from_payload`:

    1. ``$CLAUDE_PROJECT_DIR`` (Claude Code's authoritative injection) when set;
    2. else the cwd, **but only if** ``$cwd/conductor/tracks/`` is a dir — the
       repo's "this is a real project, not the plugin repo" signal; without this
       gate a stray cwd could accidentally pick up an unintended file;
    3. else ``None`` (no project, no overlay).

    Inlined (not an import of ``lib.env``): this module is imported transitively
    by ``helpers.py`` and the standalone hook scripts, and ``lib.env`` resolution
    can raise — inlining keeps the resolution local and the fail-open boundary
    tight (dispatch must never crash over project-dir resolution).
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

    ``<project>/conductor/workflow/task-type-profiles.json`` — opt-in by file
    presence (absent file = plugin defaults, zero behavior change). Resolved via
    :func:`_project_root`, so the override only loads from a real project tree.
    """
    root = _project_root()
    if root is None:
        return None
    return root / "conductor" / "workflow" / "task-type-profiles.json"


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
            tags = data.get("tags")
            default = data.get("default")
            if isinstance(tags, dict) and isinstance(default, dict):
                return data
            print(
                f"WARNING: task-type registry at {cand} has invalid shape "
                f"(missing 'tags'/'default'); using built-in fallback values.",
                file=sys.stderr,
            )
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: task-type registry at {cand} unreadable ({exc}); "
            f"using built-in fallback values.",
            file=sys.stderr,
        )
    return _FALLBACK


def _merge_overlay(baseline: dict) -> dict:
    """Shallow-merge a project overlay onto the baseline, if present.

    ``tags``: project overlays baseline (project tags added; project wins a
    conflicting tag — more-specific intent). ``default``: project wins per-key
    if declared (``{**baseline_default, **overlay_default}``). The return shape
    is identical to the baseline's, so every consumer (``TAG_VOCAB``,
    ``route_for``, ``is_*_exempt``, ``derive_task_type``, ``_profile``) is
    overlay-blind — this merge is the single chokepoint that flows everywhere.

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
            f"WARNING: project task-type overlay at {overlay_path} unreadable "
            f"({exc}); using plugin baseline alone.",
            file=sys.stderr,
        )
        return baseline
    if not isinstance(overlay, dict):
        print(
            f"WARNING: project task-type overlay at {overlay_path} has invalid "
            f"shape (not an object); using plugin baseline alone.",
            file=sys.stderr,
        )
        return baseline

    merged_tags = dict(baseline.get("tags", {}))
    overlay_tags = overlay.get("tags")
    if isinstance(overlay_tags, dict):
        merged_tags.update(overlay_tags)

    merged_default = dict(baseline.get("default", {}))
    overlay_default = overlay.get("default")
    if isinstance(overlay_default, dict):
        merged_default.update(overlay_default)

    return {"default": merged_default, "tags": merged_tags}


@lru_cache(maxsize=1)
def _load() -> dict:
    """Load + cache the resolved registry (plugin baseline ⊕ project overlay).

    The baseline always loads (fail-open to :data:`_FALLBACK`); the project
    overlay, if present at ``<project>/conductor/workflow/task-type-profiles.json``,
    merges on top (project wins conflicts). Cached so the merge runs once.
    """
    baseline = _load_baseline()
    return _merge_overlay(baseline)


def _profile(tag: str) -> dict:
    """The profile for a single tag name, falling back to the default profile."""
    data = _load()
    prof = data["tags"].get(tag)
    if prof is None:
        return data["default"]
    # Inherit any missing key from the default profile so a registry row only
    # has to state what it overrides.
    merged = dict(data["default"])
    merged.update(prof)
    return merged


# --- public API -----------------------------------------------------------------

def TAG_VOCAB() -> tuple[str, ...]:
    """The closed vocabulary of known task-type tag names, in registry order.

    This is the single source ``helpers.extract_tags``/``strip_tags`` build their
    regexes from, so the registry *is* the vocab. Returns a tuple (not a live
    view) for stable regex alternation.
    """
    return tuple(_load()["tags"].keys())


def route_for(tags: list[str]) -> str:
    """Dispatch category for a task: ``manual`` | ``explore`` | ``executor``.

    ``tags`` is the list from :func:`helpers.extract_tags` (may be empty). Each
    tag's route is read from its registry profile (so a project overlay can
    change a tag's routing). Precedence when multiple routed tags are present:
    ``manual`` beats ``explore`` beats ``executor`` — mirroring the pre-registry
    ``_classify_task`` where Manual took priority over Explore. With the shipped
    registry (Manual→manual, Explore→explore) this is behavior-identical to the
    old hardcoded short-circuit.
    """
    # Priority of non-executor routes when more than one is present.
    for priority in ("manual", "explore"):
        for t in tags:
            route = _profile(t).get("route", "executor")
            if route == priority:
                return priority
    return "executor"


def is_tdd_exempt(tags: list[str]) -> bool:
    """True if the task is exempt from the TDD gate.

    A task is exempt if ANY of its tags is tdd_exempt. An empty tag list uses the
    ``default`` profile (not exempt) — matching the pre-registry set
    ``{Explore,Docs,Config,Chore,Manual,Migrate}``.
    """
    if not tags:
        return bool(_load()["default"].get("tdd_exempt", False))
    return any(_profile(t).get("tdd_exempt", False) for t in tags)


def is_coverage_exempt(tags: list[str]) -> bool:
    """True if the task is exempt from the coverage (F2/F3) gate.

    A task is exempt if ANY of its tags is coverage_exempt. An empty tag list
    uses the ``default`` profile (not exempt) — matching the pre-registry set
    ``{Docs,Config,Chore,Manual,Migrate}`` (Explore is deliberately NOT
    coverage-exempt).
    """
    if not tags:
        return bool(_load()["default"].get("coverage_exempt", False))
    return any(_profile(t).get("coverage_exempt", False) for t in tags)


def derive_task_type(name: str) -> str:
    """The lowercased primary task type for a name, or ``"default"`` when untagged.

    This is the value written to ``track-state.json``'s ``task_type`` field — a
    typed mirror of the leading tag. The name string remains the authoritative
    source (reconcile/sync key on it); this field is a cache the spine reads
    instead of re-parsing.
    """
    # Imported lazily to avoid a circular import (helpers imports this module).
    from .helpers import extract_tags

    tags = extract_tags(name)
    return tags[0].lower() if tags else "default"


def workflow_for(tag: str) -> str:
    """The prompt-shaping ``workflow`` prose injected into task-executor for a tag.

    This is the mirror of verify-mode :func:`verify_mode_profiles.protocol_for`:
    prose that *used* to live inline as a branch in ``agents/task-executor.md``
    (``[Migrate]`` §4.M) is lifted into the registry so the executor's §4.0 tag
    branch can be tag-agnostic — it follows the ``workflow`` of its leading tag
    rather than knowing each tag's behavior. Absent (the common case) = ``""`` =
    default TDD; the executor runs the full Steps 3-8 cycle.

    A project overlay may add a ``workflow`` for a project-specific tag (e.g.
    ``[K8sRollout]`` with bespoke rollout prose) and it flows to task-executor
    at dispatch with zero plugin edits — the ``[Migrate]`` generalization.
    """
    return _profile(tag).get("workflow", "")


def when_to_use_for(tag: str) -> str:
    """The one-line ``when to use this tag`` hint, injected into spec-planner.

    Lets spec-planner's tag-decision guidance be data-driven: the closed tag
    vocabulary AND the per-tag "when" hint both come from the registry, so a
    project overlay's tags are surfaced to the planner rather than refused as
    "outside the closed set." Absent = ``""`` (no hint injected for that tag).
    """
    return _profile(tag).get("when_to_use", "")
