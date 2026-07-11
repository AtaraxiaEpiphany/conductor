<!--
  Reference body for the `wiki` skill — loaded ON DEMAND by the §2.0 router
  (progressive disclosure: this file is read only when its sub-command is
  invoked, keeping the Level-2 router body thin). Section numbers are stable —
  tests and the agent-error-handling design doc index them — edit in place.
-->

## 6.0 INGEST

**Ingest a single source into the wiki — uncoupled from the track lifecycle.** This is the "drop one source → file it" path: it routes the source through the *same* canonical doc-sync pipeline (corpus-writer + wiki-synthesizer) that post-track ingest uses, preserving the merge-not-append / idempotent / drift-gated discipline. For a **batch** of sources (a folder, many files, a URL list), use `build` (§7.0) instead — it loops this same pipeline over the batch with one plan and per-chunk confirmation. The wiki skill stays a thin router; corpus-writer remains the single corpus writer, wiki-synthesizer the single wiki synthesizer.

### 6.1 Resolve & Normalize the Source

`SUB_ARGS` is the source. Determine its kind:

| Source form | How to normalize |
|---|---|
| Existing file path | `Read` it. If not markdown, read anyway (corpus-writer treats prose as the source). |
| URL (`http://`/`https://`) | `WebFetch` it as markdown. |
| Pasted block / bare text | Use `SUB_ARGS` verbatim. |

1. **Slug** the source: lowercase, hyphenate, strip special chars from a title derived from the source (heading / filename / URL path). Example: `https://x/auth-guide` → `auth-guide`.
2. **Normalize to markdown** and write to a transient file (the raw source is *working memory*, never a tracked corpus file — it respects the 3-channel model):
   ```bash
   SRC="$(mktemp /tmp/wiki-ingest-XXXXXX.md)"
   # write the normalized markdown to "$SRC" via a heredoc or Write tool
   ```
3. **Verify** the file is non-empty. If empty/failed → HALT: "Could not normalize source `<source>`."

### 6.2 Dispatch the Doc-Sync Pipeline (ad-hoc mode)

The canonical ad-hoc doc-sync run — Phase 1 (`corpus-writer`) → Phase 2 (`wiki-synthesizer`) → advisory tail (`wiki-differ` + `doc-linter`) — is owned once in **`${CLAUDE_PLUGIN_ROOT}/skills/wiki/references/doc-sync-pipeline.md`** (single source of truth, shared with `build` §7.0). Read it for the per-phase contract, result-block parsing, and the advisory tail. For `ingest` (a single source), dispatch in order:

1. **Phase 1 — `conductor:corpus-writer`** with `SOURCE_PATH={absolute path to "$SRC"}`, `SOURCE_NAME={slug}`. `STATUS: FAILURE` → announce the reason, clean up `$SRC`, HALT (a single-source ingest is fatal on failure). `STATUS: SKIPPED` → §6.4.
2. **Phase 2 — `conductor:wiki-synthesizer`** with the same `SOURCE_PATH={absolute path to "$SRC"}`, `SOURCE_NAME={slug}`.
3. **Advisory tail** — the `conductor:wiki-differ` + `conductor:doc-linter` advisory per `doc-sync-pipeline.md`.

### 6.3 Parse Result & Clean Up

1. After the pipeline completes, clean up the transient source:
   ```bash
   rm -f "$SRC"
   ```
2. Summarize: which wiki pages were merged/seeded (Phase 1 `UPDATED_FILES` / `GRADUATED_FINDINGS`), whether overview/purpose were regenerated (Phase 2 `OVERVIEW_REGENERATED` / `PURPOSE_UPDATED` / `LOG_ENTRIES_ADDED`), and any advisory drift (the `---WIKI DIFF RESULT---` STALE/MOVED/UNCOVERED counts). The tracked artifacts are the corpus + wiki pages the agents committed — the raw source is gone by design.

### 6.4 No-Op Path

If corpus-writer reports `STATUS: SKIPPED` (the source added nothing the corpus didn't already contain — idempotent ingest), Phase 2 (wiki-synthesizer) still runs — it regenerates overview from the current corpus (a no-op if nothing changed) and reports its own status. Announce "Source `<slug>` already reflected in the wiki; no changes." if both phases report no work. Clean up `$SRC`. This is correct behavior, not an error.

---
