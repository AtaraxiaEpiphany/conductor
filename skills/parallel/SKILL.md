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

You are a **thin state machine** that routes between subagents — the parallel sibling of `conductor:implement`. Runs ONLY when the user opts into within-track parallelism for a phase of file-disjoint, deps-declared tasks.

1. **NEVER read `spec.md` or `plan.md`** — subagents self-load all business context. Decide waves from the `dispatch-wave` compact envelope alone.
2. **Parse only the compact envelope** (`COMPACT_FIELDS` in `scripts/track_state/helpers.py`). Pass `--full` only to debug.
3. **Keep dispatch prompts minimal** — task identity + paths only (~100 tokens). The only additions over serial are `WORKTREE_DIR` and a worktree-pinned `TRACK_DIR`.
4. **Announce actions tersely** — one line per action, no narrative.
5. **Never abandon a mid-wave state machine.** A compaction can pause you mid-wave — never stop between `dispatch-wave` returning members and the last `wave-finalize` returning `drained`, when every member's worktree is live and its branch unmerged. `dispatch-wave` refuses with `wave_active` if a member is still in_flight (integrate it first); only rest at `drained: true`.

Wave loop: `DISPATCH-WAVE → FAN OUT → INTEGRATE (per member) → (drained) → repeat → NO_READY_TASKS → SERIAL FALLBACK or PHASE BOUNDARY → (repeat) → FINALIZE → POST-LOOP`

---

## 0.0 MODEL — when waves run vs serial

`dispatch-wave` computes a **ready-set**: pending, flat (no subtasks), executor-routed (manual/explore-route tasks excluded — `route_for == executor`), AND **opt-in via a `<!-- deps: -->` comment** whose every declared dep is satisfied (completed/skipped/deferred). Capped at the resolved wave size — `CONDUCTOR_WAVE_SIZE`, default 2 (`DEFAULT_WAVE_SIZE` in `scripts/track_state/wave.py`). Tasks with no deps comment stay on the serial spine — a wave ONLY ever contains tasks the plan author declared independent. Serial fallback (§3.0) may satisfy deps for the next wave.

---

## 1.0 SETUP + TRACK SELECTION

1. `track-state check "$ARGUMENTS"` — always exits 0; outcome is in `action`:
   - `proceed` → `<td>` = `td`; **print `announce`**; continue to step 2.
   - `ask` → `AskUserQuestion` over `candidates` (label = `track_id`), then re-run `track-state check "<chosen track_id>"`.
   - `halt` → print `message`; HALT.
2. `track-state recover "<track_dir>"`.
   - `status: "wave_active"` → an interrupted wave has in-flight members. Go to **§2.5** (resume the wave — integrate members before anything else).
   - error → HALT.
   - otherwise → continue.
3. `track-state start "<track_dir>"`.
4. If recover surfaced a normal `in_progress`/`failed`/`blocked` serial task (no wave active) → handle exactly as `implement` §2.0, then come back here.

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

The `wave` array has one member per ready task, each carrying `worktree`, `branch`, `worktree_track_dir`, `phase`, `task`, `name`. **Dispatch ALL members in ONE message** (concurrent `Agent` calls) — that parallelism is the whole point.

If the envelope carries a non-empty `deferred` list, those are eligible members the wave cap (`CONDUCTOR_WAVE_SIZE`, default 2) pushed past this wave — announce each before fanning out: `⚠️ Wave cap reached; deferring <P{p}.T{t} …> to the next wave`. They stay `pending`; the next `dispatch-wave` picks them up. **The cap is never silent** (no-silent-caps).

Per member, dispatch `conductor:task-executor` pasting the member's **pre-assembled `prompt` field verbatim** (built by `_wave_assemble_member_prompt` — the same builder the wave-step spine uses). It carries the worktree pinning (`cd "{worktree}"` first line + `WORKTREE_DIR`) and a worktree-scoped `TRACK_DIR`; `SUBTASK` is omitted (wave members are flat-only) — never re-type or extend the block by hand.

`TRACK_DIR` points into the worktree, so `track-state write-result "{TRACK_DIR}" ...` writes the worktree's own `result.json` — what `wave-finalize` reads. The agent otherwise behaves identically to serial (TDD, coverage, commits on its branch). It does NOT call dispatch-finalize — wave integration is the orchestrator's job (§4.0).

After ALL members return → **§4.0** (integrate each, in any order).

### 3.3 Serial fallback (action: `no_ready_tasks`)

No deps-declared file-disjoint pending task is ready. Make serial progress — a serial task may satisfy a dep that unlocks the next wave. **Read the `ineligible` list first** (one `{phase, task, name, reason}` per rejected pending task, `reason` ∈ `subtasked` | `non_executor` | `no_deps_comment` | `deps_unsatisfied`) and announce the blocker — do NOT silently fall to serial when the author intended parallelism:

- `subtasked` → the dominant case. A task with subtasks is **flat-only-excluded in v1**. Announce `"⚠️ P{p}.T{t} '{name}' has subtasks — v1 waves are flat-only. Flatten + add <!-- deps: --> to parallelize."` (plan-format-contract.md §8).
- `no_deps_comment` → the opt-in comment is missing/malformed (e.g. `<deps m.n>` instead of `<!-- deps: -->`). Announce the offending task.
- `deps_unsatisfied` / `non_executor` → expected (dep not met, or a non-executor route — manual/explore). No announcement unless every candidate is blocked.

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

`wave-finalize` reads the member's worktree `result.json`, squash-merges its branch onto the track branch as ONE code commit (conflict → FAILURE with `SPEC_DEVIATION`), runs the SUCCESS/FAILURE finalize transition on the real `track-state.json`, and tears down that member's worktree + branch. Emits `status`, `committed`, `member_status`, `drained`.

Route by `status`:

**SUCCESS** (`member_status: finalized`): announce tersely. `deviations > 0` → announce. `phase_checkpoint_pending` present → note it (handle at the phase boundary, §4.2). `committed: false` → announce `"wave-finalize conductor commit failed, result.json preserved"` and re-run for that member (max 3 attempts, then HALT `"wave-finalize stuck"`).

**FAILURE** (`member_status: failed` or `conflict`): the member is left `failed`. **v1 does NOT retry within the wave.** After the wave drains it's handled by the serial spine's normal retry/skip/block path (§4.1). Announce which member failed and why (`member_status: conflict` → file-overlap despite declared deps).

After each `wave-finalize`, check `drained`: `false` → integrate next member; `true` → **§4.1**.

### 4.1 Wave drained

Announce the wave summary (`N completed / M failed`). If any members FAILED → `wave-finalize` already reported which (`member_status: failed`/`conflict`). A terminal `failed` task is **invisible to `dispatch-next`** (it returns only `in_progress`/`pending`), so looping back strands the member and the track eventually finalizes `status:"failed"` with no decision. Instead run `track-state recover "<track_dir>"` (the FAILURE transition left the current indices pointing at the failed member) and relay what it emits. Under the recovery router (`recovery_policy`), an exhausted failure auto-routes to `skip-analyst` (`auto`, or continuous); only `ask`+interactive surfaces a Retry/Skip/Block `ask` — handle it per `implement` §2.2. The `skip-analyst`→`failure-analyst` path may itself resolve in a `replan` amendment `ask` (Apply amendment runs `track-state amend-apply`) — so a failed member can be re-amended and retried, not only skipped/blocked. Resolve every failed member before re-checking for the next wave. Then → **§4.15** (cross-member seam check).

### 4.15 Cross-Member Integration Review (completeness-critic)

Wave isolation gives each member a blind spot: the **seams** where its output meets another member's — a consumer read a producer's shape that the producer changed, two members touched a shared type/config, a declared `<!-- deps: -->` boundary drifted. No individual member can see these; only the integrated whole can. Review the seams once after the wave drains.

**Decide applicability:** skip (announce nothing) when **fewer than 2** members reached `member_status: finalized` this wave — a single-member wave has no seams. Otherwise dispatch `conductor:code-reviewer` (read-only) over the integrated range:

```
TRACK_DIR={td}
TRACK_ID={id}
REVISION_RANGE={base_sha}..HEAD
SCOPE=cross-member interaction defects at deps boundaries only
```

`SCOPE` narrows the pass to defects a member *could not* have seen alone. Decide from the `---REVIEW RESULT---` block (substring-check severities):

- **Zero `Critical`/`High`** → announce `"🔍 Seam review: clean"` → **§4.2**.
- **`Critical`/`High` present** → integration defects the wave *created*. **Refute first** (below) to strip single-reviewer misreads, then surface survivors via the human gate.

**Seam refute (before the human gate).** Write the Critical/High findings to `{td}/.conductor/seam-findings.json` (list of `{severity,title,file,lines,suggestion}`), then dispatch `conductor:refuter` to re-examine each against the integrated working tree:

```
PROJECT_DIR={project_root}
DOMAIN=seam
CLAIM=The findings in {td}/.conductor/seam-findings.json are real cross-member integration defects. Re-open each against the integrated code and drop any that does not hold up.
CONTEXT_PATHS={td}/.conductor/seam-findings.json {the member source files cited in the findings}
```

CLAIM framed as "the findings are real" → refuter's default `SUSTAINED` = keep-when-uncertain (a possible defect is surfaced, not silently dropped); `REFUTED` = grounded evidence of a misread (reviewer misread the interaction once both members are read together, or citation stale against the merge). Dedup survivors by signature (`severity+title+file+lines`). Then:

- **Survivors remain** → surface via `AskUserQuestion`: **fix-now** (dispatch `conductor:task-executor` against the offending member's seam on the main branch) / **accept-with-debt** (note in wave summary, proceed) / **block** (HALT). Resolve per choice → **§4.2**.
- **No survivors** → announce `"🔍 Seam review: <N> findings → all refuted on re-examination"` → **§4.2** (refuted count announced, not hidden).
- **STATUS: FAILURE** → keep all original findings, route to `AskUserQuestion` as-is (don't drop findings a crashed backup didn't vet).

Orchestration over existing `code-reviewer` + `task-executor` + `refuter` agents — no new agent/hook. Once per drained multi-member wave, not per member.

### 4.2 Phase boundary

If `phase_checkpoint_pending` was emitted by any `wave-finalize`, or after a wave drains with no failed members and no further wave/serial work in the phase:

```bash
track-state phase-done "<track_dir>" <phase>
```

`complete=true` → run the phase-checkpoint fan-out+synthesize (`implement` §3.2: fan the `verifier_wave` members' `prompt` fields verbatim, transcribe via `phase-verdict`, then dispatch `conductor:phase-checker` pasting its emitted `prompt`), then → **§3.1**. FAILED → HALT. `complete=false` (or `complete=true` without `checkpoint_due`) → **§3.1**.

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

Read `${CLAUDE_PLUGIN_ROOT}/templates/post-loop.md` and execute sections 5.0–8.0 (doc-sync, review, etc.). Same lock discipline as `implement`: never stop between a review and its reviewed-range stamp (a harness compaction mid-transaction loses the review-done signal and forces an expensive re-review).

---

## 6.0 RECOVERY — a wedged wave

If a wave is left in a bad state (stranded worktrees, corrupt ledger, or you need to abandon in-flight work):

```bash
track-state wave-abort "<track_dir>"
```

Resets in-flight members to `pending` (preserving their retry history), tears down their worktrees + branches, and deletes the ledger. Finalized/failed members keep their applied terminal status. After abort → **§2.0** (the serial spine re-dispatches the reset members normally).