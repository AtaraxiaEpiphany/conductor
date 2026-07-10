# Rail B-min: `track-state step` (dispatch-loop spike)

**Status:** spike. The serial spine now collapses the **whole** phase-checkpoint
handshake into code: `dispatch_batch` fans the read-only `ac-tracer` + `test-runner`
verifiers (pre-assembled prompts), the teleoperator transcribes their verdicts to
`phase-verdict` (writes a `synth_pending` marker), the spine dispatches the
`phase-checker` synthesizer (prompt pre-assembled from the marker), and the
teleoperator transcribes its `STATUS` to `phase-checkpoint-review` which stamps the
checkpoint (PASSED) or clears it (FAILED → halt). The old prose §3.2/§3.7
verdict-collect + synthesize is retired (WM2 verdict-on-disk, step 2; the marker
gives the verdicts a disk channel the spine consumes, mirroring `wave-finalize`
reading `result.json`). The Rail A prose loop (`skills/implement/SKILL.md`) is
untouched — `step` is an additive, A/B alternative.

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
| `dispatch_phase_checker` | `agent` (`phase-checker`), `phase`, `prompt` (verbatim, assembled from the marker) | Dispatch `conductor:phase-checker`; transcribe its `STATUS` to `phase-checkpoint-review`; PASSED loops, FAILED halts. | **spine** |
| `ask` | `decision` (question/header/options/commands/next) | `AskUserQuestion` → run `commands[choice]` verbatim → HALT or loop. | **spine** |
| `skip_analyze` | `phase`,`task`,`name` | Hand to skill §3.6 (skip-analyst → refute → route). | non-spine |
| `wave_active` | `phase` | Hand to wave spine. | non-spine |
| `done` | — | Enter post-loop (skill §4.0). | terminal |
| `error` | `error`/`errors` | HALT. | terminal |

## What stayed in the skill (the B-min boundary)

Two judgment branches are not collapsed:

1. **`skip_analyze`** — `skip-analyst` → `refuter` refute → route on the verdict.
   The refute is a conditional dispatch whose result feeds routing (judgment).
   (WM2-3 will graduate this by the same transcribe-STATUS pattern.)
2. **post-loop** (§4.0–§8.0) — has its own spine (`post-loop-step`); `step` hands
   off at `done`.

`skip_analyze` / `wave_active` surface as named `action`s and defer to
`/conductor:implement`. That is the measured boundary, not a gap.

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

## Routing ordering (matches Rail A)

`_step_emit_next_leaf` resolves in this order, preserving Rail A's
recover→dispatch-next semantics:

1. If there is dispatchable work → route it (parent-complete/stuck auto-resolve,
   manual→ask/defer, explore/execute→dispatch).
2. Else if a failed+exhausted task exists → `ask`/`skip_analyze` (recover §2.0
   surfaces the decision *before* a phase checkpoint).
3. Else if an earlier phase needs a checkpoint → fan or synthesize (gates before
   later-phase dispatch; dispatch-next §3.0's first check). No `synth_pending`
   marker (or a stale one) → `dispatch_batch` (fan) and clear any stale marker; a
   `synth_pending` marker for this phase → `dispatch_phase_checker` (the verifiers
   already fanned; verdicts are on disk). The wave spine still emits
   `phase_checkpoint` here — its §3.2 hand-off is the parallel-step skill's
   contract.
4. Else `done`.

## B-full options

`dispatch_batch` + the full phase-checkpoint synthesize shipped (serial spine
only): the §3.2 fan-out AND the verdict-collect + `phase-checker` dispatch +
§3.7 stamp/halt are now code-driven (the verdicts cross `step` calls via the
`phase-checkpoint.json` marker — the WM2-2 disk channel). What remains if
empirical A/B shows the other non-spine branches fire often enough to matter:

- **`skip_analyze` as a teleoperation** — `skip-analyst` → `refuter` → route as a
  spine sequence (WM2-3; same transcribe-STATUS pattern as the checkpoint
  handshake and post-loop review).
- **`review_round`** — a `step --review` sub-mode that drives the self-review
  loop, persisting the `seen`-signature set to a conductor-owned file (currently
  model-resident in §3.6b). Loop-until-dry in code.

The remaining options are model-judgment loops that benefit less from
determinism than the fan-out + synthesize did.

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
