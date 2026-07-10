---
type: concept
sources:
  - skills/wiki
  - runtime/contracts/doc-routing
  - runtime/contracts/doc-conventions
last_verified: 2026-07-10
---

# `/wiki build` — Bulk Organize-and-File

The missing **bulk** wiki-construction operation. `wiki ingest <source>` adds
*one* source; `wiki build <dir|file|url>` organizes and files a **pile** of
sources — a folder, a file, a URL, or a pasted block — into the wiki in one
plan-then-execute pass. It is what fixes the "the wiki feels useless" symptom at
its root: greenfield and document-rich projects stay empty until tracks run, and
there was no first-class way to point the wiki at an existing corpus of docs.

## Design decisions (and why)

1. **Subcommand of the `wiki` skill, not a new skill.** Skills are auto-discovered
   from `skills/*/SKILL.md`; `build` shares the `wiki` skill's §1.1 setup-check and
   §5.0 error-handling. One routing entry. The `## 7.0` section number is stable;
   its body was later extracted to `skills/wiki/references/build.md` as part of the
   wiki-skill **progressive-disclosure split** (query/ingest/build bodies each live
   in their own `references/*.md`, read only when their sub-command is routed) — the
   router stays thin, the heavy body loads only when `build` is invoked. Section
   numbers are preserved so cross-references (`agent-error-handling.md`'s
   `wiki query (§4)`, the §6.2/§7.3 advisory pointers) and the wiring tests stay valid.

2. **Reuses `corpus-writer` + `wiki-synthesizer` UNCHANGED, in their existing
   `ad-hoc` mode.** No shared-agent change. This is the low-blast-radius choice:
   the same two-phase pipeline `/wiki ingest` and post-track doc-sync use, looped
   over a batch. Two ingestion paths is how drift creeps in (cf. the
   `database/index.md` vs `schema.md` wording drift) — there is still exactly one
   ingestion engine.

3. **Plan-then-execute, advisory plan.** Phase A walks the target, normalizes each
   source, and proposes a target via the `doc-routing.md` signal table (endpoint →
   api-specs, table → database, component → architecture, …); sources matching no
   signal are proposed for the reference home. The plan is written to a transient
   `/tmp` file (mktemp, cleaned up — not `.conductor/`, which is not reliably
   gitignored here and accumulates scratch per the wiki-doctor D4 finding). The
   human confirms **once**. Phase B batches the approved sources into chunks of ≤8
   and dispatches `corpus-writer` once per chunk (not once per source — that would
   be N confirmation rounds), then `wiki-synthesizer` once. `corpus-writer`'s own
   merge judgment is authoritative at execution, so the plan is a preview, not a
   binding spec. This avoids the alternative (a `corpus-writer` dry-run/plan mode)
   which would be the one shared-agent change; deferred.

4. **References file via the EXISTING `conductor/resource/` convention, not a new
   layer.** `doc-conventions.md` already maps the `resource` type ("references,
   gotchas, external-tool facts") to `conductor/resource/`, and `doc-routing.md`
   already does NOT route there — so external reference docs are queryable
   (`wiki-researcher` greps `conductor/**/*.md`) but deliberately never
   auto-loaded into task-executor context. Adding a top-level `conductor/references/`
   would re-introduce the exact two-files-must-agree drift (D5) this design avoids.
   External authority ≠ project truth; it stays out of execution routing.

5. **One synthesis pass, then the advisory tail.** After all sources are filed, a
   single `wiki-synthesizer` Phase 2 regenerates `overview`/`purpose`/`log`/`index`,
   then advisory `wiki-differ` + `doc-linter` (the same non-blocking tail as
   `/wiki ingest` §6.2). Filed content carries provenance frontmatter
   (`type`/`sources`/`last_verified`) per `doc-conventions.md`, so drift/lint can
   trace claims back to a source.

## `ingest` vs `build`

| | `wiki ingest` | `wiki build` |
|---|---|---|
| Granularity | one source | a batch (dir/file/url/paste) |
| Plan | per-update confirm | one plan, one confirm |
| Engine | corpus-writer → synthesizer (ad-hoc) | same, looped |
| Use | "add this one thing" | "organize and file this pile" |

## Strategic context

`build` is the production-side half of making the wiki compound from day one. The
consumption side already works — `spec-planner`/`explorer`/`task-executor` read
the corpus + synthesis layer via `doc-routing.md`. The felt sense that the wiki is
"useless" comes from it being **empty** (cold-start) plus the missing bulk-file
operation, not from a wiring gap. `build` + the `setup` cold-start offer close that
hole. The deeper strategic framing — the wiki as the plugin's spine, the
orchestrator as code — is tracked separately; this note scopes only the `build`
feature.

## Deferred (out of scope here)

- **D1 seeding fix** — plugin-shipped runtime directives (`agent-error-handling`,
  `rail-b-*`) are consumed via bare `conductor/design/…` paths but never seeded
  into user projects by `setup`. Separate concern (plugin-contracts-via-plugin-root);
  its own change.
- **`corpus-writer` dry-run/plan mode** — would let the plan phase reuse
  `corpus-writer`'s classification instead of the skill-layer signal match. Chose
  the skill-layer advisory plan to avoid the shared-agent change; revisit if the
  advisory plan proves too lossy.
- **Orchestration-as-code consolidation** — needs a focused audit before acting.

## See Also

- [[runtime/contracts/doc-routing]] — the signal table the plan phase classifies against.
- [[runtime/contracts/doc-conventions]] — `resource` type → `conductor/resource/` placement.
- [[conductor/design/agent-error-handling]] — `build` shares the ingest row
  (`corpus-writer` → `wiki-synthesizer` / `---DOC SYNC RESULT---`).
