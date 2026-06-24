# Project Context

> All paths are project-root-relative (resolved from CWD, not from this file).

## Global Docs

Always read in full. Provide baseline context for all tasks.

<!-- WIKI: Summaries in the Purpose column are maintained by doc-syncer Phase 2.
     Do not edit manually — they are regenerated during wiki synthesis. -->

| Doc | Path | Purpose |
|-----|------|---------|
| Product Definition | conductor/product/product.md | Product description and feature list |
| Product Guidelines | conductor/product/product-guidelines.md | Brand voice, UX strategy, design principles |
| Tech Stack | conductor/design/tech-stack.md | Technology stack, frameworks, and versions |
| Glossary | conductor/resource/glossary.md | Domain terms and acronyms |
| Wiki Overview | conductor/overview.md | Global synthesis regenerated after each track |
| Wiki Purpose | conductor/purpose.md | Directional intent — goals, thesis, decisions (co-evolved) |
| Wiki Log | conductor/log.md | Append-only chronological record of doc changes |

## Scoped Docs

Read on demand. Check the Index file first to determine relevance, then open only matching documents.

| Category | Index / Entry Point | Match Strategy |
|----------|---------------------|---------------|
| PRD | conductor/requirement/prd/index.md | Match by feature area keywords from task description |
| API Specs | conductor/design/api-specs/index.md | Match by endpoint path or tags from git diff / task scope |
| Database | conductor/design/database/index.md | Match by table name from git diff / migration files |
| Architecture | conductor/design/architecture/system-architecture.md | Match by component name from git diff |
| UX/UI Spec | conductor/requirement/ux-ui/design-spec.md | Match by page or component name from task description |

## Workflow & Resources

Development workflow and reference materials. Consult as needed.

| Doc | Path |
|-----|------|
| Workflow Index | conductor/workflow/index.md |
| Code Styleguides | conductor/workflow/code-styleguides/ |
| Git Flow | conductor/workflow/git-flow.md |
| Testing Strategy | conductor/workflow/testing/strategy.md |
| References | conductor/resource/references/index.md |
| FAQ | conductor/resource/faq/index.md |

## Management

| Doc | Path |
|-----|------|
| Tracks Registry | conductor/tracks.md |
