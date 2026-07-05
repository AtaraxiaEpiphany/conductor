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

> **Write requirements in EARS** (Easy Approach to Requirements Syntax). Every
> requirement below uses one fixed clause pattern so it is clear, unambiguous,
> and testable. The EARS ruleset: zero-or-more pre-conditions, zero-or-one
> trigger, exactly one system name, one-or-more responses — and **every response
> uses `shall`** (never `should`/`may`/`will`; model optionality with a `Where`
> clause, not a weaker verb). One requirement per statement — no `and`-bundling;
> prefer positive recovery over negation (`If X, then … shall …`, not `shall not`).
> Be specific and measurable (`within 200 ms`, `≥ 12 chars`, `99.9%`), never vague
> (`fast`, `user-friendly`). The conductor's EARS lint flags any requirement
> missing `shall` or using `shall not`.
>
> **Multilingual:** `shall` is the canonical English verb, but the mandatory
> response verb may be its localized obligation equivalent — FR `doit`, ES `debe`,
> IT/PT `deve`, DE `muss`, NL `moet`, ZH `应`/`应当`/`必须`, JA `すること`, KO `한다`.
> Set `CONDUCTOR_EARS_VERBS=verb1,verb2,…` to extend the set for any other
> language (CJK verbs need no word boundary; Latin verbs are case-folded).

**EARS patterns** (keyword → when to use it):

| Pattern            | Template                                                        | Use when                                              |
| ------------------ | --------------------------------------------------------------- | ----------------------------------------------------- |
| Ubiquitous         | `The <system> shall <response>.`                                | Always in force — invariants and most NFRs.           |
| Event-driven       | `When <trigger>, the <system> shall <response>.`                | A discrete event causes an action.                    |
| State-driven       | `While <state>, the <system> shall <response>.`                 | Behavior must hold throughout a mode/condition.       |
| Optional feature   | `Where <feature>, the <system> shall <response>.`               | Only for a variant/config/feature (product-line).    |
| Unwanted behavior  | `If <trigger>, then the <system> shall <recovery>.`             | Error / exception / edge-case handling.               |

### Functional Requirements
- FR-1: When a user submits valid credentials, the system shall authenticate and issue a session token within 1 second. [(ref)](path/to/doc)
- FR-2: The system shall hash all passwords using bcrypt.

**Inline citations (optional but recommended for traceability):**
- Use Markdown links: `FR-1: User must be able to [reset password via email](conductor/design/api-specs/auth.md)`
- Or append reference: `FR-1: Password reset flow (see [Auth Design](conductor/design/api-specs/auth.md))`
- Keep inline citations lightweight — detailed derivation goes in References section.

### Non-Functional Requirements
- NFR-1: The system shall respond to read requests within 200 ms at the 95th percentile.

## Acceptance Criteria
- AC-1: [measurable criterion]
- AC-2: [measurable criterion]

> Acceptance Criteria are measurable pass/fail conditions derived from the
> requirements above; they need not use EARS (`shall`), though behavioral ACs may.

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
