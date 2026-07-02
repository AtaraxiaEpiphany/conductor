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

### 2.3 Dispatch Code Review (adversarial 3-pass)

A single holistic review self-certifies — the same agent that produced a finding is inclined to confirm it, so false positives survive and whole defect classes go unasked. This skill runs a **serial producer → refuter → critic** sequence (adversarial-verification + completeness-critic) over `code-reviewer`'s shared analysis core, then synthesizes one authoritative findings list. Serial, not concurrent: the refuter consumes the producer's findings as input, and the three passes write distinct result files.

**Pass 1 — Producer.** Dispatch `conductor:code-reviewer` (default `MODE=full`), prompt:

```
TRACK_DIR={track_dir}
TRACK_ID={track_id}
REVISION_RANGE={range}
PRODUCT_GUIDELINES={path}
TECH_STACK={path}
STYLEGUIDES_DIR={path}
```

Findings **A** land in `{track_dir}/.conductor/review-result.json` (the default `RESULT_PATH`). Parse the `---REVIEW RESULT---` block. `STATUS: FAILURE` → announce, skip passes 2–3, and treat it as a failed single-pass review (non-blocking) per §2.4.

**Pass 2 — Adversarial refuter.** Run **only if A has `Critical`/`High` findings** (Medium/Low aren't worth a refute pass). Dispatch `conductor:code-reviewer` with the same range + context paths:

```
TRACK_DIR={track_dir}
TRACK_ID={track_id}
REVISION_RANGE={range}
PRODUCT_GUIDELINES={path}
TECH_STACK={path}
STYLEGUIDES_DIR={path}
MODE=refute
FINDINGS_JSON={track_dir}/.conductor/review-result.json
RESULT_PATH={track_dir}/.conductor/review-refute.json
```

The refuter re-opens each finding against the code and **defaults to refuted when uncertain** → survivors **B** (false positives dropped) land in `review-refute.json`. If A had no Critical/High, skip — set B = A.

**Pass 3 — Completeness critic.** Dispatch `conductor:code-reviewer` with `MODE=critique RESULT_PATH={track_dir}/.conductor/review-critique.json` (same range + context). The critic reports **only defect classes the producer missed** → new findings **C** in `review-critique.json` (may be empty — "nothing missed" is a valid outcome).

**Synthesize.** Read all three result files. Merged findings = **B ∪ C** (refute survivors + the critic's newly-discovered classes), deduped by signature (`severity+title+file+lines`). Overwrite `{track_dir}/.conductor/review-result.json` with the merged set and recompute `stats`, so the §3.0 "Apply Fixes" path consumes one authoritative list. Carry the merged counts into §2.4.

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
   - **Archive** (recommended): `track-state archive "<track_dir>"` — flips status to `archived` **and relocates `tracks/<id>` → `archive/<id>`**; it returns the new `track_dir`. Then `track-state registry-update "<new track_dir>" "conductor/tracks.md"` (use the returned path — the old one no longer exists) + `git add -A && git commit -m "chore(conductor): Archive track '<desc>'"`
   - **Keep Active**: no action
   - **Delete**: confirm then `rm -rf "<track_dir>"` + remove from tracks.md + commit
