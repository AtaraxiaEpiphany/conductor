"""Strict-write validation for the two workflow registries.

The registries are **fail-open on read** (``workflow_shapes.resolve_shape`` /
``task_profiles._profile`` fall back to ``default`` + a WARNING on a typo), so a
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

# --- closed vocabularies (single source) --------------------------------------
# Mirrors the `_fields` documentation in templates/workflow/*.json and the
# accessors in workflow_shapes.py / task_profiles.py. The editor's dropdowns and
# the drift lint read these tuples; do not re-declare them elsewhere.

#: Ordered tuple of valid SPINE node agents (the `nodes` topology field).
SPINE_NODES = ("spec-planner", "explorer", "task-executor", "phase-checker")

#: Ordered tuple of valid checkpoint verifier agents (the `verifiers` field).
VERIFIERS = ("ac-tracer", "test-runner")

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


# Known per-row fields (everything else on a row is a typo → error).
_KNOWN_SHAPE_FIELDS = frozenset({
    "nodes", "verifiers", "gates", "verify_policy", "stop_condition",
    "ac_grounding", "checkpoint_policy", "instruction", "when_to_use", "requires",
})
_KNOWN_TAG_FIELDS = frozenset({
    "route", "tdd_exempt", "coverage_exempt", "when_to_use",
    "workflow", "refactor", "auto_propose", "over_tag_risk", "signals",
})

# Tag fields that must be booleans.
_TAG_BOOL_FIELDS = ("tdd_exempt", "coverage_exempt", "refactor",
                    "auto_propose", "over_tag_risk")

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

    return errs


def validate_tag_row(name: str, row) -> list[str]:
    """Errors for a single task-type row (the ``default`` block or one ``tags``
    entry). Empty list = valid.
    """
    errs: list[str] = []
    for k in row:
        if k not in _KNOWN_TAG_FIELDS:
            errs.append(f"tag {name!r}: unknown field {k!r}")

    if "route" in row and row["route"] not in ROUTES:
        errs.append(f"tag {name!r}: route={row['route']!r} not in {list(ROUTES)}")

    for b in _TAG_BOOL_FIELDS:
        if b in row and not isinstance(row[b], bool):
            errs.append(f"tag {name!r}: {b} must be a boolean")

    for s in ("when_to_use", "workflow"):
        if s in row and not isinstance(row[s], str):
            errs.append(f"tag {name!r}: {s} must be a string")

    if "signals" in row:
        val = row["signals"]
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            errs.append(f"tag {name!r}: signals must be a list of strings")

    return errs


# Top-level keys allowed on a registry document (the two data keys + the
# documentation blocks the editor must round-trip, never strip).
_SHAPE_TOP_KEYS = frozenset({"default", "shapes", "_comment", "_fields"})
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


def validate_task_types(doc) -> list[str]:
    """Errors for a task-type-profiles document fragment. Same present-only
    philosophy as :func:`validate_shapes`. Empty list = valid.
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
            errs.extend(validate_tag_row("default", default))

    tags = doc.get("tags")
    if tags is not None:
        if not isinstance(tags, dict):
            errs.append("'tags' must be an object")
        else:
            for name, row in tags.items():
                if not isinstance(row, dict):
                    errs.append(f"tag {name!r} must be an object")
                    continue
                errs.extend(validate_tag_row(name, row))
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
    # Track C2 cross-field invariant: a shape declaring
    # ``checkpoint_policy: skip-if-declared`` MUST declare an integrity
    # substitute (``ac_grounding: review``) — the "attach a guarantee to every
    # freedom" rule. A checkpoint skip with no verification substitute would
    # break the "verified against AC-N" stamp. This save-time guard is the
    # PRIMARY catch (the strict-write gate refuses the misconfiguration);
    # dispatch's runtime ``checkpoint_skip_decision`` is defense-in-depth for a
    # hand-edited/legacy registry. Judged on the default-INHERITED value (a row
    # may inherit ``ac_grounding`` from ``default``), mirroring runtime exactly.
    if isinstance(merged, dict):
        default_grounding = (merged.get("default") or {}).get(
            "ac_grounding", "test") if isinstance(merged.get("default"), dict) \
            else "test"
        shapes = merged.get("shapes")
        if isinstance(shapes, dict):
            for name, row in shapes.items():
                if not isinstance(row, dict):
                    continue
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
    return errs


def validate_merged_task_types(merged) -> list[str]:
    """Errors for a RESOLVED (baseline ⊕ overlay) task-type document — adds the
    ``default`` requirement to :func:`validate_task_types`.
    """
    errs = validate_task_types(merged)
    if isinstance(merged, dict) and not isinstance(merged.get("default"), dict):
        errs.append(
            "merged task-type registry must declare a top-level 'default' object")
    return errs
