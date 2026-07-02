---
name: wiki-doctor
description: Diagnoses wiki health — lint audits and diff against codebase reality
when_to_use: User wants to audit wiki quality, check for stale references, or compare docs against code
argument-hint: "<lint|diff> [args]"
allowed-tools: Read, Grep, Glob, Agent, AskUserQuestion, Write, Edit
model: sonnet
---

# Conductor Wiki Doctor

## 1.0 SYSTEM DIRECTIVE

You are a **Conductor Wiki Doctor** — a specialized skill that diagnoses documentation health. You audit internal wiki consistency and compare wiki claims against actual code to surface drift.

**Available sub-commands:**
- `lint` — Full wiki health audit via the doc-linter agent (see `doc-linter` §4 for the check list)
- `diff [target]` — Compare wiki docs against codebase reality (stale refs, coverage gaps, drift)

**Core Protocols:** File paths resolved via project CLAUDE.md TOC.

CRITICAL: You must validate the success of every tool call. If any tool call fails, halt immediately, announce the failure, and await instructions.

---

## 1.1 SETUP CHECK

Fetch and execute `conductor/design/wiki-setup-check.md`. (`wiki-doctor` does not require `purpose.md`.)

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
| `lint` | **Section 3.0** |
| `diff` | **Section 4.0** (optional `SUB_ARGS` to focus on a page or topic) |
| empty / unrecognized | **Usage help** (below) → HALT |

### 2.3 Usage Help

If `$ARGUMENTS` is empty or `SUBCOMMAND` is unrecognized, present:

```
# /conductor:wiki-doctor — Wiki Health Diagnostics

Usage: /conductor:wiki-doctor <subcommand> [args]

Sub-commands:
  lint             Full wiki health audit (see doc-linter §4 for the check list)
  diff [target]    Compare wiki docs against codebase — stale refs, coverage gaps, drift
```

Then HALT.

---

## 3.0 LINT

**Agent dispatch operation.** Delegates to the existing `doc-linter` agent for a full wiki health audit.

### 3.1 Dispatch Doc Linter (loop-until-dry + per-finding refute)

A single lint pass both **over-reports** (false positives a deterministic check bakes in) and **under-reports** (findings one read misses). This section runs a convergent loop — lint → dedup → refute the new findings → re-lint, stopping on a dry round (loop-until-dry + adversarial-verification). `wiki-doctor` has `Write` but no `Bash`, so the findings handoff to the refute pass is a JSON file written via `Write` (which creates `.conductor/` if absent); it is transient scratch, not a wiki doc.

**Resolve project root:** `PROJECT_DIR` = current working directory. Maintain `seen` — a set of finding signatures (`<FIELD>:<finding>`) — and an **accumulated survivor result** (starts as an all-zero/all-empty block).

Loop — **max 3 rounds**:

1. **Lint pass** — dispatch `conductor:doc-linter` (default `MODE=full`), prompt:

   ```
   PROJECT_DIR={PROJECT_DIR}
   ```

   Parse the `---DOC LINT RESULT---` block (§3.2 format). `STATUS: FAILURE` → announce `REASON` → HALT. Flatten the round's findings `F`: every non-empty field among ORPHANS/STALE_CLAIMS/CONTRADICTIONS/GAPS/LOG_ISSUES/MISSING_FRONTMATTER contributes its semicolon-separated entries. **`F` empty (or `STATUS: PASS`)** → a dry round → announce `"🔍 Wiki lint: clean"` → skip to §3.2 with the accumulated survivor result.

2. **Dedup vs `seen`.** `NEW = F − seen` (signatures). Add `NEW` to `seen`. **`NEW` empty** → a dry round (the lint re-found only already-seen findings) → announce `"🔍 Wiki lint: dry (<N> accumulated)"` → §3.2 with the accumulated survivor result.

3. **Per-finding refute** — `Write` `NEW` to `{PROJECT_DIR}/.conductor/wiki-lint-findings.json` as `{"<FIELD>": [<finding>, ...], ...}`, then dispatch `conductor:doc-linter`, prompt:

   ```
   PROJECT_DIR={PROJECT_DIR}
   MODE=refute
   FINDINGS_JSON={PROJECT_DIR}/.conductor/wiki-lint-findings.json
   ```

   Parse the returned `---DOC LINT RESULT---` block → **survivors** (the agent re-resolves each finding and **defaults to refuted when uncertain**, suppressing false positives). Merge survivors into the accumulated survivor result (union per field, deduped).

4. **Loop back to step 1** — a fresh full lint may surface findings the prior read missed. **After 3 rounds** still producing `NEW` findings → stop: announce `"🔍 Wiki lint: 3 rounds → <M> residual findings"` → §3.2 with the accumulated survivor result.

Carry the **accumulated survivor result** into §3.2 (parse) / §3.3 (present) — that merged, deduped, false-positive-stripped block is the report.

### 3.2 Parse Result

Wait for agent completion. Parse the `---DOC LINT RESULT---` block.

Expected format:
```
---DOC LINT RESULT---
STATUS: PASS|WARN|FAIL
ORPHANS: <count> -- <list>
STALE_CLAIMS: <count> -- <list>
CONTRADICTIONS: <count> -- <list>
GAPS: <count> -- <list>
LOG_ISSUES: <count> -- <list>
MISSING_FRONTMATTER: <count> -- <list>
SUMMARY: <one-line>
---END RESULT---
```

If the agent returns `STATUS: FAILURE` → announce the reason from `REASON:` field and HALT.

If no `---DOC LINT RESULT---` block is detected → announce "Doc-linter completed without structured result. Check the conversation for details."

### 3.3 Present Report

Output the lint report:

```
# Wiki Lint Report
Status: <PASS / WARN / FAIL>

## Checks
| Check | Count | Details |
|-------|-------|---------|
| Orphan [[wikilinks]] | <N> | <semicolon-separated list or "None"> |
| Stale Claims | <N> | <semicolon-separated list or "None"> |
| Contradictions | <N> | <semicolon-separated list or "None"> |
| Coverage Gaps | <N> | <semicolon-separated list or "None"> |
| Log Issues | <N> | <semicolon-separated list or "None"> |
| Missing Frontmatter | <N> | <semicolon-separated list of scoped docs missing provenance, or "None"> |

## Summary
<one-line summary from doc-linter>
```

### 3.4 Recommendations

Based on STATUS:

- **PASS:** "Wiki is healthy. No action needed."
- **WARN:** "Issues detected. Consider running targeted fixes or re-ingesting affected sources."
- **FAIL:** "Significant issues found. Review the details above and address ERROR-level findings."

---

## 4.0 DIFF

**Delegates wiki-vs-codebase drift detection to the `conductor:wiki-differ` agent.** The skill validates scope, presents the report, and recommends next steps.

### 4.1 Validate Scope

1. `SUB_ARGS` present → `SCOPE` = `SUB_ARGS` (a wiki page or topic area, e.g. `architecture` checks only architecture-related claims).
2. `SUB_ARGS` empty → `SCOPE` = full diff (all wiki documents).

### 4.2 Dispatch Wiki Differ (loop-until-dry)

`wiki-differ` is deterministic, but a single pass can miss drift a closer re-read catches — so wrap the dispatch in a convergent loop (loop-until-dry). Unlike lint (§3.1), `wiki-differ` has no `refute` mode, so this loop is **completeness-only** (re-dispatch until a dry round), not precision.

**Resolve project root:** `PROJECT_DIR` = current working directory. Maintain `seen` — drift-item signatures (`<STALE|MOVED|UNCOVERED>:<path>`).

Loop — **max 3 rounds**:

1. Dispatch `conductor:wiki-differ`, prompt:

   ```
   PROJECT_DIR={PROJECT_DIR}
   SCOPE={scope, or empty for full diff}
   ```

   The agent loads the wiki docs, extracts verifiable claims (file/module/function/directory references; structural claims flagged unverifiable), checks each against the code via Glob/Grep (valid/moved/stale), verifies code→wiki coverage (full diff only), and returns a single `---WIKI DIFF RESULT---` block whose body carries the structured counts **and** the full markdown report inside it (the output filter strips anything outside the block, so wiki-differ emits no separate report).

2. `STATUS: FAILURE` → announce `REASON` → await instructions. `STATUS: COMPLETED` → collect the round's drift items `F` (the STALE/MOVED/UNCOVERED paths from the counts). **`F` empty (`STALE=MOVED=UNCOVERED=0`)** → a dry round → carry this report to §4.3.

3. `NEW = F − seen`; add `NEW` to `seen`. **`NEW` empty** → a dry round (the re-dispatch re-found only already-seen drift) → carry this report to §4.3. Otherwise this round's report is the most complete so far.

4. **Loop back to step 1.** **After 3 rounds** still producing `NEW` drift → stop → carry the latest (most complete) report to §4.3.

The report carried into §4.3 is the converged (or 3-round-budget-capped) diff.

### 4.3 Parse Result

Parse the `---WIKI DIFF RESULT---` block:

1. **`STATUS: FAILURE`** → announce the `REASON` → await instructions.
2. **`STATUS: COMPLETED`** → the block body below the count fields **is** the markdown diff report; present it to the user, then proceed to §4.4. (The report lives inside the block because the output filter strips anything outside it — wiki-differ emits no separate report.)
3. **No block detected** → announce "Wiki-differ completed without structured result. Check the conversation for details."

### 4.4 Recommendations

Branch on the counts from the block:

- `STALE > 0` → "Stale references found. Run `/conductor:wiki query <topic>` to verify the current state, then update the affected wiki pages."
- `MOVED > 0` → "Paths have moved. Update wiki references to current locations."
- `UNCOVERED > 0` → "Code areas lack wiki coverage. Consider `/conductor:new-track` to document them."
- `STALE == 0` and `MOVED == 0` and `UNCOVERED == 0` → "Wiki is consistent with codebase. No drift detected."

---

## 5.0 ERROR HANDLING

Fetch and execute `conductor/design/agent-error-handling.md`. Substitute the relevant agent + result-block delimiter for the current path: lint (§3) → `conductor:doc-linter` / `---DOC LINT RESULT---`; diff (§4) → `conductor:wiki-differ` / `---WIKI DIFF RESULT---`.
