---
type: concept
sources:
  - conductor/design/findings-artifact-edge
  - conductor/design/extensibility-review-2026-08
  - runtime/contracts/plan-format-contract
last_verified: 2026-09-03
---

# Decision: artifact ledger + plan dataflow edges (extend Track D)

Status: **Accepted** (2026-09-03, grill-resolved) — the conductor's
cross-task dataflow becomes a declared contract (result fields + plan
annotations) verified by lint and advisory, never enforced by denial. Full
design: [[conductor/design/findings-artifact-edge]].

## Context

Plans carry control flow only; artifacts travel by side-channel. The
extensibility review's Finding 2 fixed the compiled-findings edge; the live
`fastjson2jackson` track showed the same failure one layer wider — a
baseline file produced at P1 with a P6 consumer that was never handed the
pointer. Two candidate postures existed: hard-gate consumption (a
checkpoint fails while any artifact is unconsumed) or deliver-and-surface
(pointers injected by contract; orphans surfaced as warnings).

## Decision

1. **Deliver and surface, never deny.** The invariant is *delivery*, not
   consumption: every artifact a later task needs reaches it through
   task-context / the findings catalog; an artifact nobody declares a
   consumer for surfaces as a warning at the plan lint and the phase
   checkpoint. A deny would punish legitimately-optional artifacts and
   breed fake reads.
2. **Declaration is dual — runtime truth and planner intent.** result.json
   gains `artifacts` (produced) and `artifacts_used` (attestation); plan
   rows gain `produces:` / `uses:` annotations. The runtime ledger proves
   the producer kept its word; the plan edges prove the planner planned a
   consumer; the checkpoint diffs the two.
3. **The durable ledger home is the handoff + findings catalog**, not
   track-state.json — the state schema stays closed, result fields stay
   transient, and the roll reuses the one execution-record home all three
   finalize rails already share.

## Consequences

- Hard to reverse: new result fields and plan-annotation grammar become
  contract surface other tooling will read; removing them later strands
  plans and handoffs mid-flight. The fields are additive (absent = no
  artifacts), so old tracks and old plans stay valid.
- Surprising without context: "artifact" already names two other concepts
  (spec Artifact Anchors; forbidden build artifacts) — the glossary entry
  "task artifact" is load-bearing disambiguation, not decoration.
- Trade-off rejected: the hard-gate posture — stronger on paper, wrong in
  practice (fail-open doctrine; simple tracks pay for complex tracks'
  needs; forced consumption is noise, not safety).
- Trade-off rejected: catalog-only (smallest diff) — leaves the pointer in
  the weakest contract class (path recall), which is exactly the observed
  failure.
