"""Workflow-shape registry — the node SEQUENCE axis.

The task-type registry (:mod:`task_profiles`) says what each node *says*
(routing + workflow prose). This module says the node *sequence*: which dispatch
agents a workflow runs, in what order, its verify policy, and its stop
condition. The conductor's fixed state machine topology (planner → executor →
checker) becomes *declared* here, so a project ships a custom shape (e.g.
``research-first``) with zero plugin edits.

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

**Shape is PARTIALLY load-bearing.** The ``verifiers`` field IS load-bearing:
the dispatch checkpoint fan-out (``cmd_dispatch_next`` / ``_step_emit_dispatch_batch``)
iterates :func:`verifiers_for` — the shape's declared verifier set
— so a shape controlling which verifiers run is one registry row, not code. The
``nodes`` topology stays ADVISORY: the single ``nodes``-consumer is
:func:`shape_allows`, and its result is **never** used to block or reroute — when
a dispatched action's agent is outside the resolved shape's ``nodes``, the spine
attaches an advisory ``shape_violation`` disclosure to the emitted leaf envelope
(no-silent-caps) and the dispatch still proceeds. ``nodes`` is the SPINE
topology; ``verifiers`` are its checkpoint CHILDREN (declared in the same row) —
two distinct lists, never conflated. The other accessors (:func:`nodes_for`,
:func:`verify_policy_for`, :func:`stop_condition_for`, :func:`instruction_for`,
:func:`planning_doc_for`, :func:`signals_for`) are consumed **only** by
``registry-doc`` display and the PLANNING layer (:func:`rank_shapes` is the
selection engine ``propose-shape`` calls), never by dispatch ordering,
wave.py, handoff.py, or any
SubagentStart injection. ``instruction_for`` in particular is NOT injected into
an orchestrator prompt (contrast the task-type ``workflow`` field, which IS
injected). ``research-first``'s explore-before-plan intent is honored at the
PLANNING layer via its planning docfile's Prelude — the dispatch spine never
reorders, so setting the shape still surfaces ``shape_violation`` disclosures
when dispatch drifts off ``nodes``; only the verifier set a shape declares
actually drives dispatch.

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
        "verifiers": ["ac-tracer", "build-runner", "test-runner"],
        "gates": ["tdd", "coverage", "checkpoint"],
        "ac_grounding": "test",
        "verify_policy": "checkpoint",
        "checkpoint_policy": "run",
        "stop_condition": "all_nodes_done",
    },
    "shapes": {
        "default": {
            "nodes": ["spec-planner", "task-executor", "phase-checker"],
            "verifiers": ["ac-tracer", "build-runner", "test-runner"],
            "gates": ["tdd", "coverage", "checkpoint"],
            "ac_grounding": "test",
            "verify_policy": "checkpoint",
            "checkpoint_policy": "run",
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
    """The ordered SPINE dispatch-agent node list a shape runs — the topology.

    This is the allowlist the dispatch spine consults (advisory): an action
    whose agent is not in this list surfaces a ``shape_violation`` disclosure
    when the shape is resolved, but the dispatch still proceeds. Returns a tuple
    for stable membership tests. Unknown shape → ``default`` shape's nodes
    (fail-open: a typo never blocks dispatch, it falls back to the standard loop).

    Note this is the SPINE topology only. Checkpoint verifiers (``ac-tracer`` /
    ``build-runner`` / ``test-runner``) are NOT spine nodes — they are checkpoint
    *children* declared via :func:`verifiers_for`, and a ``phase-checker``
    dispatching one is on-topology regardless of ``nodes``. Do not conflate the
    two lists.
    """
    return tuple(_shape(shape).get("nodes", ()))


def verifiers_for(shape: str) -> tuple[str, ...]:
    """The ordered checkpoint-VERIFIER list a shape's checkpoint fans out.

    This is the **load-bearing** seam where the workflow-shape axis meets the
    verifier fan-out: the dispatch fan-out (``dispatch.cmd_dispatch_next`` /
    ``dispatch._step_emit_dispatch_batch``) iterates this tuple and emits one
    wave member per verifier (built by :func:`dispatch._build_verifier`). So a
    project shape omitting ``test-runner`` simply doesn't fan it out — the shape
    controls WHICH verifiers its checkpoint runs.

    Distinct from :func:`nodes_for` (the spine topology): verifiers are
    checkpoint *children*, never spine nodes. Absent/empty/malformed → the
    standard ``("ac-tracer", "build-runner", "test-runner")`` triple (fail-open,
    mirroring :func:`nodes_for`'s fail-open to default) — the cheapest-first
    graduated gate (compile floor → test bar). Unknown shape → the default
    shape's verifiers. Returns a tuple for stable membership.
    """
    raw = _shape(shape).get("verifiers")
    if not isinstance(raw, list) or not raw:
        return tuple(_shape("default").get("verifiers") or
                     ("ac-tracer", "build-runner", "test-runner"))
    # Drop non-str / empty entries defensively (a malformed row never crashes
    # the fan-out).
    return tuple(str(v) for v in raw if isinstance(v, str) and v) or \
        ("ac-tracer", "build-runner", "test-runner")


def verify_policy_for(shape: str) -> str:
    """The shape's declared verify policy: ``checkpoint`` | ``none``.

    DISPLAY-ONLY today — read by ``build_view_envelope`` / ``registry-doc`` to
    RENDER the resolved workflow, but NO dispatch or integrity code consults it
    to gate progress (``_phase_needs_checkpoint`` reads plan.md stamps, not
    this). The actual checkpoint gate is driven by
    :func:`checkpoint_policy_for` (whether the phase-checker checkpoint runs at
    all), composed with :func:`gates_for` (the track-level ``checkpoint`` gate
    member) — those are the load-bearing seams, not this field. Kept as a
    declared-intent field a shape states for tooling/display (e.g. a
    research/exploration shape declares ``none`` to RECORD that it produces no
    committable artifact); do not read it as a routing primitive.
    """
    return _shape(shape).get("verify_policy", "checkpoint")


def checkpoint_policy_for(shape: str) -> str:
    """Whether a shape's checkpoint phase actually RUNS: ``run`` | ``skip-if-declared``.

    This is the **load-bearing** verification-layer plasticity seam — the 3rd
    field (after :func:`verifiers_for` + :func:`gates_for`) that genuinely
    drives dispatch. The dispatch emit sites
    (:func:`dispatch._step_emit_dispatch_batch` Rail A, and the Rail B-min
    spine) consult it: ``run`` (default) fans the phase-checker checkpoint out
    as normal; ``skip-if-declared`` short-circuits the checkpoint emit — but
    ONLY when the shape declares an integrity substitute (``ac_grounding ==
    "review"`` → the review attestation is the substitute). A
    ``skip-if-declared`` shape with NO declared substitute fails HARD (a
    ``shape_violation`` halt), never a silent skip — that is the "attach a
    guarantee to every freedom" invariant: ``checkpoint_policy`` must not become
    a second advisory ``nodes``. Absent/malformed → ``"run"`` (fail-open to
    today's behavior, so every shipped shape runs its checkpoint unchanged).
    """
    return _shape(shape).get("checkpoint_policy", "run")


def checkpoint_skip_decision(shape: str) -> str:
    """The checkpoint-policy verdict for a resolved shape: ``run`` | ``skip`` | ``violation``.

    Composes :func:`checkpoint_policy_for` with the declared-integrity-substitute
    check, so dispatch has ONE call that returns the operational decision:

    - ``run`` — the checkpoint fans out normally (policy is ``run``, the default
      for every shipped shape).
    - ``skip`` — policy is ``skip-if-declared`` AND the shape declares an
      integrity substitute (``ac_grounding == "review"`` → the review
      attestation is the substitute). The checkpoint is WAIVED: the review
      channel is the verification, not the phase-checker checkpoint.
    - ``violation`` — policy is ``skip-if-declared`` with NO declared
      substitute. The "attach a guarantee to every freedom" invariant is
      violated: a freedom (skip the checkpoint) was taken without its
      verification substitute, so the "verified against AC-N" stamp would be
      hollow. Dispatch must **fail-hard** (a ``shape_violation`` halt), never a
      silent skip — ``checkpoint_policy`` must not become a second advisory
      ``nodes``.

    The substitute criterion is ``ac_grounding == "review"`` (the one declared
    substitute today). Reads the resolved (default-inherited) shape, so a
    project overlay that sets ``checkpoint_policy`` and inherits
    ``ac_grounding`` from ``default`` is judged on its resolved value — the same
    lens the save-time ``validate_merged_shapes`` cross-field guard uses.
    """
    if checkpoint_policy_for(shape) != "skip-if-declared":
        return "run"
    if ac_grounding_for(shape) == "review":
        return "skip"
    return "violation"


def stop_condition_for(shape: str) -> str:
    """What makes a shape DONE (``all_nodes_done`` by default)."""
    return _shape(shape).get("stop_condition", "all_nodes_done")


def max_retries_for(shape: str) -> int:
    """The shape-level default retry budget, or ``0`` (= inherit the global).

    A shape row may declare ``max_retries: <int >= 1>`` — the per-track retry
    ceiling for its job family (e.g. a migration shape may want 1: each retry
    re-runs a risky port; a research shape may want more: dead ends are the
    job). The chain lives in :func:`constants.task_max_retries`:
    task-level ``max_retries`` wins, then this, then the global ``MAX_RETRIES``.
    Defensive read (cf. :func:`planning_doc_for`): absent/malformed/bool/``< 1``
    → ``0`` = inherit — a bad registry row must never zero out the retry
    budget for every task under the shape.
    """
    val = _shape(shape).get("max_retries")
    if isinstance(val, int) and not isinstance(val, bool) and val >= 1:
        return val
    return 0


def instruction_for(shape: str) -> str:
    """The optional prompt-shaping prose for the orchestrator when this shape is
    active (mirrors task-type ``workflow``). Absent (the common case) = ``""`` =
    the default §3.0 dispatch loop.

    The LEGACY small form: the home for a shape's planning procedure is a
    planning-library docfile (:func:`planning_doc_for` /
    :func:`resolve_planning_doc`); a row carrying both is a two-homes drift the
    strict-write validator rejects. No shipped shape carries ``instruction``
    anymore — kept for a small project overlay, the same status as the
    task-type registry's inline ``workflow`` field.
    """
    return _shape(shape).get("instruction", "")


#: The docfile every shape without a bespoke ``planning_doc`` resolves to — the
#: default tested-code planning procedure, relocated verbatim from the three
#: former prose homes (new-track §2.1 arms, spec-planner §4.1 branches, the
#: shape ``instruction`` fields) into the planning library.
DEFAULT_PLANNING_DOC = "default.md"


def planning_doc_for(shape: str) -> str:
    """The ``planning_doc`` docfile NAME for a shape, or ``""`` (default play).

    The registry-driven pointer into the **planning library**: a shape row
    declaring ``planning_doc: "<name>.md"`` gets its planning procedure (the
    orchestrator-facing Prelude + the planner-facing body) from that docfile.
    Absent (a custom overlay row that omits it) = the default docfile via
    default-inheritance (:func:`_shape` merges the ``default`` row under every
    named shape, and ``default`` declares ``default.md``). Mirrors
    :func:`task_profiles.workflow_doc_for`'s bare-string shape so presence
    checks and renders treat the two registries' docfile fields uniformly. Use
    :func:`resolve_planning_doc` for the actual path.
    """
    doc = _shape(shape).get("planning_doc", "")
    return doc if isinstance(doc, str) else ""


def resolve_planning_doc(shape: str) -> Path:
    """The resolvable PATH to a shape's planning docfile (fail-open to default).

    Resolution: the declared ``planning_doc`` name — project planning dir over
    plugin planning dir (``<project>/conductor/planning/<name>`` over
    ``<plugin>/templates/planning/<name>``; a project overrides a shipped
    docfile or adds a bespoke one with zero plugin edits) — falling back to
    :data:`DEFAULT_PLANNING_DOC` (plugin copy) when the shape declares none,
    the name is malformed (not a bare ``.md`` filename — a path-y name is a
    typo or traversal attempt, never a docfile), or no planning dir holds it.
    Fail-open with a loud stderr warning, never a raise — the exact contract
    :func:`task_profiles.resolve_workflow_doc` holds one layer down (a bad
    overlay docfile must not crash the skill that renders it).
    """
    # Lazy import: the name grammar is single-homed in registry_validate (the
    # strict-write gate enforces the same shape) — the same pattern as
    # resolve_workflow_doc.
    from .registry_validate import DOCFILE_NAME_RE

    name = planning_doc_for(shape)
    if name and not DOCFILE_NAME_RE.match(name):
        print(
            f"WARNING: planning_doc {name!r} for shape {shape!r} is not a bare "
            f".md filename; falling back to {DEFAULT_PLANNING_DOC}.",
            file=sys.stderr,
        )
        name = ""
    if not name:
        name = DEFAULT_PLANNING_DOC

    root = _project_root()
    candidates = []
    if root is not None:
        candidates.append(root / "conductor" / "planning" / name)
    plugin_default = (_plugin_root() / "templates" / "planning"
                      / DEFAULT_PLANNING_DOC)
    candidates.append(_plugin_root() / "templates" / "planning" / name)
    for cand in candidates:
        if cand.is_file():
            return cand
    if name != DEFAULT_PLANNING_DOC:
        print(
            f"WARNING: planning docfile {name!r} (shape {shape!r}) not found "
            f"in any planning dir; falling back to {DEFAULT_PLANNING_DOC}.",
            file=sys.stderr,
        )
        return plugin_default
    # The default docfile itself is missing (plugin install damage) — return
    # the plugin path anyway; the caller's read fails open downstream.
    return plugin_default


def signals_for(shape: str) -> tuple[str, ...]:
    """The shape's EXPLICIT ``signals`` keyword tuple — the selection data
    ``propose-shape`` matches a track description against.

    Only a row that DECLARES ``signals`` returns a non-empty tuple; ``default``
    deliberately omits it (the fail-open fallback is never a competitor —
    mirrors an opt-in task tag omitting ``signals`` so it is never goal-detected).
    No derived fallback from ``when_to_use`` here, unlike
    :func:`task_profiles._signals_for`: shape selection is consequential (it
    changes gates and verifiers for the WHOLE track), so a shape without
    authored signals is simply not a candidate — never guessed.
    """
    raw = _shape(shape).get("signals")
    if not isinstance(raw, list) or not raw:
        return ()
    # Lowercased + deduped (a duplicate would double-count one hit in the
    # plurality score) — the same normalization _dedupe_signals applies.
    out: list[str] = []
    for s in raw:
        k = str(s).lower()
        if k and k not in out:
            out.append(k)
    return tuple(out)


def rank_shapes(text: str) -> list[dict]:
    """Rank candidate shapes by distinct ``signals`` hits over ``text``.

    The pure core of ``track-state propose-shape`` (D2: deterministic, no model
    call — the registry-driven selection engine for the PLANNING layer, the
    mirror of :func:`task_profiles.derive_task_tag` one axis over). Returns a
    list of ``{"shape", "score", "hits"}`` dicts, score-descending, registry
    order stable within a tie (deterministic output). Each entry clears the
    minimum bar of **>= 1 distinct hit** — deliberately lower than
    ``derive_task_tag``'s >= 2, because a tag silently exempts gates (needs the
    conservative bar) while a proposed shape is ALWAYS confirmed by the user
    before it takes effect (``confirm_required`` — the human is the bar). The
    caller applies the strict-plurality rule: a top score TIED with the runner-up
    is ambiguity, not a proposal (fall back to ``default``).

    Only shapes with authored ``signals`` compete (see :func:`signals_for`);
    text is lowercased before matching, and each signal matches via the
    word-boundary-aware matcher (:func:`task_profiles._signal_in` — imported,
    not copied: one matcher, one home).
    """
    # Lazy import keeps this module import-cheap for the standalone hook
    # scripts; task_profiles does not import this module (no cycle).
    from .task_profiles import _signal_in

    if not text or not text.strip():
        return []
    lowered = text.lower()
    candidates: list[dict] = []
    for shape in SHAPES_VOCAB():
        signals = signals_for(shape)
        if not signals:
            continue
        hits = [sig for sig in signals if _signal_in(sig, lowered)]
        if hits:
            candidates.append(dict(shape=shape, score=len(hits), hits=hits))
    # Score-descending; Python's stable sort preserves registry order on a tie.
    candidates.sort(key=lambda c: -c["score"])
    return candidates[:3]


def gates_for(shape: str) -> tuple[str, ...]:
    """The quality gates a shape enforces at the TRACK level — the ON/OFF.

    A gate fires for a task iff it is BOTH declared here AND not waived per-task
    by the task-type registry (``tdd_exempt`` / ``coverage_exempt``). Compose as
    ``(gate in gates_for(shape)) and (not task_exempt)``. This is the shape-level
    switch; the task-type exemption is the per-task refinement within an enabled
    gate — so a non-code shape drops F2/F3 at the track level while a code-bearing
    task on a code shape still owes them unless its own tag exempts it.

    Members: ``tdd`` (F2 red/green/refactor), ``coverage`` (F3 ≥80%), ``checkpoint``
    (F5 — the phase-checker checkpoint gates progress; distinct from
    :func:`verify_policy_for`, which is whether a checkpoint phase runs at all).
    Absent/empty/malformed → the default shape's gates → ``("tdd", "coverage",
    "checkpoint")`` (fail-open to today's behavior). Unknown shape → the default
    shape's gates. Returns a tuple for stable membership tests.
    """
    raw = _shape(shape).get("gates")
    if not isinstance(raw, list) or not raw:
        return tuple(_shape("default").get("gates") or
                     ("tdd", "coverage", "checkpoint"))
    # Drop non-str / empty entries defensively (a malformed row never crashes a
    # gate-composition membership test).
    return tuple(str(g) for g in raw if isinstance(g, str) and g) or \
        ("tdd", "coverage", "checkpoint")


def ac_grounding_for(shape: str) -> str:
    """How acceptance criteria are GROUNDED for a shape.

    ``test`` (default) → ACs are grounded by ``test_TC_*`` functions (the basis
    of ``spec_integrity``'s AC-grounding scan: Rate 1 measures AC→TC coverage,
    Rate 3 the measured test twin). ``review`` → ACs are grounded by an artifact
    anchor + a review attestation (a non-code deliverable shape), so the scan's
    review branch measures AC→anchor coverage and AC→attestation instead. The
    integrity engine reads this (``spec_integrity._resolve_grounding``) so it
    does not insist on test-grounding a review shape. Absent → ``"test"``
    (fail-open to today's behavior). Unknown shape → the default shape's value.
    """
    return _shape(shape).get("ac_grounding", "test")


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
