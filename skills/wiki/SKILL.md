---
name: wiki
description: Queries the Conductor documentation wiki — status snapshots and topic search with citations
when_to_use: User wants to check wiki health overview or search the wiki for a topic
argument-hint: "<status|query> [args]"
allowed-tools: Bash, Read, Grep, Glob, Agent, AskUserQuestion, Write, Edit, WebFetch
model: sonnet
---

# Conductor Wiki

## 1.0 SYSTEM DIRECTIVE

You are a **Conductor Wiki Agent** — a specialized skill that reads and queries the project's documentation wiki. You inspect wiki health and search for information without requiring a running track.

**Available sub-commands:**
- `status` — Health snapshot of wiki infrastructure and coverage
- `purpose` — Read / co-edit the project's directional intent (`purpose.md`)
- `query <topic>` — Search wiki and synthesize an answer with citations
- `ingest <source>` — Build the wiki from an arbitrary source (file path / URL / pasted block) — no track required

**For health audits and drift detection**, use `/conductor:wiki-doctor` instead.

**Core Protocols:** File paths resolved via project CLAUDE.md TOC.

CRITICAL: You must validate the success of every tool call. If any tool call fails, halt immediately, announce the failure, and await instructions.

---

## 1.1 SETUP CHECK

Fetch and execute `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/wiki-setup-check.md`. Additionally resolve `conductor/purpose.md` (directional intent — read/co-edited by the `purpose` sub-command).

---

## 2.0 PARSE & ROUTE

Parse `$ARGUMENTS` and dispatch to the appropriate sub-command.

### 2.1 Argument Parsing

1. Read `$ARGUMENTS`.
2. Split on first whitespace into `SUBCOMMAND` and `SUB_ARGS` (remainder).
3. Trim and lowercase `SUBCOMMAND`.

### 2.2 Routing

| SUBCOMMAND | Target Section |
|------------|---------------|
| `status` | **Section 3.0** |
| `purpose` | **Section 3.5** |
| `query` | **Section 4.0** (requires `SUB_ARGS` as topic) |
| `ingest` | **Section 6.0** (requires `SUB_ARGS` as source) |
| empty / unrecognized | **Usage help** (below) → HALT |

### 2.3 Usage Help

If `$ARGUMENTS` is empty or `SUBCOMMAND` is unrecognized, present:

```
# /conductor:wiki — Wiki Read Operations

Usage: /conductor:wiki <subcommand> [args]

Sub-commands:
  status           Health snapshot of wiki infrastructure and coverage
  purpose          Read / co-edit the project's directional intent (purpose.md)
  query <topic>    Search wiki and synthesize an answer with [[wikilink]] citations
  ingest <source>  Build the wiki from an arbitrary source (no track needed)

Health diagnostics:
  /conductor:wiki-doctor lint     Full wiki health audit (see doc-linter §4)
  /conductor:wiki-doctor diff     Compare wiki docs against codebase
```

Then HALT.

---

## 3.0 STATUS

**Delegates metric gathering to `wiki-status`.** The skill runs the script and renders its JSON.

### 3.1 Run `wiki-status`

```bash
wiki-status "<project root>"
```

Parse the JSON. If `status == "infra_missing"` → halt: "Wiki infrastructure incomplete — missing: `<missing>`. Run `/conductor:setup` to initialize." Otherwise render (§3.2).

The JSON carries: `document_count`; `log` (`entries`, `last_timestamp`, `last_summary`); `overview` (`timestamp`, `classification` ∈ fresh/stale/outdated); `orphan_scan` (`broken_count`, `broken_targets[]`, `in_files`); `tracks` (`completed`/`in_progress`/`new`/…).

### 3.2 Present Status Report

Render the metrics:

```
# Wiki Status
Generated: <current date>

## Infrastructure
- Overview: <overview.classification> (last updated: <overview.timestamp>)
- Log: ✅ <log.entries> entries (last: <log.last_timestamp> — <log.last_summary>)

## Coverage
- Wiki documents: <document_count>
- Broken [[wikilinks]]: <orphan_scan.broken_count> (in <orphan_scan.in_files> files)
- Targets: <orphan_scan.broken_targets, or "None detected">

## Tracks
- Completed: <tracks.completed> | In Progress: <tracks.in_progress> | New: <tracks.new>
```

### 3.3 Recommendations

Append based on the metrics:

- `overview.classification != fresh` → "Overview is <stale|outdated>. Run `/conductor:implement` on a track to trigger wiki regeneration."
- `orphan_scan.broken_count > 0` → "Broken cross-references detected. Run `/conductor:wiki-doctor lint` for a full audit."
- `log.entries == 0` → "Log is empty. Wiki may not have been initialized properly."
- Otherwise → "Wiki is healthy. No action needed."

---

## 3.5 PURPOSE

**Inline read + co-edit operation.** Reads the project's directional intent; offers to co-edit it.

### 3.5.1 Read

1. **Locate** `conductor/purpose.md` via Glob.
2. **Missing** → halt: "`purpose.md` not found. It is created by `/conductor:setup` (and maintained by wiki-synthesizer Phase 2). Run `/conductor:setup`, or I can seed it from the template now." Offer via `AskUserQuestion`: "Seed `purpose.md` from template?" → **Yes** → Read `${CLAUDE_PLUGIN_ROOT}/templates/wiki-purpose.md`, replace `{TIMESTAMP}`, Write to `conductor/purpose.md`, then continue. **No** → HALT.
3. **Present** the full `purpose.md` content to the user verbatim (it is short by design).

### 3.5.2 Offer Co-Edit

`purpose.md` is **co-evolved** — the human owns the Goals and In/Out-of-Scope sections; wiki-synthesizer maintains Thesis/Decisions/Key-Questions. Ask via `AskUserQuestion`:

> "Edit `purpose.md`? You own the Goals and Scope sections; the Thesis/Decisions/Questions are auto-maintained."

Options:
- **Add a goal / scope note** → prompt for the text, Edit the matching section (append).
- **Refine a key question** → prompt, Edit the Key Questions section.
- **Done (read-only)** → HALT.

On any edit: announce the section changed and note "wiki-synthesizer will reconcile Thesis/Decisions on the next track — your Goals/Scope edits are preserved."

---

## 4.0 QUERY

**Delegates retrieval + synthesis to the `conductor:wiki-researcher` agent.** The skill validates input, presents the answer, and offers to persist it.

### 4.1 Validate Input

1. Check that `SUB_ARGS` is non-empty (the topic to search for).
2. If empty → `AskUserQuestion`: "What topic would you like to search the wiki for?"
3. Use the response as the search topic.

### 4.2 Research (fan-out-and-synthesize)

A broad topic spans several wiki corners; a single `wiki-researcher` pass must trade breadth for depth across them. This step **decomposes** the topic into scoped sub-queries, **fans out** one researcher per corner in parallel, **synthesizes** the answers, and **verifies** every citation resolves. The common case — a narrow, single-corner topic — collapses to a single dispatch (no fan-out overhead). This is **skill-orchestrated fan-out**: each branch reuses `conductor:wiki-researcher` **unchanged** — the scoped `TOPIC` itself constrains the branch to its corner, so the researcher's own §3.0 routing lands in-lane. Splitting the work this way is *why* no `maxTurns` bump or new deep-research agent is needed: each branch is narrower than the original broad topic, not wider.

#### 4.2.1 Route & Decompose

Lift the orientation `wiki-researcher` does internally up to the skill, so it can decide the fan-out shape:

1. Read `conductor/overview.md` (its **Knowledge Base** table maps concepts to source `[[wikilinks]]`) and `conductor/index.md` (the **Scoped Docs** table is a routing index with a Match Strategy per category).
2. Route `{topic}` through the Scoped Docs Match Strategy (`${CLAUDE_PLUGIN_ROOT}/runtime/contracts/doc-routing.md`). Collect the routed scoped doc(s) / index categories the topic touches.
3. **Decompose into scoped sub-queries** — one per routed corner. A topic that routes to a single scoped doc (or none) is **single-corner** → one sub-query (the original `{topic}`); the rest of §4.2 runs as a single dispatch. A topic spanning two or more corners → N scoped sub-queries (N = number of distinct routed corners), each a narrower `TOPIC` naming that corner.
4. **Cap at 4 (no-silent-caps).** If routing identifies more than 4 corners, keep the 4 highest-signal (Knowledge-Base hit beats index Match-Strategy strength beats keyword density) and announce "Topic spans more than 4 wiki corners; fanning out the top 4 (`<topics>`)." The truncation is surfaced, not silent.

#### 4.2.2 Fan Out

- **N = 1 (single-corner):** dispatch one `conductor:wiki-researcher`, prompt:

  ```
  PROJECT_DIR={project root}
  TOPIC={topic}
  ```

- **N >= 2 (multi-corner):** dispatch **N `conductor:wiki-researcher` in ONE message (parallel fan-out)**, one per scoped sub-query, each prompt:

  ```
  PROJECT_DIR={project root}
  TOPIC=<scoped sub-query for this corner>
  ```

  Each researcher orients, routes, greps, graph-expands, and synthesizes within its own corner — breadth *and* depth, neither sacrificed.

Each dispatch returns a synthesized answer (markdown) followed by a `---WIKI RESEARCH RESULT---` block.

#### 4.2.3 Synthesize

- **N = 1:** the single answer (with its `SOURCES`) is the synthesized result; apply §4.2.4 to it.
- **N >= 2:** parse all N `---WIKI RESEARCH RESULT---` blocks. Drop any branch that returned `STATUS: FAILURE` or `STATUS: NO_RESULTS` (note which sub-query had no matches). Merge the surviving answers into one coherent summary: dedupe overlapping claims, **note any contradiction between branches explicitly** (do not silently pick one side), and union the `SOURCES` lists (deduped). If **every** branch was `NO_RESULTS` → the overall result is NO_RESULTS (carry the union of `RELATED` topics into §4.3). If **every** branch was `FAILURE` → overall FAILURE.

#### 4.2.4 Citation Verify

A final skill-level check that every citation in the synthesized answer actually resolves. The merge can introduce a cross-branch reference the researcher's own §4.3 neighbor-verify never saw, and a hallucinated `[[wikilink]]` must never reach the user unmarked (generate-and-filter).

1. Extract every `[[...]]` token from the synthesized answer.
2. For each, resolve via Glob — try the path as-written, then with `.md` appended.
3. **Unresolvable citations** are dropped from the answer (or annotated `*(unresolved)*`); if any are dropped, announce "Dropped N unresolved citations: <list>." Resolvable citations are kept verbatim.

Carry the synthesized, citation-verified answer (markdown) plus the merged `SOURCES` into §4.3.

### 4.3 Present Answer

Consume the §4.2 research outcome (single dispatch or fan-out synthesis — either way §4.2 has reduced it to one overall result):

1. **Overall FAILURE** (the single dispatch failed, or every fan-out branch failed) → announce the `REASON` → await instructions.
2. **Overall NO_RESULTS** (the single dispatch found nothing, or every fan-out branch was empty) → announce: "No matches found for `<topic>` in the wiki." Surface the merged `RELATED` topics: "Related topics in the index: <list>." → HALT.
3. **COMPLETED** → present the synthesized, citation-verified answer (the §4.2.4 markdown) to the user, then proceed to §4.4.

### 4.4 Offer Save

After presenting the answer, ask the user via `AskUserQuestion`:

> "Save this query result to the wiki?"

Options:
- **Yes, save** → proceed to **§4.5**
- **No** → HALT (answer already displayed)

### 4.5 Save Query Result

On user confirmation, persist the answer presented in §4.3 using the merged `SOURCES` list from §4.2.3:

1. **Generate slug** from the topic: lowercase, replace spaces with hyphens, remove special characters. Example: `tech stack` → `tech-stack`.

2. **Write query file:** `conductor/queries/<slug>.md`

   ```markdown
   ---
   type: query
   topic: <topic>
   created: <ISO-8601 date>
   sources:
     - <source1 from agent SOURCES>
     - <source2 from agent SOURCES>
   ---

   # Wiki Query: <topic>

   ## Answer
   <answer presented in §4.3>

   ## Sources
   <agent SOURCES list, verbatim>

   ## See Also
   - [[conductor/overview]] — Project overview
   ```

3. **Append to log:** Edit `conductor/log.md` to add a new row:

   ```
   | <ISO-8601> | wiki | QUERY_SAVE | conductor/queries/<slug>.md | Query: <topic> |
   ```

4. Announce: "Query saved to `conductor/queries/<slug>.md` and logged."

---

## 5.0 ERROR HANDLING

Fetch and execute `conductor/design/agent-error-handling.md`. Substitute the relevant agent + result-block delimiter for the current path: query (§4) → `conductor:wiki-researcher` / `---WIKI RESEARCH RESULT---`; ingest (§6) → `conductor:corpus-writer` then `conductor:wiki-synthesizer` (then advisory `conductor:wiki-differ` plus advisory `conductor:doc-linter`) / `---DOC SYNC RESULT---` (plus the `---DOC LINT RESULT---` block). (The `wiki` skill dispatches `doc-linter` only as the §6.2 post-ingest advisory lint; the full loop-until-dry plus refute repair loop lives in `/conductor:wiki-doctor lint`.)

---

## 6.0 INGEST

**Build the wiki from an arbitrary source — uncoupled from the track lifecycle.** This is the missing "drop a source → build the wiki" path: it routes the source through the *same* canonical doc-sync pipeline (corpus-writer + wiki-synthesizer) that post-track ingest uses, preserving the merge-not-append / idempotent / drift-gated discipline. The wiki skill stays a thin router; corpus-writer remains the single corpus writer, wiki-synthesizer the single wiki synthesizer.

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
