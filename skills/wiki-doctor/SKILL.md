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

### 3.1 Dispatch Doc Linter

1. **Resolve project root:** Set `PROJECT_DIR` to the current working directory (project root).
2. Dispatch `conductor:doc-linter`, prompt:

```
PROJECT_DIR={PROJECT_DIR}
```

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

### 4.2 Dispatch Wiki Differ

1. **Resolve project root:** `PROJECT_DIR` = current working directory.
2. Dispatch `conductor:wiki-differ`, prompt:

```
PROJECT_DIR={PROJECT_DIR}
SCOPE={scope, or empty for full diff}
```

The agent loads the wiki docs, extracts verifiable claims (file/module/function/directory references; structural claims flagged unverifiable), checks each against the code via Glob/Grep (valid/moved/stale), verifies code→wiki coverage (full diff only), and returns a single `---WIKI DIFF RESULT---` block whose body carries the structured counts **and** the full markdown report inside it (the output filter strips anything outside the block, so wiki-differ emits no separate report).

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
