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
- `lint` — Full 5-check health audit via doc-linter agent (orphans, stale claims, contradictions, coverage gaps, log issues)
- `diff [target]` — Compare wiki docs against codebase reality (stale refs, coverage gaps, drift)

**Core Protocols:** File paths resolved via project CLAUDE.md TOC.

CRITICAL: You must validate the success of every tool call. If any tool call fails, halt immediately, announce the failure, and await instructions.

---

## 1.1 SETUP CHECK

**PROTOCOL: Verify that the Conductor wiki infrastructure exists.**

1. **Locate Wiki Files:** Resolve via project CLAUDE.md TOC or default paths:
   - `conductor/overview.md` — Wiki overview (regenerated after each track)
   - `conductor/log.md` — Append-only chronological record
   - `conductor/index.md` — Central navigation hub
2. **Verify Existence:** Check each file exists using Glob.
3. **Handle Failure:** If `conductor/overview.md` or `conductor/log.md` is missing → halt: "Wiki infrastructure incomplete — missing: `<files>`. Run `/conductor:setup` to initialize."

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
  lint             Full 5-check health audit (orphans, stale claims, coverage gaps)
  diff [target]    Compare wiki docs against codebase — stale refs, coverage gaps, drift
```

Then HALT.

---

## 3.0 LINT

**Agent dispatch operation.** Delegates to the existing `doc-linter` agent for a full 5-check health audit.

### 3.1 Dispatch Doc Linter

1. **Resolve project root:** Set `PROJECT_DIR` to the current working directory (project root).
2. Dispatch:

`Agent` tool, `subagent_type: "conductor:doc-linter"`. Description: `"Lint documentation wiki"`.

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
2. Dispatch:

`Agent` tool, `subagent_type: "conductor:wiki-differ"`. Description: `"Wiki diff: <scope>"`.

```
PROJECT_DIR={PROJECT_DIR}
SCOPE={scope, or empty for full diff}
```

The agent loads the wiki docs, extracts verifiable claims (file/module/function/directory references; structural claims flagged unverifiable), checks each against the code via Glob/Grep (valid/moved/stale), verifies code→wiki coverage (full diff only), and returns a markdown report followed by a `---WIKI DIFF RESULT---` block.

### 4.3 Parse Result

Parse the `---WIKI DIFF RESULT---` block:

1. **`STATUS: FAILURE`** → announce the `REASON` → await instructions.
2. **`STATUS: COMPLETED`** → present the agent's markdown report (above the block) to the user, then proceed to §4.4.
3. **No block detected** → announce "Wiki-differ completed without structured result. Check the conversation for details."

### 4.4 Recommendations

Branch on the counts from the block:

- `STALE > 0` → "Stale references found. Run `/conductor:wiki query <topic>` to verify the current state, then update the affected wiki pages."
- `MOVED > 0` → "Paths have moved. Update wiki references to current locations."
- `UNCOVERED > 0` → "Code areas lack wiki coverage. Consider `/conductor:new-track` to document them."
- `STALE == 0` and `MOVED == 0` and `UNCOVERED == 0` → "Wiki is consistent with codebase. No drift detected."

---

## 5.0 ERROR HANDLING

### 5.1 Infrastructure Missing

If `conductor/overview.md` or `conductor/log.md` does not exist:

→ HALT: "Wiki infrastructure missing: `<files>`. Run `/conductor:setup` to initialize."

### 5.2 Tool Call Failure

If any Read/Grep/Glob/Agent/Write/Edit tool call fails:

→ STOP → announce: "Wiki tool failure: `<tool>` failed with: `<error>`." → await instructions.

### 5.3 Agent Failure

If doc-linter agent returns `STATUS: FAILURE`:

→ Announce: "Doc-linter failed: `<reason>`." → await instructions.

### 5.4 No Result Block

If doc-linter completes but no `---DOC LINT RESULT---` block is detected:

→ Announce: "Doc-linter completed without structured result. Check the conversation for details."
