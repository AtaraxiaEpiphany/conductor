---
title: Hooks Reference
audience: developer
status: stable
last_updated: 2026-05-12
related:
  - ../../docs/reference/hooks.md
  - ./INTERACTION_REFERENCE.md
---

# Conductor Plugin: Hooks Reference

> Complete developer reference for all hook scripts, shared libraries, I/O protocol, and testing.

---

## Table of Contents

- [Architecture](#architecture)
- [Hook Protocol](#hook-protocol)
- [Hook Events (by lifecycle)](#hook-events-by-lifecycle)
- [Shared Library API](#shared-library-api)
- [Testing](#testing)
- [Debugging](#debugging)
- [Performance Profile](#performance-profile)

---

## Architecture

```
hooks/
  hooks.json              # Hook event bindings and matcher config
scripts/
  lib/
    hook_io.py            # JSON input/output (hook protocol)
    logging.py            # Structured log files under .data/logs/
    env.py                # Environment variable and path helpers
    validation.py         # Command/state validation utilities
    json_utils.py         # Safe JSON load, merge, format
    git_utils.py          # Git commands, notes, status
    path_utils.py         # Track root detection, file age
  session-start.py        # SessionStart
  session-end.py          # SessionEnd
  enhance-conductor-context.py  # InstructionsLoaded
  pre-command-check.py    # PreToolUse (Bash)
  filter-subagent-output.py     # PostToolUse (Agent)
  on-subagent-result.py         # PostToolUse (Agent)
  on-test-run.py                # PostToolUse (Bash)
  on-batch-complete.py          # PostToolBatch
  on-subagent-start.py          # SubagentStart
  on-subagent-stop.py           # SubagentStop
  on-phase-checkpoint-stop.py   # SubagentStop (phase-checker)
  on-review-stop.py             # SubagentStop (code-reviewer)
  on-task-event.py              # TaskCreated / TaskCompleted
  on-config-change.py           # ConfigChange
  on-cwd-change.py              # CwdChanged
  on-compact.py                 # PreCompact
  state-consistency-check.py    # Stop
```

All scripts are pure Python 3.8+ with no external dependencies. They communicate with the Claude Code runtime via JSON on stdin/stdout following the [Claude Code hook protocol](https://code.claude.com/docs/en/hooks).

---

## Hook Protocol

### Input (stdin)

The runtime sends a JSON object on stdin. All events share these common fields:

| Field | Description |
|-------|-------------|
| `session_id` | Current session identifier |
| `transcript_path` | Path to conversation JSONL |
| `cwd` | Working directory when hook fires |
| `hook_event_name` | Event name (e.g. `"PreToolUse"`) |
| `permission_mode` | Current permission mode (not all events) |

Event-specific fields are documented per-hook below.

### Output (stdout)

On exit code 0, stdout is parsed as JSON. The runtime validates output against the event's schema.

**Universal fields** (all events):

| Field | Default | Description |
|-------|---------|-------------|
| `continue` | `true` | If `false`, stops Claude entirely |
| `stopReason` | none | Message shown to user when `continue` is `false` |
| `suppressOutput` | `false` | Omit stdout from debug log |
| `systemMessage` | none | Warning message shown to user |

**Top-level decision** (select events):

| Field | Description |
|-------|-------------|
| `decision` | `"block"` to block the action |
| `reason` | Required when `decision` is `"block"` |

**`hookSpecificOutput`** (select events):

Only emit this object when you have actual event-specific fields. The runtime rejects `hookSpecificOutput` for events that do not support it (e.g. `InstructionsLoaded`, `SessionEnd`, `CwdChanged`).

| Field | Events | Description |
|-------|--------|-------------|
| `hookEventName` | all that use it | Must match the hook event name |
| `additionalContext` | SessionStart, SubagentStart, PreToolUse, PostToolUse, PostToolBatch | Injected into Claude's context |
| `permissionDecision` | PreToolUse | `"allow"`, `"deny"`, `"ask"`, or `"defer"` |
| `permissionDecisionReason` | PreToolUse | Shown to user or Claude |
| `updatedInput` | PreToolUse | Modified tool input |
| `updatedToolOutput` | PostToolUse | Replacement tool output |

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success. stdout parsed for JSON output. |
| 2 | Blocking error. stderr shown as error message. Effect depends on event. |
| Other | Non-blocking error. First line of stderr shown in transcript. Execution continues. |

### Which events support what

| Control | Events |
|---------|--------|
| `hookSpecificOutput` | PreToolUse, PostToolUse, PostToolBatch, SubagentStart, SessionStart, UserPromptSubmit |
| Top-level `decision` | Stop, SubagentStop, ConfigChange, PreCompact, PostToolBatch, PostToolUse, UserPromptSubmit |
| Exit code 2 blocks | PreToolUse, Stop, SubagentStop, TaskCreated, TaskCompleted, TeammateIdle, PermissionRequest |
| No control (observability) | InstructionsLoaded, SessionEnd, CwdChanged, FileChanged, Notification, PostCompact, StopFailure |

---

## Hook Events (by lifecycle)

### SessionStart

| | |
|---|---|
| **Script** | `session-start.py` |
| **Matcher** | `startup\|resume\|clear\|compact` |
| **Timeout** | 10s |
| **Can block** | No |
| **Output** | `hookSpecificOutput.additionalContext` |

**Input fields**: `source` (`"startup"`, `"resume"`, `"clear"`, `"compact"`), `model`

**Behavior**:

1. On `startup` or `resume`: loads `runtime/core-contract.md` and injects it as `additionalContext`.
2. On `compact`: injects a compact summary instead (task state model, commit format, firewall rules).
3. Loads `.data/session-handoff.md` if present and appends it to the context.

**Output example** (startup):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "...contents of core-contract.md...\n\n--- Previous Session Handoff ---\n..."
  }
}
```

---

### InstructionsLoaded

| | |
|---|---|
| **Script** | `enhance-conductor-context.py` |
| **Matcher** | `session_start\|include` |
| **Timeout** | 3s |
| **Can block** | No |
| **Output** | `{}` (observability only) |

**Input fields**: `file_path`, `memory_type`, `load_reason`, `globs`, `trigger_file_path`, `parent_file_path`

**Behavior**: Logs when conductor-related instruction files are loaded. Does not produce any output — exit code 0 with empty JSON object `{}`.

This event does **not** support `hookSpecificOutput`. The script uses `write_hook_output()` with no arguments, which outputs `{}`.

---

### PreToolUse (Bash)

| | |
|---|---|
| **Script** | `pre-command-check.py` |
| **Matcher** | `Bash` |
| **Timeout** | 3s |
| **Can block** | Yes — via `permissionDecision: "ask"` |
| **Output** | `hookSpecificOutput.permissionDecision`, `additionalContext` |

**Input fields**: `tool_name`, `tool_input.command`, `tool_use_id`

**Checks performed** (in order):

1. **Dangerous git operations**: Blocks `git reset --hard`, `git rebase`, `git clean`, `git filter-branch`, `git checkout --force`, `git branch -D` with `permissionDecision: "ask"`.
2. **Track-state lock violations**: If the command references `track-state` and a track has an `in_progress` task, blocks deletion/move commands.
3. **Direct track-state.json modification**: Blocks `rm`, `mv`, `sed`, or Python write operations targeting `track-state.json` directly. Directs user to `track-state` CLI.
4. All other commands: allowed.

**Output example** (dangerous git):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": "[Conductor] DANGER: Git reset command detected...",
    "permissionDecision": "ask",
    "permissionDecisionReason": "Git history-modifying operation (reset) detected. Use /conductor:revert workflow instead."
  }
}
```

Uses `lib/validation.py`: `is_dangerous_git_operation()`, `contains_dangerous_pattern()`.

---

### PostToolUse (Agent) — filter-subagent-output

| | |
|---|---|
| **Script** | `filter-subagent-output.py` |
| **Matcher** | `Agent` |
| **Timeout** | 5s |
| **Can block** | No |
| **Output** | `hookSpecificOutput.updatedToolOutput` |

**Input fields**: `tool_name`, `tool_input`, `tool_response`

**Behavior**: Extracts only `---RESULT---` delimited blocks from subagent output, discarding narrative/thinking text to reduce context pressure in the parent session.

**Recognized block types**:

| Delimiter start | Delimiter end |
|-----------------|---------------|
| `---TASK RESULT---` | `---END RESULT---` |
| `---CHECKPOINT RESULT---` | `---END RESULT---` |
| `---SKIP ANALYSIS---` | `---END ANALYSIS---` |
| `---DOC SYNC RESULT---` | `---END RESULT---` |
| `---REVIEW RESULT---` | `---END REVIEW RESULT---` |
| `---SPEC PLAN RESULT---` | `---END SPEC PLAN RESULT---` |

When no result block is found, replaces the output with a compact summary:

```
[Conductor] Subagent completed. No structured result block found. Check .conductor/ for artifacts.
```

---

### PostToolUse (Agent) — on-subagent-result

| | |
|---|---|
| **Script** | `on-subagent-result.py` |
| **Matcher** | `Agent` |
| **Timeout** | 5s |
| **Can block** | No |
| **Output** | `hookSpecificOutput.additionalContext` |

**Behavior**: Complements the SubagentStop hook by injecting recovery context into the **parent** session (not the subagent). Detects failure indicators (`status.*FAILURE`, `BUILD FAILED`, `Traceback`, `Command failed`, `test.*failed`) and recovery success indicators in subagent output.

This hook runs **after** the subagent has fully returned (unlike SubagentStop which fires during the stop attempt).

**Output on failure**:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "[Conductor] Subagent reported failure. If retries remain, the orchestrator will re-dispatch..."
  }
}
```

---

### PostToolUse (Bash) — on-test-run

| | |
|---|---|
| **Script** | `on-test-run.py` |
| **Matcher** | `Bash` |
| **Timeout** | 5s |
| **Can block** | No |
| **Output** | `hookSpecificOutput.additionalContext` (on failure) |

**Behavior**: Detects test commands (`test`, `pytest`, `jest`, `vitest`, `go test`, `cargo test`, `dotnet test`) and logs results. On failure, injects TDD guidance context:

```
[Conductor TDD] Test command produced errors. If this is the Red phase (Step 3), failure is expected.
If this is the Green phase (Step 4), fix the implementation.
```

**Failure detection patterns**: `\bFAILED\b`, `\bFAILURES\b`, `\d+\s+failed\b`, `tests?\s+failed\b`, `assertion\s+error`, `test\s+run\s+failed\b`, `runtime\s+error`.

---

### PostToolBatch

| | |
|---|---|
| **Script** | `on-batch-complete.py` |
| **Matcher** | none (fires on every batch) |
| **Timeout** | 35s |
| **Can block** | Yes — via top-level `decision: "block"` |
| **Output** | `hookSpecificOutput.additionalContext` |

**Input fields**: `tool_calls` (array of `{tool_name, tool_input, tool_use_id, tool_response}`)

**Behavior**:

1. **Pattern analysis**: Scans tool calls for state drift indicators:
   - Multiple `git commit` operations without a `track-state` update
   - Git operations during active subagent calls
2. **Server-side coverage gate (F3)**: After any `git commit`, runs the project's coverage tool (Python `coverage`, Jest, Go) and checks if coverage >= 80%. This prevents agents from self-reporting false coverage.
3. Logs batch metrics (git ops count, track-state ops count).

**Coverage tools supported**:

| Project type | Detection file | Coverage command |
|-------------|---------------|-----------------|
| Python | `pyproject.toml` or `setup.py` | `coverage report --format=text` |
| Node | `package.json` | `npm test -- --coverage` |
| Go | `go.mod` | `go test -coverprofile=/dev/stdout -cover` |

---

### SubagentStart

| | |
|---|---|
| **Script** | `on-subagent-start.py` |
| **Matcher** | `task-executor\|code-reviewer\|explorer\|phase-checker\|doc-syncer\|skip-analyst\|spec-planner\|spec-reviewer\|project-analyzer` |
| **Timeout** | 5s |
| **Can block** | No |
| **Output** | `hookSpecificOutput.additionalContext` |

**Behavior**: Injects role-specific reminders into each subagent type at spawn time.

**Agent reminders**:

| Agent type | Reminder |
|-----------|----------|
| `task-executor` | TDD workflow, validate tool calls, `---TASK RESULT---` format |
| `code-reviewer` | READ-ONLY for application code, `---REVIEW RESULT---` format |
| `explorer` | READ-ONLY, produce `exploration.md`, `---TASK RESULT---` format |
| `phase-checker` | Full checkpoint protocol, `---CHECKPOINT RESULT---` format |
| `doc-syncer` | Targeted updates with confirmation, `---DOC SYNC RESULT---` format |
| `skip-analyst` | READ-ONLY, conservative: prefer `pause_and_escalate` |
| `spec-planner` | Write `spec.md` and `plan.md`, `---SPEC PLAN RESULT---` format |
| `spec-reviewer` | Present summaries, `---REVIEW RESULT---` format |
| `project-analyzer` | READ-ONLY, `---ANALYSIS RESULT---` format |

---

### SubagentStop — on-subagent-stop

| | |
|---|---|
| **Script** | `on-subagent-stop.py` |
| **Matcher (sync)** | `task-executor\|explorer\|phase-checker` |
| **Matcher (async)** | `code-reviewer\|doc-syncer\|skip-analyst\|spec-planner\|spec-reviewer\|project-analyzer` |
| **Timeout** | 10s (sync), 5s (async) |
| **Can block** | Yes — via `decision: "block"` + `reason` |
| **Output** | Top-level `decision`, `reason` |

**Behavior**: Detects failure patterns in the subagent's last message. For critical agents (task-executor, explorer, phase-checker), returns `decision: "block"` which keeps the subagent running. The `reason` is delivered as the subagent's next instruction, enabling self-recovery.

**Failure detection patterns**: `Traceback (most recent call last)`, `Error:`, `Permission denied`, `File not found`, `Command failed`, `BUILD FAILED`, `test.*failed`, `AssertionError`.

**Safe context exclusions** (avoid false positives): `error handling`, `error message`, `errors?: none`, `error code`, `catch error`.

**Recovery message on block**:

```json
{
  "decision": "block",
  "reason": "[Conductor Recovery] Failure detected (pattern: ...). Review the error above, correct the issue, and retry. If the issue is unresolvable, report FAILURE in your result block."
}
```

---

### SubagentStop — on-phase-checkpoint-stop

| | |
|---|---|
| **Script** | `on-phase-checkpoint-stop.py` |
| **Matcher** | `phase-checker` |
| **Timeout** | 5s |
| **Can block** | No |
| **Output** | `{}` |

**Behavior**: Logs checkpoint completion event. Fire-and-forget.

---

### SubagentStop — on-review-stop

| | |
|---|---|
| **Script** | `on-review-stop.py` |
| **Matcher** | `code-reviewer\|doc-syncer\|skip-analyst\|spec-planner\|spec-reviewer\|project-analyzer` |
| **Timeout** | 5s |
| **Can block** | No |
| **Output** | `{}` |
| **Async** | Yes |

**Behavior**: Logs code review completion event. Async fire-and-forget.

---

### TaskCreated / TaskCompleted

| | |
|---|---|
| **Script** | `on-task-event.py` |
| **Matcher** | none |
| **Timeout** | 3s |
| **Can block** | No (async) |
| **Output** | `{}` |
| **Async** | Yes |

**Input fields**: `task_id`, `task_subject`, `task_description`, `teammate_name`, `team_name`

**Behavior**: Logs task lifecycle events to `.data/logs/`. Async fire-and-forget.

---

### Stop

| | |
|---|---|
| **Script** | `state-consistency-check.py` |
| **Matcher** | none |
| **Timeout** | 5s |
| **Can block** | Yes — via `decision: "block"` |
| **Output** | `additionalContext`, `systemMessage` |

**Behavior**:

1. **State validation**: Checks all active tracks in `conductor/tracks.md` for stale `in_progress` tasks in `track-state.json`.
2. **Session handoff**: Writes `.data/session-handoff.md` with active track information (track ID, status, position, execution mode) for next-session recovery.
3. **Cleanup**: Removes handoff file if no active tracks remain.

**Output on inconsistency**:

```json
{
  "additionalContext": "[Conductor] Stale in_progress tasks found in track-xxx: Phase 1 Task 2: ...",
  "systemMessage": "[Conductor] Stale in_progress tasks found..."
}
```

---

### SessionEnd

| | |
|---|---|
| **Script** | `session-end.py` |
| **Matcher** | none |
| **Timeout** | 5s |
| **Can block** | No |
| **Output** | `{}` |

**Input fields**: `reason` (`"clear"`, `"resume"`, `"logout"`, `"prompt_input_exit"`, `"bypass_permissions_disabled"`, `"other"`)

**Behavior**:

1. Validates `session-handoff.md` consistency — removes stale handoff if no active tracks.
2. Cleans orphaned temp files in `.data/tmp/` older than 24 hours.
3. Logs session duration (reads `.session-{id}.start` timestamp).
4. Ensures `.data/logs/` directory structure exists.

---

### ConfigChange

| | |
|---|---|
| **Script** | `on-config-change.py` |
| **Matcher** | `project_settings\|skills` |
| **Timeout** | 3s |
| **Can block** | Yes — via `decision: "block"` (not used currently) |
| **Output** | `systemMessage` on danger |

**Input fields**: `source` (`"user_settings"`, `"project_settings"`, `"local_settings"`, `"policy_settings"`, `"skills"`), `file_path`

**Behavior**: Validates hook configuration files for dangerous patterns:

| Pattern | Description |
|---------|-------------|
| `rm -rf` | Recursive delete |
| `curl.*\|.*sh` | Pipe remote script to shell |
| `eval $` | Dynamic evaluation |
| `; rm ` | Command chaining with delete |
| `> /etc/` | System file overwrite |
| `mv /usr/` | System file move |

---

### CwdChanged

| | |
|---|---|
| **Script** | `on-cwd-change.py` |
| **Matcher** | none |
| **Timeout** | 2s |
| **Can block** | No |
| **Output** | `systemMessage` |

**Input fields**: `old_cwd`, `new_cwd`

**Behavior**: When the working directory changes to a project with a `conductor/` directory and active tracks, shows a system message with the track count.

---

### PreCompact

| | |
|---|---|
| **Script** | `on-compact.py` |
| **Matcher** | `auto` |
| **Timeout** | 3s |
| **Can block** | Yes — via `decision: "block"` (not used currently) |
| **Output** | `hookSpecificOutput.additionalContext` |

**Behavior**: Injects compression priority instructions to preserve dispatch loop state during auto-compaction:

```
COMPRESSION PRIORITY:
[KEEP] Sections 3.0-3.7 (active dispatch loop) + last track-state output + last subagent result
[COMPRESS] All completed task results to: task_name=sha,status (one line each)
[DISCARD] Sections 1.0-2.0 (one-time setup, re-read from disk if needed)
[DISCARD] All intermediate CLI outputs (lock, sync-plan, phase-done details)
[DISCARD] Section 4.0 post-loop (re-read from workflow file when needed)
```

---

## Shared Library API

### lib/hook_io.py

Core I/O handling following the Claude Code hook protocol.

#### Reading input

```python
from lib.hook_io import read_hook_input, get_hook_field, get_hook_event_name, get_session_id, get_cwd, get_tool_name, get_agent_id, get_agent_type

data = read_hook_input()              # Read and cache JSON from stdin
event = get_hook_event_name()         # Get hook_event_name field
session = get_session_id()            # Get session_id field
cwd = get_cwd()                       # Get cwd field
tool = get_tool_name()                # Get tool_name field
agent_id = get_agent_id()             # Get agent_id (subagent hooks)
agent_type = get_agent_type()         # Get agent_type (subagent hooks)
value = get_hook_field("source", "")  # Get arbitrary field
```

#### Writing output

```python
from lib.hook_io import write_hook_output, write_simple_output, write_decision_block, write_decision_allow

# Full control — only emits hookSpecificOutput when event-specific fields are present
write_hook_output(
    additional_context="...",         # Context for Claude
    decision="block",                 # Top-level decision
    reason="...",                     # Reason for decision
    system_message="...",             # Warning to user
    suppress_output=False,            # Omit from debug log
    hook_event_name="PreToolUse",     # Override event name
    permission_decision="ask",        # PreToolUse only
    permission_decision_reason="...", # PreToolUse only
    updated_input={"command": "..."}, # PreToolUse only
    updated_tool_output="...",        # PostToolUse only
)

# Shortcut: only additionalContext
write_simple_output(additional_context="...")

# Block execution
write_decision_block("reason here")

# Allow execution
write_decision_allow()
```

**Important**: `write_hook_output()` only emits `hookSpecificOutput` when event-specific fields are provided (additional_context, permission_decision, etc.). For observability-only events (InstructionsLoaded, SessionEnd, CwdChanged), calling `write_hook_output()` with no arguments outputs `{}`, which is correct.

### lib/logging.py

Structured logging to `.data/logs/`.

```python
from lib.logging import init_logging, log_entry

log_file = init_logging("my-hook")              # Returns Path to .data/logs/my-hook.log
log_entry(log_file, "session=abc event=done")    # Append timestamped entry
```

### lib/env.py

Environment and path helpers.

```python
from lib.env import get_plugin_root, get_data_dir, get_logs_dir, get_conductor_dir, get_handoff_dir, get_session_id, get_permission_mode, is_remote_env, is_compact_mode, get_track_state_json, get_plan_md_path

plugin_root = get_plugin_root()         # CLAUDE_PLUGIN_ROOT or fallback
data_dir = get_data_dir()               # CLAUDE_PLUGIN_DATA or .data/
logs_dir = get_logs_dir()               # .data/logs/
```

### lib/validation.py

Command and state validation utilities.

```python
from lib.validation import (
    is_dangerous_git_operation,    # Check for git reset --hard, rebase, etc.
    contains_dangerous_pattern,    # Check for rm -rf, curl|sh, eval $, etc.
    validate_json_structure,       # Check required/optional fields
    validate_track_state,          # Validate track-state.json structure
    validate_plan_markers,         # Check plan.md has CURRENT-TASK markers
    check_state_file_age,          # Detect stale locks (>24h)
    validate_no_duplicate_tasks,   # Check for duplicate task IDs
    validate_git_commit_sha,       # Validate SHA format (7-40 hex chars)
    sanitize_filename,             # Remove dangerous characters
    validate_path_safe,            # Check for directory traversal
)
```

### lib/json_utils.py

Safe JSON operations.

```python
from lib.json_utils import load_json_safe, save_json_safe, merge_json_fields, pretty_format, compact_format

data = load_json_safe(path)              # Returns None on error instead of raising
save_json_safe(path, data, indent=2)     # Atomic write with error handling
```

### lib/git_utils.py

Git command wrappers.

```python
from lib.git_utils import get_current_sha, write_git_note, get_git_status, get_recent_commits
```

### lib/path_utils.py

Filesystem path utilities.

```python
from lib.path_utils import find_track_root, get_file_age_hours, safe_resolve_path, cleanup_old_files
```

---

## Testing

### Test suite

```bash
python3 scripts/test-all.py
```

This runs basic smoke tests for every hook script with appropriate mock input.

### Manual testing

Feed JSON input via stdin:

```bash
# Test PreToolUse with a dangerous command
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git reset --hard HEAD~1"}}' | \
  python3 scripts/pre-command-check.py

# Test SubagentStart
echo '{"hook_event_name":"SubagentStart","agent_type":"task-executor"}' | \
  python3 scripts/on-subagent-start.py

# Test PostToolBatch
echo '{"hook_event_name":"PostToolBatch","session_id":"test","cwd":"/tmp","tool_calls":[{"tool_name":"Bash","tool_input":{"command":"git commit -m test"}}]}' | \
  python3 scripts/on-batch-complete.py

# Test Stop (state consistency)
echo '{"hook_event_name":"Stop","session_id":"test","cwd":"/tmp"}' | \
  python3 scripts/state-consistency-check.py
```

### Expected output verification

For observability-only events, the output should be `{}`:

```bash
echo '{"hook_event_name":"InstructionsLoaded","session_id":"test"}' | \
  python3 scripts/enhance-conductor-context.py
# Expected: {}
```

For events with context injection, check that `hookSpecificOutput` is present:

```bash
echo '{"hook_event_name":"SubagentStart","agent_type":"task-executor"}' | \
  python3 scripts/on-subagent-start.py
# Expected: {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "[Conductor] You are a task-executor..."}}
```

---

## Debugging

### Enable debug logging

```bash
claude --debug-file /tmp/conductor-debug.txt
```

Or read the session debug log:

```bash
cat ~/.claude/debug/<session-id>.txt
```

### Check hook output validation

The runtime validates JSON output against the event's schema. If validation fails, the debug log shows:

```
[ERROR] InstructionsLoaded:session_start [...] failed to run: Hook JSON output validation failed
```

Common causes:
- Emitting `hookSpecificOutput` for an event that doesn't support it (e.g. `InstructionsLoaded`)
- Missing required fields in `hookSpecificOutput` (e.g. `additionalContext` for `UserPromptSubmit`)

### Hook logs

All hooks write to `.data/logs/`:

| Log file | Produced by |
|----------|------------|
| `session-lifecycle.log` | session-start.py, session-end.py |
| `subagent-failures.log` | on-subagent-stop.py |
| `on-batch-complete.log` | on-batch-complete.py |
| `on-task-event.log` | on-task-event.py |
| `on-config-change.log` | on-config-change.py |
| `on-cwd-change.log` | on-cwd-change.py |
| `on-test-run.log` | on-test-run.py |
| `session-metrics.log` | session-end.py |
| `cleanup.log` | session-end.py |

---

## Performance Profile

| Hook | Timeout | Async | Critical path | Notes |
|------|---------|-------|---------------|-------|
| `session-start.py` | 10s | No | Yes | Reads `core-contract.md` from disk |
| `pre-command-check.py` | 3s | No | Yes | Regex matching on command string |
| `filter-subagent-output.py` | 5s | No | Yes | Regex on subagent output |
| `on-subagent-result.py` | 5s | No | Yes | Regex on subagent output |
| `on-test-run.py` | 5s | No | No | Only fires on test commands |
| `on-batch-complete.py` | 35s | No | Yes | May run coverage tool (up to 30s) |
| `on-subagent-start.py` | 5s | No | No | Short string lookup and output |
| `on-subagent-stop.py` | 10s/5s | Partial | Yes | Sync for critical agents, async for others |
| `on-phase-checkpoint-stop.py` | 5s | No | No | Logging only |
| `on-review-stop.py` | 5s | Yes | No | Async logging |
| `on-task-event.py` | 3s | Yes | No | Async logging |
| `state-consistency-check.py` | 5s | No | No | File reads, no heavy computation |
| `session-end.py` | 5s | No | No | Cleanup and logging |
| `on-config-change.py` | 3s | No | No | Pattern matching on config file |
| `on-cwd-change.py` | 2s | No | No | Directory existence check |
| `on-compact.py` | 3s | No | No | Static string output |
| `enhance-conductor-context.py` | 3s | No | No | Logging only |

---

**Last Updated**: 2026-05-12
