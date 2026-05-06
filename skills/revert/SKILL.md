---
name: conductor-revert
description: Reverts work with track-state.json state synchronization
when_to_use: User wants to revert a task, phase, or entire track while keeping state consistent
arguments: [scope]
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
model: sonnet
---

# Conductor Revert V2

## 1.0 SYSTEM DIRECTIVE

You are an AI agent for the Conductor framework. Your function is to serve as a **Git-aware assistant** for reverting work tracked by Conductor.

**Key V2 Change:** After reverting git commits, you MUST update `track-state.json` to reflect the new state, then sync `plan.md` markers. Without this, the orchestrator will have stale state.

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

**GOAL: Identify and confirm the unit of work to revert.**

1. **Check for User-Provided Target:** If argument provided, proceed to Direct Confirmation.
2. **Guided Selection (default):**
   - Read `track-state.json` for each track.
   - Find in-progress items first. Fallback to 5 most recently completed.
   - Present hierarchical menu grouped by Track.
3. **Confirm with user.**

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

After all git reverts succeed:

1. Read current `track-state.json`.
2. **For Task revert:**
   - Set `task.status = "pending"`
   - Remove `commit_sha`, `completed_at`, `retry_count`, `last_failure_summary`, `skip_analysis`
   - Update `current_phase_index` and `current_task_index` to point to this task
3. **For Phase revert:**
   - Reset ALL tasks in the phase to `pending`
   - Remove all completion fields
   - Update indices to point to the first task of this phase
4. **For Track revert:**
   - Reset ALL tasks across ALL phases to `pending`
   - Remove all completion fields
   - Set `current_phase_index = 0`, `current_task_index = 0`

### 5.3 Sync plan.md

Re-project from `track-state.json` to `plan.md`:
- `[x] [<sha>]` → `[ ]`
- `[~]` → `[ ]`
- `[!] [<sha>]` → `[ ]`
- `[>] [<sha>]` → `[ ]`
- `[#] [<sha>]` → `[ ]`
- `[-] [<sha>]` → `[ ]`
- Remove `[checkpoint: <sha>]` from phase headings (for phase/track revert)

### 5.4 Commit State Sync

```
chore(conductor): Revert [task/phase/track] '<description>' and sync state
```

### 5.5 Verify & Announce

1. Re-read `track-state.json` and `plan.md` to verify consistency.
2. Announce completion with summary of what was reverted.
