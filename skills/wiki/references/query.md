<!--
  Reference body for the `wiki` skill — loaded ON DEMAND by the §2.0 router
  (progressive disclosure: this file is read only when its sub-command is
  invoked, keeping the Level-2 router body thin). Section numbers are stable —
  tests and the agent-error-handling design doc index them — edit in place.
-->

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
