---
type: concept
sources:
  - agents/corpus-writer
  - agents/wiki-synthesizer
last_verified: 2026-08-20
---

# Doc Sync Procedure Reference

Per-document analysis criteria, proposal templates, and Phase 2 wiki-synthesis
specs factored out of the doc-sync agents (`agents/corpus-writer.md` runs Phase 1,
`agents/wiki-synthesizer.md` runs Phase 2). Loaded on demand by both during
Phase 1 (analysis + proposals) and Phase 2 (overview/purpose synthesis).

The agent body holds the **procedure** — two-step CoT ordering, control flow,
gates, the execute/commit/log steps, and the Execution Firewall. This page
holds the **reference material** the procedure points at: the per-document
table, the proposal template text, and the field-level synthesis specs.

## A. Per-Document Analysis & Proposal Routing

Phase 1 compares the source against each project document. For each document,
apply its **analysis criteria** below — if any criterion matches, the document
**owes an update** and is carried into the proposal step. Skip a document
whose guard says SKIP, and skip any document that does not exist (per §3.2).

### Proposal template

§A's per-document rows all use this prompt; only the **Doc name** and the
change list vary:

> "The completed track '{TRACK_DESCRIPTION}' affects {DocName}. Proposed changes:\n\n{specific additions/modifications}\n\nApply these updates?"

Options: "Yes, apply" / "Skip".

**Variants** (apply per the row's Proposal column):

- **caution** (Product Guidelines) — prefix the prompt with `⚠️ ` and append " (Use extreme caution)".
- **terms** (Glossary) — replace the "Proposed changes" line with "introduces new terms. Proposed additions:\n\n{term definitions}".
- **base** — the template unchanged.

### Per-document table

| Document | Analysis criteria (flag if any match) | Proposal | Doc name |
| :------- | :------------------------------------- | :------- | :------- |
| Product Definition (`conductor/product/product.md`) | • significant change to the product description • new user-facing features or capabilities to document • removed or deprecated features | base | Product Definition |
| Tech Stack | • new technologies, frameworks, or libraries introduced • technologies removed or replaced • version changes that need documentation | base | Tech Stack |
| Product Guidelines (`conductor/product/product-guidelines.md`) | • ONLY analyze if the track explicitly describes branding, voice, or strategy changes • if the track is a technical feature with no UX/brand impact → SKIP entirely (apply with **extreme caution** when it does match) | caution | Product Guidelines |
| System Architecture | • system components, services, or data flows added, removed, or modified • new integrations, external services, or infrastructure changes • component boundaries or responsibilities changed | base | System Architecture |
| Database Schema | • tables, columns, indexes, or constraints created, modified, or dropped • new migrations or schema changes that need documentation | base | Database Schema |
| API Specifications | • API endpoints added, modified, or removed • changes to request/response schemas, authentication, or error codes • if changes exist, also check individual endpoint spec files in `conductor/design/api-specs/` | base | API Specifications |
| UX/UI Design Spec | • ONLY analyze if the track changes user interface components, layouts, or interaction flows • new screens, components, or navigation changes | base | UX/UI Design Spec |
| Glossary | • new domain terms, acronyms, or concepts that need defining • terms used in the spec that are not yet in the glossary • the glossary may already carry grill-stage entries (crystallization writes, [[runtime/contracts/grill-discipline]] §7) — merge alongside, never duplicate or clobber | terms | Glossary |

## B. Overview Regeneration Spec

Regenerate `conductor/overview.md` **in its entirety** (not append). Synthesize
from all currently loaded documents:

1. **Summary:** 2–4 sentences synthesizing the project from `product.md` + track history.
2. **Architecture:** High-level system description from `system-architecture.md`. Component names become `[[wikilinks]]`.
3. **Knowledge Base:** Table of key concepts from all docs. Format: `| Topic | Summary | Source |` where Source is a `[[wikilink]]`.
4. **Active Decisions:** Architecture/design decisions accumulated from track specs and design docs.
5. **Track History Summary:** Compact summary of completed tracks from `tracks.md` + `log.md`.
6. **Cross-Reference Index:** Alphabetical list of all `conductor/**/*.md` files with their `[[wikilink]]` paths.

Replace the entire file.

## C. Purpose Update Spec

`conductor/purpose.md` is the wiki's directional intent — **co-evolved**, not
auto-owned like `overview.md`. Update it with Edit (targeted), **never** a
wholesale Write. The Goals and In/Out-of-Scope sections are **user-authored**;
touch them only to append a settled exclusion the user confirmed.
LLM-maintained sections:

1. **Evolving Thesis** — refresh the synthesized direction from this track's spec + the harvested `decisions[]`. Surface — do not hide — any contradiction this track introduced with the prior thesis.
2. **Active Decisions** — append each harvested `## Technical Decision:` outcome as one bullet: `**{title}**: {chosen} — {reasoning} → [[source doc]]`. Merge (dedupe by title); never re-add a decision already present.
3. **Key Questions** — if this track resolved an open question, strike it (`~~question~~`) and move the resolution into Thesis; if it surfaced a new open question, add it.

If `purpose.md` does not exist, create it from the `${CLAUDE_PLUGIN_ROOT}/templates/wiki-purpose.md` template, seed Goals from `conductor/product/product.md`, then apply the updates above.

If this run had **no** decisions, no spec-level direction change, and resolved/raised no key questions → leave `purpose.md` unchanged (a no-op Phase 2 is correct; do not force a touch).

## See Also

- [[runtime/contracts/doc-conventions]] — provenance frontmatter + wikilink format used by the execute step.
- [[runtime/contracts/doc-routing]] — scope→doc routing shared with task-executor, explorer, code-reviewer.
- [[skills/wiki/references/doc-sync-pipeline]] — ad-hoc dispatch contract, Phase 1/2 sequencing, and advisory tail. This page is the **content/reference** layer (what to analyze + synthesize); the pipeline reference is the **orchestration** layer (how to dispatch, sequence, and parse in ad-hoc mode) for `wiki ingest` / `wiki build`.
