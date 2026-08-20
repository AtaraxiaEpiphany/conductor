---
name: implement
description: Execute a planned track task-by-task — dispatches each task to a subagent, tracks results and retries through track-state.json, and runs the loop to archive
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

1. **NEVER read `spec.md` or `plan.md`** — subagents self-load all business context (Tier C of the three-tier context model, `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/context-model.md`).
2. **Parse only the compact envelope** track-state emits by default (the per-command field allowlist is `COMPACT_FIELDS` in `scripts/track_state/helpers.py`). Pass `--full` only to debug.
3. **Keep dispatch prompts minimal** — task identity + file paths only (~100 tokens).
4. **Announce actions tersely** — one line per action, no narrative.
5. **Never abandon a mid-transaction state machine.** A harness compaction can pause you mid-transaction — never stop between `dispatch-prepare` and `dispatch-finalize` (abandons a stale `[~]` lock the next `recover` must reap), nor between `code-reviewer` returning and the `.conductor/post-loop.json` reviewed-range stamp (loses the review-done signal → expensive re-review). Re-entry is automatic: `recover` reaps stale locks, `dispatch-next` re-emits `action=finalize`, `post-loop-status` gates skip what already ran.

Dispatch loop: `RECOVER → DISPATCH → PROCESS → PHASE_BOUNDARY → (repeat) → FINALIZE`. Tag inheritance: subtasks inherit dispatch tags from parent when the subtask name has none.

---

## 1.0 SETUP + TRACK SELECTION

1. `track-state check "$ARGUMENTS"` — always exits 0; outcome is in `action`:
   - `proceed` → `<td>` = `td`; **print `announce`**; continue to step 2.
   - `ask` → `AskUserQuestion` over `candidates` (label = `track_id`), then re-run `track-state check "<chosen track_id>"`.
   - `halt` → print `message`; HALT.
2. `track-state recover "<track_dir>"` — if error → HALT.
3. `track-state start "<track_dir>"`.

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
| `failed` + retry >= max | **Auto-route** (`recovery_policy=auto`, or continuous): dispatch `conductor:skip-analyst`. **Ask** (`recovery_policy=ask` + interactive): surface to the user via `AskUserQuestion` — Retry / Skip / Block (see §2.2). |
| `blocked` | Report → HALT. |
| `completed`/`skipped`/`no_active_task` | Check `phase_checkpoint_pending`. If set → dispatch `conductor:phase-checker`. Otherwise → **Section 3.0**. |

Store `execution_mode` from recover output. Default `"interactive"`.
If state changed → commit: `chore(conductor): Fix state consistency after recovery`

### 2.1 Resume Phase Checkpoint

If recover output contains `phase_checkpoint_pending: <phase_index>`: dispatch `conductor:phase-checker` (§3.2), `PHASE=<phase_index>`, then → **§3.7**.

### 2.2 Failed Task Decision (interactive only)

When `recover` surfaces a `failed` task whose retries are exhausted AND `execution_mode == "interactive"`, the recover envelope carries a pre-computed `decision` blob. Act as a pure transducer — do NOT judge retry-exhaustion or construct commands:

1. `AskUserQuestion(decision.question, decision.header, decision.options)` → user picks Retry / Skip / Block.
2. Run `decision.commands[<chosen label>]` **verbatim** — each is one shell-safe line (task name already `shlex`-quoted; do not edit or re-quote).
3. Go to `decision.next[<chosen label>]` (`3.1` for Retry/Skip, `HALT` for Block).

**Continuous** (no `decision` blob): dispatch `conductor:skip-analyst` (§3.6). A parent failed via the parent-stuck path surfaces the same blob — `reset task` clears the parent **and** its subtasks for a full retry.

---

## 3.0 DISPATCH LOOP

### 3.1 Get Next Action

```bash
track-state dispatch-next "<track_dir>"
```

Returns `action` enum — switch on it:

### 3.2 Action: `dispatch_phase_checker`

The phase checkpoint is a **fan-out-and-synthesize**: read-only verifier tiers run in parallel, then `conductor:phase-checker` consumes their verdicts (cheapest-first graduated gate) and owns the L1 fix-and-retry + L2 + L4 + commit. Three verifiers fan out every code-phase checkpoint: `conductor:build-runner` (L0 compile/build — the floor), `conductor:test-runner` (L1 suite), and `conductor:ac-tracer` (the AC-evidence trace). `build-runner` is the fail-fast floor — a compile failure fails the checkpoint before the test tier is spent.

**Step 1 — Fan out the verifiers in ONE message:** `conductor:build-runner`, `conductor:ac-tracer`, and `conductor:test-runner`, pasting each member's **pre-assembled `prompt` field verbatim** (built by `build_dispatch_prompt` — the single source both rails share; do NOT hand-interpolate `TRACK_DIR`/`TRACK_ID`/`PHASE_INDEX`). On a **code-free phase** (every task resolves `coverage_exempt`; the code-free set is registry-derived, not a literal tag list — `track-state registry-doc`), the wave omits `build-runner` and `test-runner` (nothing to compile or run) and **only `ac-tracer` fans out** — see Step 3 for how that surfaces in the synth.

**Step 2 — Parse the result blocks.** From `build-runner`'s `---BUILD VERIFY RESULT---`: `STATUS`/`COMMAND` (the compile verdict — `passed`/`failed`/`error`; `error` = no build command resolvable, e.g. an interpreted language, NON-BLOCKING). From `ac-tracer`'s `---AC TRACE RESULT---`: `VERDICT` (passed/warn/skipped/FAILED/ERROR), `GATE` (when FAILED), `N_UNGROUNDED` (when warn). From `test-runner`'s `---L1 VERIFY RESULT---`: `STATUS`/`COMMAND` (the suite verdict). On a code-free phase there are no `build-runner` or `test-runner` result blocks — record both as `skipped` in Step 3.

**Step 3 — Transcribe the verdicts, then dispatch `conductor:phase-checker`.** Run `track-state phase-verdict "<td>" --ac-verdict <V> [--ac-gate <G>] [--ac-n-ungrounded <N>] --build-status <S> [--build-command "<CMD>"] --l1-status <S> [--l1-command "<CMD>"]` — on a code-free phase pass `--build-status "skipped (no code-producing tasks)"` and `--l1-status "skipped (no code-producing tasks)"` and omit both `--*-command`. The command's output carries the synthesizer dispatch envelope: **dispatch `conductor:phase-checker` pasting the emitted `prompt` field verbatim** (built by `build_dispatch_prompt` from the just-transcribed verdicts — the same builder the step spine uses; do NOT hand-write the `KEY=value` block). Then → **Section 3.6** (Phase Boundary).

### 3.3 Action: `dispatch_explorer`

```bash
track-state dispatch-prepare "<track_dir>"   # makes the "Start task" commit internally (skipped on resume)
```

Dispatch `conductor:explorer`, pasting the envelope's **pre-assembled `prompt` field verbatim** (built by `build_dispatch_prompt` — the single source both rails share; do NOT hand-interpolate the `KEY=value` lines). The `agent` field tells you which agent (`explorer` for explore-classified tasks).

After return → `track-state dispatch-finalize "<track_dir>"` → **Section 3.7**. The explorer records findings via `track-state append-handoff` and writes gitignored `.conductor/result.json` — both conductor-managed, so `dispatch-finalize`'s internal commit stages them. **No separate `docs(explore)` commit, no `git add -A` sweep, no `--override commit_sha`** — ship `commit_sha: ""`.

### 3.4 Action: `dispatch_executor`

```bash
track-state dispatch-prepare "<track_dir>"   # makes the "Start task" commit internally (skipped on resume)
```

Dispatch `conductor:task-executor` (canonical dispatch — §2.2 / §3.6 retry re-dispatches reuse this with incremented `ATTEMPT`; retry status self-detected from the handoff, not a flag), pasting the envelope's **pre-assembled `prompt` field verbatim** (built by `build_dispatch_prompt` — the single source both rails share; `ATTEMPT`/`MAX_RETRIES` are already resolved from the task's real retry_count, do NOT hand-interpolate the `KEY=value` lines). The `agent` field tells you which agent.

After return → **Section 3.6**.

### 3.5 Action: `parent_stuck`

Parent has failed subtasks (retries exhausted) and no other work remains. `dispatch-next` marks it **failed** (renders `[!]`, not `[x]`) and commits. Announce: `"⚠️ Parent '{name}' marked failed — subtasks exhausted retries (P{phase}.T{task}). Next run's recover surfaces it for a Retry/Skip/Block (§2.2)."`. Then `track-state sync-plan "<track_dir>"` → **§3.7**.

### 3.5b Action: `defer_manual`

```bash
track-state defer "<track_dir>" --phase <p> --task <t> --reason 'Deferred: manual task requires human verification'
track-state sync-plan "<track_dir>"
git commit -m "chore(conductor): Defer manual task '<name>'"
```

Check the output of ALL three commands (especially `defer` and `sync-plan`) for `phase_checkpoint_pending` / `next_action: dispatch_phase_checker`. Found → dispatch `conductor:phase-checker` (§3.2, `PHASE=<phase>`), then → **§3.1**. Not found → **§3.7**.

### 3.5c Action: `manual_task`

(Interactive only — continuous mode emits `defer_manual`, §3.5b.) A `[Manual]` task requires human verification, so surface it rather than silently defer. `AskUserQuestion` defer-or-skip, then run the matching command:

- **Defer** → `track-state defer "<track_dir>" --phase <p> --task <t> --reason 'Deferred: manual task requires human verification'`
- **Skip** → `track-state skip "<track_dir>" --phase <p> --task <t> --reason 'Skipped: manual task not required'`

Then `track-state sync-plan "<track_dir>"` → `git commit -m "chore(conductor): {Defer|Skip} manual task '<name>'"` → **§3.1**.

### 3.6 Process Result (after task-executor)

**ALWAYS** call `dispatch-finalize` after the task-executor returns — even with no/incomplete result block (it synthesizes a result from state: committed code → SUCCESS; nothing → FAILURE with retry handoff). `dispatch-finalize` creates the conductor commit internally — **do NOT commit separately**.

> **A missing `---TASK RESULT---` block is not a signal to do the work yourself.** It means task-executor exhausted turns (e.g. a hard task hit the §7.0 tripwire). `dispatch-finalize` synthesizes a result from git state and the retry/skip queue takes over. **Never read `spec.md`/`plan.md`/source to compensate** — enforced by the `on-orchestrator-read-guard` hook, which denies those reads while a task is in flight.

```bash
track-state dispatch-finalize "<track_dir>"
```

**SUCCESS**: `committed: false` → announce `"conductor commit failed, result.json preserved"` → re-run `dispatch-finalize` (max 3 attempts, then HALT with `"dispatch-finalize stuck"`). Deviations > 0 → announce. If `phase_checkpoint_pending` present → dispatch `conductor:phase-checker` immediately. Otherwise, when the envelope carries the opt-in follow-ups, run them in order — `self_review` (§3.6b) → `refactor` (§3.6c) — then **§3.7**. Neither key present → straight to **§3.7** (zero latency when opted out).

**FAILURE**: the finalize envelope carries `next_action` (+ `agent` + pre-assembled `prompt` when the next step is an agent dispatch):
- `next_action: "dispatch_executor"` (retry remains, not penultimate) → re-dispatch via **§3.1** (dispatch-prepare → paste its `prompt`).
- `next_action: "dispatch_failure_analyst"` (exactly one attempt remains on an auto-routing track) → dispatch `conductor:failure-analyst` pasting the envelope's `prompt` field verbatim — the final attempt is a *modified* retry, not another identical one.
- `next_action: "dispatch_skip_analyst"` (retries exhausted, auto-routing track) → dispatch `conductor:skip-analyst` pasting the envelope's `prompt` field verbatim.
- `next_action: "ask"` (retries exhausted, ask-surface track) → §2.2's Retry/Skip/Block decision (next `recover` surfaces it).

Skip-analyst result — parse the `---SKIP ANALYSIS---` JSON and act by `recommendation`:

- **`recommendation: skip`** (`can_skip: true`) → **run the skip refute first**: transcribe via `track-state skip-analyst-verdict "<td>" --recommendation skip --reasoning "<text>" --impact "<text>" --can-skip <true|false>` — its output carries `next_action: "dispatch_refuter"` with the refuter's `prompt` pre-assembled (the CLAIM embeds skip-analyst's reasoning verbatim, assembled in code). Dispatch `conductor:refuter` pasting that `prompt` verbatim. If the refute lets the skip stand → `track-state skip "<track_dir>" <phase> <task>` → Section 3.1. If the refute overrides → handle as `pause_and_escalate`.
- **`recommendation: pause_and_escalate`** (or skip-refute override) → `track-state sync-plan "<track_dir>"` → commit → **HALT**: surface `impact` + `reasoning` (and the refuter's `EVIDENCE`/`REASONING` if it overrode). An unattended continuous track stops for human judgment rather than silently skipping or blocking.
- **`recommendation: retry_with_modification`** → the skip-analyst says "fixable, just not by skipping." Transcribe via `skip-analyst-verdict`: on an auto-routing track its output carries `next_action: "dispatch_failure_analyst"` with the failure-analyst `prompt` pre-assembled — dispatch it (see the failure-analyst block below); its `retry_modified` verdict re-dispatches task-executor with a modified approach. In **interactive mode** (human in the loop), `track-state sync-plan` → commit → HALT with the reasoning as modification guidance.

**Skip refute (continuous mode only).** `§2.2`'s interactive path already has a human gate; this refute runs only on the unattended continuous path, where a wrong skip silently cascades a hole into downstream work. The refuter's challenge framing (why the CLAIM asserts "the skip is UNSAFE", and why `SUSTAINED` = block-when-uncertain) is single-homed in `agents/refuter.md` — the prompt you paste is assembled by `skip-analyst-verdict`, never re-derived here.

Parse the `---REFUTATION RESULT---` block:

- **`STATUS: SUSTAINED`** (skip unsafe — default when uncertain) → **override to block**: handle as `pause_and_escalate` (sync-plan → commit → HALT with the refuter's evidence). The refute found grounded evidence the skip breaks something; do not skip.
- **`STATUS: REFUTED`** (grounded evidence the skip is safe) → let the skip stand → `track-state skip` → Section 3.1.
- **`STATUS: FAILURE`** → defer to skip-analyst's primary verdict: announce `"⚠️ skip refute could not complete — proceeding on skip-analyst's recommendation"` and let the skip stand. A backup-agent crash is not new evidence the skip is safe; the announce keeps it visible without halting the track on a backup failure.

**Failure-analyst (continuous mode only).** A read-only diagnostic that answers *why* a task keeps failing before spending the retry budget on another identical attempt. The spine dispatches it (a) when one attempt remains (`retry == max - 1` — the finalize envelope's `dispatch_failure_analyst` prompt) and (b) on a skip-analyst `retry_with_modification` hand-off (the `skip-analyst-verdict` envelope's `dispatch_failure_analyst` prompt). Dispatch `conductor:failure-analyst` **pasting that emitted `prompt` field verbatim** (`TRACK_DIR`/`TRACK_ID`/`PHASE_INDEX`/`TASK_INDEX`/`TASK_NAME`/`RETRY_COUNT`/`MAX_RETRIES` are resolved from live state — never re-type them).

Parse the `---FAILURE ANALYSIS---` JSON and act by `recommendation`:

- **`retry_modified`** (`category: deterministic_bug`) → `track-state failure-analyst-verdict "<td>" --category <C> --recommendation retry_modified --root-cause "<text>" --modification "<text>"`. The spine writes the modification to a guidance marker, reactives the failed task (retry budget preserved), and re-dispatches task-executor with the modification injected as a `[Conductor Modified Retry]` block. → §3.1.
- **`replan`** (`category: spec_plan_defect`) → the analyst returns `--root-cause`, `--ac-superseded <AC-N>` (the criterion the failure disproves), `--ac-prime-text "<corrected criterion>"`, and `--affected-tasks <P.T,…>`: `track-state failure-analyst-verdict "<td>" --category spec_plan_defect --recommendation replan --root-cause "<text>" --ac-superseded <AC-N> --ac-prime-text "<text>" [--affected-tasks "<list>"]`. With those AC details the spine stages an **in-place additive amendment** and emits an **`ask`** (not a halt): `Apply amendment` / `Edit manually` / `Halt`. `Apply amendment` runs `track-state amend-apply "<td>"` verbatim — it appends a `## Amendment N` to spec.md (the original AC line is kept untouched), reactivate the failed task, and injects a `[Conductor Amendment]` nudge on its re-dispatch; then resumes. This is the ONE human touchpoint in the recovery router — superseding an AC is a deliberate confirm, never silent. A `replan` WITHOUT the AC details, and **`escalate`** (`category: environmental|stuck`), HALT instead, surfacing `root_cause` + `modification` **plus a `recovery` recipe** — the safe manual path (the analyst cannot decide intent; act on `recovery` then re-invoke to resume). Additive-only + the confirm are the governing invariant — see `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/plan-amendment.md`.
- **`decompose`** (`category: context_budget`) → `track-state failure-analyst-verdict ... --recommendation decompose --modification "<proposed split>"` → the spine emits an **`ask`** (not a halt): `Apply split` / `Skip original only` / `Escalate`. `Apply split` runs `track-state split "<td>" <phase> <task> [<subtask>] --subtasks "a;b;c" --note "..."` verbatim — it skips the original (commit preserved — do NOT revert) and appends the pieces as pending subtasks, then resumes. **The original task's commit is preserved — do NOT revert it.** A modified retry that fails again re-triggers the analyst; the retry arm is bounded by loop-until-dry + a hard round budget (see `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/recovery-policy.md`) — it escalates once the analyst stops producing *novel* root causes or the budget is spent, so it never loops analyze→retry→fail forever.

### 3.6b Self-Review Loop (opt-in — "Ralph Wiggum")

**DEFAULT OFF.** Runs ONLY when the just-completed task opts in — zero latency otherwise:
- the task NAME contains the marker `[Review]` (per-task opt-in), OR
- env `CONDUCTOR_SELF_REVIEW=1` (global opt-in for every task this session).

`[Review]` is a **name marker, not a tag** — it does NOT enter the exemption logic (TDD/coverage exemption derives from a task's registry profile, not name markers), so a reviewable task still owes TDD (F2) and coverage (F3).

When opted in (after a SUCCESSFUL `dispatch-finalize`, before §3.7), run a **convergent review loop** (review → fix → re-review) that stops on a *dry* round (loop-until-dry — zero NEW Critical/High), not a fixed count. Maintain a `seen` set of finding **signatures** (`severity+title+file+lines`); dedup **new** findings vs `seen` (NOT vs the set you just fixed — a re-appearing finding is a *residual*, counted separately).

**Persist `seen` across compaction** (loop state a compaction would otherwise lose): at loop **entry**, load `.conductor/review-seen.json` (conductor-owned, gitignored) — if its `task_sha` matches this task, restore its `seen` (resuming a compacted loop); else start empty (never inherit another task's set). After each round that adds signatures, write it back as `{"task_sha": "<sha>", "seen": [...]}`. On **any terminal exit**, delete the file.

1. **Reviewer pass** — dispatch `conductor:code-reviewer` (read-only) pasting the finalize envelope's `self_review.prompt` field verbatim (`REVISION_RANGE` is resolved to this task's `code_sha~1..code_sha` — never re-type it):
2. **Decide from the `---REVIEW RESULT---` block** (substring-check severities), counting only NEW `Critical`/`High` (signatures not in `seen`):
   - **Zero NEW `Critical`/`High`** — dry round → announce `"🔍 Self-review [Review]: clean"` → delete `.conductor/review-seen.json` → §3.7.
   - **NEW `Critical`/`High`** → add signatures to `seen`, **write `review-seen.json` back**; re-dispatch `conductor:task-executor` with `ATTEMPT={n+1}` and the NEW findings as remediation (the agent fixes its own changes), `dispatch-finalize` again, loop to step 1.
3. **Budget guard — max 3 fix iterations.** If still not dry after 3 → announce `"🔍 Self-review [Review]: {N} findings → 3 fix iterations → {M} residual"` → delete `.conductor/review-seen.json` → step 4.
4. **Escalate on residual Critical only** — surface via `AskUserQuestion` (fix-guidance / accept-with-debt / block). Medium/Low residual → note and proceed.

### 3.6c Tactical Refactor (opt-in — orchestrator-dispatched)

**DEFAULT OFF.** Runs ONLY when the just-completed task opts in:
- the task's leading-tag profile has `refactor: true` (declarative — e.g. a `[Refactor]` task; resolve via `track-state registry-doc --tag <Tag>` or the `[Conductor Registry]` block's `refactor:` line), OR
- the task NAME contains the marker `[Refactor]` (per-task escape hatch — for a task whose leading tag is something else, e.g. `[Config] Refactor the env loader`), OR
- env `CONDUCTOR_TASK_REFACTOR=1` (global opt-in for every task this session).

`[Refactor]` is now BOTH a real tag (declarative — `refactor: true` on the tag row) AND a name marker (per-task escape hatch). Either form opts into this seam; neither enters the `[Docs]`/`[Config]`/… TDD/coverage-exemption logic, so a refactorable task still owes TDD (F2) and coverage (F3). (Tier rationale — mechanical Step 5 vs tactical refactorer — lives in `agents/refactorer.md` §1.0.)

When opted in, the finalize envelope carries `refactor` with the agent + pre-assembled prompt — dispatch `conductor:refactorer` pasting that `prompt` field verbatim (`REVISION_RANGE` resolves to the task's `code_sha~1..code_sha`).

Parse the `---REFACTOR RESULT---` block (non-blocking — the task already succeeded):
- **STATUS: SUCCESS** → announce `"🔨 [Refactor]: {REFACTORED} → {COMMITTED}"` → §3.7.
- **STATUS: FAILURE** → announce `"🔨 [Refactor]: failed (non-blocking) — {SUMMARY}"` → §3.7.

One bounded pass (no loop, no transient state). The refactorer runs the suite itself and self-reverts on regression — no separate green-confirm dispatch.

### 3.7 Phase Boundary

```bash
track-state phase-done "<track_dir>" <phase>
```

`complete=true` with `checkpoint_due: true` → fan out the `verifier_wave` members (§3.2 Step 1 — paste each member's `prompt` verbatim; the wave's code-free narrowing is already resolved), `PHASE=<phase>`. `complete=true` without `checkpoint_due` (checkpoint present or waived by shape) → Section 3.1. On the phase-checker: FAILED → HALT (surface `FAILURE_REASON`; an AC-trace authoring defect requires editing `spec.md`/`plan.md` then re-running the phase — not a `task-executor` retry). *(Rail A halts on a FAILED checkpoint. The step spine — `/conductor:implement-step` — instead routes a FAILED phase through the recovery router on an auto-routing track, so a long-running track finally succeeds; see `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/recovery-policy.md` § "Phase-level recovery".)* Otherwise → Section 3.1.
`complete=false` → Section 3.1.

### 3.8 Action: `finalize`

→ **Section 4.0**.

---

## 4.0 POST-LOOP

Run `track-state post-loop-status "<track_dir>"` and keep the envelope (`finalized`, `doc_synced`, `review.done`/`review.range`, `shas_count`). §5.5/§6.0/§7.0 gate on it to skip phases already completed across an interruption (if you resume without it, re-run — it's a cheap git-log grep + state load). Then read `${CLAUDE_PLUGIN_ROOT}/templates/post-loop.md` and execute sections 5.0–8.0.

---