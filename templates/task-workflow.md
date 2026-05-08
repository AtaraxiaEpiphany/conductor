# Standard Task Workflow

## Task Selection Protocol (Absolute Locking)

**Every leaf-level unit of work in `plan.md` (main task or subtask) is a full task that must undergo the complete 11-step workflow.**

**Selection Algorithm**

1. **Parse Current State:** Read `plan.md` fully. Identify the first Main Task marked `[~]` (active main task).
2. **If an active Main Task exists:**
   - Look at its **direct subtasks** (indented items beginning with `- [ ]`, `- [~]`, or `- [x]`).
   - **IF there is ANY subtask marked `[ ]`:**
     → Choose the **first** pending subtask as the next task to execute.
     → Go to Step 3 (Mark In Progress).
   - **IF all subtasks are `[x]`:**
     → The Main Task itself is now complete. Complete the Main Task's own records (if needed) then proceed to the next Main Task.
3. **If no Main Task is `[~]`:**
   - Find the first Main Task with `[ ]` status and mark it `[~]`.
   - Then apply rule 2 recursively to pick its first pending subtask.
4. **No subtasks defined for a Main Task:**
   - Treat the Main Task itself as a single task and execute the full workflow for it.

**After selecting a task, you MUST emit a locking statement:**
`🔒 TASK LOCK ACQUIRED: 'MainTask: X -> Subtask: Y'. Only this unit of work exists until Step 11 completion.`

---

## 11-Step Standard Task Workflow

1. **Select Task** – per the **Task Selection Protocol** above.
2. **Mark In Progress** – change `[ ]` to `[~]` in `plan.md`.
3. **Write Failing Tests (Red)** – create test file in the project's designated test directory (typically `tests/`), following the naming and placement conventions in the loaded code styleguide. Run it, **confirm failure**; show the failing output.
4. **Implement to Pass Tests (Green)** – write **minimal** code to make the tests pass; confirm pass.
5. **Refactor (optional)** – improve code under the safety of passing tests.
6. **Verify Coverage** – run coverage tool, **must be >80%**. If not, add tests until the threshold is met. **Do not commit if coverage is below 80%**.
7. **Document Deviations** – if implementation diverges from the tech stack, **stop**, update `tech-stack.md` with the change and rationale, then resume.
8. **Commit Code Changes** – stage all code changes and commit with a conventional message (e.g., `feat(ui): ...`).
9. **Attach Git Notes** – get the commit hash, compose a summary (task name, changed files, reason), and run `git notes add -m "<note>" <sha>`.
10. **Record Task SHA** – in `plan.md`, change the task status to `[x]` and append the short hash: `- [x] Task description [a1b2c3d]`.
11. **Commit plan.md Update** – stage `plan.md` and commit (e.g., `conductor(plan): mark task '...' complete`).

**When the task ends a development phase, immediately run the Phase Completion Verification & Checkpointing Protocol (see `./phase-checkpoint.md`).**
