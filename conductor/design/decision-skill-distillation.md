---
type: concept
sources:
  - conductor/design/extensibility-review-2026-08
last_verified: 2026-08-31
---

# Decision: Skill Distillation (system-proposed, human-gated)

Status: **Accepted** (2026-08-31, grill-resolved) — the improvement loop
gains its missing direction: completed tracks distill into capabilities,
proposed by the system, adopted only by the human. Full design:
[[conductor/design/extensibility-review-2026-08]] Finding 4.

## Context

The extensibility goal includes compounding: once agents walk a path for
some jobs, summarize it into a skill. Today only the install direction
exists (`/conductor:adopt-skill`: external skill → registry row + docfile or
roster wrapper). Knowledge graduates to the wiki, but procedures never
re-enter the executable rails — every recurring job family pays full
exploration cost on every track.

## Decision

A distillation loop with six stages: **harvest** (pure
`distill-candidates` recurrence detection over handoffs, manifest lanes,
failure verdicts, tag usage, misroute classes) → **propose**
(`skill-distiller` agent routes each candidate to its home: job family →
tag + workflow docfile; role → roster wrapper; knowledge → wiki; planning
pattern → planning docfile) → **verify** (refuter pass argues the
counterfactual; recurrence-before-extraction — one traversal is noise) →
**gate** (one AskUserQuestion round; nothing auto-adopts) → **land**
(existing validating generators only; zero-plugin-edits invariant
preserved) → **GC** (usage probes surface retire candidates; unfired
skills are sediment).

The routing rule is the node test plus the invocation axis: deterministic
dispatch-time behavior is a tag; model-reached context-time behavior is a
preloaded wrapper; human-read reference is the wiki.

## Gate check (all three hold)

- **Hard to reverse:** adds a harvester subcommand, an agent, a skill, and
  a standing post-loop phase — a new subsystem surface.
- **Surprising without context:** the system proposing its own new
  capabilities reads as self-modification; the anchors (refuter verify +
  human adoption gate + validating generators) are what keep it
  institutional memory rather than drift.
- **A real trade-off was rejected:** user-invoked-only distillation
  (rejected: recurrence is exactly what the system sees and human memory
  does not) and fully automatic adoption (rejected: unverified
  crystallization of one-offs compounds as sediment; the human is the
  adoption anchor).

## When to revisit

- Refuter pass rejects nearly all candidates → loosen the recurrence
  threshold, never the gate.
- Adopted skills never fire (GC reports) → placement routing is wrong
  (wrong home), not the loop; re-route, don't delete the harvester.

## See Also

- [[conductor/design/extensibility-review-2026-08]] — the review that
  selected this loop; Finding 1's `examples` field is its first content
  consumer.
- [[conductor/design/agent-roster]] — the wrapper road the distiller
  routes to.
- [[conductor/resource/glossary]] — **skill distillation** entry.
