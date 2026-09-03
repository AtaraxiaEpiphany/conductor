# Planning: deliverable — the non-code artifact track

The planning docfile for the `deliverable` workflow shape (declared
`planning_doc: deliverable.md` in the workflow-shape registry). Two
audiences: the **Prelude** is orchestrator-facing (pre-planning steps
`new-track` runs BEFORE dispatching spec-planner); the **Planning
procedure** below is planner-facing. Procedure only — the verifier set
(ac-tracer alone) and the grounding paradigm (`ac_grounding: review`) stay
registry-owned. A project may override this file at
`conductor/planning/deliverable.md` (project wins).

## Prelude (orchestrator)

None — dispatch spec-planner directly (§2.3). A deliverable track needs no
pre-planning step: the artifact's shape is stated in the description or
Brief, and the planner decomposes it from that.

## Planning procedure (spec-planner)

Plan a NON-CODE DELIVERABLE track: the output is an artifact (a design
doc, research report, spec, runbook, data deliverable) whose correctness
is witnessed by an artifact anchor + a review attestation, not by tests.

1. **Ground every AC by review.** Emit `## Artifact Anchors` INSTEAD of
   `## Test Scenarios`: every AC maps to a concrete deliverable anchor — a
   `| AC-N | <artifact> | <location> |` row naming what the deliverable IS
   (a doc section, report chapter, data file) and where it lives. There
   are no TCs and no `test_TC_*` functions; the AC is met by the anchor
   existing AND a review attesting it satisfies the criterion. A spec
   missing the anchors substrate fails `spec-anchors`.
2. **Decompose into artifact-producing tasks.** Each task's AC comment
   names the anchor(s) it produces, so the ac-tracer can witness each AC
   at the checkpoint by checking the anchor landed where the row said.
3. **Every task still lands a real commit.** A deliverable task commits
   its artifact (a docs/chore commit) so the non-empty-commit rule holds —
   the artifact IS the commit's content.
4. **The review attestation is the grounding.** The review channel is the
   verification (the shape's checkpoint fans out ac-tracer only — no
   tests to run); do not plan test-writing tasks on this shape.
5. **Declare durable cross-task files with `produces:`/`uses:` edges.** A
   deliverable task's artifact is often consumed by a later task (an
   inventory a report cites, a dataset an analysis reads): the producing
   task declares `<!-- produces: docs/inventory.md -->`, the consuming
   task declares `<!-- uses: docs/inventory.md -->`. Every `produces:`
   should gain a `uses:` before the final phase — an unconsumed artifact
   is dead code (plan-format-contract.md rule 9).
