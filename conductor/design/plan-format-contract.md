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
6. **AC Traceability**: Each implementation task MUST have an HTML comment `<!-- AC-n, TC-n.n, ... -->` linking to the acceptance criteria and test scenarios it covers. This enables the orchestrator to pass precise AC context to conductor:task-executor subagents. **Only the parent task carries the AC annotation** — subtasks inherit AC context from their parent. This rule is **enforced by the `check-plan-annotations` PreToolUse hook** (sibling to `check-plan-checkboxes`): a missing or malformed annotation on an untagged implementation task line hard-denies the Write/Edit before it lands — otherwise the parser silently records empty refs and the task loses all traceability. Dispatch-tagged tasks (`[Explore]`/`[Docs]`/`[Config]`/`[Chore]`/`[Manual]`) and indented subtasks are exempt.
7. **Test ↔ TC Naming Link**: Each `TC-{n}.{m}` row is verified by a test function named `test_TC_{n}_{m}_*(…)` — one function per TC row (`async def` is fine; the `{n}.{m}` become underscores). This **closes the traceability loop**: the orchestrator's `tc_consistency_gate` resolves the TCs a task *claims* (`tc_coverage`) to these real functions (the third link of the self-extraction chain: declared → claimed → grounded), and `ac_verification_measured_rate` measures how many ACs are backed by such tests. Without the link the gate can only trust self-report. The check measures *naming* coverage, not test isolation — a kitchen-sink `test_TC_2_1_and_2_2` grounds both TC-2.1 and TC-2.2, which is accepted.

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

## Inter-Task Dependencies (optional, advisory)

A top-level task MAY declare which earlier tasks it depends on with a second HTML comment, separate from the AC/TC comment:

```markdown
- [ ] Task: build user API <!-- AC-3, TC-3.1 --> <!-- deps: P1.T1 -->
```

`P{n}.T{n}` is the runtime's own positional coordinate — Phase `n`, Task `n` (1-based, top-level only), the same `P{pi}.T{ti}` notation `lint-track-state` and the handoff use. Multiple deps are comma-separated: `<!-- deps: P1.T1, P1.T3 -->`.

**Rules:**
1. **Optional.** Deps are not required. A task with no `<!-- deps: -->` comment simply has no declared predecessor — the conductor's default serial order still applies.
2. **The AC/TC comment (§6) is still mandatory and separate.** Deps is an *additional* comment, never a replacement. A line with only `<!-- deps: -->` and no AC/TC still fails the `check-plan-annotations` hook.
3. **Top-level tasks only.** Subtasks inherit context and are sequentially decomposed (they ARE one deliverable), so they are never parallel candidates and their `deps` are ignored by the parser.
4. **Positional refs shift on reorder.** `P1.T2` means "the second top-level task in phase 1." If you insert a task above it, the coordinate moves — a known v1 tradeoff. (A future revision may add stable `<!-- id: name -->` anchors if reordering becomes common.)
5. **Inert in v1 — validated but not executed on.** The parser (`plan_parse.validate_deps`) checks every `deps` annotation for dangling refs, self-deps, and cycles and surfaces them as **warnings** at `track-state init-from-plan --check`. They do **not** block init, because nothing executes on deps yet — the conductor still runs strictly serial under the F1 Global State Lock. A future scheduler may consume `collect_deps` to build a ready-set (tasks whose deps are all terminal) for parallel execution; until then, declare deps so the dependency graph is **explicit and machine-checkable** rather than implicit in phase/task ordering.

**When to declare deps:** emit `<!-- deps: ... -->` whenever a task is *not file-disjoint* from its siblings — i.e., it builds on an artifact an earlier task produced (a model, a utility, a config key). Omit it when tasks in a phase touch genuinely disjoint files/modules and could in principle run concurrently. Making coupling explicit here is the upstream input any future within-phase parallelism depends on.

