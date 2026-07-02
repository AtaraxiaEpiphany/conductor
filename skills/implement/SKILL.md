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
          command: "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/on-stop-conductor.py\""
          timeout: 10
---

# Conductor Implement — Thin Orchestrator

## ORCHESTRATOR CONTRACT

You are a **thin state machine** that routes between subagents. Context budget is precious.

1. **NEVER read `spec.md` or `plan.md`** — subagents self-load all business context.
2. **Parse only the compact envelope's emitted fields** from track-state outputs. The dispatch commands (`next`, `recover`, `dispatch-next/prepare/finalize`) emit a **compact envelope by default** — the per-command allowlist in `scripts/track_state/helpers.py` (`COMPACT_FIELDS`) is the single source of truth for exactly which fields each command emits. Pass `--full` only to debug a raw envelope; the compact default is the contract.
3. **Keep dispatch prompts minimal** — task identity + file paths only (~100 tokens).
4. **Announce actions tersely** — one line per action, no narrative.
5. **Yield cleanly when context runs low.** The dispatch loop is long-running; if your context budget is running low (heuristic: ~6+ subagent dispatches this session, or you sense compaction approaching), first finish the in-flight task to a terminal, committed state, then stop with exactly:
   `"⏸️ Conductor checkpoint at P{phase}.T{task} — state committed. Re-invoke /conductor:implement to resume (recover picks up here)."`
   **NEVER stop between `dispatch-prepare` and `dispatch-finalize`** — that abandons a stale `[~]` lock the next run's `recover` must reap (and the Stop hook will flag it). Yield only at a clean task boundary: after `dispatch-finalize` succeeds, after a phase boundary, or at a genuine HALT.

6. **Yield cleanly mid-post-loop too.** The post-loop (§4.0–§8.0) is also long-running. If context runs low mid-post-loop, yield at a phase boundary — after the §5.5 finalize commit, after the §6.0 doc-sync commit, or after §7.0 review + reviewed-range stamp — with exactly:
   `"⏸️ Conductor checkpoint in post-loop (after §X) — state committed. Re-invoke /conductor:implement to resume (post-loop-status skips completed phases)."`
   **NEVER stop between `code-reviewer` returning and the `.conductor/post-loop.json` reviewed-range stamp** — that loses the review-done signal and forces an expensive re-review. Re-entry is automatic: `dispatch-next` re-emits `action=finalize` → §4.0 re-enters the post-loop, and `post-loop-status` gates skip what already ran.

Dispatch loop: `RECOVER → DISPATCH → PROCESS → PHASE_BOUNDARY → (repeat) → FINALIZE`

Tag inheritance: subtasks inherit dispatch tags from parent when subtask name has none.

---

## 1.0 SETUP + TRACK SELECTION

1. Locate track from `conductor/tracks.md` — resolve `$ARGUMENTS` or auto-select `[~]`/`[ ]`.
2. Run `track-state preflight "<track_dir>"`. If `ok: false` (missing `spec.md`/`plan.md`/`track-state.json`, unreadable state, **or missing `conductor/workflow/index.md`/`post-loop.md`** — reported in `missing_workflow`) → `"Conductor environment incomplete. Run /conductor:setup."` → HALT.
3. (Belt-and-suspenders) `conductor/workflow/index.md` is already gated by preflight step 2; if it is somehow still missing here → same halt message → HALT.
4. `track-state recover "<track_dir>"` — if error → HALT.
5. If `status == "new"` → `track-state start` + `registry-update` + commit.

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
| `pending` + retry_count > 0 | Re-dispatch (retry). Pass `ATTEMPT={retry_count+1}` `MAX_RETRIES={m}` to task-executor (it self-detects retry from the handoff — no retry flag needed). |
| `failed` + retry < max | Re-dispatch. |
| `failed` + retry >= max | **Interactive**: surface to the user via `AskUserQuestion` — Retry / Skip / Block (see §2.2). **Continuous**: dispatch `conductor:skip-analyst`. |
| `blocked` | Report → HALT. |
| `completed`/`skipped`/`no_active_task` | Check `phase_checkpoint_pending`. If set → dispatch `conductor:phase-checker`. Otherwise → **Section 3.0**. |

Store `execution_mode` from recover output. Default `"interactive"`.
If state changed → commit: `chore(conductor): Fix state consistency after recovery`

### 2.1 Resume Phase Checkpoint

If recover output contains `phase_checkpoint_pending: <phase_index>`:
- Dispatch `conductor:phase-checker` (§3.2), `PHASE=<phase_index>`
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

The phase checkpoint is a **fan-out-and-synthesize**: two read-only verifier tiers run in parallel, then `conductor:phase-checker` (the synthesizer) consumes their verdicts and owns the L1 fix-and-retry + L2 + L4 + commit.

**Step 1 — Fan out the verifiers.** Dispatch BOTH in ONE message (parallel):

- `conductor:ac-tracer` — prompt: `TRACK_DIR={td} TRACK_ID={id}`
- `conductor:test-runner` — prompt: `TRACK_DIR={td} TRACK_ID={id} PHASE_INDEX={phase}`

**Step 2 — Parse the fleet's result blocks.** From `ac-tracer`'s `---AC TRACE RESULT---`: `VERDICT` (passed/warn/skipped/FAILED/ERROR), `GATE` (when FAILED), `N_UNGROUNDED` (when warn). From `test-runner`'s `---L1 VERIFY RESULT---`: `STATUS` (passed/failed/error), `COMMAND`.

**Step 3 — Dispatch the synthesizer** `conductor:phase-checker` (canonical dispatch — §2.1, §3.5b, §3.7 reuse this fan-out+synthesize; only the `PHASE` value source differs), passing the fleet's verdicts through:

```
TRACK_DIR={td}
TRACK_ID={id}
PHASE_INDEX={phase from output}
EXECUTION_MODE={interactive|continuous}
AC_TRACE_VERDICT=<ac-tracer VERDICT>
AC_TRACE_GATE=<ac-tracer GATE — include only when VERDICT is FAILED>
AC_TRACE_N_UNGROUNDED=<ac-tracer N_UNGROUNDED — include only when VERDICT is warn>
L1_VERIFY_STATUS=<test-runner STATUS>
L1_VERIFY_COMMAND=<test-runner COMMAND>
```

After return → **Section 3.6** (Phase Boundary).

### 3.3 Action: `dispatch_explorer`

```bash
track-state dispatch-prepare "<track_dir>"
# Only commit start if commit_msg is present (null on resume — avoids duplicate start commits)
if commit_msg: git add -A && git diff --cached --quiet || git commit -m "<commit_msg>"
```

Dispatch `conductor:explorer`, prompt:

```
TRACK_DIR={td}
PHASE={p}
TASK={t}
SUBTASK={s}
NAME={name}
```

After return → `track-state dispatch-finalize "<track_dir>"` → **Section 3.7**.

The explorer records findings via `track-state append-handoff` (→ `.conductor/handoff/`, the sanctioned channel) and writes `.conductor/result.json` (gitignored). Both are conductor-managed, so `dispatch-finalize`'s internal conductor commit stages them — **no separate `docs(explore)` commit, no `git add -A` sweep, no `--override commit_sha`**. The explorer's result ships `commit_sha: ""`; `dispatch-finalize` stores the conductor completion SHA for empty-sha explorer results. (This also kills the result.json history-churn bug: the transient file is no longer swept into a commit.)

### 3.4 Action: `dispatch_executor`

```bash
track-state dispatch-prepare "<track_dir>"
# Only commit start if commit_msg is present (null on resume — avoids duplicate start commits)
if commit_msg: git add -A && git diff --cached --quiet || git commit -m "<commit_msg>"
```

Dispatch `conductor:task-executor`, prompt (canonical dispatch — retry re-dispatches in §2.2 and §3.6 reuse this with an incremented `ATTEMPT`; retry status is self-detected from the handoff, not a flag):

```
TRACK_DIR={td}
PHASE={p}
TASK={t}
SUBTASK={s}
NAME={name}
ATTEMPT={n}
MAX_RETRIES={m}
```

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

If found → dispatch `conductor:phase-checker` (§3.2), `PHASE=<phase from output>`, then → **Section 3.1**.

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

**SUCCESS**: `committed: false` → announce `"conductor commit failed, result.json preserved"` → re-run `dispatch-finalize` (max 3 attempts, then HALT with `"dispatch-finalize stuck"`). Deviations > 0 → announce. If `phase_checkpoint_pending` present → dispatch `conductor:phase-checker` immediately. Otherwise → **Section 3.6b** (self-review, if the task opted in) → **Section 3.7**.

**FAILURE**: retry < max → re-dispatch (Section 3.1). retry >= max → dispatch `conductor:skip-analyst`, prompt:

```
TRACK_DIR={td}
TRACK_ID={id}
PHASE_INDEX={p}
TASK_INDEX={t}
TASK_NAME={name}
```

Skip-analyst result — parse the `---SKIP ANALYSIS---` JSON and act by `recommendation`:

- **`recommendation: skip`** (`can_skip: true`) → **run the skip refute first** (below). If the refute lets the skip stand → `track-state skip "<track_dir>" <phase> <task>` → Section 3.1. If the refute overrides → handle as `pause_and_escalate`.
- **`recommendation: pause_and_escalate`** (or skip-refute override) → `track-state sync-plan "<track_dir>"` → commit → **HALT**: surface `impact` + `reasoning` (and the refuter's `EVIDENCE`/`REASONING` if it overrode). An unattended continuous track stops for human judgment rather than silently skipping or blocking.
- **`recommendation: retry_with_modification`** → `track-state sync-plan` → commit → HALT with the reasoning as the modification guidance for the next attempt.

**Skip refute (continuous mode only).** `§2.2`'s interactive path already has a human gate; this refute runs only on the unattended continuous path, where a wrong skip silently cascades a hole into downstream work. When `recommendation == skip`, dispatch `conductor:refuter` to challenge it before acting:

```
PROJECT_DIR={project_root}
DOMAIN=skip
CLAIM=Skip-analyst recommended skipping task P{p}T{t} ("{name}"), reasoning: "{skip-analyst reasoning}". Challenge framing: this skip is UNSAFE — a dependency marked completed is only superficially done (its own ACs not actually met), or the failure handoff describes a fix cheap relative to the cost of skipping.
CONTEXT_PATHS={td}/plan.md {td}/track-state.json {td}/.conductor/handoff/P{p}T{t}.md
```

> The CLAIM is framed as "the skip is unsafe" deliberately. The refuter defaults to `SUSTAINED` when uncertain, so `SUSTAINED` = block-when-uncertain — the conservative direction for a skip, because skipping is the riskier action and uncertainty must fall toward *not* skipping. (`new-track` §2.3b frames its CLAIM the opposite way — "the plan is sound" — because a plan gate should proceed-when-uncertain, not block.) `REFUTED` = grounded evidence the skip IS safe.

Parse the `---REFUTATION RESULT---` block:

- **`STATUS: SUSTAINED`** (skip unsafe — default when uncertain) → **override to block**: handle as `pause_and_escalate` (sync-plan → commit → HALT with the refuter's evidence). The refute found grounded evidence the skip breaks something; do not skip.
- **`STATUS: REFUTED`** (grounded evidence the skip is safe) → let the skip stand → `track-state skip` → Section 3.1.
- **`STATUS: FAILURE`** → defer to skip-analyst's primary verdict: announce `"⚠️ skip refute could not complete — proceeding on skip-analyst's recommendation"` and let the skip stand. A backup-agent crash is not new evidence the skip is safe; the announce keeps it visible without halting the track on a backup failure.

### 3.6b Self-Review Loop (opt-in — "Ralph Wiggum")

**DEFAULT OFF.** Runs ONLY when the just-completed task opts in — zero latency otherwise:
- the task NAME contains the marker `[Review]` (per-task opt-in), OR
- env `CONDUCTOR_SELF_REVIEW=1` (global opt-in for every task this session).

`[Review]` is a **name marker, not a tag** — it does NOT enter the `[Docs]`/`[Config]`/… exemption logic, so a reviewable task still owes TDD (F2) and coverage (F3).

When opted in (after a SUCCESSFUL `dispatch-finalize`, before §3.7), run a **convergent review loop** — review own changes → fix → re-review — that stops on a *dry* round, not a fixed count (loop-until-dry). A single self-certifying pass is exactly the self-preferential bias this loop exists to cure; convergence drives it to zero NEW Critical/High instead of declaring victory after one pass.

Maintain a `seen` set of finding **signatures** (`severity+title+file+lines`). Dedup **new** findings vs `seen` (NOT vs the set you just fixed) — a finding that re-appears unchanged after a fix is a *residual*, counted separately, not "new".

1. **Reviewer pass** — dispatch `conductor:code-reviewer` (read-only) on the task's own commit range `<task_sha>~1..<task_sha>`, prompt:

   ```
   TRACK_DIR={td}
   TRACK_ID={id}
   REVISION_RANGE={sha}~1..{sha}
   ```
2. **Decide from the `---REVIEW RESULT---` block** (substring-check the severities), counting only NEW `Critical`/`High` (signatures not already in `seen`):
   - **Zero NEW `Critical`/`High`** — a dry round (K=1 empty pass) → loop satisfied → announce `"🔍 Self-review [Review]: clean"` → §3.7.
   - **NEW `Critical`/`High` present** → add their signatures to `seen`; re-dispatch `conductor:task-executor` with `ATTEMPT={n+1}` and the NEW findings as remediation context (the agent fixes its own changes), `dispatch-finalize` again, then loop back to step 1.
3. **Budget guard — max 3 fix iterations.** No runaway loop. If still not dry after 3 fix iterations, stop iterating and announce: `"🔍 Self-review [Review]: {N} findings → 3 fix iterations → {M} residual"`.
4. **Escalate on residual judgment only** — if `Critical` findings persist once the budget is spent (or at any dry stop that still leaves residual Critical), surface them via `AskUserQuestion` (fix-guidance / accept-with-debt / block). Medium/Low residual → note and proceed (do not block the loop on nits).

This loop is orchestration over the existing `code-reviewer` + `task-executor` agents — no new agent, no new hook.

### 3.7 Phase Boundary

```bash
track-state phase-done "<track_dir>" <phase>
```

`complete=true` → dispatch `conductor:phase-checker` (§3.2), `PHASE=<phase>`. FAILED → HALT (surface `FAILURE_REASON`; an AC-trace authoring defect requires editing `spec.md`/`plan.md` then re-running the phase — not a `task-executor` retry). Otherwise → Section 3.1.
`complete=false` → Section 3.1.

### 3.8 Action: `finalize`

→ **Section 4.0**.

---

## 4.0 POST-LOOP

Run `track-state post-loop-status "<track_dir>"` and keep the envelope (`finalized`, `doc_synced`, `review.done`/`review.range`, `shas_count`). §5.5/§6.0/§7.0 gate on it to skip phases already completed across a context-budget interruption. (If you resume the post-loop after a compaction without the envelope, re-run it — it's a cheap git-log grep + state load.)

Read `conductor/workflow/post-loop.md` and execute sections 5.0–8.0.

---