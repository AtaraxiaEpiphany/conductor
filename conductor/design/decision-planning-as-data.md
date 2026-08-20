---
type: concept
sources:
  - conductor/design/planning-as-data
  - conductor/design/dispatch-manifest
  - conductor/design/decision-workflow-as-data
last_verified: 2026-08-20
---

# Decision: Planning-as-Data (planning docfile seam)

Status: **Accepted** (2026-08-20, grill-resolved) — planning-side workflow
becomes registry-derived data pointed at per track; shape selection becomes
a pure code proposal the user confirms. Full decision set and campaign
phases: [[conductor/design/planning-as-data]].

## Context

A follow-on grill to the same-day workflow-as-data decision asked to make
planning more extendable and generic. The premise challenge redirected it:
the executor half had just shipped; the live pain was the planning front
door — shape selection as keyword prose in a skill, authoring branches in
the spec-planner agent, procedure restated as registry display prose, and an
unreachable `research-first` shape. "Planning-as-data" is deliberately
narrower than a generic planner or a dynamic spine: **the planning
procedure's home moves into registry-indexed docfiles, and shape selection
becomes a pure proposal the user confirms. Nothing about dispatch changes.**

The decision sits in the same crowded decision space as its predecessor and
must not be confused with the neighbors: 1B declined a step *semantics*
axis; D2 dynamic spine and D1 native Workflow stay deferred; this decision
touches neither — `nodes` remains advisory.

## Decision

1. **Planning docfiles.** Each shape's planning procedure lives in
   `templates/planning/<name>.md`, named by the shape row's `planning_doc`;
   project-overridable; procedure only (policy stays in registry fields).
   Extending = one docfile + one shape row; zero plugin edits.
2. **Selection in code, decision with the user.** `track-state
   propose-shape` ranks shapes by registry `signals` over (description ⊕
   brief) — pure, deterministic, no model call. Default proceeds silently;
   non-default confirms once, recommended-first.
3. **`research-first` goes live at the planning layer.** Its docfile
   Prelude dispatches explorer before spec-planner. Planning-side ordering,
   not spine reordering.

## Rationale

1. **The seam already proved itself one layer down.** The executor collapse
   (branching agent prose → manifest + docfile) shipped hours earlier with
   green gates; planning has the identical shape — branching §4.1 prose,
   three homes, a declared capability nothing selects.
2. **Single-source discipline.** Keyword prose in a skill is an unwatched
   restatement of registry intent — the drift class every prior campaign
   hunted. Signals in the registry make the matcher data-driven and
   lint-covered; docfiles make the procedure single-homed.
3. **The wiki's own ladder, applied upward.** Deterministic seam (selection
   is control flow → code); loop before graph (no new axis, no spine work);
   decisions are the user's (confirm non-default).
4. **Cheapest honest fix for research-first.** Honoring explore-before-plan
   via a Prelude converts a false declaration into real behavior without
   re-opening the deferred dynamic-spine question.

## Gate check (all three hold)

- **Hard to reverse:** moves planning prose's home (skill + agent +
  registry display fields → docfile library), adds registry fields with
  validator cross-checks, retargets new-track + spec-planner, adds a
  command-surface subcommand.
- **Surprising without context:** it resembles the declined 1B and the
  deferred D2 while being neither — a docfile seam + a selection proposal,
  not a step axis or a spine reorder. It also resembles the dispatch
  manifest while explicitly declining its artifact (no planning manifest).
- **A real trade-off was rejected:** the clean-slate re-derivation
  (floated in the grill; re-litigates same-day decisions), the planning
  manifest, the archetype axis, and model-judgment selection were all live
  options with named costs.

## When to revisit

- **≥2 shapes need the same play, or one shape needs two** — the archetype
  axis re-proposal bar (mirrors 1B's).
- **A second selection dimension emerges** (novelty/risk) that `signals`
  cannot express without bloating — revisit keying.
- **Dispatch ever needs to honor `nodes`** — that is D2's question, not
  this seam's; its conditions are unchanged here.

## See Also

- [[conductor/design/planning-as-data]] — the full decision set + campaign phases this record governs
- [[conductor/design/decision-workflow-as-data]] — the predecessor ADR whose discipline this inherits
- [[conductor/design/dispatch-manifest]] — the executor seam being mirrored
- [[conductor/resource/glossary]] — **planning docfile**, **shape proposal** entries
