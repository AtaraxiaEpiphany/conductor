---
title: Hook Reference
audience: reference
status: stable
last_updated: 2026-05-11
---

# Hook Reference

> Quick reference for all hooks in Conductor

---

## Hook Events Reference

| Event | Trigger | Purpose |
|-------|---------|---------|
| `SessionStart` | Session begins/resumes | Load conductor-core.md, session handoff |
| `SessionEnd` | Session terminates | Cleanup, handoff validation, metrics |
| `InstructionsLoaded` | CLAUDE.md loads | Progressive conductor context disclosure |
| `PreToolUse` | Before tool execution | Block dangerous git ops, enforce state lock |
| `PostToolUse` | After tool success | Filter subagent output, log test results |
| `SubagentStart` | Subagent spawns | Inject role-specific reminders |
| `SubagentStop` | Subagent finishes | Lifecycle logging, failure recovery |
| `TaskCreated/Completed` | Task lifecycle events | Async logging |
| `ConfigChange` | Settings modify | Configuration validation & audit |
| `CwdChanged` | Directory changes | Conductor state awareness |

---

## Key Hooks

### PreToolUse (pre-command-check.py)

**Purpose**: Block dangerous git operations and enforce state lock

**Blocked Operations**:
- `git reset --hard` (use `/conductor:revert` instead)
- `git rebase`, `git cherry-pick`
- State lock violations (multiple `[~]` tasks)

### SubagentStop (on-subagent-stop.py)

**Purpose**: Lifecycle logging and critical agent failure detection

**Critical Agents**: task-executor, explorer, phase-checker (trigger asyncRewake on failure)

### filter-subagent-output

**Purpose**: Filter subagent output to reduce context pressure

**Behavior**: Extracts only delimited `---TASK RESULT---` blocks

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success / Allow |
| 2 | Block / Wake session (for asyncRewake) |

---

**Last Updated**: 2026-05-11
