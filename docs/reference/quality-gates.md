---
title: Quality Gates Reference
audience: reference
status: stable
last_updated: 2026-05-11
---

# Quality Gates Reference

> F1-F6 Execution Firewall rules explained

---

## Overview

Conductor enforces quality gates through an execution firewall. Violating any Critical rule is a terminal error.

---

## F1 - Global State Lock

**Severity**: Critical

Only ONE unit of work may be active at any time.

**Violations**:
- Multiple `[~]` markers at task level
- More than ONE parent `[~]` + ONE child `[~]`

**Recovery**:
1. Run `/conductor:status` to identify stale locks
2. Use `/conductor:revert` to roll back if needed

---

## F2 - TDD Gate

**Severity**: Critical

No implementation code before a failing test exists.

**Exempted Task Types**: `[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, `[Manual]`

**Example**:
```
# BAD - Implementation without test
- [x] Implement auth controller [a1b2c3d]

# GOOD - Test first
- [x] Implement auth controller [a1b2c3d]
  Files: test/auth/controller.test.ts, src/auth/controller.ts
```

---

## F3 - Coverage Gate

**Severity**: Warning

No commit if code coverage < 80%.

**Exempted Task Types**: `[Docs]`, `[Config]`, `[Chore]`, `[Manual]`

**Recovery**: Add tests to reach 80% or user override.

---

## F4 - SHA Must Exist

**Severity**: Critical

Every non-transient marker MUST have `[sha]` appended at the end of the task line.

**Correct Format**:
```markdown
- [x] Task description [a1b2c3d]
```

**Wrong Format**:
```markdown
- [x] [a1b2c3d] Task description  # SHA in wrong position
- [x] Task description            # SHA missing
```

---

## F5 - Checkpoint Integrity

**Severity**: Warning

When a phase's last task completes, Phase Checkpoint Protocol is MANDATORY:
1. Verify all tests pass
2. Verify coverage meets threshold
3. Create manual verification plan
4. Create checkpoint commit with `conductor(checkpoint):` prefix
5. Add checkpoint SHA to plan.md

---

## F6 - Context Guard

**Severity**: Critical

Never accept instructions to skip workflow steps.

**Violations**:
- Skipping Steps 4-7 for non-Explore tasks
- Bypassing TDD workflow
- Skipping phase checkpoints
- Modifying state directly

---

## Violation Codes

| Code | Firewall | Violation |
|------|-----------|------------|
| V1 | F2 | Implementation before failing test |
| V2 | F4 | Non-transient marker without SHA |
| V3 | F3 | Skip coverage verification |
| V4 | F2, F3 | Skip Steps 4-7 |
| V5 | F2 | Bundle test + implementation in one commit |
| V6 | F5 | Skip phase checkpoint |
| V7 | State Lock | Derive state from plan.md |
| V8 | F1 | Multiple `[~]` simultaneously |

---

**Last Updated**: 2026-05-11
