---
name: new-track
description: Creates a new track with spec, plan, and track-state.json for orchestrator-driven execution
when_to_use: User wants to create a new feature track, bug fix track, or chore track with specification and plan
argument-hint: "[track_description]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet
---

# Conductor NewTrack

## 0.0 RESOLVE PATHS

Key paths (resolve via `conductor/index.md` if non-default):
- Product: `conductor/product/product.md`
- Tech Stack: `conductor/design/tech-stack.md`
- Tracks Registry: `conductor/tracks.md`
- Workflow Index: `conductor/workflow/index.md`

## 0.5 RESUME CHECK

A new-track run can be interrupted before state artifacts exist (§2.6). A lightweight progress marker — created, advanced, and deleted by the `track-state new-track-*` commands (the orchestrator **never** hand-edits the JSON) — lets an interrupted run resume instead of starting over.

1. Detect any interrupted track:
   ```bash
   track-state new-track-resume
   ```
   Parse the JSON. Switch on `action`:
   - `none` → fresh track → proceed to §1.0.
   - `resume` → `candidates[]` is the partial track(s). For each candidate, `AskUserQuestion`:
     *"Found an incomplete track `<track_id>` ('<description>'), last reached `<last_step>`. Resume?"*
     - **Yes (resume)** → adopt the candidate's `track_id` / `track_dir` / `description` / `type` / `execution_mode` for everything below, then **jump to the candidate's `resume_target`** — the first section whose step key is NOT in `steps_done` (`spec_planned` → §2.3, `reviewed` → §2.4, `state_created` → §2.6, `registry_updated` → §2.6). Skip sections already marked done.
     - **No** → warn the user an orphaned partial track exists at that `track_dir`, then proceed to a fresh track (§1.0).

The marker is created in §2.1 (`new-track-init`) and deleted once the track is committed (`new-track-finalize`, end of §2.6).

## 1.0 SETUP CHECK

1. Verify via project CLAUDE.md TOC: Tracks Registry, Product Definition, Tech Stack, Workflow Index.
2. If ANY missing → halt: `"Conductor environment incomplete — missing: <files>. Run /conductor:setup."`

**Subagents:**
- `conductor:spec-planner` — generates spec.md and plan.md.
- `conductor:spec-reviewer` — read-only auditor (EARS + tag + structure); returns a verdict + findings. Non-interactive — §2.4 owns the human review loop.

CRITICAL: Validate every tool call. On failure → halt → announce.

---

## 2.0 TRACK INITIALIZATION

### 2.1 Description & Type

1. Get description from `$ARGUMENTS` or `AskUserQuestion`.
2. Infer track type (feature/bugfix/chore) — do NOT ask user.
3. **Derive the track id deterministically** — pick a short slug (1–3 lowercase words) summarizing the track, then run:
   ```bash
   track-state derive-name <slug>
   ```
   Parse the JSON. Use `track_id` and `track_dir` from the result for **everything** below (resume marker, spec-planner `TRACK_DIR`, §2.6 init `--track-id` and `<track_dir>`). Never hand-write the date — the command stamps it from the clock.
   > **Existing-track adoption:** If `$ARGUMENTS` is a bare track_id whose `<track_dir>` already exists (commonly one carrying a `brief.md` from `/conductor:brief`, but any existing track dir qualifies), **adopt that track_id/track_dir directly** — do NOT re-derive (re-deriving would mint a new dated id and orphan the Brief). Skip `derive-name` for this case.
4. **Initialize resume marker** (skip if resuming — `new-track-resume` already found it). Creates `<track_dir>/.conductor/` and the marker in one call (idempotent — a no-op if the marker already exists):
   ```bash
   track-state new-track-init "<track_dir>" --track-id <id> --description "<desc>" --type <type>
   ```

### 2.2 Context Discovery (Paths Only)

1. **Scan & Match:** Search `conductor/index.md` for file paths semantically related to track's goal.
2. **Found relevant docs** → collect paths only (do NOT read contents). Pass paths to spec-planner as `RELATED_DOCS`.
3. **Not found** → no discovered docs *and* (per §2.2b) no brief — this is the only path where new-track does its own grilling, and it is the weak legacy path (bare sequential Q&A: no recommended-first, no look-it-up, no premise-challenge). Offer a single `AskUserQuestion`: *"No brief found — run `/conductor:brief <track_id>` for a grilled shared understanding (recommended), or proceed with minimal Q&A now?"*
   - **Run `/conductor:brief`** → halt new-track and hand off. The user runs `/conductor:brief <track_id>`, then re-invokes `/conductor:new-track <track_id>` (existing-track adoption in §2.1 picks up the same dir + its new `brief.md`, which §2.2b then consumes). A brief is the last input before spec-planner freezes the plan — it earns the grill's turn-cost where ad-hoc Q&A does not.
   - **Proceed with Q&A** → the low-friction escape hatch for trivial tracks; keep it. 2-5 questions sequentially, manual context, or correct paths. Pass answers as `USER_ANSWERS`.

> Context content is loaded by spec-planner itself. The orchestrator handles only paths and summaries.

### 2.2b Brief Detection (authoritative pre-planned input)

Before the §2.2 Q&A branch above, check for a **Track Brief** at `<track_dir>/brief.md` (written by `/conductor:brief`). A Brief is comprehensive, human-authored context; when present it is the **authoritative** planning input and supersedes the scan/Q&A fallback.

1. Check `<track_dir>/brief.md`.
2. **Found** → Read it. Announce: *"Consuming brief.md at `<track_dir>/brief.md` as authoritative planning input."* Then:
   - **Skip the §2.2 interactive Q&A entirely** — the Brief already captured it.
   - Set `RELATED_DOCS` from the Brief's `## References` section (paths only; if empty, `N/A`).
   - Build a rich `USER_ANSWERS` block for spec-planner from the Brief's structured sections: Problem & Motivation, Goals, **Out of Scope (verbatim)**, Context & Constraints, Stakeholders, Open Questions, Suggested Acceptance Signals. Prefix it `USER_CONTEXT: brief`.
   - **Seed `conductor/purpose.md` from this first Brief** (additive, intersection-only — the compounding-context the wiki pattern wants, so purpose.md has real content on day one instead of placeholders). For each of:
     - `## Open Questions` → lift each question into purpose.md `## Key Questions` **only if not already present** (dedupe by gist).
     - `## Out of Scope` → lift each exclusion into purpose.md `## Out of Scope` **only if not already present**, and only where it does not **widen** past what setup established (intersect, never contradict — mirror the Brief's own Out-of-Scope-vs-purpose.md rule). A Brief's Out-of-Scope is a *track-level* cut; only the project-persistent ones belong here.
     Skip the append silently if purpose.md already has the content (idempotent) or if the Brief section is empty/`"None identified."`. This is a `## Key Questions`/`## Out of Scope` append only — never touch Goals, Thesis, or Active Decisions (those are owned by setup / wiki-synthesizer respectively). If `conductor/purpose.md` is absent, skip (setup owns creating it).
   - Pass `USER_ANSWERS` (with `USER_CONTEXT: brief`) and `RELATED_DOCS` to the §2.3 dispatch. spec-planner §3.0 reads the Brief itself first and honors `## Out of Scope` verbatim.
3. **Not found** → proceed with §2.2 (scan → brief-first offer at step 3, then the Q&A fallback). This is the default; without a Brief the user chooses between grilling a brief now or the minimal Q&A.

> The Brief is additive: it only changes behavior when present. A re-invoked new-track on a track with a Brief will plan from the Brief; without one, the original flow applies.

### 2.3 Dispatch Spec-Planner

**Existing spec/plan guard (collision check).** Before regenerating, detect a pre-existing `plan.md` — the user may have re-invoked new-track, or a prior run wrote `plan.md` but left no resume marker. Skip this guard when resuming via §0.5 with `spec_planned` already in `steps_done` (that plan is owned by the active run).

1. If `<track_dir>/plan.md` exists, validate it in place — `--check` writes
   nothing:
   ```bash
   track-state init-from-plan "<track_dir>" --check
   ```
   - `ok: true` → the existing plan.md is well-formed (the JSON reports
     `phases`/`tasks` counts). `AskUserQuestion`:
     *"An existing `plan.md` (N phases, M tasks) was found at `<track_dir>`. How should I proceed?"*
     - **Reuse existing plan** → run the two remaining §2.3 validations —
       `track-state spec-anchors "<track_dir>"` and `track-state spec-integrity "<track_dir>"`
       (the `--check` above already covered format). All clean → skip spec-planner, append
       `"spec_planned"` to `steps_done`, and jump to §2.4 review (carrying the `ac_integrity_gate`
       verdict forward, same as a fresh dispatch). Either fails → do NOT reuse; announce the
       defect, then **Regenerate** (dispatch spec-planner below) or **Cancel** (halt).
     - **Regenerate (overwrite)** → continue to dispatch spec-planner below.
     - **Cancel** → halt.
   - `ok: false` → the existing plan.md is malformed (missing `## Phase N:`
     headings or task `- [ ]` checkbox lines). Announce the reported `errors`, then
     `AskUserQuestion`: **Regenerate** (dispatch spec-planner below) or
     **Cancel** (halt). Never reuse a broken plan.

Dispatch `conductor:spec-planner`, prompt:

```
TRACK_DIR={track_dir}
TRACK_DESCRIPTION={desc}
TRACK_TYPE={type}
USER_ANSWERS={answers or N/A}
RELATED_DOCS={paths or N/A}
```

Parse `---SPEC PLAN RESULT---` block. Confirm `STATUS: SUCCESS` (halt on FAILURE and announce `SUMMARY`).

**Absent block (spec-planner exhausted turns mid-generation).** If the `---SPEC PLAN RESULT---` block is **missing entirely** (not `STATUS: FAILURE` — the block itself never appeared), the spec-planner ran out of turns reading context before it could emit §5.0. **Do NOT read `spec.md`/`plan.md` to recover** (the read-guard hook denies that while a dispatch is open, and doing the work yourself violates the thin-router contract). The `on-subagent-stop` recovery hook fires a bounded recovery turn first; this is the backstop when recovery is exhausted. **Re-dispatch `conductor:spec-planner` once** with `PREVIOUS_ERRORS: prior attempt returned no result block — emit §5.0 FIRST, then do only minimal §3.0 discovery`. If the second dispatch also returns no block → **halt**: `"spec-planner failed to emit a result block after 2 attempts — inspect <track_dir>/plan.md / spec.md manually."` `plan.md` and `spec.md` are now on disk — `PLAN_STRUCTURE` is **no longer required**: Section 2.6 derives the full task/subtask structure mechanically from `plan.md`, eliminating manual transcription.

**Validate the generated plan + spec before §2.6.** Run three read-only checks; re-dispatch spec-planner with the combined defects if any fails. Max **2 re-dispatches (3 total attempts)**, counting from the first dispatch above:

1. **Format** — `track-state init-from-plan "<track_dir>" --check` (the same `parse_plan` §2.6 uses). `ok: false` → collect `errors[]`. `ok: true` → continue.
2. **Spec anchors** — `track-state spec-anchors "<track_dir>"` (run only if format passed). Catches a `spec.md` written as free-form narrative with no `## Acceptance Criteria` section / `## Test Scenarios` table. Language-agnostic: it checks the English machine-anchor tokens, not prose. `ok: false` → collect `errors[]`. `ok: true` → continue.
3. **AC integrity** — `track-state spec-integrity "<track_dir>"` (run only if anchors passed). Runs at planning time (no `track-state.json` yet → degrades gracefully). Branch on `ac_integrity_gate` **and** `ac_integrity_reason`:
   - `N/A` + `"spec_missing"` → clean (legitimately spec-less track).
   - `N/A` + `"no_acs"` → treat as FAILED: spec.md exists but has no `## Acceptance Criteria` (the weak-model anchor-drift failure). Collect the gate string **verbatim**.
   - `FAILED` → collect the gate string **verbatim** (it names the offending AC IDs + the fix).
   - `PASS` / `WARN` → clean (WARN is advisory; carry into §2.4).

**All three clean → break (proceed to §2.3b).** Any failed → if a re-dispatch remains, re-dispatch `conductor:spec-planner` with the combined defects:

```
TRACK_DIR={track_dir}
TRACK_DESCRIPTION={desc}
TRACK_TYPE={type}
USER_ANSWERS={answers or N/A}
RELATED_DOCS={paths or N/A}
PREVIOUS_ERRORS:
{the format errors[], the spec-anchors errors[], and/or the AC-integrity gate string, verbatim}
REGEN_FOCUS: The prior plan.md/spec.md failed validation. FORMAT: every task/subtask line begins with `- [ ]`; every phase begins with `## Phase N: Name`. SPEC-ANCHOR: spec.md has `## Acceptance Criteria` with `- AC-N:` bullets and `## Test Scenarios` with `| TC-N.M | AC-N |` rows — these headings + ID tokens are machine anchors, keep them ASCII even when prose is another language. AC-INTEGRITY: every AC-n appears in some task's `<!-- AC-n -->` AND maps to a `TC-{n}.{m} | AC-n` row. Re-read `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/plan-format-contract.md` and `${CLAUDE_PLUGIN_ROOT}/templates/spec-scaffold.md`, then regenerate a conforming plan.md/spec.md.
```

Re-parse the returned `---SPEC PLAN RESULT---` block (halt on FAILURE), then loop back to step 1.

Still failing after the final attempt → **halt**: `"Spec-planner produced a plan/spec that still fails validation after 3 attempts — errors: <combined defects>. Inspect <track_dir>/plan.md / spec.md."` Do NOT proceed to §2.6 (it would fail identically).

### 2.3b Adversarial Plan Refuter (semantic gate)

The §2.3 loop catches **format** and **AC-integrity** defects deterministically — but a plan can conform to every deterministic check and still be semantically weak: a Test Scenario that does not actually exercise its AC, an AC that drifts from the user's stated intent, or a task that maps to an AC in name only. These are judgment calls a deterministic gate cannot make.

**Refuter opt-out (ask once per track).** Before dispatching, `AskUserQuestion`:
*"Run the adversarial plan refuter? It's one read-only pass that challenges weak AC↔TC mappings and intent drift before review."*
- **Yes (Recommended)** → run the refute below.
- **No, skip it** → skip the dispatch; the deterministic §2.3 checks + the §2.4 spec-reviewer remain the gate. Treat as SUSTAINED for resume purposes (proceed to §2.4).

When the user opts in, run ONE adversarial refuter pass to challenge the plan's soundness before §2.4 review.

**Niche guard (do not duplicate §2.3).** The refuter must NOT re-derive what §2.3 already checked — AC→TC existence, dangling references, EARS well-formedness, and the TC/plan/verification coverage rates are §2.3's deterministic lane. Its value is the semantic layer above those: does a TC actually exercise its AC; does an AC match stated intent; does a task genuinely realize its AC.

Dispatch `conductor:refuter`, prompt:

```
PROJECT_DIR={project_root}
DOMAIN=plan
CLAIM=The spec.md + plan.md are semantically sound — every acceptance criterion reflects the user's stated intent, every AC is genuinely exercised by a Test Scenario (not merely name-matched), no task is semantically orphaned from the AC it claims to realize, AND every task tag is semantically correct (no business-logic task is wrongly exempted from TDD by a `tdd_exempt` tag).
CONTEXT_PATHS={track_dir}/spec.md {track_dir}/plan.md {USER_ANSWERS path or N/A}
AC_EVIDENCE={the ac_evidence list from the §2.3 spec-integrity JSON — each AC's measured/claimed/missing TCs}
```

> The CLAIM is framed as "the plan is sound" deliberately. The refuter defaults to `SUSTAINED` when it cannot pin a specific grounded defect, so `SUSTAINED` = proceed-when-uncertain and `REFUTED` = grounded evidence of unsoundness. A consequential plan gate must not hard-block the track on a hunch — only a cited, re-confirmable semantic defect justifies a regen. (The skip gate in `implement` §3.6 frames its CLAIM the opposite way, because skipping is the riskier action there.)

Parse the `---REFUTATION RESULT---` block:

- **STATUS: SUSTAINED** (default — no grounded semantic defect found) → proceed to §2.4.
- **STATUS: REFUTED** (positive, grounded defect, with `file:line` citations in EVIDENCE) → re-dispatch `conductor:spec-planner` ONCE with the refuter's challenges appended to `PREVIOUS_ERRORS` (reuse the §2.3 regen envelope; `REGEN_FOCUS` = the refuter's EVIDENCE + REASONING). Re-run this refuter once on the regenerated plan. If still `REFUTED`, announce the sustained challenges and proceed to §2.4 **non-blocking** — the spec-reviewer and user assess them there. A semantic disagreement the deterministic pipeline cannot close is a human-judgment call, not a hard halt.
- **STATUS: FAILURE** → treat as SUSTAINED (the refuter could not complete; the plan stands) and proceed to §2.4.

> **Resume:** stamp the step **only after ALL THREE checks (`init-from-plan --check`, `spec-anchors`, `spec-integrity`) pass AND §2.3b is resolved** — the refute ran to SUSTAINED / non-blocking REFUTED, **or the user declined it at the §2.3b prompt** (decline = SUSTAINED for resume purposes). A plan/spec that has not yet validated and been resolved is not "planned":
> ```bash
> track-state new-track-step "<track_dir>" spec_planned
> ```

### 2.4 Dispatch Spec-Reviewer

`spec-reviewer` is a **read-only auditor**: it reads `spec.md` + `plan.md`, runs the EARS-conformance + dispatch-tag + structure audits, and returns a verdict + findings. It is **non-interactive** — it does not call `AskUserQuestion` or edit files. **You (the orchestrator) own the human review loop**: surface its findings to the user and apply revisions.

Dispatch `conductor:spec-reviewer`, prompt:

```
TRACK_DIR={track_dir}
```

Parse the `---REVIEW RESULT---` block and switch on `STATUS`:

- **`APPROVED`** → review clean → proceed to §2.5.
- **`CHANGES_REQUESTED`** → the `FINDINGS` list is defects to surface. `AskUserQuestion`, presenting a compact summary of the findings (one line each: `location — issue → fix`). Options:
  - **Apply all fixes & re-plan** → the findings touch requirements/structure (e.g. EARS rewrites, added/removed tasks) → re-dispatch `conductor:spec-planner` ONCE with the findings appended to a `PREVIOUS_ERRORS` line (`REGEN_FOCUS: address spec-reviewer findings — <bulleted list>`), then re-run §2.3 refuter + §2.4. Still `CHANGES_REQUESTED` after one regen → announce the sustained findings and proceed **non-blocking** (a human-judgment call, not a hard halt).
  - **Apply minor fixes inline** → the findings are localized edits (a tag drop, a missing `<!-- AC-n -->`, a single EARS rewrite) you can apply yourself with Edit → apply them → re-dispatch §2.4 once to confirm clean.
  - **Accept & proceed (with debt)** → announce the unaddressed findings as known debt → proceed to §2.5.
- **`FAILURE`** → announce `REASON` → re-dispatch `conductor:spec-reviewer` ONCE. Still `FAILURE` → halt: `"spec-reviewer could not complete — inspect <track_dir>/spec.md and plan.md."`

`STRUCTURE_CHANGED: true` from a revision you applied → note for §2.6 `init-from-plan` (it re-derives structure from `plan.md` regardless, so this is informational).

**Carry forward the §2.3 `ac_integrity_gate` verdict.** It is `PASS` or `N/A`+`ac_integrity_reason:"spec_missing"` → nothing to surface (a legitimately spec-less track). If `WARN`, announce the advisory before the review: `"⚠️ AC-integrity WARN: <gate string> — these ACs are traced but not fully grounded; review with this in mind."` so you + the user assess the spec informed by AC traceability (the `ac_evidence` list from the §2.3 JSON shows each AC's measured/claimed/missing TCs). A `FAILED` gate, and an `N/A`+`"no_acs"` gate, cannot reach here — §2.3 loops until they pass or halts.

> The audit happens in the subagent; the human review loop happens here. You only see the compact verdict + findings.

> **Resume:** `track-state new-track-step "<track_dir>" reviewed`

### 2.5 Execution Mode Selection

Before creating state artifacts, let the user choose execution mode.

Use `AskUserQuestion`:

> "Choose execution mode for this track:"

Options:
- **Interactive** (recommended) — pauses for your confirmation at phase checkpoints. Best for complex or high-risk work.
- **Continuous** — auto-proceeds through all phases without pausing. Only stops on failures or blocked tasks.

Store the user's choice as `$EXECUTION_MODE` for use in Section 2.6.

> **Resume:** `track-state new-track-set-mode "<track_dir>" --mode <interactive|continuous>`

### 2.6 Create State Artifacts

1. **Check uniqueness:** List existing track dirs. If `track_dir` already exists:
   - Contains `.conductor/new-track-progress.json` → **this is a resume** (not a collision) — proceed.
   - Otherwise → halt → suggest alternatives.
2. **Track ID:** Already derived in §2.1 via `track-state derive-name`. Do not re-derive or hand-write the date.
3. **Initialize track** (structure derived mechanically from `plan.md` — the orchestrator never hand-extracts tasks/subtasks):
   ```bash
   track-state init-from-plan "<track_dir>" \
     --track-id <id> \
     --type <type> \
     --description '<desc>' \
     --execution-mode <interactive|continuous>
   ```
   This validates `plan.md` syntax and creates `track-state.json` + `index.md` in one call, extracting every task and subtask deterministically. On `ok: false` (malformed `plan.md`) → halt → announce the reported `errors`.
   > **Resume:** `track-state new-track-step "<track_dir>" state_created`
4. **Update Tracks Registry:** `track-state registry-add "<track_dir>"` — appends the canonical entry (`- [<marker>] <description> (conductor/tracks/<track_id>/)`) from `track-state.json`; idempotent and auto-locates `conductor/tracks.md`. **Never hand-write the line** — a freeform entry (no `(link)`, plain bullet, bold id) is silently dropped by `setup`/`resolve-track`, which breaks auto-select AND explicit `setup <track>`.
   > **Resume:** `track-state new-track-step "<track_dir>" registry_updated`
5. **Commit:**
   ```bash
   git add -A && git commit -m "chore(conductor): Add track '<track_id>'"
   ```
6. **Finalize resume marker:** delete it (track is durable now) — `track-state new-track-finalize "<track_dir>"` (idempotent).
7. Announce: `"New track '<track_id>' created at <track_dir> (mode: $EXECUTION_MODE)."`

### 2.7 Offer Auto-Start

After announcing track creation, offer to start implementation:

**If `$EXECUTION_MODE` is "interactive":** Use `AskUserQuestion`:
> "Track '<track_id>' is ready. Start implementation now?"

Options:
- "Yes, start implementation" → invoke `/conductor:implement <track_id>`
- "No, start later" → end skill. User can manually call `/conductor:implement` later.

**If `$EXECUTION_MODE` is "continuous":** Auto-start `/conductor:implement <track_id>` without asking.

This provides seamless handoff while preserving user control in interactive mode.