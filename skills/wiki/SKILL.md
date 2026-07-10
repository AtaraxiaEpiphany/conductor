---
name: wiki
description: Reads and builds the Conductor documentation wiki — health/status, topic search with citations, directional intent, single-source ingest, and bulk organize-and-file (build)
when_to_use: User wants to check wiki health, search the wiki, edit purpose.md, ingest one source, or build (organize and file) the wiki from a folder/file/URL
argument-hint: "<status|purpose|query|ingest|build> [args]"
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
- `ingest <source>` — Ingest a single source (file path / URL / pasted block) into the wiki — no track required
- `build <source>` — Organize and file a **batch** of sources (dir / file / URL / pasted block) into the wiki in one plan-then-execute pass — no track required

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

| SUBCOMMAND | Target |
|------------|--------|
| `status` | **Section 3.0** (inline below) |
| `purpose` | **Section 3.5** (inline below) |
| `query` | **Section 4.0** → Read `${CLAUDE_PLUGIN_ROOT}/skills/wiki/references/query.md` and execute (requires `SUB_ARGS` as topic) |
| `ingest` | **Section 6.0** → Read `${CLAUDE_PLUGIN_ROOT}/skills/wiki/references/ingest.md` and execute (requires `SUB_ARGS` as source) |
| `build` | **Section 7.0** → Read `${CLAUDE_PLUGIN_ROOT}/skills/wiki/references/build.md` and execute (requires `SUB_ARGS` as source — dir/file/URL/block) |
| empty / unrecognized | **Usage help** (below) → HALT |

`status` and `purpose` stay inline — they are small enough that a file + read-hop is not worth it. The heavy sub-commands (`query`, `ingest`, `build`) live in `references/` and are read **only when their sub-command is invoked** (progressive disclosure — this router body stays thin; a `status` call never loads the `query` fan-out logic).

### 2.3 Usage Help

If `$ARGUMENTS` is empty or `SUBCOMMAND` is unrecognized, present:

```
# /conductor:wiki — Wiki Read Operations

Usage: /conductor:wiki <subcommand> [args]

Sub-commands:
  status           Health snapshot of wiki infrastructure and coverage
  purpose          Read / co-edit the project's directional intent (purpose.md)
  query <topic>    Search wiki and synthesize an answer with [[wikilink]] citations
  ingest <source>  Ingest a single source (file path / URL / pasted block) into the wiki
  build <source>   Organize and file a batch of sources (dir/file/URL/block) into the wiki

Health diagnostics:
  /conductor:wiki-doctor lint     Full wiki health audit (see doc-linter §4)
  /conductor:wiki-doctor diff     Compare wiki docs against codebase
```

Then HALT.

### 2.4 References (loaded on demand)

The heavy sub-command bodies live off-page under `skills/wiki/references/`. Each is read only when its sub-command is routed here (Level-3 progressive disclosure). Section numbers are preserved across the split so cross-references (`agent-error-handling.md`'s `wiki query (§4)`, etc.) and the wiring tests stay valid.

| File | Sub-command | What it does |
|---|---|---|
| `references/query.md` | `query` | Validate topic → fan-out `wiki-researcher` per corner → synthesize + citation-verify → present + offer save |
| `references/ingest.md` | `ingest` | Normalize one source → corpus-writer → wiki-synthesizer → advisory wiki-differ/doc-linter |
| `references/build.md` | `build` | Enumerate a batch → advisory plan + one confirm → chunked corpus-writer → synthesizer → advisory tail |

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

The full query procedure (validate → fan-out-and-synthesize → present → offer save) lives in **`${CLAUDE_PLUGIN_ROOT}/skills/wiki/references/query.md`**. **Read that file now and execute it** with `SUB_ARGS` as the topic. (Body extracted to keep the router thin — see §2.4. The `## 4.0` number is preserved so `agent-error-handling.md`'s `wiki query (§4)` cross-reference stays valid.)

---

## 5.0 ERROR HANDLING

Fetch and execute `${CLAUDE_PLUGIN_ROOT}/conductor/design/agent-error-handling.md`. Substitute the relevant agent + result-block delimiter for the current path: query (§4) → `conductor:wiki-researcher` / `---WIKI RESEARCH RESULT---`; ingest (§6) and build (§7) → `conductor:corpus-writer` then `conductor:wiki-synthesizer` (then advisory `conductor:wiki-differ` plus advisory `conductor:doc-linter`) / `---DOC SYNC RESULT---` (plus the `---DOC LINT RESULT---` block). (The `wiki` skill dispatches `doc-linter` only as the §6.2/§7.3 post-ingest advisory lint; the full loop-until-dry plus refute repair loop lives in `/conductor:wiki-doctor lint`.)

---

## 6.0 INGEST

The full ingest procedure (resolve & normalize one source → corpus-writer → wiki-synthesizer → advisory wiki-differ/doc-linter → clean up) lives in **`${CLAUDE_PLUGIN_ROOT}/skills/wiki/references/ingest.md`**. **Read that file now and execute it** with `SUB_ARGS` as the source. (Body extracted to keep the router thin — see §2.4. `## 6.0` number preserved for cross-references.)

---

## 7.0 BUILD

The full build procedure (enumerate a batch → advisory plan + one confirm → chunked corpus-writer → synthesizer → advisory tail → clean up) lives in **`${CLAUDE_PLUGIN_ROOT}/skills/wiki/references/build.md`**. **Read that file now and execute it** with `SUB_ARGS` as the target (dir/file/URL/block). (Body extracted to keep the router thin — see §2.4. `## 7.0` number preserved for cross-references.)
