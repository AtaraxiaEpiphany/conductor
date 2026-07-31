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
import re
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
            "when_to_use": "A mid-migration phase whose goal is \"it compiles\" — the test suite is expected red, the build is the gate.",
            "fix_policy": "none",
            "ignore": ["test-suite"],
            "report_field": "BUILD",
        },
        "test": {
            "runs": ["test-suite"],
            "when_to_use": "Explicitly opt back into the suite gate, e.g. as part of test,start. Alone, equivalent to omitting the directive.",
            "fix_policy": "fix-and-retry",
            "report_field": "L1_VERIFY",
        },
        "start": {
            "runs": ["boot-smoke"],
            "when_to_use": "A phase whose deliverable includes \"the app boots.\" Typically combined with test on the final integration phase, or with compile on the phase that first achieves a bootable build.",
            "fix_policy": "fail-fast",
            "report_field": "START",
        },
        "anchor": {
            "runs": ["frozen-subset"],
            "when_to_use": "A phase whose safety net is the frozen subset, not the full suite. A refactoring phase where the broader suite is in flux but the pinned anchor must hold.",
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


def when_to_use_for(mode: str) -> str:
    """The one-line ``when_to_use`` hint for a mode, or ``""``.

    Sourced from the registry row (mirrors :func:`task_profiles.when_to_use_for`
    for tags). This is the signal surface :func:`derive_verify_modes` matches a
    phase goal against to ADVISORILY classify it into modes. Absent on a row
    => the derivation falls back to tokens lifted from :func:`protocol_for`
    (weaker matching), so the mechanism never *depends* on the field being
    present.
    """
    return _profile(mode).get("when_to_use", "")


def _mode_signals(mode: str) -> list[str]:
    """Lowercased keyword signals for a mode, derived from its ``when_to_use``.

    There is no explicit ``signals`` array on verify-mode rows (unlike task-type
    rows) — modes are a small fixed set whose ``when_to_use`` is already a dense
    keyword surface, so we tokenize it directly. ≥4-char alphabetic tokens are
    kept; stop-words and short tokens are dropped. This is the weaker-but-
    adequate path; the quality path is a richer ``when_to_use`` string, which the
    contract table already provides.
    """
    text = when_to_use_for(mode).lower()
    if not text:
        # protocol_for is the floor — better than nothing when when_to_use is absent.
        text = protocol_for(mode).lower()
    tokens = []
    for tok in text.split():
        cleaned = "".join(ch for ch in tok if ch.isalpha())
        if len(cleaned) >= 4:
            tokens.append(cleaned)
    return tokens


def _any_in(text: str, signals: tuple[str, ...]) -> bool:
    """True if any signal string is a substring of ``text`` (both lowered).

    A tiny helper so the precedence branches read as signal sets, not
    ``any(... for ... in ...)`` noise. Kept as plain substring matching for the
    multi-word phrases (e.g. ``"start the app"``) where word boundaries would
    over-split; the lone substring-prone token (``boot``) is handled with a
    regex lookbehind at its call site, not here.
    """
    return any(s in text for s in signals)


def derive_verify_modes(phase_goal: str) -> list[str]:
    """Advisory verify-mode directive for a phase GOAL, or ``[]`` (default gate).

    The verify-mode analog of :func:`task_profiles.derive_task_tag`. Given a
    phase's goal/description as free text, propose the modes a
    ``<!-- verify: <modes> -->`` directive SHOULD carry. Empty = emit no
    directive = the default full gate (the correct outcome for feature work).

    This is advisory-only: ``init-from-plan --check`` still **warns** (not
    blocks) on the final directive, and the phase-checker no-ops ``anchor`` on an
    unfrozen track — so a wrong proposal is self-correcting at the gate, never
    fatal. The realistic ceiling is "generator proposes, ``--check`` disposes";
    do not over-tune the keyword sets chasing precision.

    Resolution rules (the real decision logic, encoded explicitly):

    * **Migration intermediate** ("compiles"/"compile"/"build" without a
      boot/suite-green signal) → ``["compile"]``. The suite is expected red;
      the build is the gate.
    * **Final integration** ("boots"/"starts"/"ready" — the app must run) →
      ``["test", "start"]``. Suite green AND boot smoke.
    * **Refactor with frozen anchor** ("refactor"/"tech debt" AND "frozen"/
      "anchor"/"pinned") → ``["anchor"]`` (advisory; no-ops on an unfrozen
      track — the existing graceful degradation).
    * **Plain feature work** (none of the above) → ``[]`` (default full gate).

    Fail-open: any ambiguity or exception → ``[]``. Never invents a mode not in
    :func:`MODE_VOCAB`; never raises into a caller.
    """
    try:
        if not phase_goal or not phase_goal.strip():
            return []
        text = phase_goal.lower()

        # Final integration: the app must boot — the strongest signal (start is
        # only ever meaningful combined with a green gate, so test,start together).
        # In a *phase goal* description these tokens overwhelmingly mean "the app
        # runs"; false positives (bootstrap/bootloader code) don't appear in goals.
        # ``boot`` is matched with a negative lookbehind so it does NOT false-fire
        # inside "spring-boot" (the classic substring trap — a migration goal
        # mentioning the Spring Boot framework is a *compile* goal, not a
        # boot-smoke goal). The lookbehind excludes word chars AND hyphen.
        if re.search(r"(?<![-\w])boot", text) or _any_in(text, (
            "boots", "starts up", "app starts", "the app starts",
            "start the app", "starts the app", "run the app",
            "boot smoke", "startup stack trace",
        )):
            return ["test", "start"]

        # Refactor with a frozen anchor: the suite is in flux, the pinned
        # subset is the safety net. Requires BOTH a refactor intent and an
        # anchor signal — a plain "refactor for readability" is just default
        # TDD work, not an anchor phase.
        refactor_signals = ("refactor", "tech debt", "tech-debt", "restructure",
                            "consolidate", "cleanup", "clean up", "tidy")
        anchor_signals = ("anchor", "frozen", "pinned", "subset", "regression set",
                          "counter-anchor", "goodhart")
        if (_any_in(text, refactor_signals) and _any_in(text, anchor_signals)):
            return ["anchor"]

        # Migration intermediate: it compiles. The suite is expected red, so the
        # build — NOT the suite — is the gate. "compile"/"build" without a boot
        # signal (handled above) lands here.
        compile_signals = ("compile", "compiles", "compilation", "build",
                           "it builds", "type-check", "typecheck")
        migration_signals = ("migrat", "upgrade", "bump", "rename", "javax",
                             "jakarta", "framework version", "deprecation",
                             "major dependency", "spring boot")
        if _any_in(text, compile_signals):
            return ["compile"]
        # A migration goal WITHOUT an explicit compile/boot word: the safest
        # intermediate-phase proposal is still compile (the suite is red), but
        # only if the goal reads as an *intermediate* migration step, not the
        # final "tests pass" step. We require a migration signal AND that the
        # goal does NOT promise a green suite ("tests pass"/"green"/"suite").
        suite_green_signals = ("tests pass", "test suite passes", "green",
                               "suite green", "all tests")
        if (_any_in(text, migration_signals)
                and not _any_in(text, suite_green_signals)):
            return ["compile"]

        # Default: feature work, or a migration's final "tests pass" phase → the
        # full gate. Emitting [] (no directive) is the correct, safe outcome.
        return []
    except Exception:
        return []


def default_verify_for_phase(task_tags: list[str]) -> list[str]:
    """Tag-driven default verify-modes for a phase, or ``[]`` (full gate).

    The tag-driven analog of :func:`derive_verify_modes`: where that one reads a
    phase's *goal text*, this one reads the phase's *task tags*. For each
    top-level task tag it pulls that tag's ``default_verify``
    (:func:`task_profiles.default_verify_for`) and reduces the set across the
    phase. spec-planner composes the two at authoring time — see the precedence
    in ``plan-format-contract.md`` §"Phase Verify Directives → Default source":
    explicit directive > this tag-derived default > goal-derived
    :func:`derive_verify_modes` > full gate.

    Reduction rules:
    * **No tag contributes** (every tag's ``default_verify`` is empty) → ``[]``.
    * **Agreement** — every contributing tag proposes the SAME mode set → that
      set (order taken from the first contributor; de-duped). Two ``[Migrate]``
      tasks both proposing ``["compile"]`` is agreement, not a conflict.
    * **Conflict** — two tags propose DIFFERENT non-empty sets → ``[]``. A
      mixed-type phase has no single gate semantics, so the safe default (the
      full gate) wins rather than the planner silently picking one tag's modes.

    Pure, fail-open: any exception → ``[]`` (never raises into a caller). Empty
    or non-list input → ``[]``.
    """
    try:
        if not task_tags:
            return []
        # Lazy import: task_profiles.default_verify_for imports MODE_VOCAB from
        # THIS module at call time (not load time), so the cycle is already
        # broken in that direction; importing task_profiles here at call time
        # keeps this function self-contained and the dependency direction
        # explicit (the reducer's RESULT is a verify-mode directive).
        from .task_profiles import default_verify_for

        contributed: list[list[str]] = []
        for tag in task_tags:
            modes = default_verify_for(tag)
            if modes:
                contributed.append(modes)
        if not contributed:
            return []

        # Agreement check: every non-empty contribution must equal the first.
        # Sets, because order within one tag's list is not load-bearing for the
        # conflict decision (a tag proposing ["compile"] vs ["test"] is a real
        # conflict regardless of order); the returned list preserves the first
        # contributor's order so the emitted directive is stable.
        first = contributed[0]
        first_set = set(first)
        for modes in contributed[1:]:
            if set(modes) != first_set:
                return []  # conflict → full gate
        # De-dup the first contributor's list preserving order.
        seen: set[str] = set()
        out: list[str] = []
        for m in first:
            if m not in seen:
                seen.add(m)
                out.append(m)
        return out
    except Exception:
        return []

