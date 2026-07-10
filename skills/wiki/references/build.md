<!--
  Reference body for the `wiki` skill — loaded ON DEMAND by the §2.0 router
  (progressive disclosure: this file is read only when its sub-command is
  invoked, keeping the Level-2 router body thin). Section numbers are stable —
  tests and the agent-error-handling design doc index them — edit in place.
-->

## 7.0 BUILD

**Organize and file a batch of sources into the wiki — uncoupled from the track lifecycle.** `ingest` (§6.0) files *one* source; `build` files a *pile* — a directory (walked recursively), a single file, a URL, or a pasted block — in one plan-then-execute pass. This is the "point me at a folder of docs and file them properly" operation. It reuses the *same* canonical doc-sync pipeline (corpus-writer Phase 1 + wiki-synthesizer Phase 2 + advisory wiki-differ/doc-linter) as ingest and post-track sync: sources are batched into chunks and corpus-writer is dispatched once per chunk, so there is still exactly **one** ingestion engine (no parallel path to drift). The wiki skill stays a thin router.

### 7.1 Resolve & Enumerate the Target

`SUB_ARGS` is the target. Determine its kind and enumerate sources:

| Target form | How to enumerate |
|---|---|
| Directory | `find <dir> -type f` filtered to text docs (`.md`, `.txt`, `.markdown`, `.org`, `.rst`); skip binaries; skip any `conductor/` nested inside it (never re-ingest the wiki into itself). |
| Existing file | One source (the file). |
| URL (`http://`/`https://`) | One source; `WebFetch` it as markdown. |
| Pasted block / bare text | One source; verbatim. |

For each source, derive a **slug** (lowercase, hyphenate, strip specials, from heading / filename / URL-path) and **normalize to markdown** into a transient file. Accumulate `(slug, source_kind, origin, normalized_temp_path, first_heading)`. If enumeration yields zero sources → HALT: "No sources found at `<target>`."

> **Raw sources are working memory, never tracked corpus files** (the 3-channel model): normalize to `/tmp`, not under `conductor/`. Validate every tool call; halt on failure.

### 7.2 Plan (Phase A — classify + preview + confirm)

A preview the human approves **once**, before any merge. Classification is **advisory** — corpus-writer's own merge judgment is authoritative at execution (§7.3); this plan orients the human and bounds the batch.

1. **Classify each source** against `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/doc-routing.md` signal table — propose a target home:
   - endpoint / API signal → `conductor/design/api-specs/`
   - table / migration / entity → `conductor/design/database/`
   - component / service / flow → `conductor/design/architecture/`
   - screen / UX / view → `conductor/requirement/ux-ui/`
   - domain term / acronym → `conductor/resource/glossary.md`
   - tech / framework / version → `conductor/design/tech-stack.md`
   - **no signal (external reference, background reading)** → `conductor/resource/` as `type: resource` (query-only; deliberately not routed into task-executor context).
2. **Write the plan** to a transient file (mktemp), one row per source: `slug | origin | proposed home | action {merge|seed} | one-line rationale`.
3. **Cap (no silent caps).** If enumeration yields more than **40** sources, keep the 40 highest-signal (named-doc signal match beats keyword density) and announce "Found <N> sources; building the top 40 (`<slugs>`). Re-run on a narrower target for the rest." The truncation is surfaced, not silent.
4. **Present + confirm once.** Show a plan summary (counts per target home + a few example rows; full detail at the plan-file path). `AskUserQuestion`: "File `<N>` sources per this plan?" → **Yes** → §7.3. **Edit** → accept edits (drop sources, retarget) → proceed. **Cancel** → clean up temp files → HALT.

### 7.3 Execute (Phase B — batched corpus-writer → one synthesizer → advisory)

Apply the approved plan. Sources are **batched into chunks** so corpus-writer confirms once per chunk (not once per source), keeping the batch tractable.

1. **Chunk** the approved sources into groups of **≤ 8** (announce "Filing `<N>` sources in `<C>` chunks."). Concatenate each chunk's normalized sources into one chunk file with `<!-- source: <slug> (<origin>) -->` separators (preserves per-source identity for provenance).
2. **Per chunk — dispatch `conductor:corpus-writer` Phase 1** (ad-hoc mode; the same §6.2 Phase 1 prompt), prompt:
   ```
   SOURCE_TYPE=ad-hoc
   SOURCE_PATH={absolute path to the chunk file}
   SOURCE_NAME=wiki-build
   ```
   corpus-writer analyzes the chunk against the corpus, proposes + applies user-confirmed edits (its normal per-chunk confirmation), graduates durable findings, and commits (`[wiki-ingest]`). Parse `---DOC SYNC RESULT---` (`PHASE: 1`); `STATUS: FAILURE` → announce reason, continue to the next chunk (non-blocking; prior chunk commits stand). Collect each chunk's `UPDATED_FILES` / `GRADUATED_FINDINGS`.
3. **Once after all chunks — dispatch `conductor:wiki-synthesizer` Phase 2** (ad-hoc mode), prompt:
   ```
   SOURCE_TYPE=ad-hoc
   SOURCE_PATH={absolute path to the plan file}
   SOURCE_NAME=wiki-build
   ```
   It re-reads the plan for direction, regenerates `overview.md` from the now-updated full corpus, co-edits `purpose.md`, appends the log (one `INGEST` row for the batch + `WIKI_REGEN` / `PURPOSE_UPDATE`), and commits (`[wiki-ingest]`). Parse `---DOC SYNC RESULT---` (`PHASE: 2`); `STATUS: FAILURE` → announce, continue (non-blocking; Phase 1 commits already landed).
4. **Advisory verify — `conductor:wiki-differ`** scoped to the regenerated overview (`PROJECT_DIR={project root}`, target `conductor/overview.md`). Parse `---WIKI DIFF RESULT---`; non-zero STALE/MOVED/UNCOVERED → surface counts, recommend `/conductor:wiki-doctor diff` for the repair loop. Advisory, non-blocking.
5. **Advisory lint — `conductor:doc-linter`** on the merged corpus (default `MODE=full`), prompt:
   ```
   PROJECT_DIR={project root}
   ```
   Parse `---DOC LINT RESULT---`; `STATUS: WARN`/`FAIL` → surface counts, recommend `/conductor:wiki-doctor lint` for the repair loop. Advisory, non-blocking — this is the one-shot advisory, not the loop-until-dry repair loop (that lives in `/conductor:wiki-doctor lint`).

### 7.4 Parse Results & Clean Up

1. Clean up all transient files:
   ```bash
   rm -f /tmp/wiki-build-*   # chunk files + plan file
   ```
2. **Summarize:** sources filed (union of chunks' `UPDATED_FILES` / `GRADUATED_FINDINGS`, grouped by target home), whether overview/purpose were regenerated (Phase 2 `OVERVIEW_REGENERATED` / `PURPOSE_UPDATED` / `LOG_ENTRIES_ADDED`), any advisory drift (the `---WIKI DIFF RESULT---` STALE/MOVED/UNCOVERED counts), and any advisory lint counts. The tracked artifacts are the corpus + wiki pages the agents committed — the raw sources and the plan are gone by design.

### 7.5 No-Op / Partial Path

If every chunk's corpus-writer reports `STATUS: SKIPPED` (the batch added nothing the corpus didn't already contain — idempotent), Phase 2 still runs (regenerates overview from the current corpus; a no-op if nothing changed). Announce "Batch already reflected in the wiki; no changes." if both phases report no work. Clean up temp files. Correct behavior, not an error. A chunk that `FAILURE`s while others succeed is a *partial* build — announce which chunks filed and which failed; the failed chunks' sources remain available at their origin for a re-run.
