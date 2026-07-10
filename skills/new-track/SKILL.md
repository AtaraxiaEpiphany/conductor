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

A new-track run can be interrupted before state artifacts exist (§2.6). A lightweight
progress marker — created, advanced, and deleted by the `track-state new-track-*`
commands (the orchestrator **never** hand-edits the JSON) — lets an interrupted run
resume instead of starting over.

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
- `conductor:spec-reviewer` — interactive review (keeps full files out of orchestrator context).

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
4. **Initialize resume marker** (skip if resuming — `new-track-resume` already found it). Creates `<track_dir>/.conductor/` and the marker in one call (idempotent — a no-op if the marker already exists):
   ```bash
   track-state new-track-init "<track_dir>" --track-id <id> --description "<desc>" --type <type>
   ```

### 2.2 Context Discovery (Paths Only)

1. **Scan & Match:** Search `conductor/index.md` for file paths semantically related to track's goal.
2. **Found relevant docs** → collect paths only (do NOT read contents). Pass paths to spec-planner as `RELATED_DOCS`.
3. **Not found** → ask user: interactive Q&A (2-5 questions sequentially), manual context, or correct paths. Pass answers as `USER_ANSWERS`.

> Context content is loaded by spec-planner itself. The orchestrator handles only paths and summaries.

### 2.3 Dispatch Spec-Planner

**Existing spec/plan guard (collision check).** Before regenerating, detect a
pre-existing `plan.md` — the user may have re-invoked new-track, or a prior run
wrote `plan.md` but left no resume marker. This is the "create fail on existing
plan.md/spec.md" case (issue #2). Skip this guard when resuming via §0.5 with
`spec_planned` already in `steps_done` (that plan is owned by the active run).

1. If `<track_dir>/plan.md` exists, validate it in place — `--check` writes
   nothing:
   ```bash
   track-state init-from-plan "<track_dir>" --check
   ```
   - `ok: true` → the existing plan.md is well-formed (the JSON reports
     `phases`/`tasks` counts). `AskUserQuestion`:
     *"An existing `plan.md` (N phases, M tasks) was found at `<track_dir>`. How should I proceed?"*
     - **Reuse existing plan** → skip spec-planner, append `"spec_planned"` to
       `steps_done`, and jump to §2.4 review.
     - **Regenerate (overwrite)** → continue to dispatch spec-planner below.
     - **Cancel** → halt.
   - `ok: false` → the existing plan.md is malformed (missing `## Phase N:`
     headings or task `- [ ]` checkbox lines — the same defect that caused the
     false-completion bug, issue #4). Announce the reported `errors`, then
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

Parse `---SPEC PLAN RESULT---` block. Confirm `STATUS: SUCCESS` (halt on FAILURE and announce `SUMMARY`). `plan.md` and `spec.md` are now on disk — `PLAN_STRUCTURE` is **no longer required**: Section 2.6 derives the full task/subtask structure mechanically from `plan.md`, eliminating manual transcription.

**Validate the generated plan + spec (catch format AND acceptance-criteria defects now, not at §2.6).** `plan.md` is sometimes written with a format defect the LLM does not self-catch — a task/subtask line missing its `- [ ]` checkbox, or a missing `## Phase N:` heading. Such a plan reads fine to a human but fails `init-from-plan` at §2.6, halting the whole track. And a plan can conform syntactically while an Acceptance Criterion in `spec.md` is never traced to a task or lacks a Test Scenario — a completeness hole the format check cannot see (completeness-critic). Validate **both** now and re-dispatch spec-planner with the exact defects if either fails.

Loop — max **2 re-dispatches (3 total attempts)**, counting from the first dispatch above:

1. **Format check** (writes nothing — same `parse_plan` parser §2.6 uses):
   ```bash
   track-state init-from-plan "<track_dir>" --check
   ```
   `ok: false` → collect the emitted `errors` (the defect(s)). `ok: true` → note the reported `phases`/`tasks` counts, continue to step 2.

2. **AC-integrity check** (run only if the format check passed; writes nothing):
   ```bash
   track-state spec-integrity "<track_dir>"
   ```
   Parse `ac_integrity_gate`. This runs at planning time — no `track-state.json` exists yet (§2.6 creates it); the command degrades gracefully. `N/A` (no `spec.md` / no `## Acceptance Criteria`) → a track without a formal spec is not penalized; treat as clean. `FAILED` → collect the gate string **verbatim** — it names the offending AC IDs and the authoring fix (e.g. "annotate the implementing task in plan.md with a `<!-- AC-n -->`", "add a `TC-{n}.{m} | AC-{n}` row under ## Test Scenarios"). Any other verdict (`PASS` / `WARN`) → clean (WARN is advisory; carry it into §2.4).

3. **Both clean** → break out of the loop; proceed to the resume marker below.

4. **Either failed** → if a re-dispatch remains, dispatch `conductor:spec-planner` again with the combined defects appended so it can fix both:

   ```
   TRACK_DIR={track_dir}
   TRACK_DESCRIPTION={desc}
   TRACK_TYPE={type}
   USER_ANSWERS={answers or N/A}
   RELATED_DOCS={paths or N/A}
   PREVIOUS_ERRORS:
   {the format errors[] and/or the AC-integrity gate string, verbatim}
   REGEN_FOCUS: The prior plan.md/spec.md failed validation. For FORMAT defects: every task AND subtask line MUST begin with `- [ ]`; every phase MUST begin with `## Phase N: Name`; subtasks are indented 2 spaces under their parent and never replace the `[ ]` with a tag. For AC-INTEGRITY defects: address the gate string — every AC-n must appear in some task's `<!-- AC-n -->` annotation AND map to a `TC-{n}.{m} | AC-n` row under ## Test Scenarios. Re-read `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/plan-format-contract.md`, then regenerate a conforming plan.md/spec.md.
   ```

   Re-parse the returned `---SPEC PLAN RESULT---` block (halt on FAILURE), then loop back to step 1.

5. Still failing after the final attempt → **halt**: `"Spec-planner produced a plan/spec that still fails validation after 3 attempts — errors: <combined defects>. Inspect <track_dir>/plan.md / spec.md."` Do NOT proceed to §2.6 (it would fail identically).

### 2.3b Adversarial Plan Refuter (semantic gate)

The §2.3 loop catches **format** and **AC-integrity** defects deterministically — but a plan can conform to every deterministic check and still be semantically weak: a Test Scenario that does not actually exercise its AC, an AC that drifts from the user's stated intent, or a task that maps to an AC in name only. These are judgment calls a deterministic gate cannot make. Run ONE adversarial refuter pass to challenge the plan's soundness before §2.4 review.

**Niche guard (do not duplicate §2.3).** The refuter must NOT re-derive what §2.3 already checked — AC→TC existence, dangling references, EARS well-formedness, and the TC/plan/verification coverage rates are §2.3's deterministic lane. Its value is the semantic layer above those: does a TC actually exercise its AC; does an AC match stated intent; does a task genuinely realize its AC.

Dispatch `conductor:refuter`, prompt:

```
PROJECT_DIR={project_root}
DOMAIN=plan
CLAIM=The spec.md + plan.md are semantically sound — every acceptance criterion reflects the user's stated intent, every AC is genuinely exercised by a Test Scenario (not merely name-matched), and no task is semantically orphaned from the AC it claims to realize.
CONTEXT_PATHS={track_dir}/spec.md {track_dir}/plan.md {USER_ANSWERS path or N/A}
AC_EVIDENCE={the ac_evidence list from the §2.3 spec-integrity JSON — each AC's measured/claimed/missing TCs}
```

> The CLAIM is framed as "the plan is sound" deliberately. The refuter defaults to `SUSTAINED` when it cannot pin a specific grounded defect, so `SUSTAINED` = proceed-when-uncertain and `REFUTED` = grounded evidence of unsoundness. A consequential plan gate must not hard-block the track on a hunch — only a cited, re-confirmable semantic defect justifies a regen. (The skip gate in `implement` §3.6 frames its CLAIM the opposite way, because skipping is the riskier action there.)

Parse the `---REFUTATION RESULT---` block:

- **STATUS: SUSTAINED** (default — no grounded semantic defect found) → proceed to §2.4.
- **STATUS: REFUTED** (positive, grounded defect, with `file:line` citations in EVIDENCE) → re-dispatch `conductor:spec-planner` ONCE with the refuter's challenges appended to `PREVIOUS_ERRORS` (reuse the §2.3 regen envelope; `REGEN_FOCUS` = the refuter's EVIDENCE + REASONING). Re-run this refuter once on the regenerated plan. If still `REFUTED`, announce the sustained challenges and proceed to §2.4 **non-blocking** — the spec-reviewer and user assess them there. A semantic disagreement the deterministic pipeline cannot close is a human-judgment call, not a hard halt.
- **STATUS: FAILURE** → treat as SUSTAINED (the refuter could not complete; the plan stands) and proceed to §2.4.

> **Resume:** stamp the step **only after BOTH the `--check` and `spec-integrity` checks pass AND the §2.3b refute completes** — a plan/spec that has not yet validated and been semantically vetted is not "planned":
> ```bash
> track-state new-track-step "<track_dir>" spec_planned
> ```

### 2.4 Dispatch Spec-Reviewer

Dispatch `conductor:spec-reviewer`, prompt:

```
TRACK_DIR={track_dir}
```

Parse `---REVIEW RESULT---` block. If `STATUS: CANCELLED` → halt. If `STRUCTURE_CHANGED: true` → note for init.

**Carry forward the §2.3 `ac_integrity_gate` verdict.** It is `PASS`/`N/A` → nothing to surface. If `WARN`, announce the advisory before the review: `"⚠️ AC-integrity WARN: <gate string> — these ACs are traced but not fully grounded; review with this in mind."` so the user + spec-reviewer assess the spec informed by AC traceability (the `ac_evidence` list from the §2.3 JSON shows each AC's measured/claimed/missing TCs). A `FAILED` gate cannot reach here — §2.3 loops until it passes or halts.

> Full file review happens in the subagent. The orchestrator only sees the compact result.

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
