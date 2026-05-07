---
name: new-track
description: Creates a new track with spec, plan, and track-state.json for orchestrator-driven execution
when_to_use: User wants to create a new feature track, bug fix track, or chore track with specification and plan
arguments: [track_description]
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet
---

# Conductor NewTrack

## 1.0 SYSTEM DIRECTIVE

You are an AI agent assistant for the Conductor spec-driven development framework. Your current task is to guide the user through creating a new "Track" (a feature or bug fix), generate the necessary specification (`spec.md`), plan (`plan.md`), and state (`track-state.json`) files, and organize them within a dedicated track directory.

**Available Subagents:**
- **`conductor-spec-planner`** — Generates spec.md and plan.md from collected requirements and project context. Dispatch via `Agent` tool with `subagent_type: "conductor-spec-planner"`.

**Core Protocols:** File paths resolved via project CLAUDE.md TOC.

CRITICAL: You must validate the success of every tool call. If any tool call fails, halt immediately, announce the failure, and await instructions.

---

## 1.1 SETUP CHECK

**PROTOCOL: Verify that the Conductor environment is properly set up.**

1. **Verify Core Context:** Using the file paths from **project CLAUDE.md TOC**, resolve and verify the existence of:
   - **Tracks Registry** (to confirm setup completed)
   - **Product Definition**
   - **Tech Stack**
   - **Workflow Index** (`conductor/workflow/index.md`)

2. **Handle Failure:**
   - If ANY are missing, halt immediately.
   - Announce: "Conductor environment incomplete — missing: <files>. Please run `/conductor:setup`."

---

## 2.0 NEW TRACK INITIALIZATION

### 2.1 Get Track Description and Determine Type

1. **Load Project Context:** Read and understand the project documents.
2. **Get Track Description:**
   - If `{{args}}` contains a description: use it.
   - If empty: ask the user for a brief description.
3. **Infer Track Type:** Analyze the description. Do NOT ask the user to classify it.

### 2.2 Interactive Requirements Gathering

#### Context Discovery & Workflow Decision

1. **Scan & Match:** Search for files semantically related to the track's goal.
2. **Evaluate & Branch:**
   - **PATH A: Relevant Documents FOUND** — Read and synthesize. Skip questioning phase. Collect document paths for the subagent.
   - **PATH B: NO Relevant Documents FOUND** — Present options to user:
     1. Start Interactive Q&A
     2. Provide Context Manually
     3. Correct Search paths

#### Questioning Phase (if needed)

1. Ask questions **sequentially (one by one)**. Wait for each response.
2. **Classify** each question as "Additive" or "Exclusive Choice".
3. For features: ask 3-5 questions. For bugs/chores: ask 2-3 questions.
4. Present 2-3 options with "Type your own answer" as last option.

### 2.3 Dispatch Spec & Plan Generator Subagent

Once requirements are gathered, dispatch the `conductor-spec-planner` subagent. The subagent writes `spec.md` and `plan.md` directly to disk and returns a compact summary.

**Build the dispatch prompt:**

```
## Generation Input
- TRACK_DIR: {track_dir}
- TRACK_DESCRIPTION: {description}
- TRACK_TYPE: {type}
- USER_ANSWERS: {collected answers or "N/A"}
- RELATED_DOCS: {comma-separated paths or "N/A"}
```

**Launch the subagent:**
1. Use the **Agent tool** with `subagent_type: "conductor-spec-planner"`.
2. Description: `"Generate spec and plan for track '<track_description>'"`.
3. Pass the dispatch prompt above as the prompt.
4. Wait for the subagent to complete.
5. Parse the `---SPEC PLAN RESULT---` / `---END SPEC PLAN RESULT---` block.
6. On **FAILURE** → announce error and halt.
7. On **SUCCESS** → extract `PLAN_STRUCTURE` for Step 2.5. The files are already on disk.

### 2.4 Review Generated Artifacts

1. **Read spec.md** from disk. Present to user for review. Revise until confirmed.
2. **Read plan.md** from disk. Present to user for review. Revise until confirmed.

### 2.5 Create Track State Artifacts

1. **Check for existing track name:** Resolve **Tracks Directory**. List existing track directories. If proposed short name matches, halt and suggest alternatives.

2. **Generate Track ID:** Format `shortname_YYYYMMDD`.

3. **Create `track-state.json`** using `PLAN_STRUCTURE` from the subagent result:
   ```json
   {
     "track_id": "<track_id>",
     "type": "<inferred_type>",
     "status": "new",
     "created_at": "<ISO 8601 timestamp>",
     "updated_at": "<ISO 8601 timestamp>",
     "description": "<user description>",
     "current_phase_index": 0,
     "current_task_index": 0,
     "phases": [
       {
         "name": "Phase 1: ...",
         "status": "pending",
         "tasks": [
           { "name": "Task name from plan", "status": "pending" }
         ]
       }
     ]
   }
   ```
   Map each entry in `PLAN_STRUCTURE.phases[]` to the `phases[]` array above. Set all statuses to `"pending"`.

4. **Write Track index.md:**
   ```markdown
   # Track <track_id> Context

   ## Track Files
   - [Specification](./spec.md)
   - [Implementation Plan](./plan.md)
   - [Track State](./track-state.json)
   - [Issues Log](./issues.md) (created lazily on first failure)
   ```
   - Do NOT create `issues.md` — it is created lazily on first failure.

5. **Update Tracks Registry:**
   - Append new section:
     ```markdown

     ---

     - [ ] **Track: <Track Description>**
       *Link: [./<Relative Track Path>/](./<Relative Track Path>/)*
     ```

6. **Announce Completion:**
   > "New track '<track_id>' created. Start implementation with `/conductor:implement`."
