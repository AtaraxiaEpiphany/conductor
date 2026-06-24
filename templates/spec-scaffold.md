<!--
  spec.md scaffold — Read by agents/spec-planner.md (§4.1 Generate spec.md).
  Substitute {Track Description} and {TRACK_TYPE}; fill every section. Italic /
  parenthetical notes are generation guidance (what good content looks like),
  not literal output — replace them with real content. Apply the §4.1 Rules.
-->

# Specification: {Track Description}

## Overview
[Brief summary of what this track delivers]

## Type
{TRACK_TYPE}

## Requirements

### Functional Requirements
- FR-1: [requirement] [(ref)](path/to/doc)
- FR-2: [requirement]

**Inline citations (optional but recommended for traceability):**
- Use Markdown links: `FR-1: User must be able to [reset password via email](conductor/design/api-specs/auth.md)`
- Or append reference: `FR-1: Password reset flow (see [Auth Design](conductor/design/api-specs/auth.md))`
- Keep inline citations lightweight — detailed derivation goes in References section.

### Non-Functional Requirements
- NFR-1: [requirement]

## Acceptance Criteria
- AC-1: [measurable criterion]
- AC-2: [measurable criterion]

## Test Scenarios

Map each AC to concrete test scenarios. These guide the conductor:task-executor's TDD Step 3 (Red phase).

| ID     | AC Ref | Scenario                     | Expected Outcome  |
| ------ | ------ | ---------------------------- | ----------------- |
| TC-1.1 | AC-1   | [test scenario description]  | [expected result] |
| TC-1.2 | AC-1   | [edge case / error scenario] | [expected result] |
| TC-2.1 | AC-2   | [test scenario description]  | [expected result] |

## Constraints
- [technical or business constraints]

## Out of Scope
- [features, technologies, or use cases explicitly excluded from this track]
  Examples:
  - [Feature X] deferred to future Track ID-YYYYMMDD
  - [Technology Y] — team standardized on [Alternative Z]
  - [Edge case] — not supported in this iteration

## References

(For 3+ references, group by category)

### Project Context
- [Tech Stack](conductor/design/tech-stack.md) — Framework choices, language decisions
- [Product Guidelines](conductor/product/product-guidelines.md) — UX requirements, brand voice

### Related Design Docs (if applicable)
- [System Architecture](conductor/design/architecture/system-architecture.md) — Component boundaries
- [API Specs](conductor/design/api-specs/index.md) — Endpoint patterns

### Prior Track Context (if building on existing work)
- [Track: auth-flow](conductor/tracks/auth-flow_20250115/spec.md) — Reusable auth components

(For minimal references, use flat format below)
- [Tech Stack](conductor/design/tech-stack.md) — Framework choices
- [Product Guidelines](conductor/product/product-guidelines.md) — UX voice
