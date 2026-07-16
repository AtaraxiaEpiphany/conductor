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
| `failed` + retry >= max | **Interactive**: surface to the user via `AskUserQuestion` — Retry / Skip / Block (see §2.2). **Continuous**: dispatch `conductor:skip-analyst`. |
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

The phase checkpoint is a **fan-out-and-synthesize**: two read-only verifier tiers run in parallel, then `conductor:phase-checker` consumes their verdicts and owns the L1 fix-and-retry + L2 + L4 + commit.

**Step 1 — Fan out BOTH verifiers in ONE message:** `conductor:ac-tracer` (`TRACK_DIR={td} TRACK_ID={id}`) and `conductor:test-runner` (`TRACK_DIR={td} TRACK_ID={id} PHASE_INDEX={phase}`).

**Step 2 — Parse the result blocks.** From `ac-tracer`'s `---AC TRACE RESULT---`: `VERDICT` (passed/warn/skipped/FAILED/ERROR), `GATE` (when FAILED), `N_UNGROUNDED` (when warn). From `test-runner`'s `---L1 VERIFY RESULT---`: `STATUS` (passed/failed/error), `COMMAND`.

**Step 3 — Dispatch `conductor:phase-checker`** (canonical dispatch — §2.1, §3.5b, §3.7 reuse this fan-out+synthesize; only the `PHASE` value source differs), passing the fleet's verdicts through:

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
track-state dispatch-prepare "<track_dir>"   # makes the "Start task" commit internally (skipped on resume)
```

Dispatch `conductor:explorer`, prompt:

```
TRACK_DIR={td}
PHASE={p}
TASK={t}
SUBTASK={s}
NAME={name}
```

After return → `track-state dispatch-finalize "<track_dir>"` → **Section 3.7**. The explorer records findings via `track-state append-handoff` and writes gitignored `.conductor/result.json` — both conductor-managed, so `dispatch-finalize`'s internal commit stages them. **No separate `docs(explore)` commit, no `git add -A` sweep, no `--override commit_sha`** — ship `commit_sha: ""`.

### 3.4 Action: `dispatch_executor`

```bash
track-state dispatch-prepare "<track_dir>"   # makes the "Start task" commit internally (skipped on resume)
```

Dispatch `conductor:task-executor` (canonical dispatch — §2.2 / §3.6 retry re-dispatches reuse this with incremented `ATTEMPT`; retry status self-detected from the handoff, not a flag):

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

```bash
track-state dispatch-finalize "<track_dir>"
```

**SUCCESS**: `committed: false` → announce `"conductor commit failed, result.json preserved"` → re-run `dispatch-finalize` (max 3 attempts, then HALT with `"dispatch-finalize stuck"`). Deviations > 0 → announce. If `phase_checkpoint_pending` present → dispatch `conductor:phase-checker` immediately. Otherwise → **Section 3.6b** (self-review, if `[Review]`) → **Section 3.6c** (refactor, if `[Refactor]`) → **§3.7**.

**FAILURE**: retry < max → re-dispatch (§3.1). In **continuous mode**, when exactly one attempt remains (`retry == max - 1`), the spine dispatches `conductor:failure-analyst` first so the final attempt is a *modified* retry rather than another identical one — see the failure-analyst block below. retry >= max → dispatch `conductor:skip-analyst`:

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
- **`recommendation: retry_with_modification`** → the skip-analyst says "fixable, just not by skipping." In **continuous mode**, dispatch `conductor:failure-analyst` (below) for a real diagnosis — its `retry_modified` verdict re-dispatches task-executor with a modified approach. In **interactive mode** (human in the loop), `track-state sync-plan` → commit → HALT with the reasoning as modification guidance.

**Skip refute (continuous mode only).** `§2.2`'s interactive path already has a human gate; this refute runs only on the unattended continuous path, where a wrong skip silently cascades a hole into downstream work. When `recommendation == skip`, dispatch `conductor:refuter` to challenge it before acting:

```
PROJECT_DIR={project_root}
DOMAIN=skip
CLAIM=Skip-analyst recommended skipping task P{p}T{t} ("{name}"), reasoning: "{skip-analyst reasoning}". Challenge framing: this skip is UNSAFE — a dependency marked completed is only superficially done (its own ACs not actually met), or the failure handoff describes a fix cheap relative to the cost of skipping.
CONTEXT_PATHS={td}/plan.md {td}/track-state.json {td}/.conductor/handoff/P{p}T{t}.md
```

> The CLAIM is framed as "the skip is unsafe" deliberately: the refuter defaults to `SUSTAINED` when uncertain, so `SUSTAINED` = block-when-uncertain (the conservative direction for a skip, the riskier action). `REFUTED` = grounded evidence the skip IS safe. (`new-track` §2.3b frames its CLAIM the opposite way — "the plan is sound" — because a plan gate should proceed-when-uncertain.)

Parse the `---REFUTATION RESULT---` block:

- **`STATUS: SUSTAINED`** (skip unsafe — default when uncertain) → **override to block**: handle as `pause_and_escalate` (sync-plan → commit → HALT with the refuter's evidence). The refute found grounded evidence the skip breaks something; do not skip.
- **`STATUS: REFUTED`** (grounded evidence the skip is safe) → let the skip stand → `track-state skip` → Section 3.1.
- **`STATUS: FAILURE`** → defer to skip-analyst's primary verdict: announce `"⚠️ skip refute could not complete — proceeding on skip-analyst's recommendation"` and let the skip stand. A backup-agent crash is not new evidence the skip is safe; the announce keeps it visible without halting the track on a backup failure.

**Failure-analyst (continuous mode only).** A read-only diagnostic that answers *why* a task keeps failing before spending the retry budget on another identical attempt. The spine dispatches it (a) when one attempt remains (`retry == max - 1`) and (b) on a skip-analyst `retry_with_modification` hand-off. Dispatch `conductor:failure-analyst`:

```
TRACK_DIR={td}
TRACK_ID={id}
PHASE_INDEX={p}
TASK_INDEX={t}
TASK_NAME={name}
RETRY_COUNT={retry}
MAX_RETRIES={max}
```

Parse the `---FAILURE ANALYSIS---` JSON and act by `recommendation`:

- **`retry_modified`** (`category: deterministic_bug`) → `track-state failure-analyst-verdict "<td>" --category <C> --recommendation retry_modified --root-cause "<text>" --modification "<text>"`. The spine writes the modification to a guidance marker, reactives the failed task (retry budget preserved), and re-dispatches task-executor with the modification injected as a `[Conductor Modified Retry]` block. → §3.1.
- **`replan`** (`category: spec_plan_defect`) / **`decompose`** (`category: context_budget`) / **`escalate`** (`category: environmental|stuck`) → `track-state failure-analyst-verdict "<td>" --category <C> --recommendation <R> --root-cause "<text>" [--modification "<text>"]` → the spine HALTs surfacing `root_cause` + `modification` (a proposed AC correction, task split, or just the diagnosis) **plus a `recovery` recipe** — the safe manual path. These are intentional human gates, not gaps: the analyst cannot decide intent (`replan`) or safely bisect committed work (`decompose`), so act on `recovery` then re-invoke to resume. **On `decompose`, the original task's commit is preserved — do NOT revert it** (split in plan.md, skip the original, insert the remainder). A modified retry that fails again is capped (`MAX_ANALYSIS_ROUNDS`) → escalate.

### 3.6b Self-Review Loop (opt-in — "Ralph Wiggum")

**DEFAULT OFF.** Runs ONLY when the just-completed task opts in — zero latency otherwise:
- the task NAME contains the marker `[Review]` (per-task opt-in), OR
- env `CONDUCTOR_SELF_REVIEW=1` (global opt-in for every task this session).

`[Review]` is a **name marker, not a tag** — it does NOT enter the `[Docs]`/`[Config]`/… exemption logic, so a reviewable task still owes TDD (F2) and coverage (F3).

When opted in (after a SUCCESSFUL `dispatch-finalize`, before §3.7), run a **convergent review loop** (review → fix → re-review) that stops on a *dry* round (loop-until-dry — zero NEW Critical/High), not a fixed count. Maintain a `seen` set of finding **signatures** (`severity+title+file+lines`); dedup **new** findings vs `seen` (NOT vs the set you just fixed — a re-appearing finding is a *residual*, counted separately).

**Persist `seen` across compaction** (loop state a compaction would otherwise lose): at loop **entry**, load `.conductor/review-seen.json` (conductor-owned, gitignored) — if its `task_sha` matches this task, restore its `seen` (resuming a compacted loop); else start empty (never inherit another task's set). After each round that adds signatures, write it back as `{"task_sha": "<sha>", "seen": [...]}`. On **any terminal exit**, delete the file.

1. **Reviewer pass** — dispatch `conductor:code-reviewer` (read-only) on `<task_sha>~1..<task_sha>`:
   ```
   TRACK_DIR={td}
   TRACK_ID={id}
   REVISION_RANGE={code_sha}~1..{code_sha}
   ```
2. **Decide from the `---REVIEW RESULT---` block** (substring-check severities), counting only NEW `Critical`/`High` (signatures not in `seen`):
   - **Zero NEW `Critical`/`High`** — dry round → announce `"🔍 Self-review [Review]: clean"` → delete `.conductor/review-seen.json` → §3.7.
   - **NEW `Critical`/`High`** → add signatures to `seen`, **write `review-seen.json` back**; re-dispatch `conductor:task-executor` with `ATTEMPT={n+1}` and the NEW findings as remediation (the agent fixes its own changes), `dispatch-finalize` again, loop to step 1.
3. **Budget guard — max 3 fix iterations.** If still not dry after 3 → announce `"🔍 Self-review [Review]: {N} findings → 3 fix iterations → {M} residual"` → delete `.conductor/review-seen.json` → step 4.
4. **Escalate on residual Critical only** — surface via `AskUserQuestion` (fix-guidance / accept-with-debt / block). Medium/Low residual → note and proceed.

### 3.6c Tactical Refactor (opt-in — orchestrator-dispatched)

**DEFAULT OFF.** Runs ONLY when the just-completed task opts in:
- the task NAME contains the marker `[Refactor]` (per-task opt-in), OR
- env `CONDUCTOR_TASK_REFACTOR=1` (global opt-in for every task this session).

`[Refactor]` is a **name marker, not a tag** — it does NOT enter the `[Docs]`/`[Config]`/… exemption logic, so a refactorable task still owes TDD (F2) and coverage (F3). (Tier rationale — mechanical Step 5 vs tactical refactorer — lives in `agents/refactorer.md` §1.0.)

When opted in, dispatch `conductor:refactorer`, prompt:

```
TRACK_DIR={td}
REVISION_RANGE={code_sha}~1..{code_sha}
```

Parse the `---REFACTOR RESULT---` block (non-blocking — the task already succeeded):
- **STATUS: SUCCESS** → announce `"🔨 [Refactor]: {REFACTORED} → {COMMITTED}"` → §3.7.
- **STATUS: FAILURE** → announce `"🔨 [Refactor]: failed (non-blocking) — {SUMMARY}"` → §3.7.

One bounded pass (no loop, no transient state). The refactorer runs the suite itself and self-reverts on regression — no separate green-confirm dispatch.

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

Run `track-state post-loop-status "<track_dir>"` and keep the envelope (`finalized`, `doc_synced`, `review.done`/`review.range`, `shas_count`). §5.5/§6.0/§7.0 gate on it to skip phases already completed across an interruption (if you resume without it, re-run — it's a cheap git-log grep + state load). Then read `conductor/workflow/post-loop.md` and execute sections 5.0–8.0.

---