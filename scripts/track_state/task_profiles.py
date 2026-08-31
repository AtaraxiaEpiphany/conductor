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
with **zero** Python edits. A tag whose executor behavior needs a full bespoke
workflow declares ``workflow_doc`` instead (a docfile in the steps library —
see :func:`workflow_doc_for`/:func:`resolve_workflow_doc`): one docfile + one
registry row, still zero Python edits.
"""

from __future__ import annotations

import json
import os
import re
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


def _task_is_code_free(tags: list[str]) -> bool:
    """True if a single task produces no code (every one of its tags is
    ``coverage_exempt``).

    The ALL-exempt complement of :func:`is_coverage_exempt`'s ANY — and the
    per-task predicate :func:`phase_is_code_free` composes. ``is_coverage_exempt``
    is ANY on purpose (it answers the F2/F3 gate: "is this task *exempt from the
    coverage gate*"), but "does this task *produce code*?" is the stricter
    question the test-runner fan-out narrows on: a ``[Config][Refactor]`` task
    carries a code-producing tag, so it is NOT code-free even though
    ``is_coverage_exempt([Config, Refactor])`` returns True. Untagged tasks use
    the ``default`` profile (not exempt) → not code-free (conservative — keep
    test-runner).
    """
    if not tags:
        return bool(_load()["default"].get("coverage_exempt", False))
    return all(_profile(t).get("coverage_exempt", False) for t in tags)


def phase_is_code_free(state, phase) -> bool:
    """True if every task in a phase is code-free (no code → no tests).

    The phase-composition predicate the checkpoint fan-out narrows on
    (:func:`dispatch._build_verifier_wave`): a phase of pure
    ``[Config]``/``[Docs]``/``[Chore]``/``[Manual]`` tasks produces no code, so
    test-runner has nothing to run and is dropped from the fan-out. Auto-detected
    from each task's tags (:func:`helpers.extract_tags` on the live name — NOT
    the lowercased ``task_type`` cache dispatch never reads); no directive, no
    authoring — the lightweight alternative to the per-phase verify apparatus.

    Composes :func:`_task_is_code_free` (ALL-exempt per task), NOT
    :func:`is_coverage_exempt` (ANY) — a phase of ``[Config][Refactor]`` tasks
    carries code-producing tags, so it must NOT narrow out test-runner even
    though each task is coverage-exempt under the ANY predicate. Deliberately
    keys on ``coverage_exempt`` (the F2/F3 gate's predicate), NOT ``tdd_exempt``:
    ``[Explore]`` is tdd_exempt but not coverage_exempt, yet an explore-heavy
    track uses the ``research-first`` shape whose ``verify_policy: none`` runs no
    checkpoint at all — so an explore-only phase reaching this fan-out is a
    non-case, and Explore stays out of the "code-free" set. False (conservative —
    keep test-runner) for a mixed phase, an empty phase (malformed, not
    code-free), or an out-of-range index.
    """
    from .helpers import extract_tags
    phases = state.get("phases") or []
    try:
        pi = int(phase) - 1
    except (TypeError, ValueError):
        return False
    if not (0 <= pi < len(phases)):
        return False
    tasks = phases[pi].get("tasks") or []
    if not tasks:
        return False
    return all(_task_is_code_free(extract_tags(t.get("name", ""))) for t in tasks)


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


def strip_dispatch_tags(name: str) -> str:
    """A task name with its dispatch ``[Tag]`` tokens removed.

    The inverse of :func:`derive_task_type`: the lint (:func:`quality`'s
    declared-vs-signals advisory) and the manifest mismatch advisory classify
    the *description* — what the matcher would have seen — so the declared
    tags must come off before ``derive_task_tag`` runs, or the tag's own
    keywords would match themselves.
    """
    # Imported lazily to avoid a circular import (helpers imports this module).
    from .helpers import extract_tags

    out = name
    for tag in extract_tags(name):
        out = out.replace(f"[{tag}]", "")
    return " ".join(out.split())


def derive_child_task_type(parent: dict) -> str:
    """The ``task_type`` for a subtask created/split/absorbed under ``parent``.

    Subtasks never carry their own tag (contract: only top-level tasks are
    tagged), so a new subtask inherits its parent's ``task_type`` — the same
    rule :func:`quality._init_state` applies at construction. Used by the
    mid-track creation paths (``validate._fix_plan_mismatches`` absorb,
    ``mutations._do_split`` / ``reconcile`` split, ``sync`` absorb) so every
    task dict carries the cache, not just init-created ones — otherwise
    :func:`on_subagent_start._resolve_locked_task_type` reads ``None`` for an
    absorbed task and the executor loses its per-tag profile.

    Falls back to re-deriving from the parent *name* when the parent's cached
    field is itself absent (a parent absorbed by a pre-fix run). The name is
    authoritative; this keeps the child's mirror in lockstep with it.
    """
    tt = parent.get("task_type")
    if tt and tt != "default":
        return tt
    return derive_task_type(parent.get("name", ""))


def workflow_for(tag: str) -> str:
    """The prompt-shaping ``workflow`` prose injected into task-executor for a tag.

    Prose that *used* to live inline as a branch in ``agents/task-executor.md``
    is lifted into the registry so the executor's §4.0 tag branch can be
    tag-agnostic — it follows the ``workflow`` of its leading tag rather than
    knowing each tag's behavior. Absent (the common case) = ``""`` = default TDD;
    the executor runs the full Steps 3-8 cycle.

    A project overlay may add a ``workflow`` for a project-specific tag (e.g.
    ``[K8sRollout]`` with bespoke rollout prose) and it flows to task-executor
    at dispatch with zero plugin edits. For a full bespoke workflow prefer the
    docfile form (:func:`workflow_doc_for`) — prose lives in a steps-library
    markdown file, not a JSON string; a declared ``workflow_doc`` wins over
    inline ``workflow`` prose at render time.
    """
    return _profile(tag).get("workflow", "")


#: The docfile every tag without a bespoke ``workflow_doc`` resolves to — the
#: default TDD cycle (Steps 3-8), relocated verbatim from
#: ``templates/task-workflow.md`` into the steps library.
DEFAULT_WORKFLOW_DOC = "default-tdd.md"


def workflow_doc_for(tag: str) -> str:
    """The ``workflow_doc`` docfile NAME for a tag, or ``""`` (default TDD).

    The registry-driven pointer into the **workflow steps library**: a tag row
    declaring ``workflow_doc: "<name>.md"`` gets its step prose from that
    docfile instead of the default TDD cycle. Absent (the common case) = the
    default docfile (:data:`DEFAULT_WORKFLOW_DOC`). Use
    :func:`resolve_workflow_doc` for the actual path — this accessor mirrors
    :func:`workflow_for`'s bare-string shape so presence checks and renders
    treat the two fields uniformly.

    Resolution order (project wins, mirroring the registry overlay rule):
    ``<project>/conductor/workflow/steps/<name>`` over
    ``<plugin>/templates/workflow/steps/<name>`` — a project overrides a
    shipped docfile (or adds a bespoke one) with zero plugin edits.
    """
    doc = _profile(tag).get("workflow_doc", "")
    return doc if isinstance(doc, str) else ""


def resolve_workflow_doc(tag: str) -> Path:
    """The resolvable PATH to a tag's workflow docfile (fail-open to default).

    Resolution: the declared ``workflow_doc`` name — project steps dir over
    plugin steps dir — falling back to :data:`DEFAULT_WORKFLOW_DOC` (plugin
    copy) when the tag declares none, the name is malformed (not a bare
    ``.md`` filename — a path-y name is a typo or traversal attempt, never a
    docfile), or no steps dir holds it. Fail-open with a loud stderr warning,
    never a raise: a bad overlay docfile must not crash dispatch (the same
    contract as :func:`_load_baseline`).
    """
    # Lazy import: the name grammar is single-homed in registry_validate (the
    # strict-write gate enforces the same shape); the same lazy-relative-
    # import pattern as `from .helpers import extract_tags` above.
    from .registry_validate import DOCFILE_NAME_RE

    name = workflow_doc_for(tag)
    if name and not DOCFILE_NAME_RE.match(name):
        print(
            f"WARNING: workflow_doc {name!r} for tag {tag!r} is not a bare "
            f".md filename; falling back to {DEFAULT_WORKFLOW_DOC}.",
            file=sys.stderr,
        )
        name = ""
    if not name:
        name = DEFAULT_WORKFLOW_DOC

    root = _project_root()
    candidates = []
    if root is not None:
        candidates.append(root / "conductor" / "workflow" / "steps" / name)
    plugin_default = (_plugin_root() / "templates" / "workflow" / "steps"
                      / DEFAULT_WORKFLOW_DOC)
    candidates.append(_plugin_root() / "templates" / "workflow" / "steps" / name)
    for cand in candidates:
        if cand.is_file():
            return cand
    if name != DEFAULT_WORKFLOW_DOC:
        print(
            f"WARNING: workflow docfile {name!r} (tag {tag!r}) not found in "
            f"any steps dir; falling back to {DEFAULT_WORKFLOW_DOC}.",
            file=sys.stderr,
        )
        return plugin_default
    # The default docfile itself is missing (plugin install damage) — return
    # the plugin path anyway; the caller's read fails open downstream.
    return plugin_default


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


def auto_propose_for(tag: str) -> bool:
    """True (the default) if :func:`derive_task_tag` may goal-detect this tag.

    ``False`` marks an OPT-IN tag — one that is authored onto a task name and must
    NEVER be inferred from a free-text description. Today both opt-in tags carry
    ``auto_propose: false``: ``[Refactor]`` (a modifier that augments a primary
    task) and ``[Migrate]`` (a behavior-preservation primary). The unified
    mechanism that keeps them out of the advisory classifier — without it,
    ``[Migrate]``'s ``when_to_use`` tokens (refactor/upgrade/rename) would
    auto-propose it and silently drop TDD/coverage, and ``[Refactor]``'s would
    auto-trigger the tactical refactorer. Distinct from :func:`refactor_for`
    (which gates the refactorer, not classification). Absent = ``True`` (the tag
    is a normal goal-detection candidate — the common case).
    """
    return bool(_profile(tag).get("auto_propose", True))


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
    De-duped (order-preserving): a duplicate token in a row would otherwise
    double-count a single hit in :func:`derive_task_tag`'s plurality score, so a
    repeated signal is collapsed here rather than scored twice.
    """
    prof = _profile(tag)
    raw = prof.get("signals")
    if isinstance(raw, list) and raw:
        return _dedupe_signals(str(s).lower() for s in raw)
    hint = when_to_use_for(tag).lower()
    # Minimal fallback: alphabetic tokens of length >= 4 from the when_to_use
    # hint (filters out stopwords like "the/with/that"). This is deliberately
    # coarse — `signals` is the quality path; this is just "better than nothing."
    return _dedupe_signals(
        t for t in ("".join(ch for ch in w if ch.isalpha()) for w in hint.split())
        if len(t) >= 4
    )


def _dedupe_signals(signals):
    """Lowercased, de-duped, order-preserving copy of an iterable of signals."""
    seen, out = set(), []
    for s in signals:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _signal_in(sig: str, text: str) -> bool:
    """Word-boundary-aware signal match — the fix for bare ``sig in text``.

    A bare substring check let short alpha signals (``ci``, ``map``) match
    inside longer words (``ci`` in "discipline"/"specificity"), inflating
    plurality scores past the over-tag guard. A signal now matches only when it
    is not glued to extra *letters* on either flank: a letter-flanked signal
    edge requires a non-letter (or string edge) beside it, while a
    punctuation-flanked edge (file extensions like ``.md``/``.env``, hyphenated
    ``cross-browser``) is unconstrained on that side — so ``.md`` still matches
    "readme.md" but ``ci`` no longer matches "discipline".
    """
    if not sig:
        return False
    pat = re.escape(sig)
    if sig[0].isalpha():
        pat = r"(?<![A-Za-z])" + pat
    if sig[-1].isalpha():
        pat = pat + r"(?![A-Za-z])"
    return re.search(pat, text) is not None


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


def rank_tags(text: str) -> list[dict]:
    """Scored tag candidates for a free-text DESCRIPTION — the lint engine core.

    The planning-layer mirror of :func:`workflow_shapes.rank_shapes`: pure
    signal-matching over the resolved registry, deterministic, no plurality /
    guard / fail-open — those policies belong to the consumer. Each auto-
    proposable tag (``auto_propose: false`` rows are skipped — opt-in tags are
    authored, never surfaced) is scored by distinct ``signals`` hits
    (:func:`_signal_in`, word-boundary aware). Returns ``[{"tag", "score",
    "hits"}]``, score-descending with registry order stable within a tie,
    capped at the top 3. Empty/blank text scores nothing (``[]``).
    """
    if not text or not text.strip():
        return []
    lowered = text.lower()
    candidates = []
    for tag in TAG_VOCAB():
        # An opt-in tag (auto_propose: false) is never surfaced — it is authored
        # onto a task name, not inferred from a description ([Refactor],
        # [Migrate]; see derive_task_tag).
        if not auto_propose_for(tag):
            continue
        hits = [sig for sig in _signals_for(tag) if _signal_in(sig, lowered)]
        if hits:
            candidates.append(dict(tag=tag, score=len(hits), hits=hits))
    # Stable sort on score only — registry order breaks ties (no preference
    # among equals; the consumers treat a tie as ambiguity, not a ranking).
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:3]


def derive_task_tag(description: str) -> str | None:
    """Advisory leading tag for a task DESCRIPTION, or ``None`` (default TDD).

    The inverse of :func:`derive_task_type` (which reads a tag *already on*
    a name string): this classifies **free text that has no tag yet**, by
    strict-plurality over :func:`rank_tags` scores. It is the advisory oracle
    behind the init lint's declared-vs-signals comparison (labels themselves
    are planner-authored — decision: task-type ownership) — a project overlay
    tag with a ``signals`` field joins the advisories with zero code edits.

    **Safe-failure-mode bias.** ``None`` means "no exemption, full TDD" — the
    correct outcome for the majority of tasks and the safe failure mode: a
    wrongly-untagged ``[Config]`` task costs one extra Red cycle, but a
    wrongly-tagged feature task silently skips TDD and the coverage gate
    (F2/F3 exempt). So the matcher is deliberately conservative:

    - returns a tag only when it wins a **strict plurality** of distinct signal
      hits (any single hit can enter the ranking; the effective >= 2-hit bar
      exists only inside the over-tagging guard below, where a weak exemption
      signal on feature-marker text is refused);
    - feature work (descriptions carrying a :data:`_FEATURE_MARKERS` term with no
      stronger exemption signal) returns ``None`` even if it incidentally
      matches an exemption tag's signals;
    - ``[Manual]`` requires a human-action signal;
    - an opt-in tag (``auto_propose: false`` — today ``[Refactor]`` the modifier
      and ``[Migrate]`` the behavior-preservation primary) is **never**
      auto-derived — it is skipped entirely (authored onto a task name, never a
      goal detection; ``[Refactor]`` is a deliberate opt-in via the leading tag
      or inline name marker, ``[Migrate]`` via the leading tag on a
      ``migration``-shaped track).

    This is **advisory only** — :func:`track_state.init_from_plan` still
    hard-validates the final tag against the resolved registry, so an
    over-confident return is caught at plan-init. Fail-open: any exception
    returns ``None`` (never raises into a caller).
    """
    try:
        ranked = rank_tags(description)
        if not ranked:
            return None

        # Strict plurality: a unique winner with more hits than every other.
        winner, top = ranked[0]["tag"], ranked[0]["score"]
        if len(ranked) > 1 and ranked[1]["score"] >= top:
            return None  # tied — ambiguous, refuse to guess into an exemption

        # Over-tagging guard. Feature work that merely *touches* an exemption
        # surface stays untagged. Neutralize "feature flag"/"feature toggle"
        # first (config/infra that contains the word "feature" is NOT feature
        # work), then if a remaining feature marker is present AND the winning
        # tag did not clear a comfortable plurality (top >= 2 distinct signals,
        # i.e. the exemption signal is strong, not incidental), refuse to tag.
        guard_text = (description or "").lower()
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
    except Exception as exc:  # noqa: BLE001 — fail-open contract: never raise
        # Surface the defect rather than swallow it silently. A malformed
        # registry row (e.g. a bad overlay: signals as a string, a missing key a
        # future change assumes) would otherwise make EVERY description classify
        # as None (default TDD) with no diagnostic — masking a real registry
        # defect init-from-plan's validator does not cover. Still returns None
        # (the safe default) per the fail-open contract.
        import sys
        print(f"derive_task_tag: classifier failed ({exc!r}); defaulting to None",
              file=sys.stderr)
        return None


# --- overlay generator ----------------------------------------------------------

def tag_add(name, when_to_use=None, route=None, tdd_exempt=False,
            coverage_exempt=False, workflow=None, workflow_doc=None,
            refactor=False, auto_propose=False, over_tag_risk=False,
            signals=None, force=False, project_dir=None) -> dict:
    """Generate (or replace) a task-type row in the PROJECT overlay registry.

    The validating generator for project task types — the task-type counterpart
    of ``agent_roster.roster_add``. Upserts one row into
    ``<project>/conductor/workflow/task-type-profiles.json`` (whole-doc
    adoption: existing rows, the ``default`` block, ``_comment``/``_fields``
    preserved; same-name row replaced wholesale — exactly the ``tags`` merge
    semantics of :func:`_merge_overlay`). The new row becomes live in
    ``TAG_VOCAB``/``route_for``/``is_tdd_exempt``/``when_to_use_for`` (and
    ``extract_tags``, which builds its regex from the vocab) with zero Python
    edits.

    Field policy: ``route``/``when_to_use``/``tdd_exempt``/``coverage_exempt``
    are always written explicitly; ``auto_propose`` is always written too — a
    *generated* tag defaults to ``false`` because at read time an absent
    ``auto_propose`` means True, and an adopted/bespoke tag must never surface
    in the mechanical proposer (:func:`rank_tags`) without an explicit decision
    to opt in. ``over_tag_risk``/``refactor`` are written only when true;
    ``signals`` when provided (comma-separated string, lowercased, deduped).

    ``when_to_use`` is REQUIRED here even though :func:`validate_tag_row` does
    not demand it: without it spec-planner's tag guidance silently degrades to
    an empty hint and the tag is invisible in the ``registry-doc`` render — the
    one field whose absence costs comprehension rather than correctness, so the
    generator holds the bar the validator leaves to convention.

    Validates the row and the post-write merge BEFORE touching disk and returns
    ``{ok: False, errors: [...]}`` on a bad name (must match
    ``[A-Za-z0-9][A-Za-z0-9_-]*``, not the reserved ``default``), missing
    ``when_to_use``, unknown ``route``, an existing same-name overlay row
    (unless *force* — shadowing a BASELINE tag is the overlay mechanism and
    needs no flag), or an unreadable existing overlay (the write gate refuses
    to clobber; fail-open is read-time behavior). On success writes a ``.bak``
    beside the registry, clears the read cache (the first production
    self-clear — previously only the studio did this), and returns
    ``{ok, tag, registry_path, lint: []}``.
    """
    # Lazy imports keep the hook path clean (roster_add precedent): the
    # dispatch hooks importing this module must not pay for the validator or
    # the atomic-write helper.
    from lib.atomic_io import atomic_write_json
    from .registry_validate import ROUTES, validate_tag_row, validate_merged_task_types

    if not name or name != name.strip() or "/" in name or "[" in name or "]" in name:
        return {"ok": False, "errors": [
            f"invalid tag name {name!r} — letters/digits/-/_ only, no brackets "
            f"(it becomes both a plan marker and a registry key)"]}
    import re as _re
    if not _re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]*$", name):
        return {"ok": False, "errors": [
            f"invalid tag name {name!r} — must match [A-Za-z0-9][A-Za-z0-9_-]*"]}
    if name == "default":
        return {"ok": False, "errors": [
            "'default' is reserved for the top-level default profile — pass "
            "per-key default overrides by editing the overlay directly"]}

    if not when_to_use or not when_to_use.strip():
        return {"ok": False, "errors": [
            "--when-to-use is required — a tag without it is invisible in the "
            "registry-doc render and spec-planner's tag guidance"]}

    if route is not None and route not in ROUTES:
        return {"ok": False, "errors": [
            f"unknown --route {route!r} (expected {list(ROUTES)})"]}

    if project_dir is not None:
        root = Path(project_dir).resolve()
        if not root.is_dir():
            return {"ok": False, "errors": [
                f"project dir {project_dir!r} does not exist — refusing to "
                f"create conductor/ scaffolding in a typo'd path"]}
    else:
        root = _project_root()
    if root is None:
        return {"ok": False, "errors": [
            "no project dir resolved — pass --project-dir or run inside a "
            "project tree (one with conductor/tracks/)"]}

    if isinstance(signals, str):
        signal_list = _dedupe_signals(
            s.strip().lower() for s in signals.split(","))
    elif signals:
        signal_list = _dedupe_signals(str(s).strip().lower() for s in signals)
    else:
        signal_list = []

    row = {"route": route or "executor",
           "when_to_use": when_to_use.strip(),
           "tdd_exempt": bool(tdd_exempt),
           "coverage_exempt": bool(coverage_exempt),
           # Always explicit: at read time absent means True, and a generated
           # tag must not join the proposer's candidates by accident.
           "auto_propose": bool(auto_propose)}
    if over_tag_risk:
        row["over_tag_risk"] = True
    if refactor:
        row["refactor"] = True
    if workflow is not None:
        row["workflow"] = workflow
    if workflow_doc is not None:
        row["workflow_doc"] = workflow_doc
    if signal_list:
        row["signals"] = signal_list

    # The overlay as it will exist AFTER this write: whole-doc adoption of the
    # existing file (preserves default/_comment/_fields/rows), this row
    # replacing any same-name row wholesale (the _merge_overlay tags
    # semantics). A malformed existing file is a write-gate refusal, not a
    # fail-open — clobbering it would discard rows the user cannot see.
    overlay = {"default": {}, "tags": {}}
    registry_path = root / "conductor" / "workflow" / "task-type-profiles.json"
    if registry_path.exists():
        try:
            existing = json.loads(registry_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                raise ValueError("not an object")
            overlay = existing
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return {"ok": False, "errors": [
                f"{registry_path} is unreadable ({exc}) — fix or remove it "
                f"before adding tags (the conductor currently fails open to "
                f"the baseline)"]}
    tags = overlay.setdefault("tags", {})
    if name in tags and not force:
        return {"ok": False, "errors": [
            f"overlay tag {name!r} already exists — pass --force to replace it "
            f"(shadowing a BASELINE tag is the overlay mechanism and needs "
            f"no flag)"]}
    tags[name] = row

    # Validate before touching disk: the row itself, then the merge the
    # conductor WOULD resolve after the write (baseline ⊕ the on-disk overlay
    # as amended above — reusing _merge_overlay for the pre-write disk state,
    # then applying the same tags update it would perform).
    errs = list(validate_tag_row(name, row))
    merged = _merge_overlay(_load_baseline())
    merged.setdefault("tags", {}).update(tags)
    errs.extend(validate_merged_task_types(merged))
    if errs:
        return {"ok": False, "errors": errs}

    if registry_path.exists():
        import shutil
        shutil.copy2(registry_path,
                     registry_path.parent / (registry_path.name + ".bak"))

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(registry_path, overlay)

    _load.cache_clear()
    return {"ok": True, "tag": name, "registry_path": str(registry_path),
            "lint": []}
