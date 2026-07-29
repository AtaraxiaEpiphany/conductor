"""Phase-verify-mode registry — the single source of truth for verify-mode semantics.

The mode *name* still lives in plan.md phase headings (e.g.
``## Phase 1: X <!-- verify: compile -->``) and is re-extracted at every read via
:func:`plan_parse._extract_verify`. This module holds what the mode *means*: which
gate steps it performs, its fix policy, and — crucially — the prompt-shaping
``protocol`` prose the phase-checker emits for it. It replaces the hardcoded
``_VERIFY_MODES`` tuple in ``plan_parse.py`` (line ~130) AND the per-mode
``if/elif`` branch ladder that lived as prose in ``agents/phase-checker.md``
(Step-3 addendum). Adding a verify-mode is now one JSON row in the registry with
zero Python edits and zero agent-prose edits — the phase-checker loop reads each
mode's ``protocol`` from here.

The registry resolves as **plugin baseline ⊕ project overlay**, identical in
mechanics to :mod:`task_profiles`. The baseline is the JSON data file shipped at
``templates/workflow/verify-mode-profiles.json``. A project may drop
``conductor/workflow/verify-mode-profiles.json`` (alongside the other workflow
files setup scaffolds there) to add project-specific modes or override a built-in
mode's semantics — opt-in by file presence (absent = plugin defaults, zero
behavior change). The overlay merges over the baseline: project modes are added,
the project wins a conflicting mode, and a project ``default`` profile wins
per-key. Loading is **fail-open**: if the baseline is missing or unparseable, we
fall back to ``_FALLBACK`` (a verbatim copy of the pre-registry hardcoded values)
and log loudly to stderr; a malformed overlay falls back to the baseline alone —
the phase-checker must never crash over a malformed registry. The fallback is the
emergency mirror that keeps the plugin working even if the data file is deleted;
it is deliberately a separate constant so a future editor can see exactly what
the registry is supposed to contain.

This is the OpenSpec pattern (see ``openspec-design-architecture.md`` §5): a
generic engine (the phase-checker's directive loop) behind a declarative schema
(this registry), where each unit (a verify-mode) carries its own instruction (the
``protocol`` prose) rather than being a branch in a shared ``if/elif``.
"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path


# --- fallback: verbatim copy of the pre-registry hardcoded values ----------------
# DO NOT edit this to change a mode's behavior — edit the registry JSON instead.
# This exists ONLY so a missing/malformed registry never crashes the phase-checker.
# If you find yourself changing a value here, you are changing the fail-open floor,
# not the real config.
_FALLBACK = {
    "default": {
        "runs": ["test-suite"],
        "fix_policy": "fix-and-retry",
        "max_fix_attempts": 2,
    },
    "modes": {
        "compile": {
            "runs": ["build"],
            "fix_policy": "none",
            "ignore": ["test-suite"],
            "report_field": "BUILD",
        },
        "test": {
            "runs": ["test-suite"],
            "fix_policy": "fix-and-retry",
            "report_field": "L1_VERIFY",
        },
        "start": {
            "runs": ["boot-smoke"],
            "fix_policy": "fail-fast",
            "report_field": "START",
        },
        "anchor": {
            "runs": ["frozen-subset"],
            "fix_policy": "none",
            "report_field": "ANCHOR",
        },
    },
}


def _plugin_root() -> Path:
    """Resolve the plugin root, preferring ``$CLAUDE_PLUGIN_ROOT`` when it matches
    the ``__file__``-derived root (same ground-truth-first discipline as
    ``lib.env.get_plugin_root`` and :func:`task_profiles._plugin_root`). This
    module is at ``<plugin>/scripts/track_state/verify_mode_profiles.py``.
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

    ``<plugin>/templates/workflow/verify-mode-profiles.json``.
    """
    return _plugin_root() / "templates" / "workflow" / "verify-mode-profiles.json"


def _project_root() -> Path | None:
    """Resolve the *project* root (NOT the plugin root), or ``None`` when not in
    a real project tree.

    Same ladder as :func:`task_profiles._project_root` (and
    :func:`lib.env.resolve_data_dir` / ``infer_project_dir_from_payload``):

    1. ``$CLAUDE_PROJECT_DIR`` (Claude Code's authoritative injection) when set;
    2. else the cwd, **but only if** ``$cwd/conductor/tracks/`` is a dir — the
       repo's "this is a real project, not the plugin repo" signal; without this
       gate a stray cwd could accidentally pick up an unintended file;
    3. else ``None`` (no project, no overlay).

    Inlined (not an import of ``lib.env``): this module is imported transitively
    by ``helpers.py`` and the standalone hook scripts, and ``lib.env`` resolution
    can raise — inlining keeps the resolution local and the fail-open boundary
    tight (the phase-checker must never crash over project-dir resolution).
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

    ``<project>/conductor/workflow/verify-mode-profiles.json`` — opt-in by file
    presence (absent file = plugin defaults, zero behavior change). Resolved via
    :func:`_project_root`, so the override only loads from a real project tree.
    """
    root = _project_root()
    if root is None:
        return None
    return root / "conductor" / "workflow" / "verify-mode-profiles.json"


def _load_baseline() -> dict:
    """Load the plugin baseline registry, fail-open to :data:`_FALLBACK`.

    This is the always-present floor: if the shipped registry is missing,
    unparseable, or structurally wrong, we use the hardcoded fallback so the
    phase-checker never crashes over the plugin's own registry.
    """
    cand = _plugin_registry_path()
    try:
        if cand.exists():
            data = json.loads(cand.read_text(encoding="utf-8"))
            modes = data.get("modes")
            default = data.get("default")
            if isinstance(modes, dict) and isinstance(default, dict):
                return data
            print(
                f"WARNING: verify-mode registry at {cand} has invalid shape "
                f"(missing 'modes'/'default'); using built-in fallback values.",
                file=sys.stderr,
            )
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: verify-mode registry at {cand} unreadable ({exc}); "
            f"using built-in fallback values.",
            file=sys.stderr,
        )
    return _FALLBACK


def _merge_overlay(baseline: dict) -> dict:
    """Shallow-merge a project overlay onto the baseline, if present.

    ``modes``: project overlays baseline (project modes added; project wins a
    conflicting mode — more-specific intent). ``default``: project wins per-key
    if declared (``{**baseline_default, **overlay_default}``). The return shape
    is identical to the baseline's, so every consumer (``MODE_VOCAB``,
    ``protocol_for``, ``runs_for``, ``fix_policy_for``, ``_profile``) is
    overlay-blind — this merge is the single chokepoint that flows everywhere.

    Fail-open to *baseline alone* on any overlay read/shape error (the baseline
    is valid; a malformed project file must NOT drag the phase-checker down to
    :data:`_FALLBACK`).
    """
    overlay_path = _project_override_path()
    if overlay_path is None or not overlay_path.exists():
        return baseline
    try:
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: project verify-mode overlay at {overlay_path} unreadable "
            f"({exc}); using plugin baseline alone.",
            file=sys.stderr,
        )
        return baseline
    if not isinstance(overlay, dict):
        print(
            f"WARNING: project verify-mode overlay at {overlay_path} has invalid "
            f"shape (not an object); using plugin baseline alone.",
            file=sys.stderr,
        )
        return baseline

    merged_modes = dict(baseline.get("modes", {}))
    overlay_modes = overlay.get("modes")
    if isinstance(overlay_modes, dict):
        merged_modes.update(overlay_modes)

    merged_default = dict(baseline.get("default", {}))
    overlay_default = overlay.get("default")
    if isinstance(overlay_default, dict):
        merged_default.update(overlay_default)

    return {"default": merged_default, "modes": merged_modes}


@lru_cache(maxsize=1)
def _load() -> dict:
    """Load + cache the resolved registry (plugin baseline ⊕ project overlay).

    The baseline always loads (fail-open to :data:`_FALLBACK`); the project
    overlay, if present at ``<project>/conductor/workflow/verify-mode-profiles.json``,
    merges on top (project wins conflicts). Cached so the merge runs once.
    """
    baseline = _load_baseline()
    return _merge_overlay(baseline)


def _profile(mode: str) -> dict:
    """The profile for a single mode name, falling back to the default profile."""
    data = _load()
    prof = data["modes"].get(mode)
    if prof is None:
        return data["default"]
    # Inherit any missing key from the default profile so a registry row only
    # has to state what it overrides.
    merged = dict(data["default"])
    merged.update(prof)
    return merged


# --- public API -----------------------------------------------------------------

def MODE_VOCAB() -> tuple[str, ...]:
    """The closed vocabulary of known verify-mode names, in registry order.

    This is the single source ``plan_parse`` validates declared modes against, so
    the registry *is* the vocab. Returns a tuple (not a live view) for stable
    comparison. Order matches the baseline file's declaration order so the
    "unrecognized verify mode" warning lists them in a stable, human-friendly
    sequence.
    """
    return tuple(_load()["modes"].keys())


def runs_for(mode: str) -> list[str]:
    """The gate steps this mode performs, in order.

    Step tokens the phase-checker directive loop understands: ``build``,
    ``test-suite``, ``boot-smoke``, ``frozen-subset``. An unknown mode inherits
    the ``default`` runs (``["test-suite"]``).
    """
    return list(_profile(mode).get("runs", ["test-suite"]))


def fix_policy_for(mode: str) -> str:
    """The fix policy for this mode: ``none`` | ``fix-and-retry`` | ``fail-fast``.

    ``none``: report the verdict, no fix attempts (compile/anchor).
    ``fix-and-retry``: on failure, propose a fix up to ``max_fix_attempts`` times
    (the default gate / ``test`` mode).
    ``fail-fast``: a single failure is terminal, no retry (``start``).
    """
    return _profile(mode).get("fix_policy", "fix-and-retry")


def protocol_for(mode: str) -> str:
    """The prompt-shaping ``protocol`` prose the phase-checker emits for this mode.

    This is the text that *used* to live inline as a branch in
    ``agents/phase-checker.md``'s Step-3 directive ``if/elif``. It is now lifted
    into the registry so the phase-checker loop is mode-agnostic — it reads each
    declared mode's protocol from here rather than knowing what the mode does.
    An unknown mode inherits the ``default`` protocol (the full-gate prose).
    """
    return _profile(mode).get("protocol", "")
