---
name: new-track
description: Creates a new track with spec, plan, and track-state.json for orchestrator-driven execution
when_to_use: User wants to create a new feature track, bug fix track, or chore track with specification and plan
argument-hint: "[track_description]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet
---

# Conductor NewTrack

## 0.0 LOAD REFERENCE LAYER

Read and internalize `${CLAUDE_PLUGIN_ROOT}/conductor-reference.md` for file resolution rules.

## 1.0 SETUP CHECK

1. Verify via project CLAUDE.md TOC: Tracks Registry, Product Definition, Tech Stack, Workflow Index.
2. If ANY missing → halt: `"Conductor environment incomplete — missing: <files>. Run /conductor:setup."`

**Subagent:** `conductor:spec-planner` — generates spec.md and plan.md.

CRITICAL: Validate every tool call. On failure → halt → announce.

---

## 2.0 TRACK INITIALIZATION

### 2.1 Description & Type

1. Get description from `$ARGUMENTS` or `AskUserQuestion`.
2. Infer track type (feature/bugfix/chore) — do NOT ask user.

### 2.2 Context Discovery

1. **Scan & Match:** Search for files semantically related to track's goal.
2. **Found relevant docs** → read + synthesize, skip questioning. Collect paths for subagent.
3. **Not found** → ask user: interactive Q&A (2-5 questions sequentially), manual context, or correct paths.

### 2.3 Dispatch Spec-Planner

`Agent` tool, `subagent_type: "conductor:spec-planner"`. Description: `"Generate spec/plan for '<desc>'"`.

```
TRACK_DIR={track_dir}
TRACK_DESCRIPTION={desc}
TRACK_TYPE={type}
USER_ANSWERS={answers or N/A}
RELATED_DOCS={paths or N/A}
```

Parse `---SPEC PLAN RESULT---` block. Extract `PLAN_STRUCTURE`. Files are on disk.

### 2.4 Review Artifacts

1. Read spec.md → present for review → revise until confirmed.
2. Read plan.md → present for review → revise until confirmed.

### 2.5 Create State Artifacts

1. **Check uniqueness:** List existing track dirs. If name matches → halt → suggest alternatives.
2. **Track ID:** Format `shortname_YYYYMMDD`.
3. **track-state.json:** Generate from `PLAN_STRUCTURE`. Schema:
   ```json
   {"track_id":"...", "type":"...", "status":"new", "description":"...",
    "current_phase_index":0, "current_task_index":0,
    "phases":[{"name":"...", "status":"pending",
      "tasks":[{"name":"...", "status":"pending",
        "subtasks":[{"name":"...", "status":"pending"}]}]}]}
   ```
   Tasks with `subtasks` key → include subtasks array. Without → flat task.
4. **Track index:** Read `${CLAUDE_PLUGIN_ROOT}/templates/track-index.md`, replace `{TRACK_ID}`, write to `<track_dir>/index.md`.
5. **Update Tracks Registry:** Append entry to `conductor/tracks.md`.
6. Announce: `"New track '<track_id>' created. Run /conductor:implement."`
