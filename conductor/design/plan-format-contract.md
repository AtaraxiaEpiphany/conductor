---
type: concept
sources:
  - agents/spec-planner
last_verified: 2026-06-26
---

# Plan Format Contract

The mandatory structure, status-marker, dispatch-tag, and subtask rules for `plan.md`. `spec-planner` (§4.2) reads this when generating `plan.md` and must follow it exactly. The orchestrator's plan parser and dispatch router depend on this structure: a task line without a `[ ]` checkbox is **silently dropped** by the parser, and dispatch tags (`[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, `[Manual]`) drive task routing and TDD gating. Violating any rule below breaks the orchestrator.

## Status-Marker & Structure Rules

These rules are **non-negotiable**. Violating any rule will break the orchestrator.

1. **Status Markers**: Every task and subtask gets a `[ ]` status marker. Indented subtasks use two-space indentation under their parent. A line without `[ ]` is silently dropped by the parser — the subtask would vanish. ❌ `- Subtask: xxx` → ✅ `- [ ] Subtask: xxx`. A `[Tag]` is **not** a substitute for the checkbox: ❌ `- [Explore] Task: xxx` → ✅ `- [ ] [Explore] Task: xxx`.
2. **Manual Verification**: Append a manual verification task at the end of each phase. Tag it with `[Manual]` so the orchestrator can auto-defer it in continuous mode.
3. **Phase Order**: Phases should follow logical dependency order.
4. **Atomic Tasks**: Tasks should be atomic and independently testable.
5. **Workflow Conventions**: Read the workflow file to respect any task-level conventions.
6. **AC Traceability**: Each implementation task MUST have an HTML comment `<!-- AC-n, TC-n.n, ... -->` linking to the acceptance criteria and test scenarios it covers. This enables the orchestrator to pass precise AC context to conductor:task-executor subagents. **Only the parent task carries the AC annotation** — subtasks inherit AC context from their parent.

## Task Type Tags

Prepend the tag BEFORE the task description. Tag determines whether TDD is required.

| Tag         | Meaning                                                       | TDD Required | When to Use                                                                                                                                                                                                     |
| ----------- | ------------------------------------------------------------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `[Explore]` | Code investigation, architecture analysis, dependency mapping | **NO**       | Phase 1 exploration — understanding codebase before implementation. Examples: "Explore the authentication module architecture", "Map API endpoints and their handlers"                                          |
| `[Docs]`    | Documentation-only changes                                    | **NO**       | Writing or updating docs. No code changes.                                                                                                                                                                      |
| `[Config]`  | Configuration file changes                                    | **NO**       | .env, .yaml, .json config files. No business logic.                                                                                                                                                             |
| `[Chore]`   | Maintenance tasks                                             | **NO**       | Dependencies, tooling, CI/CD. No feature code.                                                                                                                                                                  |
| `[Manual]`  | Requires human verification, cannot be automated              | **NO**       | Tasks that need human eyes/hands: manual UI testing, cross-browser checks, staging deployment verification, email delivery confirmation, accessibility audit. These tasks are auto-deferred in continuous mode. |
| *(no tag)*  | Standard implementation task                                  | **YES**      | Default. Full TDD workflow: Red → Green → Refactor.                                                                                                                                                             |

**Important**: Subtasks inherit the parent's task type tag. Do NOT tag subtasks individually.

## Subtask Rules

Not every task needs subtasks. Follow these guidelines:

**When to use subtasks:**
- The task involves **3+ distinct logical steps** that each need independent verification.
- The task spans **multiple files or modules** with clear boundaries.
- The task has **complex acceptance criteria** that map to distinct deliverables.

**When NOT to use subtasks (keep flat):**
- The task is a single, focused change (e.g., "Add validation to form field").
- The task touches one file or one module.
- The task has simple, single-aspect acceptance criteria.

**Subtask format rules:**
- Indent subtasks with 2 spaces under the parent task.
- Subtask descriptions should be specific and actionable.
- A parent with subtasks does NOT carry its own implementation — the subtasks ARE the implementation.
- A parent without subtasks IS the implementation task.
- Subtask count: minimum 2, recommended maximum 5. If more than 5, split into separate parent tasks.
