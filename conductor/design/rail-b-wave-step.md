# Rail B-min: `track-state wave-step` (wave-loop spine)

**Status:** spike, branch `feat/small-window-long-running`. Sibling of
`track-state step` (`conductor/design/rail-b-step.md`). The Rail A prose wave loop
(`skills/parallel/SKILL.md`) is untouched — `wave-step` is an additive, A/B
alternative driven by `skills/parallel-step/SKILL.md`. This realizes the
**`dispatch_batch`** B-full option deferred in `rail-b-step.md`:

> `dispatch_batch` is the highest-value next step — it's the only branch that's
> genuinely non-serial.

## The thesis

Rail A's parallel skill is a 239-line prose loop controller: the model reads §3.0/§4.0
and routes dispatch-wave → fan out → integrate → seam review → phase boundary → repeat.
Every routing branch is a place a small/weak model fails, the per-member fan-out
prompts are model-assembled (an N× field-interpolation failure surface), and the
skill body is resident context. Rail B moves the routing into code: `track-state
wave-step` reads state and emits **one leaf action**; the model's only job is to
perform that action and call `wave-step` again.

The spine is cheap because `dispatch-wave` / `wave-finalize` already implement ~90%
of it (the compute-only halves `prepare_wave` / `finalize_wave_member` were extracted
so `wave-step` composes them DRY, exactly as `cmd_step` composes `prepare_dispatch` /
`finalize_dispatch`).

## Action contract

`track-state wave-step <td>` emits a compact envelope whose `action` field is one of:

| action | carrier fields | model's job | rail |
|---|---|---|---|
| `dispatch_batch` | `wave[]` (each: phase/task/name/worktree/branch/worktree_track_dir/**prompt**), `deferred[]`, `base_sha` | Dispatch `conductor:task-executor` for EVERY member in ONE message, each `prompt` verbatim. | **spine** |
| `wave_integrate` | `phase`, `task`, `name` | Run `wave-finalize "<td>" --phase <p> --task <t>` verbatim. | **spine** |
| `seam_review` | `phase`, `finalized_count`, `revision_range` | Hand to `parallel` §4.15 (code-reviewer → seam-findings → refuter → AskUserQuestion). | non-spine |
| `serial` | `ineligible[]`, `execution_mode` | Run `track-state step` once, relay its action, re-invoke `wave-step`. | non-spine |
| `phase_checkpoint` | `phase` | Hand to `implement` §3.2 (parallel fan-out + synthesize). | non-spine |
| `ask` / `skip_analyze` | `decision` / phase+task | Failed-member Retry/Skip/Block (interactive) or skip-analyst route (continuous). | **spine** |
| `done` / `error` | — | Enter post-loop / HALT. | terminal |

A single-member `dispatch_batch` with `is_resume: true` is the no-retry-burn
re-dispatch of one interrupted member (see below).

## What stayed in the skill (the B-min boundary)

Three branches are **not** collapsed because they are not single linear dispatches
or because they are model-judgment — the same boundary `rail-b-step.md` drew for
`step`:

1. **`seam_review`** — `code-reviewer` over the integrated range → write findings →
   `refuter` re-examines each → surface survivors via `AskUserQuestion`. Refute and
   the human gate are judgment; they stay prose (§4.15). `wave-step` only decides
   *applicability* (≥2 finalized this wave) in code.
2. **`serial`** — the step spine owns serial work; `wave-step` emits `serial` and the
   model runs `track-state step` once, then re-checks for waves (a serial task may
   satisfy a dep that unlocks the next wave, §3.3).
3. **`phase_checkpoint`** — `ac-tracer` + `test-runner` fired in parallel, then
   `phase-checker` synthesizes (shared with `implement`).

These surface as named `action`s and defer to `/conductor:parallel` or
`/conductor:implement`. On a simple parallel track (flat disjoint tasks, no seam
survivors, no failures) the spine alone drives `dispatch_batch → wave_integrate×N →
done` end-to-end.

## The three subtle spine behaviors

- **Pre-assembled per-member prompts.** Each `wave[i].prompt` is built in code
  (`_wave_assemble_member_prompt`) — the model pastes it verbatim. No field
  interpolation, no `SUBTASK` (wave members are flat-only), `ATTEMPT=1` (v1 does not
  retry in-wave). This removes the N× weak-model failure surface Rail A leaves open.
- **No-retry-burn on interrupted member.** When `wave-step` sees an `in_flight`
  member whose worktree exists, has **no** `result.json`, AND whose branch has zero
  commits past `base_sha`, it re-dispatches that ONE member (`is_resume: true`)
  without finalizing — so a dispatch that never ran (killed session, context-budget
  yield mid-batch) doesn't burn a retry. Mirrors `step`'s `_is_start_commit`
  discriminator, but wave members have **no** start-commit, so the discriminator is
  `n_commits == 0` (the same primitive `finalize_wave_member` computes), not a
  commit-message pattern. If the worktree or branch is gone (partial abort), it
  falls through to finalize, which synthesizes FAILURE correctly.
- **Drain-marker idempotency.** Seam-review applicability is decided *once* per
  drained wave. A ledger field would be clobbered (`wave-finalize` re-loads +
  full-overwrites the ledger each call), so the marker is a sidecar file
  (`.conductor/.wave-drain-processed`) keyed on `(track_id, base_sha)`: written
  before the drain decision emits, self-invalidating across waves (new `base_sha`),
  and gitignored so conductor commits never sweep it.

## Routing ordering (`cmd_wave_step`)

1. **Active wave with in_flight members** → integrate the lowest `(phase,task)`
   member, or re-dispatch it if un-started (no-retry-burn).
2. **Drained ledger not yet processed** → mark it, then `seam_review` if ≥2
   finalized (else fall through).
3. **A serial task is in_progress** → delegate to `serial` (don't start a wave
   concurrent with an in-flight serial task; §3.3 completes the serial task first).
4. **No active wave** → `prepare_wave`: `dispatch_wave` → `dispatch_batch`;
   `no_ready_tasks` → shared `_emit_quiescent_leaf` (failed-exhausted → `ask`/
   `skip_analyze`; phase checkpoint → `phase_checkpoint`; dispatchable serial work →
   `serial`; else `done`).

## What this spike does NOT change

- `skills/parallel/SKILL.md` (Rail A) — untouched, still the default.
- `dispatch-wave` / `wave-finalize` behavior — `prepare_wave` / `finalize_wave_member`
  are pure compute-half extractions; the `cmd_*` wrappers are thin `emit()` over them.
- Hooks, F1 guards, worktree plumbing, `wave-abort` — untouched.

## How to A/B test

Run the same parallel track twice on the target small-window model: once via
`/conductor:parallel`, once via `/conductor:parallel-step`. Compare: per-member
prompt-construction errors, mis-routed integrates, stuck-lock abandons, and context
consumed per wave. The spine-only path (flat disjoint tasks, no seam survivors, no
failures) is the cleanest comparison; the non-spine branches reveal whether B-full
growth is worth it.

## B-full options (not built — decision deferred to the spike's verdict)

If empirical A/B shows the non-spine branches fire often enough to matter, grow
`wave-step`'s action set rather than re-resident the full skill:

- **`dispatch_batch` + `collect_batch`** for seam review — emit the code-reviewer
  fan-out as a batch leaf; a follow-up `wave-step` collects and dispatches the
  refuter. Turns §4.15 into a teleoperation.
- **In-wave retry** — v1 leaves a failed member for the serial spine after drain;
  a `retry_member` action could re-dispatch a failed member in-wave (currently
  deferred per `parallel` §4.0).
- **Parallel integrate** — `wave_integrate_batch` would be tempting but is blocked
  by the integration race (the squash-merge block mutates the shared main-worktree
  index — see `finalize_wave_member`'s INTEGRATION RACE note). Safe parallelism
  needs per-member integration branches; deferred.
