# Planning: research-first — explore before plan

The planning docfile for the `research-first` workflow shape (declared
`planning_doc: research-first.md` in the workflow-shape registry). Two
audiences: the **Prelude** is orchestrator-facing (pre-planning steps
`new-track` runs BEFORE dispatching spec-planner) — for this shape the
Prelude is the point; the **Planning procedure** below is planner-facing.
Procedure only — topology stays registry-owned (`nodes` remains advisory;
this Prelude is planning-side ordering, never a dispatch-spine change). A
project may override this file at
`conductor/planning/research-first.md` (project wins).

## Prelude (orchestrator)

Run exploration BEFORE planning. Before the §2.3 spec-planner dispatch,
dispatch `conductor:explorer` once, prompt:

```
TRACK_DIR={track_dir}
PHASE=0
TASK=0
NAME=Research Prelude — {track_description}

PRE_PLAN=1: plan.md and spec.md do NOT exist yet — this is a pre-planning
exploration, so there is no plan task to read (skip that part of §3.0).
Derive your investigation scope from the NAME/description above: map the
modules, contracts, and constraints a plan for this track needs. Record
your Exploration Notes with phase 0 task 0
(track-state append-handoff "{track_dir}" 0 0 --type explore) so the
planning-phase map lives at .conductor/handoff/P0T0.md, and write your
result with --phase 0 --task 0.
```

Parse the `---TASK RESULT---` block:

- **SUCCESS** → pass the notes forward: add
  `RESEARCH_NOTES={track_dir}/.conductor/handoff/P0T0.md` to the §2.3
  spec-planner envelope (the planner self-loads the notes as primary
  context — Tier C, the same discipline as `USER_CONTEXT: brief`).
- **FAILURE** → announce the summary and proceed to §2.3 WITHOUT the
  notes — planning is not blocked by a failed exploration; the plan is
  simply written without the map (surface that to the user at review).

## Planning procedure (spec-planner)

Plan WITH the findings in hand. If `RESEARCH_NOTES` is present, read it
FIRST — before the codebase scan — as primary context alongside the Brief:
planning on a real map is the reason this shape exists.

1. **Anchor the plan in the explored structure.** Name the real modules,
   files, and conventions the notes found; do not plan against assumed
   structure the exploration already mapped (or corrected).
2. **Carry the map forward.** The notes' gotchas become spec Constraints;
   their `files_inventory` is already in the handoff channel each
   task-executor reads — reference it rather than restating it in the
   plan.
3. **Exploration is COMPLETE at planning time.** Do not plan an
   exploratory first phase — the Prelude already ran it. Phases are
   implementation (test-grounded; the shape inherits the full gate set
   for its executor tasks).
4. **Declare durable cross-task files with `produces:`/`uses:` edges.**
   Research artifacts the notes surfaced (or implementation tasks emit —
   a coverage number, a comparison table) that a later task reads by
   path get a `<!-- produces: reports/x.md -->` on the producer's task
   line and a `<!-- uses: reports/x.md -->` on the consumer's. Every
   `produces:` should gain a `uses:` before the final phase — an
   unconsumed artifact is dead code (plan-format-contract.md rule 9).
