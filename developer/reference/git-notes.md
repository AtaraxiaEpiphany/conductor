---
title: Git Notes Audit System
audience: reference
status: stable
last_updated: 2026-05-11
related:
  - track-state-cli.md
  - ../runtime/core-contract.md
---

# Git Notes Audit System

> Complete audit trail for all task-executor commits

---

## Overview

Every task-executor commit gets a human-readable git note for comprehensive auditability. Notes are written by `track-state process-result` — zero agent context cost.

---

## Note Structure

```json
{
  "conductor": {
    "version": "1.0",
    "timestamp": "2026-05-09T12:34:56Z",
    "session_id": "abc123",
    "track_id": "user-login",
    "track_dir": "conductor/tracks/user-login"
  },
  "task": {
    "phase": 0,
    "task": 1,
    "subtask": null,
    "name": "Implement login form",
    "attempt": 1,
    "tags": []
  },
  "requirements": {
    "tc_implemented": ["TC-1.1", "TC-1.2", "TC-2.1"],
    "spec_deviation": "NONE"
  },
  "implementation": {
    "commit_sha": "a1b2c3d",
    "summary": "Implemented user login",
    "diff_stats": "3 files changed, 127 insertions(+), 5 deletions(-)",
    "files_added": ["test/login.test.ts", "src/login.ts"],
    "files_modified": ["src/index.ts"],
    "files_deleted": [],
    "lines_added": 127,
    "lines_deleted": 5
  }
}
```

---

## How It Works

### 1. Task Execution

task-executor completes task, writes `result.json`, commits code.

### 2. Result Processing

Orchestrator calls `track-state process-result`:
- Reads `result.json` (coverage, spec deviations, TC coverage)
- Updates track-state.json state
- Syncs plan.md markers
- Updates handoff.md index
- Writes human-readable git note to commit

### 3. Recovery

`track-state recover` performs best-effort git notes recovery on interruption.

---

## Query Tool

### git-notes-query

```bash
git-notes-query --sha <commit-hash>
```

View audit data for a specific commit.

**Output**:
```json
{
  "conductor": {
    "version": "1.0",
    "timestamp": "2026-05-09T12:34:56Z",
    ...
  },
  "task": {
    "phase": 0,
    "task": 1,
    ...
  }
}
```

---

### Query by Track

```bash
git-notes-query --track <track-id>
```

View all commits for a track.

**Output**:
```json
{
  "track_id": "user-login",
  "commits": [
    {
      "sha": "a1b2c3d",
      "task": "Implement login form",
      "timestamp": "2026-05-09T12:34:56Z"
    },
    ...
  ],
  "count": 15
}
```

---

### Query by Session

```bash
git-notes-query --session <session-id>
```

Show all activity in a session.

**Output**:
```json
{
  "session_id": "abc123",
  "commits": [
    {
      "sha": "a1b2c3d",
      "task": "Implement login form",
      "timestamp": "2026-05-09T12:34:56Z"
    },
    ...
  ]
}
```

---

### Coverage Trend

```bash
git-notes-query --coverage-trend
```

Show test coverage trend over time.

**Output**:
```json
{
  "trend": [
    { "sha": "a1b2c3d", "coverage": 85, "timestamp": "..." },
    { "sha": "d4e5f6g", "coverage": 82, "timestamp": "..." },
    { "sha": "g7h8i9j", "coverage": 88, "timestamp": "..." }
  ],
  "average": 85,
  "min": 82,
  "max": 88
}
```

---

### Changed Files

```bash
git-notes-query --files
```

Show all changed files across commits.

**Output**:
```json
{
  "files": {
    "src/auth.ts": { "commits": ["a1b2c3d", "g7h8i9j"], "count": 2 },
    "test/auth.test.ts": { "commits": ["a1b2c3d"], "count": 1 },
    ...
  },
  "total_files": 45,
  "total_changes": 67
}
```

---

### Specification Deviations

```bash
git-notes-query --deviations
```

Show all specification deviations.

**Output**:
```json
{
  "deviations": [
    {
      "sha": "a1b2c3d",
      "task": "Implement login form",
      "deviation": "Added email confirmation step",
      "timestamp": "2026-05-09T12:34:56Z"
    },
    ...
  ],
  "count": 3
}
```

---

## Viewing Notes

### Using git log

```bash
git log --show-notes
```

Shows commits with notes inline.

```bash
git log --show-notes=refs/notes/conductor
```

Shows only conductor notes.

### Using git show

```bash
git show <commit-sha> --show-notes
```

Shows specific commit with notes.

---

## Benefits

- **Zero subagent overhead**: Notes written by CLI, not by agents
- **Queryable**: Human-readable notes viewable via git commands
- **Complete traceability**: Links commits to requirements, tests, and state
- **Session tracking**: All work in a session can be reconstructed
- **Quality metrics**: Coverage, deviations, TC coverage tracked per commit

---

## Note Storage

Notes are stored in git's notes ref: `refs/notes/conductor`

### Default notes ref

```bash
# Configure conductor notes as default
git config notes.displayRef "refs/notes/conductor"

# Now git log shows notes by default
git log
```

### Multiple notes refs

```bash
# View all notes refs
git notes --ref=refs/notes/conductor list
git notes --ref=refs/notes/review list
```

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| No notes showing | Notes ref not configured | Run `git config notes.displayRef "refs/notes/conductor"` |
| Notes lost | Git notes not pushed | Run `git push origin refs/notes/conductor` |
| Query tool not found | Script not in PATH | Add scripts/ to PATH or use full path |

---

## Next Steps

- [Quality Gates](quality-gates.md) - Gate enforcement in notes
- [track-state CLI](track-state-cli.md) - Note writing process
- [Hook Reference](hooks.md) - PostToolUse hook integration

---

**Last Updated**: 2026-05-11
