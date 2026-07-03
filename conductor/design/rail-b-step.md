# Rail B-min: `track-state step` (dispatch-loop spike)

**Status:** spike, branch `feat/small-window-long-running`. 13 unit tests green
(`tests/test_step.py`); end-to-end smoke (dispatch → finalize → advance →
`phase_checkpoint`) verified. The Rail A prose loop (`skills/implement/SKILL.md`)
is untouched — `step` is an additive, A/B alternative.

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
| `ask` | `decision` (question/header/options/commands/next) | `AskUserQuestion` → run `commands[choice]` verbatim → HALT or loop. | **spine** |
| `phase_checkpoint` | `phase` | Hand to skill §3.2 (parallel fan-out + synthesize). | non-spine |
| `skip_analyze` | `phase`,`task`,`name` | Hand to skill §3.6 (skip-analyst → refute → route). | non-spine |
| `wave_active` | `phase` | Hand to wave spine. | non-spine |
| `done` | — | Enter post-loop (skill §4.0). | terminal |
| `error` | `error`/`errors` | HALT. | terminal |

## What stayed in the skill (the B-min boundary)

Three branches are **not** collapsed because they are not single linear dispatches:

1. **`phase_checkpoint`** — `ac-tracer` + `test-runner` fired in *parallel*, then
   `phase-checker` synthesizes their verdicts. A "dispatch one agent" step can't
   express a parallel batch + a dependent synthesize call.
2. **`skip_analyze`** — `skip-analyst` → `refuter` refute → route on the verdict.
   The refute is a conditional dispatch whose result feeds routing (judgment).
3. **post-loop** (§4.0–§8.0) — doc-sync, code-review, comprehension digest.

These surface as named `action`s and defer to `/conductor:implement`. That is the
measured boundary, not a gap: on a simple track (flat tasks, no checkpoints, no
exhausted failures) the spine alone drives `dispatch → … → done` end-to-end.

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
3. Else if an earlier phase needs a checkpoint → `phase_checkpoint` (gates before
   later-phase dispatch; dispatch-next §3.0's first check).
4. Else `done`.

## B-full options (not built — decision deferred to the spike's verdict)

If empirical A/B shows the non-spine branches fire often enough to matter, grow
`step`'s action set rather than re-resident the full skill:

- **`dispatch_batch`** — emit N pre-assembled prompts for the phase-checker
  fan-out; the model fires them in parallel; a follow-up `step` collects and
  dispatches the synthesizer. Turns §3.2 into a 3-call teleoperation.
- **`review_round`** — a `step --review` sub-mode that drives the self-review
  loop, persisting the `seen`-signature set to a conductor-owned file (currently
  model-resident in §3.6b). Loop-until-dry in code.
- **post-loop stepping** — `step` continues past `done` into §5.0–§8.0 as
  `dispatch` leaves (doc-sync/code-review are mostly single-agent).

`dispatch_batch` is the highest-value next step — it's the only branch that's
*genuinely* non-serial. The other two are model-judgment loops that benefit less
from determinism.

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
