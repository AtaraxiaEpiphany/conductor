---
type: concept
sources:
  - conductor/design/any-job-extensibility-2026-09
  - conductor/design/task-type-ownership
  - templates/workflow/task-type-profiles.json
  - scripts/track_state/task_profiles
  - scripts/track_state/registry_validate
last_verified: 2026-09-04
---

# Decision: Class-declared grounding (the exemption inversion)

Status: **Accepted** (2026-09-04, grill-resolved; shipped in the any-job
campaign, Track 1). Full derivation:
[[conductor/design/any-job-extensibility-2026-09]].

## Context

Until this campaign a task class declared its verification posture in the
*negative*: `tdd_exempt: true` / `coverage_exempt: true` meant "this class
owes no TDD / no coverage". Defaults lived for code; every non-code class was
a carve-out — a growing list of exemptions from a code-shaped baseline. Two
consequences: (a) a new kind of work had to understand the code baseline well
enough to exempt itself from it; (b) nothing in a row said what verification
the class *does* owe — its witness was the complement of a subtraction.

## Decision

**Invert the declaration.** Every class positively declares:

- `gates`: a subset of `{tdd, coverage, checkpoint}` — the gates its
  deliverables actually owe; the default row declares all three.
- `grounding`: how the class's done-state is witnessed — `test`, `review`,
  `data-check`, or `human-attest`.

The legacy booleans remain valid input indefinitely (project-overlay compat)
but are *derived, never stored*: `tdd_exempt(tag) ≡ "tdd" not in gates`. A
two-homes XOR validator guard forbids mixing on one row; merged-level guards
re-check after default inheritance. Constraint pair, deliberate: `tdd` or
`coverage` in gates requires `grounding == "test"` — but `grounding == "test"`
without either is only a WARN, because a coherent test-witnessed class that
runs its tests outside TDD order must stay expressible.

Code is thereby demoted from the implicit universal to one deliverable class
among peers; `Docs`/`Config`/`Chore` declare `gates: [checkpoint]` +
`grounding: review`; `Manual` declares `human-attest`; `data-check` ships as
vocabulary awaiting its first class row.

## Gate check (all three hold)

- **Hard to reverse.** The row schema's meaning flips (a `gates`-less row now
  reads as legacy, not as the blessed form); generators, validators, renders,
  and three hooks speak the positive form; un-inverting means re-teaching
  every surface to subtract again.
- **Surprising given the history.** The booleans were *the* mechanism of two
  prior campaigns (task-type ownership; plugin generality) and were treated as
  load-bearing; this reverses their polarity and demotes them to derived
  compat aliases.
- **A real trade-off was rejected.** Considered and declined: keying
  code-free derivation on `grounding != "test"` — it silently re-classifies
  `[Explore]` (today deliberately NOT code-free) and entangles two orthogonal
  axes. Derivations stay gates-based (`all("coverage" not in gates(t))`); the
  stress test caught this before it shipped.

## D2 re-declined (third time, recorded here because this was the pass most
likely to overturn it)

A grounding-declared, any-job conductor could plausibly have earned
per-dispatch runtime judgment ("the class knows its witness; let the spine
re-derive routing each dispatch"). Declined again: judgment over authored
content (labels at init, remaining rows at phase gates) is safe and now
richer; judgment over control flow (shape, mode, routing per dispatch) breaks
replay, audit, and confirm — the other three reliability legs. See the
D2 section of the campaign doc.
