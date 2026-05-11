---
title: Subagent Reference
audience: reference
status: stable
last_updated: 2026-05-11
---

# Subagent Reference

> Quick reference for all subagents in Conductor

---

## Subagent Registry

| Subagent | Model | Purpose |
|----------|--------|---------|
| **task-executor** | Sonnet | TDD workflow execution for implementation tasks |
| **explorer** | Haiku | Read-only codebase investigation |
| **phase-checker** | Sonnet | Phase verification and checkpoint protocol |
| **code-reviewer** | Sonnet | Deep code review |
| **skip-analyst** | Haiku | Failed task analysis and skip recommendations |
| **spec-planner** | Sonnet | Spec and plan generation from requirements |
| **spec-reviewer** | Sonnet | Interactive spec/plan review |
| **doc-syncer** | Sonnet | Documentation synchronization after track completion |
| **project-analyzer** | Sonnet | Brownfield project analysis |

---

## Subagent Details

### task-executor

Executes TDD workflow for implementation tasks:
1. Read spec.md and plan.md
2. Write failing test (Red)
3. Implement minimal code (Green)
4. Refactor code (Refactor)
5. Run coverage check
6. Commit with conventional message
7. Write result.json with outcomes

### explorer

Read-only codebase investigation for understanding architecture, mapping dependencies, and finding relevant code paths.

### phase-checker

Verifies phase completion:
- All tasks in phase are terminal
- Tests pass
- Coverage meets threshold
- Manual verification plan created
- Checkpoint commit created

### code-reviewer

Deep code review checking:
- Plan compliance
- Style guide adherence
- Test coverage
- Code quality issues
- Security vulnerabilities

### spec-planner

Generates spec.md and plan.md from requirements:
1. Analyze requirements
2. Create spec.md with ACs and TCs
3. Break down into phases and tasks
4. Write plan.md

### doc-syncer

Updates project documentation after track completion (product.md, tech-stack.md, product-guidelines.md).

---

## Result Format

### task-executor Result

```
---TASK RESULT---
STATUS: SUCCESS|FAILURE
COMMIT_SHA: <hash or N/A>
FILES_CHANGED: <comma-separated or N/A>
SUMMARY: <one-line>
TC_COVERAGE: <IDs or N/A>
SPEC_DEVIATION: NONE|<description>
---END RESULT---
```

### phase-checker Result

```
---CHECKPOINT RESULT---
STATUS: PASSED|FAILED
CHECKPOINT_SHA: <hash or N/A>
MISSING_TESTS_CREATED: <count or 0>
TESTS_PASSED: <true|false>
USER_CONFIRMED: <true|skipped_continuous>
FAILURE_REASON: <description if FAILED>
---END RESULT---
```

---

**Last Updated**: 2026-05-11
