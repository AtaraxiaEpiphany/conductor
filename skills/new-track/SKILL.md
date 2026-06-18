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

### 2.2 Context Discovery (Paths Only)

1. **Scan & Match:** Search `conductor/index.md` for file paths semantically related to track's goal.
2. **Found relevant docs** → collect paths only (do NOT read contents). Pass paths to spec-planner as `RELATED_DOCS`.
3. **Not found** → ask user: interactive Q&A (2-5 questions sequentially), manual context, or correct paths. Pass answers as `USER_ANSWERS`.

> Context content is loaded by spec-planner itself. The orchestrator handles only paths and summaries.

### 2.3 Dispatch Spec-Planner

`Agent` tool, `subagent_type: "conductor:spec-planner"`. Description: `"Generate spec/plan for '<desc>'"`.

```
TRACK_DIR={track_dir}
TRACK_DESCRIPTION={desc}
TRACK_TYPE={type}
USER_ANSWERS={answers or N/A}
RELATED_DOCS={paths or N/A}
```

Parse `---SPEC PLAN RESULT---` block. Confirm `STATUS: SUCCESS` (halt on FAILURE and announce `SUMMARY`). `plan.md` and `spec.md` are now on disk — `PLAN_STRUCTURE` is **no longer required**: Section 2.6 derives the full task/subtask structure mechanically from `plan.md`, eliminating manual transcription.

### 2.4 Dispatch Spec-Reviewer

`Agent` tool, `subagent_type: "conductor:spec-reviewer"`. Description: `"Review spec/plan for '<desc>'"`.

```
TRACK_DIR={track_dir}
```

Parse `---REVIEW RESULT---` block. If `STATUS: CANCELLED` → halt. If `STRUCTURE_CHANGED: true` → note for init.

> Full file review happens in the subagent. The orchestrator only sees the compact result.

### 2.5 Execution Mode Selection

Before creating state artifacts, let the user choose execution mode.

Use `AskUserQuestion`:

> "Choose execution mode for this track:"

Options:
- **Interactive** (recommended) — pauses for your confirmation at phase checkpoints. Best for complex or high-risk work.
- **Continuous** — auto-proceeds through all phases without pausing. Only stops on failures or blocked tasks.

Store the user's choice as `$EXECUTION_MODE` for use in Section 2.6.

### 2.6 Create State Artifacts

1. **Check uniqueness:** List existing track dirs. If name matches → halt → suggest alternatives.
2. **Track ID:** Format `shortname_YYYYMMDD`.
3. **Initialize track** (structure derived mechanically from `plan.md` — the orchestrator never hand-extracts tasks/subtasks):
   ```bash
   track-state init-from-plan "<track_dir>" \
     --track-id <id> \
     --type <type> \
     --description '<desc>' \
     --execution-mode <interactive|continuous>
   ```
   This validates `plan.md` syntax and creates `track-state.json` + `index.md` in one call, extracting every task and subtask deterministically. On `ok: false` (malformed `plan.md`) → halt → announce the reported `errors`.
4. **Update Tracks Registry:** Append entry to `conductor/tracks.md`.
5. **Commit:**
   ```bash
   git add -A && git commit -m "conductor(track): Add track '<track_id>'"
   ```
6. Announce: `"New track '<track_id>' created at <track_dir> (mode: $EXECUTION_MODE)."`

### 2.7 Offer Auto-Start

After announcing track creation, offer to start implementation:

**If `$EXECUTION_MODE` is "interactive":** Use `AskUserQuestion`:
> "Track '<track_id>' is ready. Start implementation now?"

Options:
- "Yes, start implementation" → invoke `/conductor:implement <track_id>`
- "No, start later" → end skill. User can manually call `/conductor:implement` later.

**If `$EXECUTION_MODE` is "continuous":** Auto-start `/conductor:implement <track_id>` without asking.

This provides seamless handoff while preserving user control in interactive mode.
