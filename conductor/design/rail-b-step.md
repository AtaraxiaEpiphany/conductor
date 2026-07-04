# Rail B-min: `track-state step` (dispatch-loop spike)

**Status:** spike, branch `feat/serial-spine-dispatch-batch`. The serial spine
now collapses the §3.2 verifier fan-out into `dispatch_batch` — the parallel
`ac-tracer` + `test-runner` dispatch is pre-assembled in code, retiring the
`phase_checkpoint` non-spine hand-off for the verifier prompts. Only
verdict-collect + `phase-checker` synthesize stay in prose §3.2 (Partial scope).
The Rail A prose loop (`skills/implement/SKILL.md`) is untouched — `step` is an
additive, A/B alternative.

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
| `dispatch_batch` | `phase`, `wave` (per-member `agent` + `prompt`) | Fire each `wave` member's `conductor:<agent>` in ONE parallel message; then prose §3.2 collects verdicts + dispatches `phase-checker`. | **spine** |
| `ask` | `decision` (question/header/options/commands/next) | `AskUserQuestion` → run `commands[choice]` verbatim → HALT or loop. | **spine** |
| `skip_analyze` | `phase`,`task`,`name` | Hand to skill §3.6 (skip-analyst → refute → route). | non-spine |
| `wave_active` | `phase` | Hand to wave spine. | non-spine |
| `done` | — | Enter post-loop (skill §4.0). | terminal |
| `error` | `error`/`errors` | HALT. | terminal |

## What stayed in the skill (the B-min boundary)

The synthesize half of the phase checkpoint, plus two judgment branches, are not
collapsed:

1. **Phase-checker synthesize** — `dispatch_batch` (spine) pre-assembles and
   fires the read-only `ac-tracer` + `test-runner` verifiers in parallel, but
   their RESULT blocks are collected in the orchestrator's context and feed a
   dependent `phase-checker` dispatch. Verdicts don't flow through disk, so the
   collect + synthesize stays in prose §3.2 (skill §3.2 Step 2 onward). The
   deterministic *fan-out* — the part a weak model fumbles — is in code.
2. **`skip_analyze`** — `skip-analyst` → `refuter` refute → route on the verdict.
   The refute is a conditional dispatch whose result feeds routing (judgment).
3. **post-loop** (§4.0–§8.0) — doc-sync, code-review, comprehension digest.

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
3. Else if an earlier phase needs a checkpoint → `dispatch_batch` (gates before
   later-phase dispatch; dispatch-next §3.0's first check). The wave spine still
   emits `phase_checkpoint` here — its §3.2 hand-off is the parallel-step skill's
   contract.
4. Else `done`.

## B-full options (not built — decision deferred to the spike's verdict)

`dispatch_batch` shipped (serial spine only): the §3.2 verifier fan-out is now
code-assembled. What remains if empirical A/B shows the other non-spine branches
fire often enough to matter:

- **Phase-checker synthesize as a teleoperation** — `dispatch_batch` + a verdict
  sidecar + synthesizer dispatch as a 3-call spine sequence. Deferred because
  verdicts would have to cross `step` calls (a sidecar or staged field), and the
  Partial-scope win — killing the weak-model fan-out failure — is already banked.
- **`review_round`** — a `step --review` sub-mode that drives the self-review
  loop, persisting the `seen`-signature set to a conductor-owned file (currently
  model-resident in §3.6b). Loop-until-dry in code.
- **post-loop stepping** — `step` continues past `done` into §5.0–§8.0 as
  `dispatch` leaves (doc-sync/code-review are mostly single-agent).

The remaining options are model-judgment loops that benefit less from
determinism than the fan-out did.

## What this spike does NOT change

- `skills/implement/SKILL.md` (Rail A) — untouched, still the default.
- Wave spine, hooks, all existing commands — untouched.
- `prepare_dispatch` / `finalize_dispatch` are pure refactors; `cmd_dispatch_prepare`
  / `cmd_dispatch_finalize` are now thin `emit()` wrappers over them (behavior-preserving).

## How to A/B test

Run the same track twice on the target small-window model: once via
`/conductor:implement`, once via `/conductor:implement-step`. Compare: routing
errors, mis-constructed prompts, stuck-lock abandons, and context consumed per
task. The spine-only path (flat tasks, no checkpoints) is the cleanest
comparison; the non-spine branches reveal whether B-full is worth it.
