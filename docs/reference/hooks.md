---
title: Hook Reference
audience: reference
status: stable
last_updated: 2026-05-11
related:
  - ../developer/guides/extending-hooks.md
  - commands/implement.md
---

# Hook Reference

> Complete reference for all hooks in Conductor

---

## Overview

Conductor uses Claude Code's hook system for lifecycle automation. Hooks are configured in `hooks/hooks.json`.

## Hook Events Reference

| Event | Trigger | Script | Purpose | Exit Codes |
|-------|---------|---------|---------|------------|
| `SessionStart` | Session begins/resumes | `session-start.py` | Load conductor-core.md, session handoff | 0 |
| `SessionEnd` | Session terminates | `session-end.py` | Cleanup, handoff validation, metrics | 0 |
| `InstructionsLoaded` | CLAUDE.md loads | `enhance-conductor-context.py` | Progressive conductor context disclosure | 0 |
| `PreToolUse` | Before tool execution | `pre-command-check.py` | Block dangerous git ops, enforce state lock | 0 (allow), 2 (block) |
| `PostToolUse` | After tool success | `filter-subagent-output.py`, `on-test-run.py` | Filter subagent output, log test results | 0 |
| `PostToolBatch` | After parallel tools resolve | `on-batch-complete.py` | Batch-level validation | 0 |
| `SubagentStart` | Subagent spawns | `on-subagent-start.py` | Inject role-specific reminders | 0 |
| `SubagentStop` | Subagent finishes | `on-subagent-stop.py` | Lifecycle logging, asyncRewake for critical agents | 0, 2 (rewake) |
| `TaskCreated/Completed` | Task lifecycle events | `on-task-event.py` | Async logging | 0 |
| `ConfigChange` | Settings modify | `on-config-change.py` | Configuration validation & audit | 0 |
| `CwdChanged` | Directory changes | `on-cwd-change.py` | Conductor state awareness | 0 |

---

## Hook Configuration Format

```json
{
  "hooks": {
    "<EventName>": [
      {
        "matcher": "pattern",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/script.py\"",
            "timeout": 5,
            "async": false,
            "asyncRewake": false
          }
        ]
      }
    ]
  }
}
```

### Configuration Options

| Field | Type | Description |
|-------|------|-------------|
| `matcher` | string | Regex pattern to match event data |
| `type` | string | Hook type: `command` |
| `command` | string | Script to execute |
| `timeout` | number | Maximum execution time in seconds |
| `async` | boolean | Run in background (fire-and-forget) |
| `asyncRewake` | boolean | Wake Claude on exit code 2 (critical agents only) |

---

## Hook Scripts

### session-start.py

**Purpose**: Load conductor-core.md and session handoff on startup

**Input**: JSON via stdin
```json
{
  "session_id": "abc123",
  "match_result": "startup|resume|clear|compact"
}
```

**Output**: JSON via stdout
```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "[Conductor] Context loaded..."
  }
}
```

---

### session-end.py

**Purpose**: Cleanup, handoff validation, metrics logging

**Output**: Writes `session-handoff.md` for recovery

---

### enhance-conductor-context.py

**Purpose**: Progressive conductor context disclosure when CLAUDE.md loads

**Behavior**: Loads conductor-core.md progressively to avoid overwhelming context

---

### pre-command-check.py

**Purpose**: Block dangerous git operations and enforce state lock violations

**Exit Codes**:
- `0`: Allow command
- `2`: Block command (with permissionDecision)

**Blocked Operations**:
- `git reset --hard` (use /conductor:revert instead)
- `git rebase`, `git cherry-pick`
- State lock violations (multiple `[~]` tasks)

---

### filter-subagent-output.py

**Purpose**: Filter subagent output to reduce context pressure

**Behavior**: Extracts only delimited `---TASK RESULT---` blocks

**Output**: Returns filtered result or compact summary

---

### on-subagent-start.py

**Purpose**: Inject role-specific reminders via additionalContext

**Input**: Includes agent name and agent type

**Output**: Context reminder specific to agent role

---

### on-subagent-stop.py

**Purpose**: Lifecycle logging and critical agent failure detection

**Exit Codes**:
- `0`: Normal completion
- `2`: Critical agent failure (triggers asyncRewake)

**Critical Agents**: task-executor, explorer, phase-checker

---

### on-task-event.py

**Purpose**: Async logging for TaskCreated and TaskCompleted events

**Output**: Logs to `logs/task-lifecycle.log`

---

### on-config-change.py

**Purpose**: Configuration validation and audit logging

**Behavior**: Validates settings.json changes

---

### on-cwd-change.py

**Purpose**: Conductor state awareness across directory changes

**Behavior**: Detects when moving between projects with conductor

---

### state-consistency-check.py

**Purpose**: Detect stale in_progress tasks at session end

**Behavior**: Warns if locks are >24 hours old

---

## Hook Communication Protocol

### Input Format (JSON via stdin)

```json
{
  "session_id": "abc123",
  "transcript_path": "/path/to/session.json",
  "cwd": "/project/dir",
  "hook_event_name": "PreToolUse",
  "tool_name": "Bash",
  "tool_input": {
    "command": "git status"
  }
}
```

### Output Format (JSON via stdout)

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": "[Conductor] Context message...",
    "permissionDecision": "allow|deny|ask|defer",
    "permissionDecisionReason": "Reason for decision",
    "updatedInput": { "command": "modified command" }
  }
}
```

### Error Output (JSON via stderr)

Used for permission decisions:
```json
{
  "hookSpecificOutput": {
    "permissionDecision": "ask",
    "permissionDecisionReason": "State lock violation detected"
  }
}
```

---

## Async vs Sync Hooks

### Sync (default)
- Blocks execution until completion
- Used for permission decisions and validation
- Exit code 0 = allow, 2 = block

### Async
- Runs in background (fire-and-forget)
- Used for logging and telemetry
- Exit immediately, session continues

### AsyncRewake
- Runs in background but wakes Claude on exit code 2
- Used for critical agent failures
- Session wakes with recovery context

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| Hook not executing | Script not executable | Run `chmod +x script.sh` |
| Hook not triggering | Wrong event name | Verify event name (case-sensitive) |
| Permission denied | Exit code 2 not in stderr | Use stderr for permission decisions |
| Hook timeout | Timeout too low | Increase timeout in hooks.json |

---

## Next Steps

- [Interaction Mechanism](../developer/architecture/INTERACTION_MECHANISM.md) - Deep dive into hook flows
- [Subagent Reference](subagents.md) - Subagent definitions
- [track-state CLI](track-state-cli.md) - State management commands

---

**Last Updated**: 2026-05-11
