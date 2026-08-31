---
type: concept
sources:
  - conductor/design/extensibility-review-2026-08
last_verified: 2026-08-31
---

# Decision: Phase-Gate Replanning (confirm-gated rolling wave)

Status: **Accepted** (2026-08-31, grill-resolved) — plans become living
artifacts at phase boundaries only; within-phase dispatch stays frozen.
Full design: [[conductor/design/extensibility-review-2026-08]] Finding 3.

## Context

The extensibility review's ask included "is there a better way to do the
dynamic workflow?" The prior recorded stance
([[conductor/design/planning-as-data]] Non-goals) is plan-time selection
only: amend/split/replan are the whole dynamism story, and per-dispatch
runtime judgment (D2) is declined for determinism and byte-identical
replay. The observed gap: a complex track learns things at phase
checkpoints (explore findings, failure verdicts) that the frozen remainder
of its plan cannot absorb without a manual `/conductor:replan`.

## Decision

At each **PASSED** phase checkpoint, the spine offers a re-derive pass:
planner reads spec (unchanged) ⊕ `track-findings.md` ⊕ remaining plan rows,
proposes amendments through the **existing** plan-amendment machinery, and
applies them only after **one AskUserQuestion confirm** (recommended first),
using reconcile-plan's name-keyed, SHA-preserving semantics. Constraints:
completed phases never reopen; one pass per checkpoint; empty proposal =
silent pass; shape immutable mid-track; within-phase dispatch untouched.

This extends the *cadence* of amendment — a new trigger point for the
existing machinery — it does not reopen D2 and does not mutate shapes.
Dynamism stays at authoring boundaries, where verification is cheap and
artifacts durable.

## Gate check (all three hold)

- **Hard to reverse:** adds a standing step to the checkpoint flow on both
  rails and revises a recorded grill decision (planning-as-data's
  "plan-time selection only" non-goal gains a phase-boundary exception).
- **Surprising without context:** it looks like the declined dynamic spine
  while being its opposite — confirm-gated authored-content amendment at
  boundaries, never runtime control-flow judgment.
- **A real trade-off was rejected:** frozen plans (audit simplicity, zero
  new seam — rejected: manual replan under-covers complex tracks) and
  auto-replan without confirm (rejected: loses the human anchor, thrash and
  token burn on quiet phases).

## When to revisit

- Amendment proposals routinely empty across many tracks → demote to
  opt-in (ask-mode only) rather than deleting the seam.
- Confirm rounds experienced as friction → an auto-apply allowlist for
  trivial amendment classes (tag adds only), never for splits/reorders.

## See Also

- [[conductor/design/extensibility-review-2026-08]] — the review that
  selected this seam; Finding 2's findings edge is its input artifact.
- [[conductor/design/planning-as-data]] — the doctrine this extends at the
  cadence axis only.
- [[conductor/resource/glossary]] — **phase-gate replanning** entry.
