---
type: concept
sources:
  - runtime/core-contract
last_verified: 2026-06-24
---

# Documentation Conventions

Authoring conventions for the Conductor doc corpus. Loaded on demand by
`corpus-writer`, `wiki-synthesizer`, `doc-linter`, and the `wiki` tool — **not** resident in every
session. The behavioral invariants live in `runtime/core-contract.md`;
this page is the map for authoring corpus docs.

## Wikilink Format

Cross-references between Conductor documents use `[[wikilinks]]`:

- **Syntax:** `[[path/to/doc]]` — path relative to project root, without `.md` extension.
- **Resolution:** append `.md` to the path and check file existence.
- **Placement:** `## See Also` section at the bottom of each document.
- **Bidirectionality:** when adding A→B, also add B→A.
- **Used in:** overview.md knowledge base, corpus-writer cross-references, doc-linter orphan checks.

## Page Provenance Frontmatter

Scoped corpus docs (`conductor/design/`, `conductor/resource/`, `conductor/requirement/`, `conductor/queries/`) carry YAML frontmatter so freshness/staleness checks are **evidence-based**, not heuristic:

```yaml
---
type: architecture|api|database|ux|resource|entity|concept|source|query
sources:
  - <track_id | handoff_stem | url | path>
last_verified: <ISO-8601 date or short git SHA of the commit that last confirmed it>
---
```

- **Required fields:** `type`, `sources`, `last_verified`.
- **Exempt** (auto-owned synthesis/navigation, regenerated wholesale — frontmatter would only churn): `overview.md`, `purpose.md`, `log.md`, every `index.md`.
- **Writers:** `corpus-writer` emits/updates frontmatter on every merge or seed; the `wiki` query-save writes `type: query` pages. Merge updates `last_verified`; seed writes the full block.
- **Checkers:** `doc-linter` (§4.6) and the SessionStart GC hook report missing/empty frontmatter. `lib/frontmatter.py` is the single deterministic parser used by hooks.

## Type → Folder Placement

The frontmatter `type` (above) tells checkers *what* a doc is; this table tells writers *where* it goes. Every type has a canonical home — there is no "unplaceable" type. When corpus-writer graduates a finding (§4.10) or an agent creates a scoped doc, file it by type:

| `type`         | Canonical folder                 | Notes                                                      |
| :------------- | :------------------------------- | :--------------------------------------------------------- |
| `architecture` | `conductor/design/architecture/` | Component/structural; syncer-seeded.                       |
| `api`          | `conductor/design/api-specs/`    | One doc per endpoint; strict schema adherence.             |
| `database`     | `conductor/design/database/`     | Schema/migrations.                                         |
| `ux`           | `conductor/requirement/ux-ui/`   | UX/UI design specs.                                        |
| `concept`      | `conductor/design/`              | Cross-cutting ideas + decision records (`decision-*.md`).  |
| `entity`       | `conductor/resource/`            | Domain entities / inventory-style knowledge.               |
| `resource`     | `conductor/resource/`            | References, gotchas, external-tool facts, run constraints. |
| `source`       | `conductor/resource/`            | Ad-hoc ingest origin (provenance for a synthesized page).  |
| `query`        | `conductor/queries/`             | Saved `/conductor:wiki query` results.                     |

`overview.md`, `purpose.md`, `log.md`, and every `index.md` are exempt navigation/synthesis pages — they have no `type` and are not placed by this table.

## See Also

- [[runtime/core-contract]] — behavioral invariants; resident in every session.
- [[conductor/design/decision-serial-execution]] — why the state model is globally locked (serial execution).
- [[conductor/design/decision-loop-heartbeat]] — why housekeeping rides deterministic hooks, not a cron.
- [[conductor/design/decision-pattern-realization]] — which multi-agent patterns run on prose skills vs the Workflow tool.
