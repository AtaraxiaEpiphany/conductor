---
type: concept
sources:
  - scripts/track_state/dispatch.py
last_verified: 2026-08-08
---

# Recovery Policy

The single home for how a Conductor track recovers a failing task. **Read this on
demand and follow it** — the skills that drive recovery (`implement`, `post-loop`)
reference it rather than restating the rules, so the two never silently diverge
(see [[runtime/contracts/prose-style]] Bucket B). The numbers and the loop
mechanic live here; the code reads them from `scripts/track_state/constants.py`
and `scripts/track_state/dispatch.py`.

## The model: a verdict-driven router

Recovery is **not** "fail three times, then ask the human." The failure-analyst
returns a verdict that *routes* the next action, and the spine owns that route
judgment in code (`_step_route_failure_analysis`). The arms:

- **`retry_modified`** — the analyst prescribed a materially different approach.
  Reactivate the failed task (retry history preserved, so the attempt still
  counts against budget), inject the modification, re-dispatch.
- **`decompose`** — the task is too big, not wrong. An `ask` offers a code-applied
  split (`track-state split`); the original's commit is preserved.
- **`replan`** — the spec/plan is wrong. Stages an in-place **amendment** (see
  [[runtime/contracts/plan-amendment]]); the ONE human touchpoint in the whole
  router, because it can supersede an AC a downstream gate already measured
  against.
- **`skip` / `escalate`** — automated hand-offs (skip-analyst owns skip; escalate
  halts for a human).

The router is the same in both execution modes. What *differs* by mode is whether
the **first** failed+exhausted task surfaces a human ask or routes straight to the
skip-analyst handshake — and that is owned by `recovery_policy`, below, not by the
verdicts.

## `recovery_policy` — ask vs auto-route

One field, decoupled from `execution_mode`, read at every failed+exhausted
decision site through the single resolver `dispatch._auto_route_failure`:

- **`ask`** (legacy default for tracks predating the field): a failed+exhausted
  task on an *interactive* track surfaces a Retry/Skip/Block `ask`. (Continuous
  mode still auto-routes — it never pauses.)
- **`auto`** (the default for newly-initialized tracks): routes straight to the
  skip-analyst handshake **regardless of execution mode**. An interactive track
  can opt into auto-recovery without giving up checkpoint pausing, which
  `execution_mode` still owns.

**Byte-identical invariant:** a track without the field reads as `ask` and falls
through to the legacy execution-mode rule, so every existing track behaves
exactly as before. Flip a live track with `track-state set-recovery-policy`.

## The retry arm: loop-until-dry + twin backstop

The retry arm keeps re-analyzing + retrying while the analyst produces **novel**
root causes (a fresh diagnosis = a fresh approach worth a try). Two independent
backstops stop it (`constants.RECOVERY_DRY_K` / `MAX_RECOVERY_ROUNDS`):

1. **Dry (converged)** — `RECOVERY_DRY_K` consecutive rounds whose `root_cause`
   was already seen → the analyst has nothing new; halt (escalate) instead of
   repeating a known-bad modification. Novelty is computed when the verdict is
   transcribed (`cmd_failure_analyst_verdict`) and stamped on the marker as
   `seen_root_causes` + `consecutive_empty_rounds`; a novel root cause resets the
   dry counter, a repeat (or blank) increments it.
2. **Budget (hard ceiling)** — `MAX_RECOVERY_ROUNDS` total rounds, regardless of
   novelty → halt. Distinct-but-wrong diagnoses cannot burn budget forever.

Both backstops route to `_halt("escalate")`. The point of the dry signal is that
a fixed count is wrong exactly when the analyst is making genuine progress
(novelty) — loop-until-dry lets that run while capping the stuck case. A future
per-shape `max_recovery_rounds` field will tune the budget per workflow; today it
is a single global default read fail-open.

## Governing invariant

Every freedom added (skip a checkpoint / run no tests / commit nothing / supersede
an AC) must declare an **integrity substitute** — a "verified against AC-N" stamp
must stay sound forever. This is why the retry arm never silently rewrites an AC
in place (the replan arm stages an additive amendment instead), and why recovery
automation is the default without weakening the verification gates.

## See Also

- [[runtime/contracts/plan-amendment]] — the replan arm's additive `## Amendment N`.
- [[runtime/contracts/prose-style]] — Bucket B: why this has one home.
- [[runtime/core-contract]] — behavioral invariants; resident in every session.
