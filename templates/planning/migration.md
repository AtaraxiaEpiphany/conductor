# Planning: migration — behavior preservation

The planning docfile for the `migration` workflow shape (declared
`planning_doc: migration.md` in the workflow-shape registry). Two
audiences: the **Prelude** is orchestrator-facing (pre-planning steps
`new-track` runs BEFORE dispatching spec-planner); the **Planning
procedure** below is planner-facing. Procedure only — the gate paradigm
(`gates: [checkpoint]`, `ac_grounding: test`) stays registry-owned. A
project may override this file at `conductor/planning/migration.md`
(project wins).

## Prelude (orchestrator)

None — dispatch spec-planner directly (§2.3). A migration needs no
pre-planning step: the preservation target is stated in the description or
Brief, and the planner locates the affected surface while planning.

## Planning procedure (spec-planner)

Plan a BEHAVIOR-PRESERVATION track: correctness is witnessed by the
EXISTING test suite staying green, not by new tests. The shape drops the
tdd/coverage gates at the track level — tasks owe a green existing suite
at the checkpoint.

1. **Decompose into ordered, behavior-preserving tasks.** Sequence tasks so
   the existing suite can stay green between steps — order is the safety
   net, so make predecessor relationships explicit (`<!-- deps: -->`) where
   a task genuinely builds on an earlier one's moved/renamed surface.
2. **Tag every implementation task `[Migrate]`.** The executor then
   fetches the migrate workflow docfile instead of TDD. `[Migrate]` is
   opt-in (never auto-derived) — author it deliberately on each task line.
3. **ACs describe PRESERVED behavior, grounded by the existing suite.**
   Still emit `## Test Scenarios` (`ac_grounding` is `test`): each AC maps
   to TC rows naming the behavior the existing coverage witnesses. No task
   owes NEW tests — the TC rows are the preservation map, not a test plan.
4. **New behavior is NOT a migration.** If the description includes
   genuinely NEW behavior, surface it to the orchestrator/user before
   finalizing: this shape drops tdd/coverage for the WHOLE track, so
   new-behavior tasks would silently escape their gates. Plan those as a
   separate default-shape track instead.
5. **Declare durable cross-task files with `produces:`/`uses:` edges.** A
   migration's baselines and mapping tables are exactly the class of file
   later tasks read by path: the producing task declares
   `<!-- produces: reports/baseline.md -->`, the consuming task declares
   `<!-- uses: reports/baseline.md -->`. Every `produces:` should gain a
   `uses:` before the final phase — an unconsumed artifact is dead code
   (plan-format-contract.md rule 9).
