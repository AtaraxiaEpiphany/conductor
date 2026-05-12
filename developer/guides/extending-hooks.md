---
title: Extending Hooks
audience: developer
status: stable
last_updated: 2026-05-11
related:
  - ../../docs/reference/hooks.md
---

# Hook Implementation Details

> Developer documentation for hook script implementations

---

## Overview

This document describes the implementation details of Conductor's hook scripts. For users, see [Hook Reference](../../docs/reference/hooks.md).

---

## Hook Script Structure

### Standard Header

All hook scripts follow this structure:

```python
#!/usr/bin/env python3
"""
Conductor Hook: <hook-name>

Purpose: Brief description of hook purpose

Input: JSON from Claude Runtime
Output: JSON with additionalContext or permissionDecision

Dependencies: lib/env.py, lib/hook_io.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from lib.hook_io import HookIO
from lib.logging import get_logger

logger = get_logger(__name__)

def main():
    hook = HookIO()
    # Hook logic here
    return hook

if __name__ == "__main__":
    main()
```

---

## Hook Library Modules

### lib/env.py

Environment variable handling:

```python
CLAUDE_PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT")
CLAUDE_PLUGIN_DATA = os.environ.get("CLAUDE_PLUGIN_DATA")
```

### lib/hook_io.py

Hook input/output handling:

```python
class HookIO:
    def __init__(self):
        self.input = json.load(sys.stdin)
        self.output = {
            "hookSpecificOutput": {
                "hookEventName": self.input.get("hook_event_name")
            }
        }

    def inject_context(self, message):
        """Inject additional context"""
        self.output["hookSpecificOutput"]["additionalContext"] = message

    def block(self, reason):
        """Block with exit code 2"""
        self.output["hookSpecificOutput"]["permissionDecision"] = "ask"
        self.output["hookSpecificOutput"]["permissionDecisionReason"] = reason
        print(json.dumps(self.output))
        sys.exit(2)

    def allow(self):
        """Allow with exit code 0"""
        print(json.dumps(self.output))
        sys.exit(0)
```

### lib/git_utils.py

Git operations:

```python
def get_current_sha():
    """Get current git commit SHA"""
    return subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()

def write_git_note(sha, note_data):
    """Write git note to commit"""
    note_json = json.dumps(note_data, indent=2)
    subprocess.run([
        "git", "notes", "--ref=refs/notes/conductor",
        "add", "-m", note_json, sha
    ])
```

---

## Critical Hooks

### session-start.py

**Purpose**: Load conductor-core.md and session handoff on startup

**Flow**:
1. Determine if startup or resume based on match_result
2. Load conductor-core.md (full on startup, compact on resume)
3. Load session-handoff.md if exists
4. Inject context into session

```python
def main():
    hook = HookIO()
    match_result = hook.input.get("match_result", "")

    if "startup" in match_result:
        core_content = load_file("runtime/core-contract.md")
    else:
        core_content = load_compact_core()

    handoff = load_handoff()
    context = f"[Conductor] Context loaded: {len(core_content)} chars"
    if handoff:
        context += f"\n[Conductor] Handoff: {handoff}"

    hook.inject_context(context)
    hook.allow()
```

---

### pre-command-check.py

**Purpose**: Block dangerous git operations and enforce state lock

**Flow**:
1. Parse command from tool_input
2. Check against blocked operations
3. Check for state lock violations
4. Block or allow accordingly

**Blocked Operations**:
- `git reset --hard`
- `git rebase`
- `git cherry-pick`

**State Lock Check**:
```python
def check_state_lock():
    track_states = find_all_track_states()
    in_progress_count = sum(1 for ts in track_states if ts.status == "in_progress")
    return in_progress_count > 1
```

---

### filter-subagent-output.py

**Purpose**: Filter subagent output to reduce context pressure

**Flow**:
1. Get tool output from input
2. Parse for `---TASK RESULT---` block
3. Extract only the result block
4. Return filtered output or compact summary

```python
def extract_result_block(output):
    pattern = r"---TASK RESULT---\n(.*?)\n---END RESULT---"
    match = re.search(pattern, output, re.DOTALL)
    return match.group(1) if match else None

def main():
    hook = HookIO()
    output = hook.input.get("tool_output", "")

    result = extract_result_block(output)
    if result:
        hook.inject_context(f"[Conductor] Task result: {result[:100]}...")
    else:
        hook.inject_context("[Conductor] No result block found")
```

---

### on-subagent-stop.py

**Purpose**: Lifecycle logging and critical agent failure detection

**Flow**:
1. Get agent name and last message
2. Check for failure patterns
3. For critical agents, use asyncRewake if failure detected
4. Log lifecycle event

**Critical Agents**:
- task-executor
- explorer
- phase-checker

**Failure Detection**:
```python
FAILURE_PATTERNS = [
    "failed to",
    "error:",
    "exception",
    "unable to"
]

def check_for_failure(message):
    return any(pattern in message.lower() for pattern in FAILURE_PATTERNS)
```

---

## Hook Configuration

### hooks.json Structure

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/session-start.py\"",
            "timeout": 10
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/pre-command-check.py\"",
            "timeout": 3
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "matcher": "task-executor|explorer|phase-checker",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/on-subagent-stop.py\"",
            "asyncRewake": true,
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

---

## Debugging Hooks

### Local Testing

```bash
# Simulate hook input
echo '{"hook_event_name": "PreToolUse", "tool_input": {"command": "git status"}}' | \
  python3 scripts/pre-command-check.py
```

### Debug Logging

```bash
# Enable debug logging
export CONDUCTOR_DEBUG=1

# Check hook logs
cat logs/hook-debug.log
```

---

## Performance Considerations

| Hook | Timeout | Async | Notes |
|-------|----------|--------|--------|
| session-start | 10s | No | Loads core.md |
| pre-command-check | 3s | No | Must block dangerous ops |
| filter-subagent-output | 5s | No | Regex matching |
| on-subagent-stop | 30s | Rewake | Critical agents only |

---

## Next Steps

- [Hook Reference](../reference/hooks.md) - Public hook documentation
- [track-state CLI](../reference/track-state-cli.md) - State management

---

**Last Updated**: 2026-05-11
