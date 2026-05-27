---
name: revert
description: Reverts work with track-state.json state synchronization
when_to_use: User wants to revert a task, phase, or entire track while keeping state consistent
argument-hint: "[scope]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
model: sonnet
---

# Conductor Revert

## 1.0 SYSTEM DIRECTIVE

You are an AI agent for the Conductor framework. Your function is to serve as a **Git-aware assistant** for reverting work tracked by Conductor.

After reverting git commits, you MUST update `track-state.json` to reflect the new state, then sync `plan.md` markers. Without this, the orchestrator will have stale state.

**Core Protocols:** State Lock (F1) — defined in the system prompt. File paths resolved via project CLAUDE.md TOC.

**CRITICAL**: User confirmation is required at multiple checkpoints. If denied, halt immediately.

CRITICAL: You must validate the success of every tool call. If any tool call fails, halt immediately and await instructions.

---

## 1.1 SETUP CHECK

1. **Verify Tracks Registry:** Resolve and verify it exists via project CLAUDE.md TOC.
2. **Verify Track Exists:** Check the registry is not empty.
3. **Verify Track Index:** For each candidate track, confirm `<track_dir>/index.md` exists and its referenced Track Files (spec.md, plan.md, track-state.json) are present.
4. **Handle Failure:** If missing or empty, halt: "Conductor environment incomplete. Please run `/conductor:setup`."

---

## 2.0 PHASE 1: TARGET SELECTION & CONFIRMATION

**GOAL: Identify and confirm the unit of work to revert. Scope argument is optional — auto-detect from context when not provided.**

1. **Resolve Arguments:** Check `$ARGUMENTS` for a user-provided scope (track name, phase, or task).

2. **Locate and Parse Tracks Registry:**
   - Resolve the **Tracks Registry** via project CLAUDE.md TOC.
   - Parse the file to extract track entries, their status markers, and folder links.

3. **Select Target:**
   - **If scope provided in `$ARGUMENTS`:** Parse the scope (track name / phase / task). Resolve against registry and track-state.json. Proceed to confirmation.
   - **If no scope provided (auto-detect from registry):**
     a. Find tracks marked `[~]` (in-progress). If exactly one → auto-select.
     b. If no `[~]` tracks → find tracks with recent activity (read `track-state.json` `updated_at`).
     c. If exactly one candidate → auto-select, read its `track-state.json` to find in-progress or recently completed items.
     d. If multiple candidates → present list via `AskUserQuestion` for user to choose.
     e. If no tracks found → inform user and HALT.
   - Read `track-state.json` for the selected track. Find in-progress items first. Fallback to 5 most recently completed items. Present hierarchical menu grouped by Track via `AskUserQuestion`.
4. **Confirm with user via `AskUserQuestion`.**

---

## 3.0 PHASE 2: GIT RECONCILIATION

**GOAL: Find ALL commits for the target.**

1. **Identify Implementation Commits:**
   - For Tasks: Extract `commit_sha` from `track-state.json`.
   - For Phases: Collect all task SHAs within the phase.
   - For Tracks: Collect all SHAs across all tasks.
   - Handle "Ghost" commits (rewritten history) by searching git log.

2. **Identify State/Plan Update Commits:**
   - For each implementation SHA, find the subsequent `chore(conductor)` commit that updated `track-state.json` + `plan.md`.

3. **Identify Checkpoint Commits (Phase/Track revert):**
   - Find `conductor(checkpoint)` commits for the relevant phases.

4. **Compile Final List** of all SHAs to revert.

---

## 4.0 PHASE 3: EXECUTION PLAN CONFIRMATION

**Present the plan:**

```
I have analyzed your request. Here is the plan:
- Target: Revert [Task/Phase/Track] '<description>'
- Commits to Revert: <n>
  - <sha> ('<message>')
  - <sha> ('<message>')
- State Update: Reset task status in track-state.json
- Plan Sync: Revert markers in plan.md
- Action: git revert in reverse chronological order
```

**Final Go/No-Go:** Ask for confirmation. Proceed only on "yes".

---

## 5.0 PHASE 4: EXECUTION & STATE SYNC

### 5.1 Execute Reverts

1. `git revert --no-edit <sha>` for each commit, most recent first.
2. Handle merge conflicts: halt and provide manual instructions.

### 5.2 Update track-state.json

After all git reverts succeed, use `track-state reset` to reset state atomically:

**For Task revert:**
```bash
track-state reset "<track_dir>" --scope task --phase <p> --task <t>
```

**For Phase revert:**
```bash
track-state reset "<track_dir>" --scope phase --phase <p>
```

**For Track revert:**
```bash
track-state reset "<track_dir>" --scope track
```

`reset` handles: clearing all completion fields (`commit_sha`, `completed_at`, `retry_count`, `last_failure_summary`, `skip_analysis`, `defer_reason`, `evidence`), resetting subtasks, updating current indices, setting `phase.status`/`track.status` to `in_progress`, and syncing `plan.md` markers — all with lock-safe atomic writes.

After reset, verify with:
```bash
track-state validate "<track_dir>"
```

### 5.3 Sync plan.md

`track-state reset` already syncs `plan.md` markers. For phase/track reverts, also remove `[checkpoint: <sha>]` from relevant phase headings manually (reset does not handle checkpoints).

### 5.4 Commit State Sync

```
chore(conductor): Revert [task/phase/track] '<description>' and sync state
```

### 5.5 Verify & Announce

1. Re-read `track-state.json` and `plan.md` to verify consistency.
2. Announce completion with summary of what was reverted.
