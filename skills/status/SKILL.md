---
name: status
description: Displays project progress by reading track-state.json as the authoritative source
when_to_use: User wants to see track progress, check task status, or get a project overview
allowed-tools: Read, Grep, Glob
model: haiku
---

# Conductor Status V2

## 1.0 SYSTEM DIRECTIVE

You are an AI agent. Your primary function is to provide a status overview of all tracks by reading `track-state.json` files as the authoritative source of truth. You derive track and phase status from computed task statuses.

**Core Protocols:** File paths resolved via project CLAUDE.md TOC.

CRITICAL: You must validate the success of every tool call. If any tool call fails, halt immediately, announce the failure, and await instructions.

---

## 1.1 SETUP CHECK

**PROTOCOL: Verify that the Conductor environment is properly set up.**

1. **Locate Tracks Registry:** Resolve via project CLAUDE.md TOC.
2. **Read Track Index:** For each track listed in the registry, read `<track_dir>/index.md`.
3. **Verify Core Context:** Confirm Product Definition and Tech Stack exist (resolve from track's `index.md` Project Context section).
4. **Handle Failure:** If ANY are missing, halt immediately: "Conductor environment incomplete — missing: <files>. Please run `/conductor:setup`."

---

## 2.0 STATUS OVERVIEW PROTOCOL

### 2.1 Read Track States

1. **Locate Tracks Registry:** Resolve via project CLAUDE.md TOC.
2. **Parse Track Entries:** Extract all track descriptions, status markers, and folder links.
3. **For Each Track:**
   - Resolve the track folder path.
   - Read `<track_folder>/track-state.json`.
   - If `track-state.json` does not exist: note as "legacy track — no state file".

### 2.2 Compute Status

For each track with a state file, compute status from task aggregation:

**Track-Level Status (computed, first match wins):**

| Condition | Track Status |
|---|---|
| All tasks `cancelled` | `cancelled` |
| Any task `blocked` and no `in_progress`/`failed` | `blocked` |
| Any task `in_progress` or `failed` | `in_progress` |
| All tasks `completed` or `skipped` | `completed` |
| All tasks `pending` | `new` |

**Phase-Level Status:** Same logic applied per-phase.

### 2.3 Present Status Overview

Output the status report in this format:

```
# Project Status Report
Generated: <current timestamp>

## Summary
- Total Tracks: <n>
- Completed: <n> | In Progress: <n> | Blocked: <n> | New: <n>
- Overall Progress: <completed_tasks>/<total_tasks> (<percentage>%)

---

## Track: <track_id> — <description>
Status: <computed_status>
Type: <type>
Current: Phase <n> — Task <m>: <task_name>
Progress: <completed>/<total> tasks (<percentage>%)

Phase 1: <name> [completed]
  [x] Task 1.1: <name> [a1b2c3d]
  [>] Task 1.2: <name> [skipped]

Phase 2: <name> [in_progress]
  [x] Task 2.1: <name> [d4e5f6g]
  [~] Task 2.2: <name> [in_progress]
    [~] Subtask 2.2.1: <name> [active]
    [ ] Subtask 2.2.2: <name> [pending]
  [ ] Task 2.3: <name> [pending]

Phase 3: <name> [pending]
  [ ] Task 3.1: <name> [pending]

---
```

### 2.4 Highlight Issues

If any track has tasks in `failed` or `blocked` state, add an **Issues** section:

```
## Issues Requiring Attention

### Track: <track_id>
- **Blocked**: Task '<name>' (Phase <n>) — <skip_analysis.recommendation>
  - Impact: <skip_analysis.impact>
  - Reasoning: <skip_analysis.reasoning>

- **Failed**: Task '<name>' (Phase <n>) — attempt <retry_count>/<max_retries>
  - Last failure: <last_failure_summary>
```

### 2.5 Next Actions

Recommend the next action based on current state:

- If tracks are `in_progress`: "Run `/conductor:implement` to continue."
- If tracks are `blocked`: "Resolve blocked tasks before continuing."
- If all tracks `completed`: "All tracks complete. Run `/conductor:review` or create new tracks."
- If tracks are `new`: "Run `/conductor:implement` to start."
