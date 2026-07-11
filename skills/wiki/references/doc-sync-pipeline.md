<!--
  Shared reference for the `wiki` skill's `ingest` (§6.0) and `build` (§7.0)
  sub-commands — the canonical doc-sync pipeline run in ad-hoc mode, plus the
  post-pipeline advisory tail. Both sub-commands delegate here (single source of
  truth — no two-files-must-agree drift between ingest and build); each supplies
  only its entry shape (ingest: one normalized source; build: chunked sources).
  Loaded on demand by whichever sub-command is routed.
-->

# Doc-Sync Pipeline (ad-hoc mode) — shared reference

The same two-phase pipeline post-track doc-sync uses — `conductor:corpus-writer` (Phase 1) then `conductor:wiki-synthesizer` (Phase 2) — followed by a non-blocking advisory drift verify + advisory lint. Run in **ad-hoc mode**: synthetic assignment with no `TRACK_DIR` / `TRACK_ID`. `corpus-writer` is the single corpus writer, `wiki-synthesizer` the single wiki synthesizer — there is exactly one ingestion engine (no parallel path to drift), whether the caller is `ingest` (one source), `build` (a batch), or post-track sync.

## Ad-hoc dispatch contract

Both Phase 1 and Phase 2 take the same three params:

```
SOURCE_TYPE=ad-hoc
SOURCE_PATH=<absolute path to the normalized source / chunk / plan file>
SOURCE_NAME=<slug for a single source, or `wiki-build` for a batch>
```

In ad-hoc mode the source IS the "spec" (corpus-writer `§3.1` reads `SOURCE_PATH`), there are no handoffs to harvest, and commits are tagged `[wiki-ingest]` instead of `[{TRACK_ID}]` — no `track-state archive` gate applies, and ad-hoc ingest **never touches `track-state.json`**.

## Phase 1 — `conductor:corpus-writer`

Dispatch with the ad-hoc contract above. corpus-writer analyzes the source against the corpus, proposes + applies its user-confirmed edits, graduates durable findings, and commits (`[wiki-ingest]`). Parse `---DOC SYNC RESULT---` (`PHASE: 1`):

- `STATUS: FAILURE` → announce the reason. **Fatal for `ingest`** (a single source): clean up the transient source and HALT. **Non-blocking for `build`**: continue to the next chunk — prior chunk commits stand, and the failed chunk's sources remain at their origin for a re-run (partial build).
- `STATUS: SKIPPED` → the source added nothing the corpus didn't already contain (idempotent); Phase 2 still runs. (For `build`, SKIPPED is per-chunk; see each sub-command's no-op path.)

Collect `UPDATED_FILES` / `GRADUATED_FINDINGS` for the summary.

## Phase 2 — `conductor:wiki-synthesizer`

Dispatch **once** with the ad-hoc contract. For `ingest`, `SOURCE_PATH` is the normalized source; for `build`, `SOURCE_PATH` is the **plan file** (it re-reads the plan for direction). It regenerates `overview.md` from the now-updated full corpus, co-edits `purpose.md`, appends the log, and commits (`[wiki-ingest]`). Parse `---DOC SYNC RESULT---` (`PHASE: 2`); `STATUS: FAILURE` → announce, **continue (non-blocking — Phase 1 commits already landed)**. Capture `OVERVIEW_REGENERATED` / `PURPOSE_UPDATED` / `LOG_ENTRIES_ADDED` for the summary.

## Advisory tail

Two non-blocking, one-shot checks run after the pipeline. Neither is the loop-until-dry plus refute repair loop — that lives in `/conductor:wiki-doctor`.

### Advisory verify — `conductor:wiki-differ`

Scoped to the regenerated overview: prompt `PROJECT_DIR={project root}`, target `conductor/overview.md`. Parse `---WIKI DIFF RESULT---`; non-zero STALE/MOVED/UNCOVERED → surface the counts and recommend `/conductor:wiki-doctor diff` for the repair loop. Advisory and non-blocking — this is the **drift** gate.

### Advisory lint — `conductor:doc-linter`

Ad-hoc ingest of an arbitrary source (file / URL / paste, or a batch of them) is precisely when lint violations land — the sources rarely follow doc conventions, and corpus-writer merges them as-is. This one-shot advisory catches the orphans / stale claims / contradictions / missing frontmatter the merge may introduce. Dispatch `conductor:doc-linter` (default `MODE=full`), prompt `PROJECT_DIR={project root}`. Parse `---DOC LINT RESULT---`; `STATUS: WARN`/`FAIL` → surface the counts and recommend `/conductor:wiki-doctor lint` for the repair loop. `STATUS: FAILURE` → announce, continue. Advisory and non-blocking — **not** the loop-until-dry repair loop (that lives in `/conductor:wiki-doctor lint`).

## See Also

- [[runtime/contracts/doc-sync-procedure]] — the per-document analysis-criteria table, proposal template, and overview/purpose synthesis specs. This reference is the **orchestration** layer (ad-hoc dispatch + Phase 1/2 sequencing + advisory tail); the procedure contract is the **content/reference** layer (what corpus-writer analyzes and wiki-synthesizer regenerates). The two describe the same two-phase engine at different layers — keep them aligned.
