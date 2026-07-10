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

The pipeline is the same two sequenced agents post-track ingest uses, plus an advisory drift verify — all run in ad-hoc mode (synthetic assignment with no `TRACK_DIR` / `TRACK_ID`). Dispatch them in order:

**Phase 1 — `conductor:corpus-writer`**, prompt:

```
SOURCE_TYPE=ad-hoc
SOURCE_PATH={absolute path to "$SRC"}
SOURCE_NAME={slug}
```

corpus-writer runs Phase 1 in ad-hoc mode: the source IS the "spec" (§3.1 reads `SOURCE_PATH`), there are no handoffs to harvest, and commits are tagged `[wiki-ingest]` instead of `[{TRACK_ID}]` (no `track-state archive` gate applies — ad-hoc ingest never touches `track-state.json`). Parse `---DOC SYNC RESULT---` (`PHASE: 1`). `STATUS: FAILURE` → announce the reason, clean up `$SRC`, HALT.

**Phase 2 — `conductor:wiki-synthesizer`**, same ad-hoc prompt (`SOURCE_TYPE=ad-hoc SOURCE_PATH={absolute path to "$SRC"} SOURCE_NAME={slug}`). It regenerates overview, updates purpose, appends the log, and commits (`[wiki-ingest]`). Parse `---DOC SYNC RESULT---` (`PHASE: 2`). `STATUS: FAILURE` → announce, continue (non-blocking; Phase 1's commit already landed).

**Advisory verify — `conductor:wiki-differ`** scoped to the regenerated overview (`PROJECT_DIR={project root}`, target `conductor/overview.md`). Parse `---WIKI DIFF RESULT---`; non-zero STALE/MOVED/UNCOVERED → surface counts, recommend `/conductor:wiki-doctor diff` for the repair loop. Advisory and non-blocking — this is the **drift** gate (the **lint** gate follows).

**Advisory lint — `conductor:doc-linter`** on the merged corpus. Ad-hoc ingest of an arbitrary source (file / URL / paste) is precisely when lint violations land — the source rarely follows doc conventions, and corpus-writer merges it as-is. This one-shot advisory catches the orphans / stale claims / contradictions / missing frontmatter the merge may introduce; it is **not** the loop-until-dry plus refute repair loop (that lives in `/conductor:wiki-doctor lint`). Dispatch `conductor:doc-linter` (default `MODE=full`), prompt:

```
PROJECT_DIR={project root}
```

Parse `---DOC LINT RESULT---`; `STATUS: WARN`/`FAIL` → surface the counts and recommend `/conductor:wiki-doctor lint` for the repair loop. Advisory and non-blocking — `STATUS: FAILURE` → announce, continue.

### 6.3 Parse Result & Clean Up

1. After the pipeline completes, clean up the transient source:
   ```bash
   rm -f "$SRC"
   ```
2. Summarize: which wiki pages were merged/seeded (Phase 1 `UPDATED_FILES` / `GRADUATED_FINDINGS`), whether overview/purpose were regenerated (Phase 2 `OVERVIEW_REGENERATED` / `PURPOSE_UPDATED` / `LOG_ENTRIES_ADDED`), and any advisory drift (the `---WIKI DIFF RESULT---` STALE/MOVED/UNCOVERED counts). The tracked artifacts are the corpus + wiki pages the agents committed — the raw source is gone by design.

### 6.4 No-Op Path

If corpus-writer reports `STATUS: SKIPPED` (the source added nothing the corpus didn't already contain — idempotent ingest), Phase 2 (wiki-synthesizer) still runs — it regenerates overview from the current corpus (a no-op if nothing changed) and reports its own status. Announce "Source `<slug>` already reflected in the wiki; no changes." if both phases report no work. Clean up `$SRC`. This is correct behavior, not an error.

---
