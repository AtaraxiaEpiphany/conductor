---
name: implement
description: Orchestrates track task execution via subagents with track-state.json synchronization
when_to_use: User wants to implement a track, execute pending tasks, or run the conductor implementation workflow
argument-hint: "[track_name]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet
---

# Conductor Implement — Thin Orchestrator

## 0.0 LOAD ORCHESTRATION LAYER

Read and internalize `${CLAUDE_PLUGIN_ROOT}/conductor-orchestration.md`. This provides the dispatch loop, subagent registry, and execution mode definitions.

CRITICAL: You are a **thin orchestrator** — a pure state machine that routes between subagents. Your context budget is precious. Follow these rules:

1. **NEVER read `spec.md` or `plan.md`** — subagents self-load all business context.
2. **ONLY parse `status`, `sha`, `deviations`, `retry_count`** from track-state outputs.
3. **Keep dispatch prompts minimal** — task identity + file paths only (~100 tokens).
4. **Announce actions tersely** — one line per action, no narrative.

---

## 1.0 SETUP CHECK

Run these verifications. Announce failures tersely and HALT.

1. **Locate Track**: Resolve track directory from Tracks Registry (`conductor/tracks.md`).
2. **Verify Core Files**: Confirm `spec.md`, `plan.md`, `track-state.json` exist in track dir. Skip `issues.md` (lazy-created).
3. **Verify Workflow**: Confirm `conductor/workflow/index.md` exists and links are valid.
4. **Missing Files**: If ANY file missing → announce: `"Conductor environment incomplete — missing: <file>. Please run /conductor:setup."` → HALT.

---

## 2.0 TRACK SELECTION

1. Check `$ARGUMENTS` for user-provided track name.
2. Parse Tracks Registry for track entries.
3. **Selection Logic:**
   - Provided name → exact match → confirm via `AskUserQuestion`.
   - No name → find `[~]` track → if one → auto-select.
   - No `[~]` → find `[ ]` tracks → if one → auto-select.
   - Multiple → present via `AskUserQuestion`.
   - None → `"No active tracks. Use /conductor:new-track."` → HALT.
4. Verify: `track-state recover "<track_dir>"` — if error → HALT.

---

## 3.0 STATE RECOVERY

1. Run: `track-state recover "<track_dir>"`
2. Route by `status`:

| Recovery Status | Action |
|---|---|
| `in_progress` | Check `git log` for post-start commit. Found → `complete --sha <sha>`. Not found → re-dispatch. |
| `failed` + `retry < max` | Re-dispatch (failure context from `issues.md` loaded by subagent). |
| `failed` + `retry >= max` | → **Section 4.5.1 Skip Analysis**. |
| `blocked` | Report to user → HALT. |
| `completed`/`skipped`/`no_active_task` | → **Section 4.1 Select Next**. |

3. `track-state sync-plan "<track_dir>"`
4. If state changed → commit: `chore(conductor): Fix state consistency after recovery`

---

## 4.0 DISPATCH LOOP

### 4.1 Select Next Task

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/track-state" next "<track_dir>"
```

- `phase == -1` → all terminal → **Section 5.0**.
- `type == "parent-complete"` → `track-state complete "<track_dir>" <p> <t> --sha ""` → re-run `next`.
- Route by tags:
  - `Explore` → **4.3.E**
  - `Manual` + continuous → **4.3.M**
  - `Manual` + interactive → `AskUserQuestion` defer or execute
  - default → **4.3.T**

### 4.2 Pre-Dispatch

```bash
track-state lock "<track_dir>" <phase> <task> [<subtask>]
track-state sync-plan "<track_dir>"
git add -A && git commit -m "chore(conductor): Start task '<name>' [<p>.<t>]"
```

Emit: `LOCKED: P{p}.T{t} '<name>'`

### 4.3 Dispatch (Compact Prompt)

All dispatch prompts use this template — **never include business context**:

### 4.3.E Explorer Dispatch

`Agent` tool, `subagent_type: "conductor:explorer"`, description: `"Explore P{p}.T{t}"`.

```
TRACK_DIR={track_dir}
PHASE={phase}
TASK={task}
NAME={name}
```

After → commit `docs(explore): {name}` → **Section 4.5**.

### 4.3.M Auto-Defer Manual Tasks

```bash
track-state defer "<track_dir>" <p> <t> --reason 'Deferred: manual task in continuous mode'
track-state sync-plan "<track_dir>"
git commit -m "chore(conductor): Defer manual task '<name>'"
```

Emit: `DEFERRED: P{p}.T{t} '<name>'` → **Section 4.6**.

### 4.3.T Task Executor Dispatch

`Agent` tool, `subagent_type: "conductor:task-executor"`, description: `"Execute P{p}.T{t}"`.

```
TRACK_DIR={track_dir}
PHASE={phase}
TASK={task}
NAME={name}
ATTEMPT={attempt}
MAX_RETRIES={max_retries}
IS_RETRY={is_retry}
```

### 4.5 Process Result

```bash
track-state process-result "<track_dir>"
```

Parse output. Only these fields matter:

**SUCCESS** (`status: "success"`):
- `sha` → commit: `chore(conductor): Complete '<name>' [<sha>]`
- `deviations > 0` → announce spec deviations
- → **Section 4.6**

**FAILURE** (`status: "failure"`):
- `retry_count` + `summary`
- commit: `chore(conductor): '<name>' failed (attempt {n})`
- `retry < max` → re-dispatch (**4.3**)
- `retry >= max` → **4.5.1**

### 4.5.1 Skip Analysis

Dispatch `conductor:skip-analyst`. Pass: TRACK_DIR, PHASE, TASK, NAME, RETRY_COUNT.

- `can_skip=true` → `track-state skip` → `sync-plan` → commit → **4.1**
- `can_skip=false` → `track-state block` → `sync-plan` → commit → announce + HALT

### 4.6 Phase Boundary

```bash
track-state phase-done "<track_dir>" <phase>
```

- `complete=true` → **Section 4.7**
- `complete=false` → **Section 4.1**

### 4.7 Phase Checkpoint

Dispatch `conductor:phase-checker`. Prompt:

```
TRACK_DIR={track_dir}
PHASE={phase}
EXECUTION_MODE={mode}
```

Parse result. FAILED → announce + HALT. Otherwise → **4.1**.

---

## 5.0 FINALIZATION

```bash
track-state finalize "<track_dir>"
track-state sync-plan "<track_dir>"
```

Update Tracks Registry: `[~]` → `[x]`. Commit: `chore(conductor): Complete track '<desc>'`.

---

## 5.5 DEFERRED VERIFICATION

```bash
track-state deferred-report "<track_dir>"
```

`count == 0` → skip. Otherwise present each deferred task via `AskUserQuestion`:
- "Verify completed" → `track-state complete --sha ""`
- "Skip" → `track-state skip --reason 'User verified not needed'`
- "Defer" → no action

After → `sync-plan` + commit.

---

## 6.0 DOC SYNC

Dispatch `conductor:doc-syncer`. Prompt: `TRACK_DIR={track_dir} TRACK_ID={track_id}`.

---

## 7.0 CLEANUP

Present options: A) Review (`/conductor:review`), B) Archive, C) Delete, D) Skip.
