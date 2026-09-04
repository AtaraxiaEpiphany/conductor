"""Strict-write validation for the three workflow registries.

The registries are **fail-open on read** (``workflow_shapes.resolve_shape`` /
``task_profiles._profile`` fall back to ``default`` + a WARNING on a typo;
``agent_roster`` row accessors degrade to the unrostered behavior), so a
malformed row degrades *silently* at dispatch. That is the right behavior for a
running conductor — a typo must never block dispatch. But it is the wrong
behavior for an *edit*: the workflow-studio editor (and the ``registry-save``
CLI) must REJECT a bad row before it is written, so the source of truth is never
left holding a shape that will silently fail-open. This module is that gate.

Pure functions (no I/O) so the editor, the CLI writer, and the test suite all
share one definition of "valid." The closed vocabularies live HERE as the single
source — the frontend dropdowns and the drift lint derive from the same tuples,
never re-typed (a second hand-maintained vocab table is exactly the drift
liability ``prose-style`` Bucket B warns about).

Validation philosophy — **strict on what is present, structural on what is
missing**: an overlay fragment that declares only ``{"shapes": {...}}`` (no
``default``) is valid here, because it inherits the baseline's ``default`` at
merge time. The "a ``default`` must exist" invariant is checked against the
*merged* result (:func:`validate_merged_shapes` /
:func:`validate_merged_task_types`), which is what the conductor actually
resolves. Unknown row fields are errors (catches ``verifer``→``verifiers``
typos); the ``_comment`` / ``_fields`` documentation blocks are preserved
(allowed at the top level, never flagged).
"""

from __future__ import annotations

import re

# --- closed vocabularies (single source) --------------------------------------
# Mirrors the `_fields` documentation in templates/workflow/*.json and the
# accessors in workflow_shapes.py / task_profiles.py. The editor's dropdowns and
# the drift lint read these tuples; do not re-declare them elsewhere.

#: Ordered tuple of valid SPINE node agents (the `nodes` topology field).
SPINE_NODES = ("spec-planner", "explorer", "task-executor", "phase-checker")

#: Ordered tuple of valid checkpoint verifier agents (the `verifiers` field).
#: `ac-tracer` (AC-evidence trace) + `build-runner` (L0 compile/build) +
#: `test-runner` (L1 suite) fan out in parallel at every code-shape checkpoint.
#: `build-runner` is the cheapest-first floor — a compile gate that catches code
#: the test suite never imports (the "unimported module" hole); a shape that
#: drops it (a non-code `deliverable`) must declare an integrity substitute
#: (`ac_grounding="review"`), enforced by :func:`validate_merged_shapes`.
VERIFIERS = ("ac-tracer", "build-runner", "test-runner")

#: The verifier tiers that run CODE (a compile or a test suite) — the subset of
#: :data:`VERIFIERS` a code-free phase narrows out (nothing to compile, nothing
#: to test). Single-homed so the dispatch fan-out builder and the dashboard view
#: narrow identically (the studio view mirrors the dispatch builder off THIS
#: tuple, not a re-typed copy).
CODE_TIERS = ("build-runner", "test-runner")

#: Ordered tuple of valid track-level quality gates (the `gates` field).
GATES = ("tdd", "coverage", "checkpoint")

#: How a shape gates progress (`verify_policy` field).
VERIFY_POLICIES = ("checkpoint", "none")

#: What makes a shape DONE (`stop_condition` field).
STOP_CONDITIONS = ("all_nodes_done",)

#: How acceptance criteria are grounded (`ac_grounding` field).
#: ``test`` → ACs grounded by ``test_TC_*`` functions; ``review`` → ACs grounded
#: by an artifact anchor + a review attestation (non-code deliverable shapes).
AC_GROUNDINGS = ("test", "review")

#: Whether a shape's checkpoint phase runs (`checkpoint_policy` field).
#: ``run`` (default) → the phase-checker checkpoint fans out as normal.
#: ``skip-if-declared`` → short-circuit the checkpoint, but ONLY when the shape
#: declares an integrity substitute (e.g. ``ac_grounding="review"`` → the review
#: attestation); a ``skip-if-declared`` shape with no substitute fails hard
#: (never a silent skip — the "attach a guarantee to every freedom" invariant).
CHECKPOINT_POLICIES = ("run", "skip-if-declared")

#: Dispatch category for a task type (`route` field).
ROUTES = ("manual", "explore", "executor")

#: What "done, verified" means for a task class's DELIVERABLE (the tag-row
#: `grounding` field) — the positive declaration the exemption booleans used
#: to encode in negative space. Deliberately NOT the same vocab as
#: AC_GROUNDINGS above (test|review only): that is the track-level
#: spec-integrity scan mode; this is the task-class claim, where data-check
#: (assert/probe output as evidence) and human-attest (a person signs off)
#: are first-class. Loader fail-open home: ``task_profiles.grounding_of``
#: derives a value when a row declares none.
TAG_GROUNDINGS = ("test", "review", "data-check", "human-attest")

#: Agent-roster role classes (the `class` field). `executor` derives
#: single_writer=true (the dispatch-dedupe single-writer guard set); every
#: other class derives false — an explicit `single_writer` override is the
#: only way to differ.
AGENT_CLASSES = ("executor", "verifier", "reviewer", "advisory")

#: How a SubagentStop recovery contract is gated (`recovery` field).
#: `result-file` = a fresh .conductor/result.json gates the stop;
#: `stdout-block` = the ---END RESULT--- close tag gates it; `none` (default)
#: no recovery contract. Each non-`none` kind pairs with a
#: `recovery_instruction` (the two-homes guard below enforces the pairing).
RECOVERY_KINDS = ("result-file", "stdout-block", "none")

#: The name grammar for a `workflow_doc` row field: a BARE ``.md`` filename in
#: the workflow steps library (``templates/workflow/steps/`` plugin side,
#: ``conductor/workflow/steps/`` project side). No path separators, no leading
#: dot — a path-y value is a typo or a traversal attempt, never a docfile.
#: Single home: ``task_profiles.resolve_workflow_doc`` enforces the same shape
#: at read time (fail-open); this is the strict-write form.
DOCFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$")


# Known per-row fields (everything else on a row is a typo → error).
_KNOWN_SHAPE_FIELDS = frozenset({
    "nodes", "verifiers", "gates", "verify_policy", "stop_condition",
    "ac_grounding", "checkpoint_policy", "instruction", "when_to_use", "requires",
    "planning_doc", "signals", "max_retries",
})
_KNOWN_TAG_FIELDS = frozenset({
    "route", "tdd_exempt", "coverage_exempt", "gates", "grounding",
    "when_to_use", "workflow", "workflow_doc", "refactor", "auto_propose",
    "over_tag_risk", "signals", "examples", "agent",
})

# Tag fields that must be booleans.
_TAG_BOOL_FIELDS = ("tdd_exempt", "coverage_exempt", "refactor",
                    "auto_propose", "over_tag_risk")

# Roster fields that must be booleans when present.
_ROSTER_BOOL_FIELDS = ("single_writer", "registry_injection", "retry")

# Known per-row roster fields (everything else on a row is a typo → error).
_KNOWN_ROSTER_FIELDS = frozenset({
    "class", "fence", "single_writer", "registry_injection", "retry",
    "recovery", "recovery_instruction",
})

# Shape field → its closed vocabulary (for the list-valued and scalar-valued
# vocab fields). `nodes`/`verifiers`/`gates` are lists whose members must be in
# the vocab; `verify_policy`/`stop_condition`/`ac_grounding`/`checkpoint_policy`
# are scalars in it.
_SHAPE_VOCAB = {
    "nodes": SPINE_NODES,
    "verifiers": VERIFIERS,
    "gates": GATES,
    "verify_policy": VERIFY_POLICIES,
    "stop_condition": STOP_CONDITIONS,
    "ac_grounding": AC_GROUNDINGS,
    "checkpoint_policy": CHECKPOINT_POLICIES,
}
_SHAPE_VOCAB_LIST_FIELDS = ("nodes", "verifiers", "gates")
_SHAPE_VOCAB_SCALAR_FIELDS = (
    "verify_policy", "stop_condition", "ac_grounding", "checkpoint_policy")
_SHAPE_STR_FIELDS = ("instruction", "when_to_use")


def validate_shape_row(name: str, row) -> list[str]:
    """Errors for a single shape row (the ``default`` block or one ``shapes``
    entry). Empty list = valid. See module docstring for the philosophy.
    """
    errs: list[str] = []
    for k in row:
        if k not in _KNOWN_SHAPE_FIELDS:
            errs.append(f"shape {name!r}: unknown field {k!r}")

    for field in _SHAPE_VOCAB_LIST_FIELDS:
        if field in row:
            val = row[field]
            if not isinstance(val, list):
                errs.append(f"shape {name!r}: {field} must be a list")
                continue
            vocab = _SHAPE_VOCAB[field]
            for item in val:
                if not isinstance(item, str) or item not in vocab:
                    errs.append(
                        f"shape {name!r}: {field} entry {item!r} not in {list(vocab)}")

    for field in _SHAPE_VOCAB_SCALAR_FIELDS:
        if field in row:
            val = row[field]
            vocab = _SHAPE_VOCAB[field]
            if val not in vocab:
                errs.append(
                    f"shape {name!r}: {field}={val!r} not in {list(vocab)}")

    for field in _SHAPE_STR_FIELDS:
        if field in row and not isinstance(row[field], str):
            errs.append(f"shape {name!r}: {field} must be a string")

    if "requires" in row and not isinstance(row["requires"], list):
        errs.append(f"shape {name!r}: requires must be a list")

    # The planning-library pointer: a bare `.md` filename (the same grammar as
    # the task-type `workflow_doc` steps-library pointer — a path-y value is a
    # typo or a traversal attempt, never a docfile).
    if "planning_doc" in row:
        if not isinstance(row["planning_doc"], str):
            errs.append(f"shape {name!r}: planning_doc must be a string")
        elif not DOCFILE_NAME_RE.match(row["planning_doc"]):
            errs.append(
                f"shape {name!r}: planning_doc must be a bare .md filename in "
                f"the planning library (no path separators), got "
                f"{row['planning_doc']!r}")

    # Two-homes guard: a row carrying BOTH the inline `instruction` prose and
    # a `planning_doc` docfile holds the same planning procedure in two places
    # (the docfile is the home — the inline copy silently rots). Mirrors the
    # task-type `workflow`/`workflow_doc` guard exactly.
    if "instruction" in row and "planning_doc" in row:
        errs.append(
            f"shape {name!r}: carries both `instruction` (inline prose) and "
            f"`planning_doc` ({row['planning_doc']!r}) — two homes for one "
            f"planning procedure; keep the docfile and drop the inline string")

    if "signals" in row:
        val = row["signals"]
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            errs.append(f"shape {name!r}: signals must be a list of strings")

    # Per-shape retry budget: an int >= 1 (0/absent = inherit the global
    # MAX_RETRIES — the accessor `workflow_shapes.max_retries_for` owns the
    # chain). Not a vocab field (an int, not a closed set); mirrors
    # `constants.task_max_retries`'s task-level defensiveness.
    if "max_retries" in row:
        val = row["max_retries"]
        if not isinstance(val, int) or isinstance(val, bool) or val < 1:
            errs.append(f"shape {name!r}: max_retries must be an int >= 1 "
                        f"(0/absent = inherit the global default), got {val!r}")

    return errs


def validate_tag_row(name: str, row, *, form_checks: bool = True) -> list[str]:
    """Errors for a single task-type row (the ``default`` block or one ``tags``
    entry). Empty list = valid.

    ``form_checks`` gates the two-homes FORM guard (a row carrying both
    ``gates`` and the legacy exemption booleans): it must run on rows AS
    WRITTEN (overlay fragments, generator output) but NOT on MERGED rows —
    the merged registry legitimately mixes forms across files (a legacy
    overlay row inherits the positive default's ``gates``), and runtime
    resolves that per-row in ``task_profiles._resolve_row``. Semantic guards
    (grounding vocab, guard 1) always run: on merged rows the inherited
    values are present in the dict, so the checks read exactly what runtime
    would resolve.
    """
    errs: list[str] = []
    for k in row:
        if k not in _KNOWN_TAG_FIELDS:
            errs.append(f"tag {name!r}: unknown field {k!r}")

    if "route" in row and row["route"] not in ROUTES:
        errs.append(f"tag {name!r}: route={row['route']!r} not in {list(ROUTES)}")

    if "grounding" in row and row["grounding"] not in TAG_GROUNDINGS:
        errs.append(
            f"tag {name!r}: grounding={row['grounding']!r} not in "
            f"{list(TAG_GROUNDINGS)}")

    for b in _TAG_BOOL_FIELDS:
        if b in row and not isinstance(row[b], bool):
            errs.append(f"tag {name!r}: {b} must be a boolean")

    if "gates" in row:
        val = row["gates"]
        if not isinstance(val, list) or not all(isinstance(g, str) for g in val):
            errs.append(f"tag {name!r}: gates must be a list of strings")
        else:
            bad = [g for g in val if g not in GATES]
            if bad:
                errs.append(
                    f"tag {name!r}: gates entries {bad!r} not in {list(GATES)}")
            # Guard 1: the tdd gate (red/green ORDER for the deliverable) only
            # makes sense for a class whose grounding IS test. The coverage
            # gate deliberately carries NO grounding constraint — it is repo
            # accounting that also runs for classes whose own deliverable is
            # not test-witnessed ([Explore] owes coverage on adjacent changes
            # while its findings report is review-grounded). Raw-row check:
            # only fires when the row itself declares grounding; a gates-only
            # row may inherit a consistent grounding from the default (the
            # merged-level pass re-runs this on inherited values).
            if "tdd" in val and "grounding" in row and row["grounding"] != "test":
                errs.append(
                    f"tag {name!r}: gates include tdd but "
                    f"grounding={row['grounding']!r} — those gates witness a "
                    f"test-grounded deliverable; use grounding 'test' or drop "
                    f"them from gates")

    # Two-homes FORM guard: one fact (which gates a class owes), two encodings
    # — a row carrying BOTH `gates` and the legacy exemption booleans would
    # resolve with gates winning while the booleans silently rot. Precedent:
    # the workflow/workflow_doc guard below. Fragment-level only
    # (``form_checks``) — see the docstring for why the merged pass skips it.
    if form_checks and "gates" in row and \
            ("tdd_exempt" in row or "coverage_exempt" in row):
        errs.append(
            f"tag {name!r}: carries both `gates` and the legacy "
            f"tdd_exempt/coverage_exempt booleans — two homes for one fact; "
            f"keep `gates` (the positive form) and drop the booleans")

    for s in ("when_to_use", "workflow", "workflow_doc"):
        if s in row and not isinstance(row[s], str):
            errs.append(f"tag {name!r}: {s} must be a string")

    # The persona binding: a string naming an agent-roster row. Membership in
    # the (merged) roster is checked at the merged level — a project overlay
    # tag may bind a PROJECT wrapper agent that the fragment-level view cannot
    # see (the probes-builtin precedent for lazy cross-registry checks).
    if "agent" in row and (not isinstance(row["agent"], str) or not row["agent"]):
        errs.append(f"tag {name!r}: agent must be a non-empty string "
                    f"(a rostered wrapper agent name)")

    if "workflow_doc" in row and isinstance(row["workflow_doc"], str) \
            and not DOCFILE_NAME_RE.match(row["workflow_doc"]):
        errs.append(
            f"tag {name!r}: workflow_doc must be a bare .md filename in the "
            f"steps library (no path separators), got {row['workflow_doc']!r}")

    # Two-homes guard: a row carrying BOTH the inline `workflow` prose and a
    # `workflow_doc` docfile holds the same bespoke workflow in two places
    # (render prefers the docfile — the inline copy silently rots). The
    # docfile is the home for a full bespoke workflow; `workflow` is the
    # legacy small-overlay form.
    if "workflow" in row and "workflow_doc" in row:
        errs.append(
            f"tag {name!r}: carries both `workflow` (inline prose) and "
            f"`workflow_doc` ({row['workflow_doc']!r}) — two homes for one "
            f"workflow; keep the docfile and drop the inline string")

    if "signals" in row:
        val = row["signals"]
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            errs.append(f"tag {name!r}: signals must be a list of strings")

    # Few-shot exemplars (Finding-1 method 4): one-or-two task descriptions
    # ("canonical case" / "borderline case") rendered into the registry-doc
    # Tag Signals block the planner fetches — judgment transfers through
    # examples better than keyword lists. Render-only data; no policy reads it.
    if "examples" in row:
        val = row["examples"]
        if not isinstance(val, list) or not val or \
                not all(isinstance(x, str) and x.strip() for x in val):
            errs.append(f"tag {name!r}: examples must be a non-empty list "
                        f"of non-empty strings")

    return errs


# Top-level keys allowed on a registry document (the two data keys + the
# documentation blocks the editor must round-trip, never strip).
# ``grounding_signals``: the fog gate's top-level keyword list (data, not a
# doc block — see workflow_shapes.grounding_signals).
_SHAPE_TOP_KEYS = frozenset({"default", "shapes", "_comment", "_fields",
                             "grounding_signals"})
_TAG_TOP_KEYS = frozenset({"default", "tags", "_comment", "_fields"})


def validate_shapes(doc) -> list[str]:
    """Errors for a workflow-shapes document fragment. Validates whatever keys
    are PRESENT (an overlay fragment with only ``shapes`` is fine); does NOT
    require ``default`` — that is the merged-result check
    (:func:`validate_merged_shapes`). Empty list = valid.
    """
    if not isinstance(doc, dict):
        return ["shapes registry top-level must be an object"]
    errs: list[str] = []
    for k in doc:
        if k not in _SHAPE_TOP_KEYS:
            errs.append(f"unknown top-level key {k!r} (allowed: default, shapes)")

    default = doc.get("default")
    if default is not None:
        if not isinstance(default, dict):
            errs.append("'default' must be an object")
        else:
            errs.extend(validate_shape_row("default", default))

    gs = doc.get("grounding_signals")
    if gs is not None and (not isinstance(gs, list)
                           or not all(isinstance(x, str) for x in gs)):
        errs.append("'grounding_signals' must be a list of strings "
                    "(the fog-gate keywords)")

    shapes = doc.get("shapes")
    if shapes is not None:
        if not isinstance(shapes, dict):
            errs.append("'shapes' must be an object")
        else:
            for name, row in shapes.items():
                if not isinstance(row, dict):
                    errs.append(f"shape {name!r} must be an object")
                    continue
                errs.extend(validate_shape_row(name, row))
    return errs


def validate_task_types(doc, *, form_checks: bool = True) -> list[str]:
    """Errors for a task-type-profiles document fragment. Same present-only
    philosophy as :func:`validate_shapes`. Empty list = valid.

    ``form_checks`` threads to :func:`validate_tag_row` (fragment rows are
    validated as written; merged results pass ``False``).
    """
    if not isinstance(doc, dict):
        return ["task-type registry top-level must be an object"]
    errs: list[str] = []
    for k in doc:
        if k not in _TAG_TOP_KEYS:
            errs.append(f"unknown top-level key {k!r} (allowed: default, tags)")

    default = doc.get("default")
    if default is not None:
        if not isinstance(default, dict):
            errs.append("'default' must be an object")
        else:
            errs.extend(validate_tag_row("default", default,
                                         form_checks=form_checks))

    tags = doc.get("tags")
    if tags is not None:
        if not isinstance(tags, dict):
            errs.append("'tags' must be an object")
        else:
            for name, row in tags.items():
                if not isinstance(row, dict):
                    errs.append(f"tag {name!r} must be an object")
                    continue
                errs.extend(validate_tag_row(name, row,
                                             form_checks=form_checks))
    return errs


def validate_merged_shapes(merged) -> list[str]:
    """Errors for a RESOLVED (baseline ⊕ overlay) shapes document.

    Adds the invariant :func:`validate_shapes` deliberately omits: the merged
    result MUST declare a top-level ``default`` object, because it is the
    fail-open fallback target every unknown shape resolves to. A merged result
    without it would make every unknown shape resolve to an empty profile.
    """
    errs = validate_shapes(merged)
    if isinstance(merged, dict) and not isinstance(merged.get("default"), dict):
        errs.append(
            "merged shapes registry must declare a top-level 'default' object "
            "(the fail-open fallback target)")
    # The default row's ac_grounding — the value every shape inherits when it
    # does not override ac_grounding itself. Hoisted to the top of both cross-
    # field guards below (C2 skip-if-declared + build-gate) judge inheritance off
    # the SAME value; binding it inside the first guard leaked state across the
    # two. "test" when ``default`` is absent or not a dict.
    default_row = merged.get("default") if isinstance(merged, dict) else None
    default_grounding = (default_row.get("ac_grounding", "test")
                         if isinstance(default_row, dict) else "test")
    # Track C2 cross-field invariant: a shape declaring
    # ``checkpoint_policy: skip-if-declared`` MUST declare an integrity
    # substitute (``ac_grounding: review``) — the "attach a guarantee to every
    # freedom" rule. A checkpoint skip with no verification substitute would
    # break the "verified against AC-N" stamp. This save-time guard is the
    # PRIMARY catch (the strict-write gate refuses the misconfiguration);
    # dispatch's runtime ``checkpoint_skip_decision`` is defense-in-depth for a
    # hand-edited/legacy registry. Judged on the default-INHERITED value (a row
    # may inherit ``ac_grounding`` from ``default``), mirroring runtime exactly.
    #
    # The top-level ``default`` row is checked TOO — an overlay setting
    # ``default.checkpoint_policy='skip-if-declared'`` without ``ac_grounding=
    # 'review'`` would otherwise pass save-time validation yet halt every
    # default-shape track at runtime (the runtime resolver reads ``default`` as
    # the inheritance base). An earlier version walked only ``shapes`` and
    # missed it; the build-gate below seeds ``default`` the same way.
    if isinstance(merged, dict) and isinstance(merged.get("default"), dict):
        check_rows = [("default", merged["default"])]
        shapes = merged.get("shapes")
        if isinstance(shapes, dict):
            check_rows.extend((n, r) for n, r in shapes.items()
                              if isinstance(r, dict))
        for name, row in check_rows:
            if row.get("checkpoint_policy", "run") == "skip-if-declared":
                grounding = row.get("ac_grounding", default_grounding)
                if grounding != "review":
                    errs.append(
                        f"shape {name!r}: checkpoint_policy "
                        f"'skip-if-declared' requires an integrity "
                        f"substitute (ac_grounding='review'); found "
                        f"ac_grounding={grounding!r}. A checkpoint skip "
                        f"without a verification substitute breaks the "
                        f"AC-verification guarantee — set "
                        f"ac_grounding='review' or checkpoint_policy='run'.")
    # Build-gate cross-field invariant (same "attach a guarantee to every
    # freedom" rule): a shape whose ACs are test-grounded (``ac_grounding !=
    # "review"``) IS a code shape, so it MUST run the compile tier — its
    # resolved ``verifiers`` MUST include ``build-runner``. Dropping the build is
    # only valid for a review-grounded shape (``deliverable`` — no code to
    # compile; the review attestation is the substitute). A code shape that
    # silently drops the build reopens the "unimported module" hole the build
    # tier closes. Judged on the default-INHERITED value (a row may inherit
    # ``ac_grounding``/``verifiers`` from ``default``), mirroring runtime exactly.
    # The top-level ``default`` is checked too — a project overlaying
    # ``default.verifiers`` to drop the build floor would weaken every track.
    if isinstance(merged, dict) and isinstance(merged.get("default"), dict):
        default_verifiers = merged["default"].get("verifiers")
        if not isinstance(default_verifiers, list):
            default_verifiers = []
        check_rows = [("default", merged["default"])]
        shapes = merged.get("shapes")
        if isinstance(shapes, dict):
            check_rows.extend((n, r) for n, r in shapes.items()
                              if isinstance(r, dict))
        for name, row in check_rows:
            grounding = row.get("ac_grounding", default_grounding)
            if grounding == "review":
                continue  # non-code shape — no compile owed (review is the substitute)
            row_verifiers = row.get("verifiers", default_verifiers)
            if not isinstance(row_verifiers, list):
                row_verifiers = default_verifiers
            if "build-runner" not in row_verifiers:
                errs.append(
                    f"shape {name!r}: ac_grounding={grounding!r} (a code shape) "
                    f"requires the build tier — add 'build-runner' to its "
                    f"verifiers (the L0 compile gate catches code the test suite "
                    f"never imports). Dropping the build is only valid for a "
                    f"review-grounded shape; set ac_grounding='review' if this "
                    f"shape produces no code to compile.")
    return errs


def validate_merged_task_types(merged) -> list[str]:
    """Errors for a RESOLVED (baseline ⊕ overlay) task-type document.

    Adds three things to :func:`validate_task_types`:

    - the ``default`` requirement — the merged result MUST declare a
      top-level ``default`` object, the fail-open fallback target;
    - the merged-level grounding guard (guard 4): the merged document keeps
      each tag row wholesale (``_merge_overlay`` does not materialize
      default-inheritance into rows — :func:`_profile` does that at read
      time), so a raw-row guard CANNOT see what a gates-only overlay row
      inherits from an overlaid default block. This pass resolves each row
      exactly as runtime would (``task_profiles._resolve_row`` over the
      resolved default, then row) and re-runs guard 1 on the result — a row
      resolving to tdd-gates + a declared non-test grounding (its own or
      inherited) fails HERE, at the save gate, instead of silently at
      runtime. An ABSENT grounding never fires (runtime fail-opens to
      "test" when tdd is owed — mirroring that floor exactly).
    - the persona-membership cross-check: a tag's ``agent`` binding must name
      a row in the MERGED agent roster (baseline ⊕ project overlay — a
      project tag may bind a project wrapper agent). Raw-row validation can
      only check string-ness; membership needs the resolved roster, so it
      lives here at the save gate (the same lazy cross-registry pattern as
      the probes-builtin check below).

    Rows re-validate with ``form_checks=False``: the merged *default* is a
    per-key merge and can legitimately carry both encodings (a legacy
    overlay default's booleans beside the positive baseline's gates);
    ``_resolve_row`` settles which wins at runtime.
    """
    errs = validate_task_types(merged, form_checks=False)
    if not isinstance(merged, dict) or not isinstance(merged.get("default"), dict):
        errs.append(
            "merged task-type registry must declare a top-level 'default' object")
        return errs
    default_row = merged["default"]
    # Lazy import (probes-precedent): the validator stays import-light on the
    # hook path, and importing the loader here is safe — task_profiles never
    # imports registry_validate at module level.
    from .task_profiles import _resolve_row
    resolved_default = _resolve_row(default_row)
    tags = merged.get("tags")
    if not isinstance(tags, dict):
        return errs
    # The merged roster the binding is checked against — same lazy-import
    # posture as task_profiles above (agent_roster's loader is fail-open; a
    # missing roster yields the baseline names, so a baseline binding always
    # resolves and an overlay binding to an unrostered name fails HERE).
    from .agent_roster import merged_agent_names
    roster = set(merged_agent_names())
    for name, row in tags.items():
        if not isinstance(row, dict):
            continue
        prof = {**resolved_default, **_resolve_row(row)}
        grounding = prof.get("grounding")
        if grounding is not None and "tdd" in (prof.get("gates") or []) \
                and grounding != "test":
            errs.append(
                f"tag {name!r}: resolved gates include tdd but "
                f"grounding={grounding!r} (own or inherited from 'default') — "
                f"tdd gates witness a test-grounded deliverable; use "
                f"grounding 'test' or drop tdd from gates")
        bound = row.get("agent")
        if isinstance(bound, str) and bound and bound not in roster:
            errs.append(
                f"tag {name!r}: agent binding {bound!r} is not in the merged "
                f"agent roster — `track-state roster add` the wrapper first, "
                f"then bind it (runtime fail-opens to task-executor)")
    return errs


# --- agent roster (the dispatch-scaffold axis) --------------------------------
#
# Same strict-on-present / structural-on-missing philosophy as the two
# registries above: an overlay fragment declaring only ``{"agents": {...}}``
# is valid (it merges over the baseline); the resolved-result check
# (:func:`validate_merged_roster`) owns the one post-merge invariant.

_ROSTER_TOP_KEYS = frozenset({"agents", "_comment", "_fields"})


def validate_agent_row(name: str, row) -> list[str]:
    """Errors for a single agent-roster row. Empty list = valid.

    Enforces: `class` in the closed vocabulary (and required — the
    single_writer default derives from it, so a classless row is a broken
    derivation, not a missing optional); `fence` a non-empty string (the
    SubagentStart reminder composes from it); bool fields boolean;
    `recovery` in its vocabulary, PAIRED with a non-empty
    `recovery_instruction` iff the kind is not ``none`` (a recovery turn with
    no instruction, or an orphaned instruction, is the two-homes drift the
    pairing guard rejects).
    """
    errs: list[str] = []
    for k in row:
        if k not in _KNOWN_ROSTER_FIELDS:
            errs.append(f"agent {name!r}: unknown field {k!r}")

    cls = row.get("class")
    if cls is None:
        errs.append(f"agent {name!r}: 'class' is required "
                    f"(executor|verifier|reviewer|advisory)")
    elif cls not in AGENT_CLASSES:
        errs.append(
            f"agent {name!r}: class={cls!r} not in {list(AGENT_CLASSES)}")

    fence = row.get("fence")
    if not isinstance(fence, str) or not fence:
        errs.append(f"agent {name!r}: 'fence' must be a non-empty string "
                    f"(the SubagentStart reminder composes from it)")

    for b in _ROSTER_BOOL_FIELDS:
        if b in row and not isinstance(row[b], bool):
            errs.append(f"agent {name!r}: {b} must be a boolean")

    kind = row.get("recovery", "none")
    if kind not in RECOVERY_KINDS:
        errs.append(
            f"agent {name!r}: recovery={kind!r} not in {list(RECOVERY_KINDS)}")
        kind = None  # skip the pairing check — the kind itself is the error
    instr = row.get("recovery_instruction")
    if kind is not None:
        if kind != "none":
            if not isinstance(instr, str) or not instr:
                errs.append(
                    f"agent {name!r}: recovery={kind!r} requires a non-empty "
                    f"'recovery_instruction' (what the agent must IMMEDIATELY "
                    f"do on its recovery turn)")
        elif instr is not None:
            errs.append(
                f"agent {name!r}: 'recovery_instruction' set but recovery is "
                f"{row.get('recovery')!r} — an instruction without a recovery "
                f"kind never fires; set recovery or drop the instruction")
    return errs


def validate_agent_roster(doc) -> list[str]:
    """Errors for an agent-roster document fragment. Validates whatever keys
    are PRESENT (an overlay fragment with only ``agents`` is fine); does NOT
    require anything — the resolved-result check is
    :func:`validate_merged_roster`. Empty list = valid.
    """
    if not isinstance(doc, dict):
        return ["agent roster top-level must be an object"]
    errs: list[str] = []
    for k in doc:
        if k not in _ROSTER_TOP_KEYS:
            errs.append(
                f"unknown top-level key {k!r} (allowed: agents)")

    agents = doc.get("agents")
    if agents is not None:
        if not isinstance(agents, dict):
            errs.append("'agents' must be an object")
        else:
            for name, row in agents.items():
                if not isinstance(row, dict):
                    errs.append(f"agent {name!r} must be an object")
                    continue
                errs.extend(validate_agent_row(name, row))
    return errs


def validate_merged_roster(merged) -> list[str]:
    """Errors for a RESOLVED (baseline ⊕ overlay) agent-roster document.

    Adds to :func:`validate_agent_roster` the one invariant that only holds
    post-merge: the resolved document must carry an ``agents`` OBJECT
    (possibly empty — the empty roster IS the fail-open floor, "no scaffold",
    and is never an error state; a project *fragment* may omit the key, a
    *resolved* result may not). The single_writer derivation itself
    (``explicit bool override, else class == executor``) is single-homed in
    ``agent_roster.is_single_writer``; this validator checks the inputs it
    derives from (class enum + bool-typed override) so the derivation can
    never see a malformed operand.
    """
    errs = validate_agent_roster(merged)
    if isinstance(merged, dict) and not isinstance(merged.get("agents"), dict):
        errs.append(
            "merged agent roster must declare an 'agents' object "
            "(possibly empty — the fail-open floor)")
    return errs


# --- probe registry -------------------------------------------------------------

PROBE_KINDS = ("builtin", "command")
_PROBE_TOP_KEYS = frozenset({"probes", "_comment", "_fields"})


def validate_probes_row(name: str, row) -> list[str]:
    """Errors for a single probe-registry row. Empty list = valid.

    Enforces: ``description`` a non-empty string (it is the one-line answer to
    "what does this probe tell me?"); ``kind`` in the closed vocabulary; a
    ``command`` row carries a non-empty command; and a ``builtin`` row must
    name a builtin the loader implements (``probes._BUILTINS`` — a builtin
    name with no implementation is a dead name, not a probe).
    """
    from .probes import _BUILTINS

    errs: list[str] = []
    for k in row:
        if k not in ("description", "kind", "command"):
            errs.append(f"probe {name!r}: unknown field {k!r}")

    desc = row.get("description")
    if not isinstance(desc, str) or not desc:
        errs.append(f"probe {name!r}: 'description' must be a non-empty string")

    kind = row.get("kind")
    if kind not in PROBE_KINDS:
        errs.append(f"probe {name!r}: kind={kind!r} not in {list(PROBE_KINDS)}")
        kind = None
    if kind == "command":
        cmd = row.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            errs.append(
                f"probe {name!r}: kind=command requires a non-empty 'command' "
                f"(argv string; shlex-split, no shell)")
    else:
        if "command" in row:
            errs.append(
                f"probe {name!r}: 'command' set but kind is "
                f"{row.get('kind')!r} — an orphaned command never runs")
        if kind == "builtin" and name not in _BUILTINS:
            errs.append(
                f"probe {name!r}: builtin not implemented "
                f"(known builtins: {sorted(_BUILTINS)})")
    return errs


def validate_probes_doc(doc) -> list[str]:
    """Errors for a probes document (fragment OR resolved). Present-only keys
    are validated; the resolved result must carry a ``probes`` object (the
    fail-open floor is the EMPTY registry, which still declares the key).
    Empty list = valid."""
    if not isinstance(doc, dict):
        return ["probes registry top-level must be an object"]
    errs: list[str] = []
    for k in doc:
        if k not in _PROBE_TOP_KEYS:
            errs.append(f"unknown top-level key {k!r} (allowed: probes)")
    probes = doc.get("probes")
    if probes is not None:
        if not isinstance(probes, dict):
            errs.append("'probes' must be an object")
        else:
            for name, row in probes.items():
                if not isinstance(row, dict):
                    errs.append(f"probe {name!r} must be an object")
                    continue
                errs.extend(validate_probes_row(name, row))
    if "probes" not in doc:
        errs.append("probes registry must declare a 'probes' object "
                    "(possibly empty — the fail-open floor)")
    return errs
