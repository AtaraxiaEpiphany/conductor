"""Task-type registry — the single source of truth for task-type semantics.

The tag *name* still lives in plan.md task names (e.g. ``[Docs] Add X``) and
is re-extracted at every read via :func:`helpers.extract_tags`. This module holds
what the tag *means*: which dispatch category it routes to, whether it is exempt
from the TDD / coverage gates, a one-line ``when_to_use`` hint (injected into
spec-planner so its tag-decision guidance is data-driven), and — for tags whose
executor behavior diverges from default TDD — a ``workflow`` field (injected into
task-executor). It replaces the hardcoded exemption sets that used to live in
``helpers.py`` (``_tag_exempt_from_tdd`` / ``_tag_exempt_from_coverage``) and the
branch table in ``dispatch._classify_task``.

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
                    "when_to_use": "Investigation/analysis that produces NO code or file change.",
                    "signals": ["explore", "investigate", "map", "survey", "architecture", "analyze", "spike"]},
        "Docs":    {"route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Markdown/docs ONLY — no code touched at all.",
                    "signals": ["docs", "documentation", "readme", "markdown", ".md", "changelog", "guide", "tutorial", "design doc", "write the doc", "draft the doc", "spec doc"]},
        "Config":  {"route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Edits .env/.yaml/.json config with NO business logic.",
                    "signals": ["config", "configuration", ".env", ".yaml", ".yml", ".json", "settings", "feature flag"]},
        "Chore":   {"route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Dependencies, tooling, CI/CD, build scripts with NO feature code.",
                    "signals": ["dependency", "dependencies", "tooling", "ci", "cicd", "build script", "lint", "format", "renovate"]},
        "Manual":  {"route": "manual",   "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Requires a HUMAN — UI walkthrough, cross-browser check, staging deploy.",
                    "signals": ["human", "manual", "walkthrough", "cross-browser", "staging deploy", "accessibility", "by hand", "visual check"]},
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
    ``{Explore,Docs,Config,Chore,Manual}`` (``[Refactor]`` is NOT exempt:
    it still owes TDD/coverage, only the tactical-refactor flag is set).
    """
    if not tags:
        return bool(_load()["default"].get("tdd_exempt", False))
    return any(_profile(t).get("tdd_exempt", False) for t in tags)


def is_coverage_exempt(tags: list[str]) -> bool:
    """True if the task is exempt from the coverage (F2/F3) gate.

    A task is exempt if ANY of its tags is coverage_exempt. An empty tag list
    uses the ``default`` profile (not exempt) — matching the pre-registry set
    ``{Docs,Config,Chore,Manual}`` (Explore is deliberately NOT
    coverage-exempt).
    """
    if not tags:
        return bool(_load()["default"].get("coverage_exempt", False))
    return any(_profile(t).get("coverage_exempt", False) for t in tags)


def has_over_tag_risk(tag: str) -> bool:
    """True if a tag is an over-tagging risk in :func:`derive_task_tag`.

    The data-driven form of the bare ``winner in ("Docs", "Config", "Chore")``
    check the signal classifier used to hardcode — a tag that is tempting-but-
    wrong for feature work that merely *touches* its surface (Docs/Config/Chore
    today) declares ``over_tag_risk: true`` here, so an overlay exemption tag
    with the same risk joins the over-tag guard with zero code edits. Absent on a
    row => ``False`` (the default; most tags carry no over-tagging risk).

    Takes a single tag (the classifier's ``winner``), not a list — the guard is a
    per-tag property of the one tag the classifier settled on.
    """
    return bool(_profile(tag).get("over_tag_risk", False))


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

    Prose that *used* to live inline as a branch in ``agents/task-executor.md``
    is lifted into the registry so the executor's §4.0 tag branch can be
    tag-agnostic — it follows the ``workflow`` of its leading tag rather than
    knowing each tag's behavior. Absent (the common case) = ``""`` = default TDD;
    the executor runs the full Steps 3-8 cycle.

    A project overlay may add a ``workflow`` for a project-specific tag (e.g.
    ``[K8sRollout]`` with bespoke rollout prose) and it flows to task-executor
    at dispatch with zero plugin edits.
    """
    return _profile(tag).get("workflow", "")


def refactor_for(tag: str) -> bool:
    """True if the tactical refactorer should run after a task with this leading tag.

    The declarative form of the ``[Refactor]`` name marker /
    ``CONDUCTOR_TASK_REFACTOR=1`` env — a task-type row sets ``refactor: true`` to
    opt a whole class of tasks into the §3.6c tactical-refactor seam with zero
    plugin edits. Today only ``[Refactor]`` carries it; a project overlay may set
    it on a project-specific tag the same way (the ``[Refactor]`` generalization).
    Absent/False = no tactical refactor (the default; the inline mechanical Step 5
    refactor still runs in-task — see :mod:`agents.task-executor` §4.0).
    """
    return bool(_profile(tag).get("refactor", False))


def when_to_use_for(tag: str) -> str:
    """The one-line ``when to use this tag`` hint, injected into spec-planner.

    Lets spec-planner's tag-decision guidance be data-driven: the closed tag
    vocabulary AND the per-tag "when" hint both come from the registry, so a
    project overlay's tags are surfaced to the planner rather than refused as
    "outside the closed set." Absent = ``""`` (no hint injected for that tag).
    """
    return _profile(tag).get("when_to_use", "")


def _signals_for(tag: str) -> list[str]:
    """The keyword set :func:`derive_task_tag` matches a description against.

    Taken from the registry row's optional ``signals`` array (the overlay-aware
    source a project tag flows through with zero code edits). When a row omits
    ``signals``, a minimal set is derived by lowering :func:`when_to_use_for`
    and keeping its alphabetic tokens of length >= 4 — weaker matching, but the
    mechanism never *depends* on ``signals`` being present (a registry without
    it still classifies, just less precisely). Always lowercased for matching.
    """
    prof = _profile(tag)
    raw = prof.get("signals")
    if isinstance(raw, list) and raw:
        return [str(s).lower() for s in raw]
    hint = when_to_use_for(tag).lower()
    # Minimal fallback: alphabetic tokens of length >= 4 from the when_to_use
    # hint (filters out stopwords like "the/with/that"). This is deliberately
    # coarse — `signals` is the quality path; this is just "better than nothing."
    return [t for t in (
        "".join(ch for ch in w if ch.isalpha()) for w in hint.split()
    ) if len(t) >= 4]


# Words that mark a task as genuine business-logic work — the over-tagging guard.
# If ANY appears and no exemption tag clears the plurality bar, the task stays
# untagged (default TDD), even if it incidentally touches a config file or a
# dependency. "Feature work that happens to edit a config" is the classic trap
# this blocks: over-tagging silently skips TDD and the coverage gate.
#
# Phrases marked "(not: ...)" are exempted from the guard when followed by the
# listed continuation — e.g. "feature" is a feature marker, but "feature flag"
# is a config concern, so "feature flag" is subtracted before the marker scan.
_FEATURE_MARKER_PHRASES = (
    "feature", "implement", "add ", "build a", "create a", "fix ",
    "bug", "logic", "endpoint", "api", "service", "component", "screen",
    "page", "function", "method", "class", "model",
)
# Substrings that, when present, neutralize a "feature" marker (config/infra
# work that happens to contain the word "feature" is NOT feature work).
_FEATURE_MARKER_EXCEPTIONS = ("feature flag", "feature toggle", "feature gate")


def derive_task_tag(description: str) -> str | None:
    """Advisory leading tag for a task DESCRIPTION, or ``None`` (default TDD).

    The inverse of :func:`derive_task_type` (which reads a tag *already on* a
    name string): this classifies **free text that has no tag yet**, by
    signal-matching each registered tag's ``signals`` set. It is the
    registry-driven selection engine for dynamic plan generation — a project
    overlay tag with a ``signals`` field becomes selectable with zero code edits.

    **Safe-failure-mode bias.** ``None`` means "no exemption, full TDD" — the
    correct outcome for the majority of tasks and the safe failure mode: a
    wrongly-untagged ``[Config]`` task costs one extra Red cycle, but a
    wrongly-tagged feature task silently skips TDD and the coverage gate
    (F2/F3 exempt). So the matcher is deliberately conservative:

    - returns a tag only when it wins a **strict plurality** of distinct signal
      hits AND clears a minimum-hit bar (>= 2 distinct hits);
    - feature work (descriptions carrying a :data:`_FEATURE_MARKERS` term with no
      stronger exemption signal) returns ``None`` even if it incidentally
      matches an exemption tag's signals;
    - ``[Manual]`` requires a human-action signal;
    - an opt-in modifier tag (``refactor: true``, today ``[Refactor]``) is
      **never** auto-derived — it is skipped entirely (a modifier augments a
      primary task, it does not classify one; ``[Refactor]`` is a deliberate
      opt-in via the leading tag or inline name marker, never a goal detection).

    This is **advisory only** — :func:`track_state.init_from_plan` still
    hard-validates the final tag against the resolved registry, so an
    over-confident return is caught at plan-init. Fail-open: any exception
    returns ``None`` (never raises into a caller).
    """
    try:
        if not description or not description.strip():
            return None
        text = description.lower()

        scores: dict[str, int] = {}
        for tag in TAG_VOCAB():
            # An opt-in modifier tag (refactor: true) is NEVER auto-derived as a
            # leading tag — it augments whatever the primary task is, it does not
            # classify it. Without this guard, [Refactor]'s when_to_use tokens
            # ("refactor"/"extract"/"simplify") would auto-propose [Refactor] for
            # any readability tweak, silently opting it into the tactical refactorer.
            # [Refactor] is a deliberate opt-in (leading tag or inline marker), full stop.
            if _profile(tag).get("refactor"):
                continue
            hits = sum(1 for sig in _signals_for(tag) if sig and sig in text)
            if hits:
                scores[tag] = hits

        if not scores:
            return None

        # Strict plurality: a unique winner with more hits than every other.
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        winner, top = ranked[0]
        if len(ranked) > 1 and ranked[1][1] >= top:
            return None  # tied — ambiguous, refuse to guess into an exemption

        # Over-tagging guard. Feature work that merely *touches* an exemption
        # surface stays untagged. Neutralize "feature flag"/"feature toggle"
        # first (config/infra that contains the word "feature" is NOT feature
        # work), then if a remaining feature marker is present AND the winning
        # tag did not clear a comfortable plurality (top >= 2 distinct signals,
        # i.e. the exemption signal is strong, not incidental), refuse to tag.
        guard_text = text
        for ex in _FEATURE_MARKER_EXCEPTIONS:
            guard_text = guard_text.replace(ex, "")
        # over_tag_risk is read from the registry (has_over_tag_risk), not a
        # bare ('Docs','Config','Chore') literal, so an overlay exemption tag
        # with the same over-tagging risk joins this guard with zero code edits.
        if has_over_tag_risk(winner) and top < 2 and any(
            m in guard_text for m in _FEATURE_MARKER_PHRASES
        ):
            return None

        return winner
    except Exception:
        return None
