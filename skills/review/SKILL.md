---
name: review
description: Reviews completed track work using track-state.json for context and commit tracking
when_to_use: User wants to review a track's implementation quality, check code compliance, or verify test coverage
argument-hint: "[track_name]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet
---

# Conductor Review

## 1.0 SYSTEM DIRECTIVE

You are a **Principal Software Engineer** and **Code Review Architect**. Review implementation against standards, design guidelines, and the original plan.

**Subagent:** `conductor:code-reviewer` — deep code analysis (diff review, plan compliance, style, tests).

CRITICAL: Validate every tool call. On failure → halt → announce.

---

## 1.1 SETUP CHECK

1. Verify: spec.md, plan.md, track-state.json exist in track dir.
2. Verify project context: Product Definition, Tech Stack, Workflow.
3. If ANY missing → halt: `"Conductor environment incomplete — missing: <files>. Run /conductor:setup."`

---

## 2.0 REVIEW PROTOCOL

### 2.1 Identify Scope

1. Check `$ARGUMENTS` for track name, or auto-detect:
   - `[x]` tracks (completed) first, then `[~]` (in-progress).
   - One candidate → auto-select. Multiple → `AskUserQuestion`.
2. Confirm scope via `AskUserQuestion`.

### 2.2 Retrieve Context

1. **Get SHA range:**
```bash
track-state shas "<track_dir>"
```
Parse output: use the `range` field (`{first}~1..{last}`) — it includes the first commit's own changes. Do NOT rebuild `{first}..{last}`; that masks the first task's exclusive diff.

2. **Resolve project context paths** via CLAUDE.md TOC:
   - `product-guidelines.md`
   - `tech-stack.md`
   - code style guides directory

### 2.3 Dispatch Code Reviewer

Dispatch `conductor:code-reviewer`, prompt:

```
TRACK_DIR={track_dir}
TRACK_ID={track_id}
REVISION_RANGE={range}
PRODUCT_GUIDELINES={path}
TECH_STACK={path}
STYLEGUIDES_DIR={path}
```

Parse `---REVIEW RESULT---` block.

### 2.4 Process Result

1. Present findings. Report format:

```
# Review Report: [Track Name]

## Summary
[One sentence quality assessment]

## Verification Checks
- [ ] Plan Compliance: [Yes/No/Partial]
- [ ] Style Compliance: [Pass/Fail]
- [ ] Test Coverage: [Yes/No/Partial]
- [ ] Skipped Tasks: [None/N tasks]

## Findings (if any)
### [Critical/High/Medium/Low] Description
- **File**: path/to/file (Lines L-L)
- **Context**: [why]
- **Suggestion**: diff
```

2. Review Decision:
   - Critical/High → **CHANGES REQUESTED**
   - Medium/Low only → **APPROVE WITH COMMENTS**
   - No issues → **APPROVE**
   - STATUS: FAILURE (agent error) → announce, recommend re-running the review, and continue (non-blocking).

3. Ask user: A) Apply Fixes, B) Manual Fix, C) Complete Despite Warnings.

---

## 3.0 COMPLETION

1. If user chose "Apply Fixes" → these are **post-review patches, not plan tasks**. Dispatch ONE free-form patch agent (`general-purpose`), prompt:
   ```
   Apply the review findings in {TRACK_DIR}/.conductor/review-result.json.
   For each finding, apply its `suggestion`, then commit it separately as
   `fix(<area>): <finding title>` (<area> = code area touched). Run the test
   suite after applying and fix any regressions.
   ```
   Do NOT pass `PHASE`/`TASK`, do NOT call `dispatch-finalize`, and do NOT modify `track-state.json` — these are remediation commits, not plan tasks. After return, verify the commits landed (`git log --oneline -<count>`).
2. Offer cleanup options via `AskUserQuestion`:
   - **Archive** (recommended): `track-state archive "<track_dir>"` + `registry-update` + commit
   - **Keep Active**: no action
   - **Delete**: confirm then `rm -rf "<track_dir>"` + remove from tracks.md + commit
