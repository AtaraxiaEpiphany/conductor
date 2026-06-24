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
          prompt: "Output ONLY a raw JSON object - no prose, no markdown fences, nothing before or after it. If the input stop_hook_active is true, return {\"ok\": true, \"reason\": \"OK\"} immediately to let the agent stop. Otherwise audit the conductor state visible in the transcript for: (1) stale in_progress tasks left behind, (2) state changes not committed, (3) drift between track-state.json and plan.md. Return {\"ok\": true, \"reason\": \"OK\"} if clean, or {\"ok\": false, \"reason\": \"one-line issue description\"} if a real issue needs the agent to act before stopping. Emit the JSON and nothing else."
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
| `pending` + retry_count > 0 | Re-dispatch (retry). Pass `IS_RETRY=true` `ATTEMPT={retry_count+1}` `MAX_RETRIES=3` to task-executor. |
| `failed` + retry < max | Re-dispatch. |
| `failed` + retry >= max | **Interactive**: surface to the user via `AskUserQuestion` — Retry / Skip / Block (see §2.2). **Continuous**: dispatch `conductor:skip-analyst`. |
| `blocked` | Report → HALT. |
| `completed`/`skipped`/`no_active_task` | Check `phase_checkpoint_pending`. If set → dispatch `conductor:phase-checker`. Otherwise → **Section 3.0**. |

Store `execution_mode` from recover output. Default `"interactive"`.
If state changed → commit: `chore(conductor): Fix state consistency after recovery`

### 2.1 Resume Phase Checkpoint

If recover output contains `phase_checkpoint_pending: <phase_index>`:
- Dispatch `conductor:phase-checker` with `TRACK_DIR TRACK_ID PHASE=<phase_index> EXECUTION_MODE`
- After return → **Section 3.7** (Phase Boundary)

### 2.2 Failed Task Decision (interactive only)

When recover surfaces a `failed` task whose retries are exhausted, do NOT silently skip it. Use `AskUserQuestion`:

> "Task '<name>' (P<phase>.T<task>) failed after <retry_count> attempts. What next?"

Options:
- **Retry** → reset and re-dispatch from scratch:
  ```bash
  track-state reset "<track_dir>" task --phase <p> --task <t>
  track-state sync-plan "<track_dir>"
  git commit -m "chore(conductor): Reset failed task '<name>' for retry"
  ```
  → **Section 3.1**.
- **Skip** → `track-state skip "<track_dir>" --phase <p> --task <t> --reason 'Skipped: failed task not required'` → `sync-plan` → commit `chore(conductor): Skip failed task '<name>'` → **Section 3.1**.
- **Block** → `track-state block "<track_dir>" --phase <p> --task <t> --reason 'Blocked: failed task needs human intervention'` → `sync-plan` → commit → announce → HALT.

A parent failed via the parent-stuck path (P<phase>.T<task> rendered `[!]` because its subtasks exhausted retries) is surfaced the same way — `reset task` clears the parent **and** its subtasks for a full retry.

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

Dispatch `conductor:explorer`. Prompt: `TRACK_DIR={td} PHASE={p} TASK={t} SUBTASK={s} NAME={name}`

After return → `track-state dispatch-finalize "<track_dir>"` → **Section 3.7**.

The explorer records findings via `track-state append-handoff` (→ `.conductor/handoff/`, the sanctioned channel) and writes `.conductor/result.json` (gitignored). Both are conductor-managed, so `dispatch-finalize`'s internal conductor commit stages them — **no separate `docs(explore)` commit, no `git add -A` sweep, no `--override commit_sha`**. The explorer's result ships `commit_sha: ""`; `dispatch-finalize` stores the conductor completion SHA for empty-sha explorer results. (This also kills the result.json history-churn bug: the transient file is no longer swept into a commit.)

### 3.4 Action: `dispatch_executor`

```bash
track-state dispatch-prepare "<track_dir>"
# Only commit start if commit_msg is present (null on resume — avoids duplicate start commits)
if commit_msg: git add -A && git diff --cached --quiet || git commit -m "<commit_msg>"
```

Dispatch `conductor:task-executor`. Prompt: `TRACK_DIR={td} PHASE={p} TASK={t} SUBTASK={s} NAME={name} ATTEMPT={n} MAX_RETRIES={m} IS_RETRY={bool}`

After return → **Section 3.6**.

### 3.5 Action: `parent_stuck`

Parent has failed subtasks (retries exhausted) and no other work remains. The parent is marked **failed** (renders `[!]`, not `[x]`) and committed by `dispatch-next`. Announce:

`"⚠️ Parent '{name}' marked failed — subtasks exhausted retries (P{phase}.T{task}). On the next run, recover surfaces it for a Retry/Skip/Block decision (§2.2)."`

`track-state sync-plan "<track_dir>"` → **Section 3.7**.

### 3.5b Action: `defer_manual`

```bash
track-state defer "<track_dir>" --phase <p> --task <t> --reason 'Deferred: manual task requires human verification'
track-state sync-plan "<track_dir>"
git commit -m "chore(conductor): Defer manual task '<name>'"
```

Check the output of ALL three commands (especially `defer` and `sync-plan`) for `phase_checkpoint_pending` or `next_action: dispatch_phase_checker`.

If found → dispatch `conductor:phase-checker` with `TRACK_DIR TRACK_ID PHASE=<phase from output> EXECUTION_MODE`, then → **Section 3.1**.

If NOT found → **Section 3.7**.

### 3.5c Action: `manual_task`

(Interactive mode only — in continuous mode a `[Manual]` task emits `defer_manual`, see 3.5b.) A `[Manual]` task requires human verification and cannot be auto-executed, so it is surfaced to the user instead of silently deferred. Ask via `AskUserQuestion` whether to defer it for later or skip it, then run the matching command:

- **Defer** → `track-state defer "<track_dir>" --phase <p> --task <t> --reason 'Deferred: manual task requires human verification'`
- **Skip** → `track-state skip "<track_dir>" --phase <p> --task <t> --reason 'Skipped: manual task not required'`

Then: `track-state sync-plan "<track_dir>"` → `git commit -m "chore(conductor): {Defer|Skip} manual task '<name>'"` → **Section 3.1** (dispatch-next detects any pending phase checkpoint and routes accordingly).

### 3.6 Process Result (after task-executor)

**ALWAYS** call `dispatch-finalize` after the task-executor returns — even when no result block was detected in the output or the subagent output looks incomplete. `dispatch-finalize` handles the missing result.json case by synthesizing a result from state: it detects whether the agent committed code (→ SUCCESS) or produced nothing (→ FAILURE with handoff record for retry context).

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