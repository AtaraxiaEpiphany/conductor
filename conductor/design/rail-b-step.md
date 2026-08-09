# Rail B-min: `track-state step` (dispatch-loop spike)

**Status:** spike. The serial spine now collapses **both** multi-agent handoffs into
code, closing the verdict-on-disk gate:

- **Phase checkpoint (WM2-2):** `dispatch_batch` fans `ac-tracer` + `test-runner`
  (pre-assembled) → teleoperator transcribes verdicts to `phase-verdict` (`synth_pending`
  marker) → spine dispatches `phase-checker` (prompt from the marker) → teleoperator
  transcribes `STATUS` to `phase-checkpoint-review` (PASSED stamps + clears any
  phase-recovery marker; FAILED → on an auto-routing track writes the phase-recovery
  marker so the next `step` dispatches `dispatch_phase_failure_analyst`, else halts).
- **skip_analyze (WM2-3):** `dispatch_skip_analyst` → teleoperator transcribes the
  `recommendation` to `skip-analyst-verdict` (`analyzed` marker) → spine routes
  (`skip` → `dispatch_refuter`, else → `halt`) → teleoperator transcribes the refute
  `STATUS` to `skip-refute-review` (`refuted` marker) → spine routes (REFUTED/FAILURE →
  in-spine `_do_skip` + advance; SUSTAINED → `halt`).

Both give read-only agents' verdicts a disk channel the spine consumes (mirroring
`wave-finalize` reading `result.json`). The only remaining non-spine *routing* branch
is `wave_active` (a different spine); the opt-in `[Review]`/`[Refactor]` post-SUCCESS
seams stay Rail A prose (deferred B-full, see below). The Rail A prose loop
(`skills/implement/SKILL.md`) is untouched — `step` is an additive, A/B alternative.

## The thesis

Rail A is a prose skill as loop controller: the model reads §2.0/§3.0 and routes.
Every routing branch is a place a small/weak model fails, and the skill body is
resident context. Rail B moves the routing into code: `track-state step` reads
state and emits **one leaf action**; the model's only job is to perform that
action and call `step` again. Maximum determinism; the model's routing judgment
(a liability at small windows) is removed.

The spine is cheap because `dispatch-next` / `dispatch-prepare` /
`dispatch-finalize` already implemented ~90% of it. `cmd_step` composes the
refactored `prepare_dispatch` / `finalize_dispatch` (compute-only halves
extracted from the CLI wrappers) plus `recover`-equivalent routing.

## Action contract

`track-state step <td>` emits a compact envelope whose `action` field is one of:

| action | carrier fields | model's job | rail |
|---|---|---|---|
| `dispatch` | `agent`, `prompt` (verbatim), `attempt`, `is_resume` | Dispatch `conductor:<agent>` with the pre-assembled prompt. | **spine** |
| `dispatch_batch` | `phase`, `wave` (per-member `agent` + `prompt`) | Fire each `wave` member's `conductor:<agent>` in ONE parallel message; transcribe the two RESULT blocks to `phase-verdict`; loop. | **spine** |
| `dispatch_phase_checker` | `agent` (`phase-checker`), `phase`, `prompt` (verbatim, assembled from the marker) | Dispatch `conductor:phase-checker`; transcribe its `STATUS` to `phase-checkpoint-review`; PASSED loops, FAILED routes phase-recovery on an auto-routing track (`dispatch_phase_failure_analyst`), else halts. | **spine** |
| `dispatch_phase_failure_analyst` | `agent` (`failure-analyst`), `phase`, `prompt` (verbatim, `PHASE_INDEX` w/o `TASK_INDEX` → PHASE mode) | Dispatch `conductor:failure-analyst`; transcribe its `---FAILURE ANALYSIS---` to `phase-failure-analyst-verdict`; loops (`retry_modified`→reactivate+redispatch, `replan`-w/AC→`ask`, `replan`-w/o or `escalate`→`halt`). | **spine** |
| `dispatch_skip_analyst` | `agent` (`skip-analyst`), `phase`,`task`,`name`, `prompt` (verbatim) | Dispatch `conductor:skip-analyst`; transcribe `recommendation`/`reasoning`/`impact`/`can_skip` to `skip-analyst-verdict`; loop. | **spine** |
| `dispatch_refuter` | `agent` (`refuter`), `phase`,`task`,`name`, `prompt` (verbatim, CLAIM embeds skip-analyst's reasoning) | Dispatch `conductor:refuter`; transcribe `STATUS`/`reasoning` to `skip-refute-review`; loop. | **spine** |
| `ask` | `decision` (question/header/options/commands/next) | `AskUserQuestion` → run `commands[choice]` verbatim → HALT or loop. | **spine** |
| `halt` | `reason`, `recommendation`, `reasoning`, `impact`, `evidence` | Deliberate stop-for-human (skip pause/retry, or refute-SUSTAINED). Announce reasoning → STOP. | terminal |
| `wave_active` | `phase` | Hand to wave spine. | non-spine |
| `done` | — | Enter post-loop (skill §4.0). | terminal |
| `error` | `error`/`errors` | HALT. | terminal |

## What stayed in the skill (the B-min boundary)

The only *routing* branch not collapsed is `wave_active` — it hands to a *different*
spine (`/conductor:parallel`), not a graduation target. The post-loop (§4.0–§8.0) has
its own spine (`post-loop-step`); `step` hands off at `done`. The opt-in post-SUCCESS
seams (`[Review]`, `[Refactor]`) are not routing branches — `step` routes SUCCESS
straight to the next leaf — and are listed as B-full graduations below.

## The two subtle spine behaviors

- **Pre-assembled prompt.** `dispatch.prompt` is built in code
  (`_step_assemble_prompt`) — the model pastes it verbatim. No field
  interpolation, no `SUBTASK=None` on flat tasks (the line is omitted). This
  removes a weak-model failure surface that Rail A leaves open.
- **No-retry-burn on interrupted dispatch.** When `step` sees an `in_progress`
  task with **no** `result.json` and HEAD still a `Start task` commit, it
  re-dispatches **without** finalizing — so a dispatch that never ran (killed
  session, context-budget yield mid-dispatch) does not burn a retry. Only when
  `result.json` exists or HEAD advanced past the Start commit does `step`
  finalize (synthesizing a result from the committed code if the agent forgot to
  write one). Core to the long-running goal; covered by
  `test_interrupted_before_work_redispatches_no_retry_burn`.
- **Deterministic single-writer dispatch.** The no-retry-burn branch re-dispatches
  *after* the first agent has returned (or never ran). A different gap — a *second
  concurrent* `step` call while the first task-executor/explorer is still in flight —
  is closed by the `on-dispatch-dedupe.py` PreToolUse:Agent hook, which
  `permissionDecision:"deny"`s a second spawn for the same locked task while an
  in-flight marker (`.conductor/.dispatch-inflight-*`, same predicate as the
  no-retry-burn branch) exists. Read-only agents and wave parallelism (separate
  worktrees, no marker, no `in_progress` cursor in the worktree state) are exempt;
  only the serial-spine same-task re-dispatch is denied. The hook's deny reason
  prescribes the *terminating* recovery `track-state dispatch-finalize` (which
  synthesizes a failure from the locked-task state, clears the stuck marker, and
  lets the next dispatch stamp a fresh `start_sha`) — **not** `track-state step`,
  which in this exact state re-emits `dispatch` and would loop the orchestrator
  back into the deny (`step`→`dispatch`→deny→`step`). Pinned by
  `test_dispatch_finalize_breaks_inflight_loop`.

## Routing ordering (matches Rail A)

`_step_emit_next_leaf` resolves in this order, preserving Rail A's
recover→dispatch-next semantics:

1. If there is dispatchable work → route it (parent-complete/stuck auto-resolve,
   manual→ask/defer, explore/execute→dispatch).
2. Else if a failed+exhausted task exists → `ask` (interactive) /
   `dispatch_skip_analyst` (continuous) (recover §2.0 surfaces the decision *before*
   a phase checkpoint). *(Top of `cmd_step`, a `skip-analysis` marker short-circuits
   all of this — the §3.6 handshake routes by marker stage. A `phase-recovery` marker
   (stage `failed`/`analyzed`, i.e. not the transparent `recovering`) short-circuits
   even earlier — `_step_route_phase_recovery` owns the phase-level verdict router;
   `recovering` is skipped so the spine re-dispatches reactivated tasks normally.)*
3. Else if an earlier phase needs a checkpoint → fan or synthesize (gates before
   later-phase dispatch; dispatch-next §3.0's first check). No `synth_pending`
   marker (or a stale one) → `dispatch_batch` (fan) and clear any stale marker; a
   `synth_pending` marker for this phase → `dispatch_phase_checker` (the verifiers
   already fanned; verdicts are on disk). The wave spine still emits
   `phase_checkpoint` here — its §3.2 hand-off is the parallel-step skill's
   contract.
4. Else `done`.

## B-full options

Both multi-agent handoffs shipped (serial spine only): the §3.2 phase-checkpoint
fan-out + synthesize (WM2-2, via the `phase-checkpoint.json` marker) AND the §3.6
skip_analyze skip-analyst → refute → route (WM2-3, via the `skip-analysis.json`
marker). The verdict-on-disk gate is closed.

The refactor mechanism is **partly** in B-min already: its mechanical tier — Step 5,
inline in `task-executor` §4.0 — runs on every dispatched task regardless of spine
(the executor owns it, so the dispatching spine is irrelevant). What stays
Rail-A-only, deferred to B-full if empirical A/B shows it matters:

- **`review_round`** — a `step --review` sub-mode that drives the self-review
  loop in code (loop-until-dry). The `seen`-signature set is **already persisted**
  to `.conductor/review-seen.json` (keyed by `task_sha`) in §3.6b of the Rail A
  skill — so what remains for the B-full graduation is the loop *control flow*
  (review → fix → re-review routing), not the compaction-resilience of `seen`.
- **`refactor` (§3.6c tactical refactorer)** — graduate the opt-in `[Refactor]`
  seam (added after this spike was written). On a SUCCESS finalize where the task
  opts in (name marker `[Refactor]` or env `CONDUCTOR_TASK_REFACTOR`), emit a
  `dispatch_refactorer` action + a result-transcribe stamp command (the refactorer
  is stdout-block, like `apply-fixes`), then route to the next leaf (non-blocking).
  Simpler than `review_round` — one bounded pass, no loop, no transient `seen`
  state — but the same model-judgment character. `_step_route_after_finalize`
  currently routes SUCCESS straight to the next leaf, so this seam is invisible to
  `step` today.

Both remaining options are model-judgment passes that benefit less from determinism
than the fan-out + synthesize + skip-refute did, and both fire only after a
non-blocking SUCCESS (the task already succeeded) — so omitting them from B-min
trades a debt-improvement opportunity, never correctness.

## What this spike does NOT change

- `skills/implement/SKILL.md` (Rail A) — untouched, still the default.
- Wave spine, hooks, all existing commands — untouched.
- `prepare_dispatch` / `finalize_dispatch` are pure refactors; `cmd_dispatch_prepare`
  / `cmd_dispatch_finalize` are now thin `emit()` wrappers over them (behavior-preserving).

## How to A/B test

Run the same track twice on the target small-window model: once via
`/conductor:implement`, once via `/conductor:implement-step`. Compare: routing
errors, mis-constructed prompts, stuck-lock abandons, and context consumed per
task. The spine now owns the full dispatch + phase-checkpoint path; the remaining
non-spine branch (`skip_analyze`) reveals whether WM2-3 is worth it.
