---
type: concept
sources:
  - conductor/design/grounding-fanout
  - conductor/design/extensibility-review-2026-08
  - runtime/contracts/grill-discipline
last_verified: 2026-09-02
---

# Decision: Grounding fan-out over best-of-N (fog-gated pre-plan enumeration)

Status: **Accepted** (2026-09-02, grill-resolved) — new-track accuracy work
spends compute on enumerating the project ground, not on sampling competing
plans. Full design: [[conductor/design/grounding-fanout]].

## Context

The ask: *make new-track more accurate by trading computer power for
intelligence — define a personal search space, enumerate it with subagents,
avoid result homogenization.* The framing bundles two buys: more grounding
facts, or more diverse plan hypotheses. The recorded accuracy failures
(planner cannot name concrete files, `[Explore]` silenced by the
unsure→untagged doctrine, findings not reaching the planner) are all
grounding failures.

## Decision

1. **Grounding fan-out.** When a pure fog gate (signal matcher over
   description ⊕ brief) fires and the user confirms, new-track dispatches
   three read-only explorers in parallel — architecture/data-flow,
   api/contracts, tests/constraints/history — each enumerating its slice of
   the existing surfaces (index.md, wiki, git history, the codebase).
   Findings reach spec-planner through the existing `RESEARCH_NOTES`
   envelope seam. Implementation sequenced after Track D, with menu Track E
   folded in.
2. **Best-of-N plan sampling declined.** N plans from the same model over
   the same context share the same blind spot and converge on the median;
   selection re-introduces the homogenization the sampling bought away
   from, at ~N× planner cost.

## Gate check (all three hold)

- **Hard to reverse.** It sets the compute-for-intelligence axis for
  new-track (enumeration of ground, not resampling of judgment) and absorbs
  menu Track E's scope into one mechanism; a later best-of-N proposal must
  argue against this record, not start fresh.
- **Surprising without context.** The user asked for enumeration-for-
  diversity; the grill resolved to enumeration-for-grounding. Without this
  record the next session reads the ask and the design as contradicting
  each other.
- **A real trade-off was rejected.** Plan diversity — a mechanism with real
  upside on problems with multiple defensible decompositions — was declined
  with a named cost model and a named failure mode (selection-time
  homogenization).

## When to revisit

- Fog-gate telemetry (once Track E persists it) shows the gate misfiring —
  foggy tracks that planned fine, or quiet tracks that planned blind.
- Grounded plans still disagree across tracks in ways a second planning
  sample would plausibly catch — that is the world where best-of-N earns a
  re-hearing, now with a grounding substrate under it.

## See Also

- [[conductor/design/grounding-fanout]] — the design this decision governs.
- [[conductor/design/extensibility-review-2026-08]] — the menu and Finding 1
  this track folds in.
- [[runtime/contracts/grill-discipline]] — the premise-challenge pass that
  split grounding from diversity.
