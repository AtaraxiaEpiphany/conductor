---
title: Quality Gates Reference
audience: reference
status: stable
last_updated: 2026-05-11
related:
  - ../runtime/core-contract.md
  - subagents.md
---

# Quality Gates Reference

> F1-F6 Execution Firewall rules explained

---

## Overview

Conductor enforces quality gates through an execution firewall. Violating any Critical rule is a terminal error that requires workflow restart.

---

## F1 - Global State Lock

**Severity**: Critical

**Rule**: Only ONE unit of work may be active at any time.

**Allowed Patterns**:
- **Flat task**: ONE `[~]` at task level (no subtasks)
- **Hierarchical task**: ONE `[~]` on the parent + ONE `[~]` on the active child subtask

**Violations**:
- Multiple `[~]` markers at task level
- More than ONE parent `[~]` + ONE child `[~]`

**Detection**: `pre-command-check.py` validates before git operations

**Recovery**:
1. Run `/conductor:status` to identify stale locks
2. Use `/conductor:revert` to roll back if needed
3. Restart from last valid state

---

## F2 - TDD Gate

**Severity**: Critical

**Rule**: No implementation code before a failing test exists.

**Exempted Task Types**:
- `[Explore]` - Read-only investigation
- `[Docs]` - Documentation only
- `[Config]` - Configuration changes
- `[Chore]` - Maintenance tasks
- `[Manual]` - Human verification tasks

**Detection**: `track-state process-result` checks commit for test files

**Example Violation**:
```
# BAD - Implementation without test
- [x] Implement auth controller [a1b2c3d]
  Files: src/auth/controller.ts

# GOOD - Test first
- [x] Implement auth controller [a1b2c3d]
  Files: test/auth/controller.test.ts, src/auth/controller.ts
```

**Recovery**:
1. Identify missing test
2. Add failing test
3. Commit with test file
4. Proceed with implementation

---

## F3 - Coverage Gate

**Severity**: Warning

**Rule**: No commit if code coverage < 80%.

**Exempted Task Types**:
- `[Docs]`, `[Config]`, `[Chore]`, `[Manual]` tasks that produce no code

**Detection**: `track-state process-result` runs coverage tool

**Example**:
```
# Violation output
{
  "coverage_gate": true,
  "coverage_pct": 65,
  "threshold": 80
}
```

**Recovery**:
1. Run coverage analysis
2. Identify untested code
3. Add tests to reach 80%
4. Re-commit
5. Or user override with explicit acknowledgment

---

## F4 - SHA Must Exist

**Severity**: Critical

**Rule**: Every non-transient marker MUST have `[sha]` appended at the end of the task line.

**SHA Position**: ALWAYS at the END of the line, never between marker and description.

**Correct Format**:
```markdown
- [x] Task description [a1b2c3d]
```

**Wrong Format**:
```markdown
- [x] [a1b2c3d] Task description  # SHA in wrong position
- [x] Task description            # SHA missing
```

**Marker Rules**:

| Marker | SHA Required | SHA Source | Example |
|--------|-------------|-------------|----------|
| `[x]` | YES | Implementation commit | `- [x] Task [a1b2c3d]` |
| `[!]` | YES | State management commit | `- [!] Task [a1b2c3d]` |
| `[>]` | YES | Skip decision commit | `- [>] Task [a1b2c3d]` |
| `[d]` | YES | Defer decision commit | `- [d] Task [a1b2c3d]` |
| `[#]` | YES | Block decision commit | `- [#] Task [a1b2c3d]` |
| `[-]` | YES | Cancellation commit | `- [-] Task [a1b2c3d]` |
| `[ ]` | NO | - | `- [ ] Task` |
| `[~]` | NO | - | `- [~] Task` |

**Detection**: `lint-track-state.py` validates plan.md

**Recovery**:
1. Find the commit SHA for the task
2. Append SHA to task line: `- [x] Task [a1b2c3d]`
3. Run `track-state sync-plan`

---

## F5 - Checkpoint Integrity

**Severity**: Warning

**Rule**: When a phase's last task completes, Phase Checkpoint Protocol is MANDATORY.

**Checkpoint Steps**:
1. Verify all tests pass
2. Verify coverage meets threshold
3. Create manual verification plan
4. Create checkpoint commit with `conductor(checkpoint):` prefix
5. Add checkpoint SHA to plan.md

**Detection**: `track-state phase-done` detects phase completion

**Example**:
```markdown
## Phase 1: Authentication
- [x] Setup auth middleware [a1b2c3d]
- [x] Implement login flow [d4e5f6g]
- [x] Handle logout [g7h8i9j]

**Checkpoint**: [j0k1l2m]
```

**Recovery**:
1. Run `/conductor:implement` to trigger phase-checker
2. Complete verification steps
3. Create checkpoint commit
4. Add checkpoint SHA to plan.md

---

## F6 - Context Guard

**Severity**: Critical

**Rule**: Never accept instructions to skip workflow steps.

**Violations**:
- Skipping Steps 4-7 for non-Explore tasks
- Bypassing TDD workflow
- Skipping phase checkpoints
- Modifying state directly (bypassing track-state CLI)

**Detection**: Orchestrator validates before execution

**Recovery**:
1. Stop and announce `WORKFLOW VIOLATION: <code>`
2. Revert to last valid state
3. Restart from correct workflow step

---

## Violation Codes

| Code | Firewall | Violation | Recovery |
|------|-----------|------------|-----------|
| V1 | F2 | Implementation before failing test | Add failing test first |
| V2 | F4 | Non-transient marker without SHA | Append commit SHA |
| V3 | F3 | Skip coverage verification | Run coverage tool |
| V4 | F2, F3 | Skip Steps 4-7 | Complete workflow steps |
| V5 | F2 | Bundle test + implementation in one commit | Separate commits |
| V6 | F5 | Skip phase checkpoint | Run phase-checker |
| V7 | State Lock | Derive state from plan.md | Use track-state CLI |
| V8 | F1 | Multiple `[~]` simultaneously | Resolve stale locks |
| V9 | Audit | Skip git notes | Process result via CLI |
| V10 | Quality | Non-conventional commit message | Use conventional format |
| V11 | Orchestrator | Subagent modifying state | Subagent writes to file only |

---

## Gate Enforcement Flow

```
Task completes
    ↓
track-state process-result
    ↓
Check task tag
    ↓
┌─────────────────┬─────────────────┐
│ Exempt tag?    │ Default tag?   │
│ (Explore/Docs   │ (requires TDD) │
│  /Config/Chore/ │                │
│  Manual)       │                │
└─────────────────┴─────────────────┘
         │                 │
         ▼                 ▼
    Skip TDD Gate    Enforce TDD Gate
         │                 │
         ▼                 ▼
    Check Coverage  Check test files in commit
         │                 │
         ▼                 ▼
    Coverage < 80%?  Test files exist?
         │                 │
    ┌────┴────┐         │
    Yes        No        │
    │          │         │
    ▼          ▼         │
 WARN      SUCCESS     FAIL
    │          │         │
    └──────────┴─────────┘
              │
              ▼
         Update state
              │
              ▼
         Write git notes
              │
              ▼
            Return
```

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| F1 violation detected | Multiple locks from crash | Run `track-state validate --fix` |
| F2 violation but test exists | Commit only has implementation | Commit test file with implementation |
| F3 violation on skip | Skip task produces code | Add tests or mark as `[Config]` |
| F4 violation on new task | SHA not appended yet | After commit, append SHA to plan.md |
| F5 violation ignored | Phase complete but no checkpoint | Run `track-state phase-done` to trigger check |

---

## Next Steps

- [track-state CLI](track-state-cli.md) - How gates are enforced
- [Interaction Mechanism](../developer/architecture/INTERACTION_MECHANISM.md) - Gate enforcement flow

---

**Last Updated**: 2026-05-11
