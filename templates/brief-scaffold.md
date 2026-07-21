<!--
  brief.md scaffold — Read by agents/track-brief-writer.md (§4 Write).
  Substitute {Track Title}, {TRACK_ID}, {TRACK_TYPE}, and the date; fill every
  section. Italic / parenthetical HTML comments are generation guidance (what
  good content looks like), not literal output — replace them with real content.

  Readership / provenance: this file is consumed as authoritative USER_CONTEXT by
  agents/spec-planner.md (loaded first via the new-track §2.2b Brief Detection,
  before the codebase scan). Its `## Out of Scope` is honored VERBATIM by the
  planner. Keep the section headings ASCII (they are machine anchors); fill the
  body in any language. A brief.md is pre-state — it carries no track-state.json
  and the `status: brief` frontmatter is informal provenance, not a state enum.
-->

---
track_id: {TRACK_ID}
type: {TRACK_TYPE}
status: brief
created: <YYYY-MM-DD>
provenance: human-authored + conductor:brief
---

# Track Brief: {Track Title}

## Problem & Motivation
<!-- WHY this track exists — the one thing a thin description never captures.
     The user's pain, the opportunity, the trigger. What's wrong / missing today,
     and what changes once this lands. Write it the way you'd explain it to a
     new teammate who asked "why are we doing this?" -->

## Goals (in-scope)
<!-- WHAT — concrete, verifiable outcomes this track delivers. Bullets, each a
     single capability or behavior. These map to FRs and ACs downstream; keep
     each atomic and observable. -->

## Out of Scope
<!-- CRITICAL — make explicit. spec-planner copies this section VERBATIM into
     the spec's Out of Scope (it does NOT infer exclusions that contradict it).
     List features deferred to future tracks, technologies/patterns rejected,
     edge cases not supported this iteration, integration points not yet in
     scope. Minimal but clear — only items that would reasonably be in question. -->

## Context & Constraints
<!-- Tech-stack touchpoints (which modules/services this work crosses), related
     design/product docs, hard limits (performance budgets, compatibility,
     security, deadlines), and any decision already made that constrains the
     solution. Paths relative to project root. -->

## Stakeholders / Reviewers
<!-- Who cares about this work and who signs off. Names, roles, or teams. Who
     must review the spec, who verifies the acceptance criteria. -->

## Open Questions
<!-- Honest unknowns to resolve DURING planning (not blockers to starting).
     Each is something the spec/plan may need to answer or explicitly defer.
     "It's fine to leave these open" is a valid outcome — surfacing them is the
     value. -->

## Suggested Acceptance Signals
<!-- Draft ACs — coarse, user-facing pass/fail conditions. spec-reviewer refines
     these into EARS-measurable AC-N form and ensures TC coverage; you do not
     need to be precise here. One bullet per goal is a good baseline. -->

## References
<!-- Paths only (the agent reads contents itself). Group when 3+. -->
- [Tech Stack](conductor/design/tech-stack.md) — framework choices, language decisions
- [Product Definition](conductor/product/product.md) — product intent
