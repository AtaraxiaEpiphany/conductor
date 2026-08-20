# Standard Task Workflow

## 11-Step Standard Task Workflow

**Ownership split (Conductor orchestration).** The task lifecycle is split between the orchestrator and the task-executor agent:

- **Orchestrator** (`track-state dispatch-*` / `dispatch-finalize`) owns **Steps 1, 2, 9, 10, 11** — task selection, marking in-progress, the git-notes audit trail, SHA recording, and the `plan.md` completion commit.
- **Task-executor agent** owns **Steps 3-8 only** (Red → Green → Refactor → Coverage → Deviations → Commit code). It does **not** write git notes, modify plan markers, or append SHAs — `dispatch-finalize` performs Steps 9-11. The executor-owned steps live in the **workflow steps library** (`${CLAUDE_PLUGIN_ROOT}/templates/workflow/steps/`): `default-tdd.md` (the default Steps 3-8 TDD cycle) unless the task's leading tag declares a bespoke `workflow_doc` in the task-type registry. A project overrides or extends the library at `conductor/workflow/steps/` (project wins).

1. **Select Task** *(orchestrator)* – per the **Task Selection Protocol** above.
2. **Mark In Progress** *(orchestrator)* – change `[ ]` to `[~]` in `plan.md`.
3–8. **Executor-owned** – see the workflow steps library above. Default: `default-tdd.md` — **3** Write Failing Tests (Red) → **4** Implement to Pass (Green) → **5** Refactor → **6** Verify Coverage (>80%) → **7** Document Deviations → **8** Commit Code Changes.
9. **Attach Git Notes** *(orchestrator)* – `track-state dispatch-finalize` writes the audit-trail git note for the task commit automatically (task name, changed files, reason). The executing agent does **not** run `git notes add`.
10. **Record Task SHA** *(orchestrator)* – `dispatch-finalize` syncs `plan.md`: task status → `[x]` with the short hash appended.
11. **Commit plan.md Update** *(orchestrator)* – `dispatch-finalize` stages and commits the `plan.md` update as the conductor completion commit.

**When the task ends a development phase, immediately run the Phase Completion Verification & Checkpointing Protocol (see `./phase-checkpoint.md`).**
