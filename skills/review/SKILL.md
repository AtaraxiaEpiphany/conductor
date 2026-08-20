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

### 2.3 Dispatch Code Review (adversarial, lensed, concurrent + converging)

A single holistic review self-certifies — the same agent that produced a finding is inclined to confirm it, so false positives survive, whole defect classes go unasked, and a single pass self-limits (agentic laziness: "35/50 done"). This skill runs an adversarial flow over `code-reviewer`'s shared analysis core: the producer is **fanned out per lens** — one focused pass per review dimension (`bugs`, `security`, `spec-compliance`, `tests`), each loading only its lens-relevant context (the §2.6 gate keeps a 4-lens fan-out from costing 4× a single pass), a **completeness critic** runs concurrently with the fan-out (it hunts classes every lens skipped, so it takes no findings as input), an adversarial **refuter** then strips each lens's false positives, and a bounded convergence loop re-runs the fan-out until a round adds nothing new. The refuter is the one true barrier: it consumes a lens's findings, so it cannot start until that lens's producer lands.

**Concurrent dispatch — lens producer fan-out + Critic (one message).** Dispatch **four** `conductor:code-reviewer` producers — one per lens, each with a distinct `LENS` + `RESULT_PATH` — AND the completeness critic, ALL in ONE message (5 dispatches; the lens passes and the critic neither read nor block on each other):

Per-lens producer (repeat for each of `bugs`, `security`, `spec-compliance`, `tests`):

```
TRACK_DIR={track_dir}
TRACK_ID={track_id}
REVISION_RANGE={range}
PRODUCT_GUIDELINES={path}
TECH_STACK={path}
STYLEGUIDES_DIR={path}
LENS={bugs|security|spec-compliance|tests}
RESULT_PATH={track_dir}/.conductor/review-lens-{lens}.json
```

`LENS` gates §3.1 context to only that lens's sources and narrows §3.4 to its dimension; findings **A_{lens}** land in `review-lens-{lens}.json` (each carries a `"lens"` field so synthesis can group them).

Critic (concurrent with the fan-out):

```
TRACK_DIR={track_dir}
TRACK_ID={track_id}
REVISION_RANGE={range}
PRODUCT_GUIDELINES={path}
TECH_STACK={path}
STYLEGUIDES_DIR={path}
MODE=critique
RESULT_PATH={track_dir}/.conductor/review-critique.json
```

The critic reports **only defect classes the lens fan-out missed** → new findings **C** in `review-critique.json` (may be empty — "nothing missed" is a valid outcome). State-consistency, skipped-task justification, and style are not lens dimensions (see code-reviewer §2.6's documented scope limit), so the critic is where a missed class in those areas would surface.

Parse each `---REVIEW RESULT---` block. Any returning `STATUS: FAILURE` → announce, skip the refuter and convergence loop, and treat it as a failed single-pass review (non-blocking) per §2.4.

**Refuter (barrier — after each lens producer lands).** For EACH lens whose **A_{lens}** has `Critical`/`High` findings (Medium/Low aren't worth a refute pass), dispatch `conductor:code-reviewer` with that lens's findings AND its `LENS` (so the refute re-confirms only that dimension). Dispatch all per-lens refutes concurrently in ONE message once their producers have landed:

```
TRACK_DIR={track_dir}
TRACK_ID={track_id}
REVISION_RANGE={range}
PRODUCT_GUIDELINES={path}
TECH_STACK={path}
STYLEGUIDES_DIR={path}
LENS={lens}
MODE=refute
FINDINGS_JSON={track_dir}/.conductor/review-lens-{lens}.json
RESULT_PATH={track_dir}/.conductor/review-lens-{lens}-refute.json
```

The refuter re-opens each finding against the code and **defaults to refuted when uncertain** → survivors **B_{lens}** (false positives dropped) land in `review-lens-{lens}-refute.json`. A lens with no Critical/High skips its refute — B_{lens} = A_{lens}.

**First synthesis.** Read `review-lens-*-refute.json` (B per lens) and `review-critique.json` (C). Merged findings = **(⋃_{lens} B_{lens}) ∪ C** (per-lens refute survivors + the critic's newly-discovered classes), deduped by signature (`severity+title+file+lines`). Record `seen` = that signature set.

**Convergence loop (bounded ≤ 2 lens-fan-out rounds total).** A single lensed pass can still self-limit within its own dimension. Up to ONE more time, re-dispatch the full 4-lens fan-out (same lenses, same range + context) with the current `seen` signatures appended to each prompt ("report issues NOT already in this list"), refute that round's Critical/High survivors per lens as above (Medium/Low pass through unrefuted), and compute `NEW = round_signatures − seen`. A round with empty `NEW` is **dry** → stop. Otherwise fold `NEW` into the merged set, update `seen`, and continue. Hard cap at 2 lens-fan-out rounds total (round 0 + one re-run) — the per-lens split + the critic already cover the dimensions this loop was meant to widen, so one re-run is enough; the dry check stops it sooner when converged.

**Finalize.** Overwrite `{track_dir}/.conductor/review-result.json` with the merged set and recompute `stats`, so the §3.0 "Apply Fixes" path consumes one authoritative list. Also write `lens_verdicts` — one entry per lens with its own verdict + survivor counts (the per-axis record §2.4 renders side by side). Carry the merged counts into §2.4.

### 2.4 Process Result (two axes, side by side — never merged)

The lens fan-out ran on two axes that answer different questions: the
**Standards axis** — does the code meet engineering standards (`bugs`,
`security`, `tests`) — and the **Spec axis** — does it keep the promise it
planned (`spec-compliance`). Report the axes **side by side** —
**NEVER merge or re-rank** them into one list. The severity→decision rule
(step 2) applies WITHIN each axis, and the final `AskUserQuestion` (step 3)
is the human's consolidation of the two verdicts — not a re-rank.

1. Present the per-lens verdicts side by side (one line per lens from
   `lens_verdicts`, plus the critic). Report format:

```
# Review Report: [Track Name]

## Per-Lens Verdicts (side by side)
| Lens | Axis | Verdict | C/H/M/L | One-line |
|---|---|---|---|---|
| bugs | Standards | [verdict] | c/h/m/l | [one-line] |
| security | Standards | [verdict] | c/h/m/l | [one-line] |
| tests | Standards | [verdict] | c/h/m/l | [one-line] |
| spec-compliance | Spec | [verdict] | c/h/m/l | [one-line] |
| critic (missed classes) | — | [verdict] | c/h/m/l | [one-line] |

## Summary
[One sentence quality assessment]

## Verification Checks
- [ ] Plan Compliance: [Yes/No/Partial]
- [ ] Style Compliance: [Pass/Fail]
- [ ] Test Coverage: [Yes/No/Partial]
- [ ] Skipped Tasks: [None/N tasks]

## Findings (if any)
### [Critical/High/Medium/Low] Description
- **Lens**: {the finding's lens}
- **File**: path/to/file (Lines L-L)
- **Context**: [why]
- **Suggestion**: diff
```

2. Review Decision — apply the SAME rule within each axis:
   - An axis with any Critical/High finding → that axis is **CHANGES REQUESTED**
   - Medium/Low only → **APPROVE WITH COMMENTS**
   - No issues → **APPROVE**
   - STATUS: FAILURE (agent error) → announce, recommend re-running the review, and continue (non-blocking).

3. Ask user: A) Apply Fixes, B) Manual Fix, C) Complete Despite Warnings.
   This choice consolidates the two axes — a Standards **CHANGES REQUESTED**
   against a clean Spec verdict (or the reverse) is the human's call to
   weigh, not a merge the report performs for them.

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