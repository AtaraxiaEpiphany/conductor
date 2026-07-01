---
name: parallel
description: Orchestrates opt-in within-track worktree wave parallelism — fans out file-disjoint deps-declared tasks concurrently, then serially integrates each member's commit back
when_to_use: User wants to parallelize a track's independent tasks (deps-declared, file-disjoint) into concurrent worktree-isolated task-executor agents instead of strict serial execution
argument-hint: "[track_name]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet
hooks:
  Stop:
    - matcher: ""
      hooks:
        - type: command
          command: "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/on-stop-conductor.py\""
          timeout: 10
---

# Conductor Parallel — Worktree Wave Orchestrator

## ORCHESTRATOR CONTRACT

You are a **thin state machine** that routes between subagents — the parallel sibling of `conductor:implement`. Serial execution is the default; this skill runs ONLY when the user opts into within-track parallelism for a phase that decomposes into file-disjoint, deps-declared tasks.

1. **NEVER read `spec.md` or `plan.md`** — subagents self-load all business context. You decide waves from the `dispatch-wave` compact envelope alone.
2. **Parse only the compact envelope's emitted fields** (`COMPACT_FIELDS` in `scripts/track_state/helpers.py`). Pass `--full` only to debug.
3. **Keep dispatch prompts minimal** — task identity + paths only (~100 tokens). The only additions over serial are `WORKTREE_DIR` and a worktree-pinned `TRACK_DIR`.
4. **Announce actions tersely** — one line per action, no narrative.
5. **Yield cleanly when context runs low.** A wave (fan-out + integrate ALL its members) is the yield unit — it is bounded. If context runs low, finish integrating every in-flight member to `drained`, then stop with exactly:
   `"⏸️ Conductor wave checkpoint — wave drained, state committed. Re-invoke /conductor:parallel to resume (dispatch-wave refuses with wave_active if a member is still in_flight, signaling an interrupted wave to integrate first)."`
   **NEVER stop mid-wave** — between `dispatch-wave` returning members and the last `wave-finalize` returning `drained`, every member's worktree is live and its branch unmerged. Yield only after `drained: true`.

Wave loop: `DISPATCH-WAVE → FAN OUT → INTEGRATE (per member) → (drained) → repeat → NO_READY_TASKS → SERIAL FALLBACK or PHASE BOUNDARY → (repeat) → FINALIZE → POST-LOOP`

---

## 0.0 MODEL — when waves run vs serial

`dispatch-wave` computes a **ready-set**: pending, flat (no subtasks), executor-routed (`[Manual]`/`[Explore]` excluded), AND **opt-in via a `<!-- deps: -->` comment** whose every declared dep target is satisfied (completed/skipped/deferred). Capped at 4 members. Tasks with no deps comment are assumed serial-order-dependent and stay on the serial spine — so a wave ONLY ever contains tasks the plan author declared independent. Serial fallback (§3.0) makes progress on the remaining serial tasks, which may satisfy deps for the next wave.

---

## 1.0 SETUP + TRACK SELECTION

1. Locate track from `conductor/tracks.md` — resolve `$ARGUMENTS` or auto-select.
2. Run `track-state preflight "<track_dir>"`. If `ok: false` (missing spec/plan/state, or missing `conductor/workflow/`) → `"Conductor environment incomplete. Run /conductor:setup."` → HALT.
3. `track-state recover "<track_dir>"`.
   - `status: "wave_active"` → an interrupted wave has in-flight members. Go to **§2.5** (resume the wave — integrate members before anything else).
   - error → HALT.
   - otherwise → continue.
4. If `status == "new"` → `track-state start` + `registry-update` + commit.
5. If recover surfaced a normal `in_progress`/`failed`/`blocked` serial task (no wave active) → handle exactly as `implement` §2.0, then come back here.

---

## 2.0 STATE RECOVERY (serial spine, when no wave active)

Same as `implement` §2.0:

```bash
track-state validate "<track_dir>" --fix
track-state recover "<track_dir>"
track-state sync-plan "<track_dir>"
```

Store `execution_mode` from recover output (default `"interactive"`). If state changed → commit `chore(conductor): Fix state consistency after recovery`. Route recover `status` per the `implement` §2.0 table, then → **§3.0**.

### 2.5 Resume an interrupted wave

Re-invocation after a yield or interruption may find members still `in_flight` — their worktrees are live and their agents already ran (or were cut off). The work must be integrated before a fresh wave.

```bash
track-state wave-status "<track_dir>"      # see which members are in_flight
```

For **each** `in_flight` member (in any order): `track-state wave-finalize "<track_dir>" --phase <p> --task <t>` (→ §4.0 integrate). When all members settle, → **§3.0**.

---

## 3.0 WAVE LOOP

### 3.1 Compute + fan out a wave

```bash
track-state dispatch-wave "<track_dir>"
```

Switch on `action`:

| action | Route |
|---|---|
| `dispatch_wave` | **§3.2** — fan out the `wave` members. |
| `no_ready_tasks` | **§3.3** — no parallelizable work; fall to the serial spine. |
| `wave_active` | **§2.5** — members still in flight; integrate them first. |

### 3.2 Fan out (action: `dispatch_wave`)

The `wave` array has one member per ready task, each carrying `worktree`, `branch`, `worktree_track_dir`, `phase`, `task`, `name`. **Dispatch ALL members in ONE message** (concurrent `Agent` calls) so they run in parallel — that is the entire point of this skill.

Per member, dispatch `conductor:task-executor` with the canonical minimal prompt **plus the worktree pinning**:

```
WORKTREE_DIR={worktree}
TRACK_DIR={worktree_track_dir}
PHASE={p}
TASK={t}
SUBTASK=null
NAME={name}
ATTEMPT=1
MAX_RETRIES={m}
```

Lead the agent's first action with: `cd "{WORKTREE_DIR}"` first (Bash cwd persists across calls), so every later `git`/edit lands in the worktree. `TRACK_DIR` points into the worktree, so the agent's `track-state write-result "{TRACK_DIR}" ...` writes the worktree's own `result.json` — exactly what `wave-finalize` reads. The agent otherwise behaves identically to serial (TDD, coverage, commits on its branch). It does NOT call dispatch-finalize — wave integration is the orchestrator's job (§4.0).

After ALL members return → **§4.0** (integrate each, in any order).

### 3.3 Serial fallback (action: `no_ready_tasks`)

No deps-declared file-disjoint pending task is ready in the current phase. Make serial progress — a serial task may satisfy a dep that unlocks the next wave.

```bash
track-state dispatch-next "<track_dir>"
```

- `action: "wave_active"` → **§2.5** (shouldn't normally happen; integrate and resume).
- `action: "finalize"` → track done → **§5.0**.
- `action: "dispatch_executor"` / `"dispatch_explorer"` → run that ONE task the serial way: `dispatch-prepare` → dispatch the agent → `dispatch-finalize` (exactly `implement` §3.3/§3.4/§3.6). Then → **§3.1** (re-check for waves — the just-finished task may have unlocked one).
- `action: "dispatch_phase_checker"` / `"parent_stuck"` / `"defer_manual"` / `"manual_task"` → handle per `implement` §3.2/§3.5/§3.5b/§3.5c, then → **§3.1**.

---

## 4.0 INTEGRATE (after the wave's agents return)

For **each** member of the just-dispatched wave, **in any order, serialized**:

```bash
track-state wave-finalize "<track_dir>" --phase <p> --task <t>
```

`wave-finalize` reads the member's worktree `result.json`, squash-merges its branch onto the track branch as ONE code commit (conflict → FAILURE with a `SPEC_DEVIATION`), runs the SUCCESS/FAILURE finalize transition on the real `track-state.json`, and tears down that member's worktree + branch. It emits `status`, `committed`, `member_status`, `drained`.

Route by `status`:

**SUCCESS** (`member_status: finalized`): announce tersely. `deviations > 0` → announce. If `phase_checkpoint_pending` present → note it (handle at the phase boundary, §4.2). `committed: false` → announce `"wave-finalize conductor commit failed, result.json preserved"` and re-run `wave-finalize` for that member (max 3 attempts, then HALT `"wave-finalize stuck"`).

**FAILURE** (`member_status: failed` or `conflict`): the member is left `failed`. **v1 does NOT retry within the wave** (in-wave re-dispatch is deferred). The failed member stays `failed`; after the wave drains it is handled by the serial spine's normal retry/skip/block path (§4.1). Announce which member failed and why (`member_status: conflict` → file-overlap despite declared deps).

After each `wave-finalize`, check `drained`:
- `drained: false` → integrate the next member.
- `drained: true` → **§4.1**.

### 4.1 Wave drained

All members of this wave have settled. Announce the wave summary (`N completed / M failed`). If any members FAILED → the serial spine handles them next: → **§3.1** (next `dispatch-wave`); when the phase has no more parallel OR serial work, `dispatch-next`'s recover path surfaces the failed members with full Retry/Skip/Block logic exactly as `implement` §2.2 (interactive) or routes to `skip-analyst` (continuous). Otherwise → **§3.1** (re-check for the next wave).

### 4.2 Phase boundary

If `phase_checkpoint_pending` was emitted by any `wave-finalize`, or after a wave drains with no failed members and no further wave/serial work in the phase:

```bash
track-state phase-done "<track_dir>" <phase>
```

`complete=true` → dispatch `conductor:phase-checker` (`implement` §3.2), `PHASE=<phase>`, then → **§3.1**. FAILED → HALT. `complete=false` → **§3.1**.

---

## 5.0 FINALIZE + POST-LOOP

When `dispatch-next` returns `action: "finalize"` (all phases drained):

```bash
track-state finalize "<track_dir>"
```

Then run the shared post-loop — identical to `implement` §4.0:

```bash
track-state post-loop-status "<track_dir>"
```

Read `conductor/workflow/post-loop.md` and execute sections 5.0–8.0 (doc-sync, review, etc.). Yield discipline is the same as `implement`: yield at a post-loop phase boundary if context runs low, never between a review and its reviewed-range stamp.

---

## 6.0 RECOVERY — a wedged wave

If a wave is left in a bad state (stranded worktrees, corrupt ledger, or you need to abandon in-flight work):

```bash
track-state wave-abort "<track_dir>"
```

Resets in-flight members to `pending` (preserving their retry history), tears down their worktrees + branches, and deletes the ledger. Finalized/failed members keep their applied terminal status. After abort → **§2.0** (the serial spine re-dispatches the reset members normally).
