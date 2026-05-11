# track-state CLI Reference

> Complete reference for the track-state state management CLI

---

## Overview

All `track-state.json` mutations are handled by the `track-state` Python CLI. The orchestrator calls it via bash — never reads/edits JSON directly.

```
track-state <command> <track-dir> [options]
```

---

## Commands

### next

Find the next dispatchable task.

```bash
track-state next <track-dir>
```

**Output**:
```json
{
  "phase": 0,
  "task": 1,
  "subtask": null,
  "name": "Implement login form",
  "type": "default",
  "tags": [],
  "phase_checkpoint_pending": false
}
```

**Returns** `phase_checkpoint_pending: true` if a phase-checker was interrupted.

---

### recover

Get recovery context for current task.

```bash
track-state recover <track-dir>
```

**Output**:
```json
{
  "status": "in_progress",
  "phase": 0,
  "task": 1,
  "subtask": null,
  "name": "Implement login form",
  "type": "default",
  "retry_count": 0,
  "phase_checkpoint_pending": false
}
```

---

### lock

Set task to in_progress and update indices.

```bash
track-state lock <track-dir> <phase> <task> [subtask]
```

**Output**: `{ "ok": true }`

---

### complete

Set task to completed and check parent completion.

```bash
track-state complete <track-dir> <phase> <task> [subtask] --sha <sha>
```

**Output**:
```json
{
  "ok": true,
  "parent_completed": false
}
```

---

### fail

Set task to failed and increment retry count.

```bash
track-state fail <track-dir> <phase> <task> [subtask] --summary <text>
```

**Output**:
```json
{
  "retry_count": 1
}
```

---

### skip

Set task to skipped.

```bash
track-state skip <track-dir> <phase> <task> [subtask] --reason <text>
```

**Output**: `{ "ok": true }`

---

### block

Set task to blocked.

```bash
track-state block <track-dir> <phase> <task> [subtask] --reason <text>
```

**Output**: `{ "ok": true }`

---

### defer

Set task to deferred.

```bash
track-state defer <track-dir> <phase> <task> [subtask] --reason <text>
```

**Output**:
```json
{
  "ok": true,
  "parent_deferred": false
}
```

---

### sync-plan

Re-project all markers to plan.md from state.

```bash
track-state sync-plan <track-dir>
```

**Output**: `{ "synced": true }`

---

### registry-update

Update track entry in tracks.md based on track-state.json status.

```bash
track-state registry-update <track-dir> <tracks-md-path>
```

**Output**:
```json
{
  "updated": true,
  "marker": "[~]",
  "status": "in_progress"
}
```

Supports multiple formats in tracks.md:
- Section-based: `- **Status:** in_progress`
- Checkbox: `- [~] description`
- Table row

---

### start

Transition track from `new` to `in_progress`.

```bash
track-state start <track-dir>
```

**Output**:
```json
{
  "ok": true,
  "status": "in_progress"
}
```

---

### validate

Validate track-state.json structural and semantic integrity.

```bash
track-state validate <track-dir> [--fix]
```

**Output**:
```json
{
  "valid": false,
  "errors": [
    "Task 1.1 has parent 1 which is pending"
  ],
  "warnings": [],
  "fixes": [
    "Propagated status: 1 → in_progress"
  ]
}
```

**`--fix`** auto-repairs:
- Parent→subtask status propagation
- Phase status sync
- Orphaned lock detection

---

### phase-done

Check if all tasks in phase are terminal.

```bash
track-state phase-done <track-dir> <phase>
```

**Output**:
```json
{
  "complete": true,
  "terminal": 5,
  "total": 5
}
```

---

### add-checkpoint

Add or update checkpoint SHA for a phase in plan.md.

```bash
track-state add-checkpoint <track-dir> <phase> --sha <sha>
```

**Output**:
```json
{
  "ok": true,
  "phase": 0,
  "sha": "a1b2c3d"
}
```

---

### finalize

Set indices to -1, compute track-level status, verify checklist, compute quality score.

```bash
track-state finalize <track-dir>
```

**Output**:
```json
{
  "status": "completed",
  "quality_score": 92,
  "checklist": {
    "total": 20,
    "verified": 18,
    "unverified": 2
  }
}
```

**Quality Score Weights**:
- Completion: 40%
- Checklist verification: 30%
- Coverage: 20%
- Retry penalty: 10%

---

### process-result

Read `.conductor/result.json`, update state + plan + handoff + checklist + git notes.

```bash
track-state process-result <track-dir>
```

**Output**:
```json
{
  "status": "completed",
  "sha": "a1b2c3d",
  "parent_completed": false,
  "deviations": "NONE",
  "coverage_gate": false,
  "tdd_gate": true
}
```

Enforces F2/F3 gates and writes git notes.

---

### init

Create track-state.json + index.md + handoff.md + feature-checklist.json from plan structure.

```bash
track-state init <track-dir> \
  --plan-structure <json> \
  --track-id <id> \
  --type <type> \
  --description <desc> \
  [--execution-mode <mode>]
```

**Output**:
```json
{
  "ok": true,
  "track_id": "user-login",
  "phases": 3,
  "tasks": 15
}
```

---

### shas

List all commit SHAs for a track.

```bash
track-state shas <track-dir>
```

**Output**:
```json
{
  "shas": ["a1b2c3d", "d4e5f6g", "g7h8i9j"],
  "first": "a1b2c3d",
  "last": "g7h8i9j",
  "count": 3
}
```

---

### deferred-report

List all deferred tasks for verification.

```bash
track-state deferred-report <track-dir>
```

**Output**:
```json
{
  "deferred": [
    {
      "phase": 2,
      "task": 3,
      "name": "Manual testing",
      "reason": "Manual verification"
    }
  ],
  "count": 1
}
```

---

### get-handoff

Get handoff content for a specific task/subtask.

```bash
track-state get-handoff <track-dir> <phase> <task> [--subtask <subtask>]
```

**Output**:
```json
{
  "content": "...",
  "path": ".conductor/handoff/p0-t1.md"
}
```

---

### sync-handoff

Sync handoff.md index with current state.

```bash
track-state sync-handoff <track-dir>
```

**Output**: `{ "ok": true, "updated": true }`

---

### append-handoff

Append content to a task's handoff file.

```bash
track-state append-handoff <track-dir> <phase> <task> \
  --type <explore|decision|risk|deviation> \
  --content <json> \
  [--subtask <subtask>]
```

**Output**:
```json
{
  "ok": true,
  "type": "decision",
  "handoff_file": ".conductor/handoff/p0-t1.md"
}
```

---

### checklist-verify

Check feature-checklist.json verification status.

```bash
track-state checklist-verify <track-dir>
```

**Output**:
```json
{
  "exists": true,
  "total": 20,
  "verified": 18,
  "unverified": 2
}
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error |
| 2 | Permission denied / Block |

---

## Next Steps

- [Quality Gates](quality-gates.md) - F2/F3 gate enforcement
- [Git Notes](git-notes.md) - Audit system
- [Hook Reference](hooks.md) - Hook integration

---

**Last Updated**: 2026-05-11
