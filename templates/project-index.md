# Project Context

> All paths are project-root-relative (resolved from CWD, not from this file).

**Status** classifies each row, so a missing file is never a mystery:

- `seeded` — created by `conductor:setup`; exists immediately after setup.
- `auto` — created by a skill on first use (e.g. `corpus-writer` seeds design docs post-track; `/conductor:wiki query` files `queries/`). Absent until then — by design.
- `on-demand` — a slot you (or an agent) create when first needed. Absent until then — by design.

A missing `on-demand` / `auto` row is **not** a broken link — it is an unfilled slot. Only a missing `seeded` row indicates a setup problem (run `/conductor:wiki-doctor` to surface it).

## Global Docs

Always read in full. Provide baseline context for all tasks.

<!-- WIKI: Summaries in the Purpose column are maintained by wiki-synthesizer (doc-sync Phase 2).
     Do not edit manually — they are regenerated during wiki synthesis. -->

| Doc | Path | Status | Purpose |
|-----|------|--------|---------|
| Product Definition | conductor/product/product.md | seeded | Product description and feature list |
| Product Guidelines | conductor/product/product-guidelines.md | seeded | Brand voice, UX strategy, design principles |
| Tech Stack | conductor/design/tech-stack.md | seeded | Technology stack, frameworks, and versions |
| Glossary | conductor/resource/glossary.md | on-demand | Domain terms and acronyms |
| Wiki Overview | conductor/overview.md | seeded | Global synthesis regenerated after each track |
| Wiki Purpose | conductor/purpose.md | seeded | Directional intent — goals, thesis, decisions (co-evolved) |
| Wiki Log | conductor/log.md | seeded | Append-only chronological record of doc changes |
| Wiki Queries | conductor/queries/ | auto | Saved query answers (auto-filed by `/conductor:wiki query`, type: query pages) |

## Scoped Docs

Read on demand. Check the Index file first to determine relevance, then open only matching documents.

| Category | Index / Entry Point | Status | Match Strategy |
|----------|---------------------|--------|---------------|
| PRD | conductor/requirement/prd/index.md | on-demand | Match by feature area keywords from task description |
| API Specs | conductor/design/api-specs/index.md | auto | Match by endpoint path or tags from git diff / task scope |
| Database | conductor/design/database/index.md | auto | Match by table name from git diff / migration files |
| Architecture | conductor/design/architecture/system-architecture.md | auto | Match by component name from git diff |
| UX/UI Spec | conductor/requirement/ux-ui/design-spec.md | on-demand | Match by page or component name from task description |

## Workflow & Resources

Development workflow and reference materials. Consult as needed.

| Doc | Path | Status |
|-----|------|--------|
| Workflow Index | conductor/workflow/index.md | seeded |
| Code Styleguides | conductor/workflow/code-styleguides/ | seeded |
| Git Flow | conductor/workflow/git-flow.md | on-demand |
| Testing Strategy | conductor/workflow/testing/strategy.md | seeded |
| References | conductor/resource/references/index.md | on-demand |
| FAQ | conductor/resource/faq/index.md | on-demand |

## Management

| Doc | Path | Status |
|-----|------|--------|
| Tracks Registry | conductor/tracks.md | seeded |
