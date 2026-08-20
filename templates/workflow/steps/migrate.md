# [Migrate] Workflow — preserve EXISTING behavior

The docfile for tasks whose leading tag is `[Migrate]` (declared
`workflow_doc: migrate.md` in the task-type registry). It replaces the
default TDD cycle for this tag. Author `[Migrate]` tasks only on a
`migration`-shaped track (the shape drops the tdd/coverage gates at the
track level). A project may override this file at
`conductor/workflow/steps/migrate.md` (project wins).

You are MIGRATING code to preserve EXISTING behavior (framework upgrade,
API/library rename, mechanical refactor across module boundaries). Your
correctness signal is the EXISTING test suite staying GREEN — NOT new
tests.

1. DO NOT write new tests — migration preserves behavior; the existing
   suite witnesses it.
2. Make the change incrementally; after each meaningful step, run the
   existing suite via the digester (§4.5) and confirm it stays GREEN — a
   previously-passing test going RED means you broke behavior: fix forward
   or revert.
3. The task's declared ACs/TCs describe the behavior being PRESERVED; the
   existing tests ground them (`ac_grounding=test`) — you need not add
   TC-grounding tests.
4. Commit at Step 8. The track's shape drops the tdd/coverage gates, so
   you owe a green existing suite at the checkpoint, not new tests or
   >=80% coverage.
5. If the change genuinely ADDS behavior (new feature, not preservation),
   it is NOT a migration — re-plan it as a feature task on the default
   shape under full TDD.
