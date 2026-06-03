---
name: implement
description: Orchestrates track task execution via subagents with track-state.json synchronization
when_to_use: User wants to implement a track, execute pending tasks, or run the conductor implementation workflow
argument-hint: "[track_name]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet
hooks:
  Stop:
    - matcher: ""
      hooks:
        - type: prompt
          prompt: "You are a conductor state auditor. Review the last assistant message and any additionalContext from the state-consistency-check hook. Check for: (1) stale in_progress tasks, (2) uncommitted changes that should be tracked, (3) state inconsistencies between track-state.json and plan.md. Return JSON: {\"ok\": true/false, \"reason\": \"brief issue description or 'OK'\"}"
          model: haiku
---

# Conductor Implement — Thin Orchestrator

## ORCHESTRATOR CONTRACT

You are a **thin state machine** that routes between subagents. Context budget is precious.

1. **NEVER read `spec.md` or `plan.md`** — subagents self-load all business context.
2. **ONLY parse `action`, `status`, `sha`, `deviations`, `retry_count`** from track-state outputs.
3. **Keep dispatch prompts minimal** — task identity + file paths only (~100 tokens).
4. **Announce actions tersely** — one line per action, no narrative.

Dispatch loop: `RECOVER → DISPATCH → PROCESS → PHASE_BOUNDARY → (repeat) → FINALIZE`

Tag inheritance: subtasks inherit dispatch tags from parent when subtask name has none.

---

## 1.0 SETUP + TRACK SELECTION

1. Locate track from `conductor/tracks.md` — resolve `$ARGUMENTS` or auto-select `[~]`/`[ ]`.
2. Verify core files exist: `spec.md`, `plan.md`, `track-state.json`, `conductor/workflow/index.md`.
   Missing → `"Conductor environment incomplete. Run /conductor:setup."` → HALT.
3. `track-state recover "<track_dir>"` — if error → HALT.
4. If `status == "new"` → `track-state start` + `registry-update` + commit.

---

## 2.0 STATE RECOVERY

```bash
track-state validate "<track_dir>" --fix   # auto-fixes plan mismatches, stale indices
track-state recover "<track_dir>"
track-state sync-plan "<track_dir>"         # auto-absorbs untracked subtasks
```

Route by recover `status`:

| Status | Action |
|---|---|
| `in_progress` | `git log` for post-start commit. Found → `complete --sha <sha>`. Not found → re-dispatch. |
| `failed` + retry < max | Re-dispatch. |
| `failed` + retry >= max | Dispatch `conductor:skip-analyst`. |
| `blocked` | Report → HALT. |
| `completed`/`skipped`/`no_active_task` | Check `phase_checkpoint_pending`. If set → dispatch `conductor:phase-checker`. Otherwise → **Section 3.0**. |

Store `execution_mode` from recover output. Default `"interactive"`.
If state changed → commit: `chore(conductor): Fix state consistency after recovery`

### 2.1 Resume Phase Checkpoint

If recover output contains `phase_checkpoint_pending: <phase_index>`:
- Dispatch `conductor:phase-checker` with `TRACK_DIR TRACK_ID PHASE=<phase_index> EXECUTION_MODE`
- After return → **Section 3.7** (Phase Boundary)

---

## 3.0 DISPATCH LOOP

### 3.1 Get Next Action

```bash
track-state dispatch-next "<track_dir>"
```

Returns `action` enum — switch on it:

### 3.2 Action: `dispatch_phase_checker`

Dispatch `conductor:phase-checker` with `TRACK_DIR TRACK_ID PHASE=<phase from output> EXECUTION_MODE`.

After return → **Section 3.6** (Phase Boundary).

### 3.3 Action: `dispatch_explorer`

```bash
track-state dispatch-prepare "<track_dir>"
# Only commit start if commit_msg is present (null on resume — avoids duplicate start commits)
if commit_msg: git add -A && git diff --cached --quiet || git commit -m "<commit_msg>"
```

Dispatch `conductor:explorer`. Prompt: `TRACK_DIR={td} PHASE={p} TASK={t} NAME={name}`

After return: commit exploration artifacts (`git add -A && git diff --cached --quiet || git commit -m "docs(explore): {name}"`) → get SHA (`git rev-parse --short HEAD`) → `track-state dispatch-finalize "<track_dir>" --override commit_sha={sha}` → `dispatch-finalize` commits internally → **Section 3.7**.

### 3.4 Action: `dispatch_executor`

```bash
track-state dispatch-prepare "<track_dir>"
# Only commit start if commit_msg is present (null on resume — avoids duplicate start commits)
if commit_msg: git add -A && git diff --cached --quiet || git commit -m "<commit_msg>"
```

Dispatch `conductor:task-executor`. Prompt: `TRACK_DIR={td} PHASE={p} TASK={t} SUBTASK={s} NAME={name} ATTEMPT={n} MAX_RETRIES={m} IS_RETRY={bool}`

After return → **Section 3.6**.

### 3.5 Action: `parent_stuck`

Parent auto-completed with failed subtasks (no other work remains). Announce:

`"⚠️ Parent '{name}' completed with failed subtasks — check P{phase}.T{task}"`

`track-state sync-plan "<track_dir>"` → commit → **Section 3.7**.

### 3.5b Action: `defer_manual`

```bash
track-state defer "<track_dir>" --phase <p> --task <t> --reason 'Deferred: manual task requires human verification'
track-state sync-plan "<track_dir>"
git commit -m "chore(conductor): Defer manual task '<name>'"
```

Check the output of ALL three commands (especially `defer` and `sync-plan`) for `phase_checkpoint_pending` or `next_action: dispatch_phase_checker`.

If found → dispatch `conductor:phase-checker` with `TRACK_DIR TRACK_ID PHASE=<phase from output> EXECUTION_MODE`, then → **Section 3.1**.

If NOT found → **Section 3.7**.

### 3.6 Process Result (after task-executor)

```bash
track-state dispatch-finalize "<track_dir>"
```

`dispatch-finalize` creates the conductor commit internally. Do NOT commit separately.
Output includes `committed: true/false` and optionally `phase_checkpoint_pending: <phase_index>`.

**SUCCESS**: `committed: false` → announce `"conductor commit failed, result.json preserved"` → re-run `dispatch-finalize` (max 3 attempts, then HALT with `"dispatch-finalize stuck"`). Deviations > 0 → announce. If `phase_checkpoint_pending` present → dispatch `conductor:phase-checker` immediately. Otherwise → **Section 3.7**.

**FAILURE**: retry < max → re-dispatch (Section 3.1). retry >= max → dispatch `conductor:skip-analyst`. Skip-analyst result: `can_skip` → `track-state skip` or `block` → `sync-plan` → commit → Section 3.1 or HALT.

### 3.7 Phase Boundary

```bash
track-state phase-done "<track_dir>" <phase>
```

`complete=true` → dispatch `conductor:phase-checker` with `TRACK_DIR TRACK_ID PHASE_INDEX EXECUTION_MODE`. FAILED → HALT. Otherwise → Section 3.1.
`complete=false` → Section 3.1.

### 3.8 Action: `finalize`

→ **Section 4.0**.

---

## 4.0 POST-LOOP

Read `conductor/workflow/post-loop.md` and execute sections 5.0–8.0.

---