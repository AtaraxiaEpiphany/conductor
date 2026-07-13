# Standard Task Workflow

## 11-Step Standard Task Workflow

**Ownership split (Conductor orchestration).** The task lifecycle is split between the orchestrator and the task-executor agent:

- **Orchestrator** (`track-state dispatch-*` / `dispatch-finalize`) owns **Steps 1, 2, 9, 10, 11** — task selection, marking in-progress, the git-notes audit trail, SHA recording, and the `plan.md` completion commit.
- **Task-executor agent** owns **Steps 3-8 only** (Red → Green → Refactor → Coverage → Deviations → Commit code). It does **not** write git notes, modify plan markers, or append SHAs — `dispatch-finalize` performs Steps 9-11.

1. **Select Task** *(orchestrator)* – per the **Task Selection Protocol** above.
2. **Mark In Progress** *(orchestrator)* – change `[ ]` to `[~]` in `plan.md`.
3. **Write Failing Tests (Red)** – create test file in the project's designated test directory (typically `tests/`), following the naming and placement conventions in the loaded code styleguide. Run it, **confirm failure**; show the failing output.
4. **Implement to Pass Tests (Green)** – write **minimal** code to make the tests pass; confirm pass.
5. **Refactor** – one bounded, **diff-scoped** pass on the code you just wrote, under your passing Step-3 tests (run the project lint/format on your changed files, fix findings). **behavior-preserving** — public-API/behavior changes are Step 7, not refactor. Commit as `refactor(area): …`; Step 6's coverage run is your **green-confirm** (revert on red, never fix forward). Skip if it'd trip the §7.0 tripwire. *(executor §4.0 binds it; `[Docs]`/`[Config]`/`[Chore]` exempt.)*
6. **Verify Coverage** – run coverage tool, **must be >80%**. If not, add tests until the threshold is met. **Do not commit if coverage is below 80%**.
7. **Document Deviations** – if implementation diverges from the tech stack, **stop**, update `tech-stack.md` with the change and rationale, then resume.
8. **Commit Code Changes** – stage all code changes and commit with a conventional message (e.g., `feat(ui): ...`).
9. **Attach Git Notes** *(orchestrator)* – `track-state dispatch-finalize` writes the audit-trail git note for the task commit automatically (task name, changed files, reason). The executing agent does **not** run `git notes add`.
10. **Record Task SHA** *(orchestrator)* – `dispatch-finalize` syncs `plan.md`: task status → `[x]` with the short hash appended.
11. **Commit plan.md Update** *(orchestrator)* – `dispatch-finalize` stages and commits the `plan.md` update as the conductor completion commit.

**When the task ends a development phase, immediately run the Phase Completion Verification & Checkpointing Protocol (see `./phase-checkpoint.md`).**
