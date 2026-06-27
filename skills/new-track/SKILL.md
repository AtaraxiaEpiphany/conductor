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
progress marker lets an interrupted run resume instead of starting over.

1. Glob `conductor/tracks/*/.conductor/new-track-progress.json` for any file with `"committed": false`.
2. **Found** → `AskUserQuestion`: *"Found an incomplete track `<track_id>` ('<description>'), last reached `<last_step>`. Resume?"*
   - **Yes (resume)** → read the JSON, re-derive `description`, `type`, `track_id`, `track_dir`, `execution_mode` from it, then **jump to the first section whose step key is NOT in `steps_done`** (keys, in order: `spec_planned` → §2.3, `reviewed` → §2.4, `state_created` → §2.6, `registry_updated` → §2.6). Skip sections already marked done.
   - **No** → warn the user an orphaned partial track exists at that path, then proceed to a fresh track (§1.0).
3. **Not found** → fresh track → proceed to §1.0.

The progress file is written in §2.1 and deleted once the track is committed (end of §2.6).

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
4. **Initialize resume marker** (skip if resuming — the file already exists): create `<track_dir>/.conductor/` and write `new-track-progress.json`:
   ```json
   {"track_id":"<id>","track_dir":"<track_dir>","description":"<desc>","type":"<type>","execution_mode":null,"steps_done":[],"committed":false}
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
     headings or `Task:`/`Subtask:` lines — the same defect that caused the
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

**Validate the generated plan (catch format defects now, not at §2.6).** `plan.md` is sometimes written with a format defect the LLM does not self-catch — a task/subtask line missing its `- [ ]` checkbox or `Task:`/`Subtask:` marker, or a missing `## Phase N:` heading. Such a plan reads fine to a human but fails `init-from-plan` at §2.6, halting the whole track. Validate it *now* with `--check` (writes nothing — same `parse_plan` parser §2.6 uses) and re-dispatch spec-planner with the exact errors if it fails, instead of halting.

Loop — max **2 re-dispatches (3 total attempts)**, counting from the first dispatch above:

1. ```bash
   track-state init-from-plan "<track_dir>" --check
   ```
2. `ok: true` → plan conforms (note the reported `phases`/`tasks` counts) → break out of the loop, proceed to the resume marker below.
3. `ok: false` → the emitted `errors` describe the defect(s). If a re-dispatch remains, dispatch `conductor:spec-planner` again with the errors appended so it can fix the format:

   ```
   TRACK_DIR={track_dir}
   TRACK_DESCRIPTION={desc}
   TRACK_TYPE={type}
   USER_ANSWERS={answers or N/A}
   RELATED_DOCS={paths or N/A}
   PREVIOUS_ERRORS:
   {the errors[] list, verbatim}
   REGEN_FOCUS: The prior plan.md failed the format contract. Every task AND subtask line MUST begin with `- [ ]`; every phase MUST begin with `## Phase N: Name`; subtasks are indented 2 spaces under their parent and never replace the `[ ]` with a tag. Re-read conductor/design/plan-format-contract.md, then regenerate a conforming plan.md (keep the existing spec.md if it is adequate).
   ```

   Re-parse the returned `---SPEC PLAN RESULT---` block (halt on FAILURE), then re-run step 1.

4. Still `ok: false` after the final attempt → **halt**: `"Spec-planner produced a malformed plan.md after 3 attempts — errors: <errors>. Inspect <track_dir>/plan.md."` Do NOT proceed to §2.6 (it would fail identically).

> **Resume:** append `"spec_planned"` to `steps_done` in `<track_dir>/.conductor/new-track-progress.json` **only after the `--check` loop returns `ok: true`** — a plan that has not yet validated is not "planned".

### 2.4 Dispatch Spec-Reviewer

Dispatch `conductor:spec-reviewer`, prompt:

```
TRACK_DIR={track_dir}
```

Parse `---REVIEW RESULT---` block. If `STATUS: CANCELLED` → halt. If `STRUCTURE_CHANGED: true` → note for init.

> Full file review happens in the subagent. The orchestrator only sees the compact result.

> **Resume:** append `"reviewed"` to `steps_done` in `<track_dir>/.conductor/new-track-progress.json`.

### 2.5 Execution Mode Selection

Before creating state artifacts, let the user choose execution mode.

Use `AskUserQuestion`:

> "Choose execution mode for this track:"

Options:
- **Interactive** (recommended) — pauses for your confirmation at phase checkpoints. Best for complex or high-risk work.
- **Continuous** — auto-proceeds through all phases without pausing. Only stops on failures or blocked tasks.

Store the user's choice as `$EXECUTION_MODE` for use in Section 2.6.

> **Resume:** write `execution_mode` into `<track_dir>/.conductor/new-track-progress.json`.

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
   > **Resume:** append `"state_created"` to `steps_done`.
4. **Update Tracks Registry:** Append entry to `conductor/tracks.md`.
   > **Resume:** append `"registry_updated"` to `steps_done`.
5. **Commit:**
   ```bash
   git add -A && git commit -m "chore(conductor): Add track '<track_id>'"
   ```
6. **Finalize resume marker:** set `"committed": true` in `new-track-progress.json`, then delete the file (track is durable now).
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
