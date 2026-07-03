---
type: concept
sources:
  - agents/task-executor
  - agents/explorer
  - agents/code-reviewer
  - agents/wiki-researcher
  - agents/corpus-writer
last_verified: 2026-06-26
---

# Scoped Doc Routing

Canonical map from **task/topic scope** → the scoped wiki doc to read. Shared by
the five agents that load scoped corpus context (`task-executor`, `explorer`,
`code-reviewer`, `wiki-researcher`, `corpus-writer`). Each agent still consults
`conductor/index.md` (the **Scoped Docs** table with its Match Strategy) as the
authoritative index; this page is the quick-reference routing those agents
previously restated independently — and which had drifted (e.g.
`database/index.md` vs `schema.md`).

## Routing table

| Signal / File Pattern | Read Scoped Doc | Match By |
|---|---|---|
| Endpoint path, request/response, API verb; `routes/**`, `controllers/**`, `api/**` | `conductor/design/api-specs/index.md` → matching endpoint doc | Endpoint path or handler name |
| Models/migrations/schema files; specific table, column, or entity name | `conductor/design/database/index.md` (per-table detail: `conductor/design/database/schema.md`) | Table name from file path / entity name |
| Component, service, or data flow; `services/**`, `lib/**`, `src/**` (structural) | `conductor/design/architecture/system-architecture.md` | Component name from directory structure |
| User-facing feature, screen, UX flow; `components/**`, `pages/**`, `views/**` | `conductor/requirement/ux-ui/design-spec.md` (feature-level requirement: the PRD under `conductor/requirement/`) | Page or component name |
| Domain term or acronym | `conductor/resource/glossary.md` | Term lookup |
| Technology, framework, or version | `conductor/design/tech-stack.md` | Technology name |

## Rules

- Read **only** matching docs — never the whole corpus. Skip any scoped doc that does not exist or whose Match Strategy doesn't apply. (Retrieval is scoped on purpose: the durable architecture lives in the corpus and compounds across tracks via `corpus-writer`.)
- **Exception — `corpus-writer` reads every row**, not just the matching one: its job is to detect divergence across the whole corpus, so it loads all scoped docs listed here.
- `explorer`, `task-executor`, and `code-reviewer` share the same routing intentionally — the explorer's findings feed the same scoped docs the executor later reads.
- Greenfield / no match → record `consulted_docs: []` (a graduation signal: findings will *seed* the corpus).

## See Also

- [[runtime/contracts/doc-conventions]] — corpus authoring conventions.
