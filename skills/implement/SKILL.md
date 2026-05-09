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
        - type: command
          command: "bash \"${CLAUDE_PLUGIN_ROOT}/scripts/state-consistency-check\""
          timeout: 10
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
track-state validate "<track_dir>"          # --fix if invalid
track-state recover "<track_dir>"
track-state sync-plan "<track_dir>"
```

Route by recover `status`:

| Status | Action |
|---|---|
| `in_progress` | `git log` for post-start commit. Found → `complete --sha <sha>`. Not found → re-dispatch. |
| `failed` + retry < max | Re-dispatch. |
| `failed` + retry >= max | Dispatch `conductor:skip-analyst`. |
| `blocked` | Report → HALT. |
| `completed`/`skipped`/`no_active_task` | → **Section 3.0**. |

Store `execution_mode` from recover output. Default `"interactive"`.
If state changed → commit: `chore(conductor): Fix state consistency after recovery`

---

## 3.0 DISPATCH LOOP

### 3.1 Get Next Action

```bash
track-state dispatch-next "<track_dir>"
```

Returns `action` enum — switch on it:

### 3.2 Action: `dispatch_explorer`

```bash
track-state lock "<track_dir>" <p> <t> [<s>]
track-state sync-plan "<track_dir>"
git add -A && git commit -m "chore(conductor): Start task '<name>' [<p>.<t>]"
```

Dispatch `conductor:explorer`. Prompt: `TRACK_DIR={td} PHASE={p} TASK={t} NAME={name}`

After return: commit artifacts → `git rev-parse --short HEAD` → `track-state complete "<track_dir>" <p> <t> --sha "<sha>"` → `sync-plan` → **Section 3.6**.

### 3.3 Action: `dispatch_executor`

```bash
track-state lock "<track_dir>" <p> <t> [<s>]
track-state sync-plan "<track_dir>"
git add -A && git commit -m "chore(conductor): Start task '<name>' [<p>.<t>]"
```

Dispatch `conductor:task-executor`. Prompt: `TRACK_DIR={td} PHASE={p} TASK={t} SUBTASK={s} NAME={name} ATTEMPT={n} MAX_RETRIES={m} IS_RETRY={bool}`

After return → **Section 3.5**.

### 3.4 Action: `defer_manual`

```bash
track-state defer "<track_dir>" <p> <t> --reason 'Deferred: manual task requires human verification'
track-state sync-plan "<track_dir>"
git commit -m "chore(conductor): Defer manual task '<name>'"
```

Emit: `DEFERRED: P{p}.T{t} '<name>'` → **Section 3.6**.

### 3.5 Process Result (after task-executor)

```bash
track-state process-result "<track_dir>"
```

**SUCCESS**: commit `chore(conductor): Complete '<name>' [<sha>]`. Deviations > 0 → announce. → **Section 3.6**.

**FAILURE**: commit `chore(conductor): '<name>' failed (attempt {n})`. retry < max → re-dispatch (Section 3.1). retry >= max → dispatch `conductor:skip-analyst`. Skip-analyst result: `can_skip` → `track-state skip` or `block` → `sync-plan` → commit → Section 3.1 or HALT.

### 3.6 Phase Boundary

```bash
track-state phase-done "<track_dir>" <phase>
```

`complete=true` → dispatch `conductor:phase-checker` with `TRACK_DIR TRACK_ID PHASE_INDEX EXECUTION_MODE`. FAILED → HALT. Otherwise → Section 3.1.
`complete=false` → Section 3.1.

### 3.7 Action: `finalize`

→ **Section 4.0**.

---

## 4.0 POST-LOOP

Read `conductor/workflow/post-loop.md` and execute sections 5.0–8.0.

---

## COMPRESSION PRIORITY

When context is compressed:
1. **KEEP**: Sections 3.0–3.6 (active dispatch loop) + last track-state output
2. **COMPRESS**: completed iteration outputs (keep only sha + status per task)
3. **DISCARD**: Sections 1.0–2.0 (one-time setup) and Section 4.0 (post-loop, re-read when needed)
