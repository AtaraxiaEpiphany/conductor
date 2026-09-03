# Planning: default — the tested-code track

The planning docfile for the `default` workflow shape (declared
`planning_doc: default.md` in the workflow-shape registry). Two audiences:
the **Prelude** is orchestrator-facing (pre-planning steps `new-track` runs
BEFORE dispatching spec-planner); the **Planning procedure** below is
planner-facing (shape-specific authoring guidance spec-planner follows
after its universal context load). Procedure only — gates, verifiers, and
grounding stay registry-owned fields. A project may override this file at
`conductor/planning/default.md` (project wins).

## Prelude (orchestrator)

None — dispatch spec-planner directly (§2.3). The default shape needs no
pre-planning step: brief/scan discovery already feeds the planner
everything it needs.

## Planning procedure (spec-planner)

Plan a TESTED-CODE track: the shape's gates are the full set
(tdd / coverage / checkpoint), so every implementation task lands as real
code verified by tests.

1. **Ground every AC in tests.** Emit the `## Test Scenarios` substrate:
   every AC maps to ≥1 TC (`TC-{AC_NUMBER}.{SCENARIO_INDEX}`) that a
   `test_TC_*` function will ground — happy path plus ≥1 edge case per AC.
   A spec missing this substrate fails `spec-anchors`.
2. **Decompose for independent verification.** Each task is one coherent
   unit a single executor cycle can red → green → refactor → commit; the
   plan's phases order those units so each lands testable on top of the
   last.
3. **Tasks default untagged.** No tag IS the correct default here (full
   TDD); the registry's per-tag `when_to_use` / `signals` rules apply as
   usual — this shape adds no shape-specific tag.
4. **Declare durable cross-task files with `produces:`/`uses:` edges.** A
   task whose deliverable includes a file a LATER task must read (a
   baseline report, a mapping table) declares
   `<!-- produces: reports/x.md -->` on its task line; the consuming task
   declares `<!-- uses: reports/x.md -->`. Every `produces:` should gain a
   `uses:` before the final phase — an unconsumed artifact is dead code
   (plan-format-contract.md rule 9).
