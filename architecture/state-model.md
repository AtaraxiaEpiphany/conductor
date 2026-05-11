# State Model

> State machine and state management in Conductor

---

## Overview

Conductor uses a hierarchical state model with three levels:
1. **Track Status** - High-level track lifecycle
2. **Task State** - Individual task lifecycle
3. **Execution State** - Runtime execution tracking

---

## Track Status Lifecycle

```
┌──────────┐
│   new    │
└────┬─────┘
     │
     ▼
┌─────────────┐
│ in_progress │◄──────────┐
└─────┬───────┘           │
      │                  human reset
      ▼                   │
┌───────────┐              │
│ completed │              │
└─────┬─────┘              │
      │                    │
      ▼                    │
┌──────────┐              │
│ archived │              │
└──────────┘              │
                         │
      ┌────────────────────┴──────────────────┐
      ▼                                    ▼
┌──────────┐                         ┌──────────┐
│ blocked  │                         │ cancelled │
└──────────┘                         └──────────┘
```

### Status Definitions

| Status | Description | Transition From |
|--------|-------------|----------------|
| `new` | Track created, not yet started | - |
| `in_progress` | Track actively being implemented | `new`, `blocked` (human reset) |
| `completed` | All tasks finished, review done | `in_progress` |
| `archived` | Track archived for reference | `completed` |
| `blocked` | Track has unresolvable blockers | `in_progress` |
| `cancelled` | Track cancelled | `in_progress`, `blocked` |

---

## Task State Machine

```
┌─────────────┐
│   pending   │◄─────────────────┐
└──────┬──────┘                  │
       │ dispatch                  │
       ▼                          │ human reset
┌─────────────┐                  │
│ in_progress │◄─────────────────┘
└──────┬──────┘
       │ success
       ▼
┌─────────────┐     ┌─────────────┐
│  completed  │────▶│  archived  │
└─────────────┘     └─────────────┘

       │ failure/retry
       ▼
┌─────────────┐     ┌─────────────┐
│    failed   │────▶│   skipped   │
└──────┬──────┘     └─────────────┘
       │                         │
       │ skip analysis          │
       ▼                         │
┌─────────────┐                  │
│   blocked   │◄─────────────────┘
└──────┬──────┘
       │                        │
       │ defer                  │
       ▼                        │
┌─────────────┐                  │
│   deferred  │◄─────────────────┘
└──────┬──────┘          auto
       │                  verify
       ▼
┌─────────────┐
│  completed  │
└─────────────┘
```

### Task Markers

| Marker | State | Line Format | SHA Required |
|--------|--------|-------------|-------------|
| `[ ]` | pending | `- [ ] Task description` | No |
| `[~]` | in_progress | `- [~] Task description` | No |
| `[x]` | completed | `- [x] Task description [sha]` | Yes |
| `[!]` | failed | `- [!] Task description [sha]` | Yes |
| `[>]` | skipped | `- [>] Task description [sha]` | Yes |
| `[d]` | deferred | `- [d] Task description [sha]` | Yes |
| `[#]` | blocked | `- [#] Task description [sha]` | Yes |
| `[-]` | cancelled | `- [-] Task description [sha]` | Yes |

**SHA Position**: Always at the END of the line, after any HTML comments.

---

## track-state.json Schema

```json
{
  "track_id": "user-login",
  "status": "in_progress",
  "execution_mode": "interactive",
  "created_at": "2026-05-09T12:00:00Z",
  "updated_at": "2026-05-09T14:30:00Z",
  "phases": [
    {
      "phase_index": 0,
      "name": "Phase 1: Authentication",
      "status": "in_progress",
      "checkpoint_sha": null,
      "tasks": [
        {
          "task_index": 0,
          "phase_index": 0,
          "name": "Setup auth middleware",
          "status": "completed",
          "sha": "a1b2c3d",
          "attempt": 1,
          "type": "default",
          "tags": [],
          "subtasks": []
        },
        {
          "task_index": 1,
          "phase_index": 0,
          "name": "Implement login flow",
          "status": "in_progress",
          "sha": null,
          "attempt": 1,
          "type": "default",
          "tags": [],
          "subtasks": [
            {
              "subtask_index": 0,
              "parent_task": 1,
              "name": "Create login form UI",
              "status": "completed",
              "sha": "d4e5f6g",
              "attempt": 1,
              "type": "default",
              "tags": []
            },
            {
              "subtask_index": 1,
              "parent_task": 1,
              "name": "Connect form to API",
              "status": "in_progress",
              "sha": null,
              "attempt": 1,
              "type": "default",
              "tags": []
            }
          ]
        }
      ]
    }
  ],
  "current_indices": {
    "phase": 0,
    "task": 1,
    "subtask": 1
  },
  "quality_score": null,
  "checkpoint_pending": false
}
```

---

## State Authority Model

```
┌─────────────────────────────────────────┐
│     Authoritative Source               │
│    track-state.json                   │
└────────────┬────────────────────────┘
             │
    ┌────────┼─────────┐
    ▼        ▼         ▼
┌────────┐ ┌──────┐ ┌─────────┐
│ plan.md │ │check-│ │tracks.md│
│(projection)│list│ │registry │
└────────┘ └──────┘ └─────────┘
```

### Principles

1. **track-state.json is always source of truth**
2. **Orchestrator never reads/writes state JSON directly**
3. **All mutations go through track-state CLI**
4. **plan.md is a human-readable projection, synced automatically**
5. **Git notes provide immutable audit trail**

---

## State Transitions

### Lock Mechanism

The global state lock ensures only one task can be in_progress:

```python
# In pre-command-check.py
def has_multiple_in_progress():
    count = sum(1 for task in all_tasks if task.status == "in_progress")
    return count > 1

if has_multiple_in_progress():
    block("F1_VIOLATION", "Multiple in_progress tasks detected")
```

### Recovery State

When interrupted, track-state stores recovery information:

```json
{
  "recovery": {
    "phase_checkpoint_pending": false,
    "last_action": "dispatch_executor",
    "handoff_path": ".conductor/handoff/p0-t1.md"
  }
}
```

---

## Consistency Validation

### Validation Checks

| Check | Description | Severity |
|--------|-------------|-----------|
| Orphaned in_progress | Task in_progress with no parent in_progress | Critical |
| Invalid transition | Moving from completed to pending | Error |
| Missing SHA | Terminal marker without commit SHA | Critical |
| Parent-child sync | Parent completed with child in_progress | Warning |

### Validation Command

```bash
track-state validate <track-dir> [--fix]
```

**Auto-fixes**:
- Parent→subtask status propagation
- Phase status synchronization
- Orphaned lock cleanup

---

## State Persistence

### Session Handoff

At session end, active state is written to `session-handoff.md`:

```markdown
# Session Handoff

## Active Tracks

### user-login
- Status: in_progress
- Current Phase: 0
- Current Task: 1
- Last Action: dispatch_executor
```

### Recovery Flow

```
Session Start
    ↓
SessionStart Hook loads session-handoff.md
    ↓
track-state recover detects stale locks
    ↓
User prompted to recover or clean
    ↓
Session resumed from handoff
```

---

## Next Steps

- [track-state CLI](../reference/track-state-cli.md) - State mutation commands
- [Quality Gates](../reference/quality-gates.md) - F1-F6 rules
- [Interaction Mechanism](INTERACTION_MECHANISM.md) - State authority model

---

**Last Updated**: 2026-05-11
