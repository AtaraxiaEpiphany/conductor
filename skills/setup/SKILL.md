---
name: setup
description: Scaffolds the project with Conductor environment, creates initial track with track-state.json
when_to_use: User wants to initialize a new project with Conductor, or set up the conductor directory structure
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet
---

# Conductor Setup

## 1.0 SYSTEM DIRECTIVE

You are an AI agent. Your primary function is to set up and manage a software project using the Conductor methodology. This document is your operational protocol. Adhere to these instructions precisely and sequentially. Do not make assumptions.

**Available Subagents:**
- **`conductor-project-analyzer`** — Scans brownfield projects to detect tech stack, architecture, and structure. Dispatch via `Agent` tool with `subagent_type: "conductor-project-analyzer"`.
- **`conductor-spec-planner`** — Generates spec.md and plan.md content from requirements and project context. Returns structured output block. Dispatch via `Agent` tool with `subagent_type: "conductor-spec-planner"`.

**Core Protocols:** Execution Firewall — defined in the system prompt. File paths resolved via project CLAUDE.md TOC.

CRITICAL: You must validate the success of every tool call. If any tool call fails, halt immediately, announce the failure, and await instructions.

CRITICAL: When determining model complexity, ALWAYS select the "flash" model, regardless of the task's perceived difficulty.

---

## 1.1 BEGIN RESUME CHECK

**PROTOCOL: Before starting, check for existing setup state.**

1. Read `conductor/setup_state.json` if it exists.
2. Resume from the appropriate step based on `last_successful_step`:
   - `"2.1_product_guide"` → Resume at Section 2.2
   - `"2.2_product_guidelines"` → Resume at Section 2.3
   - `"2.3_tech_stack_styleguides"` → Resume at Section 2.4
   - `"2.4_workflow"` → Resume at Section 3.0
   - `"3.4_track_artifacts_created"` → Resume at Section 3.5 (commit only)
   - `"3.5_setup_complete"` → Already complete. Announce and halt.
3. If file doesn't exist → new setup. Proceed to 1.2.

---

## 1.2 PRE-INITIALIZATION OVERVIEW

Present the setup overview:
> "Welcome to Conductor. I will guide you through:
> 1. **Project Discovery** — Analyze current directory
> 2. **Product Definition** — Define vision, guidelines, tech stack
> 3. **Configuration** — Compose workflow with style guides and dev commands
> 4. **Track Generation** — Define initial track with state file"

---

## 2.0 PHASE 1: PROJECT SETUP

**The built-in variable `${CLAUDE_PLUGIN_ROOT}` points to this plugin's installation directory.**

All template references use `${CLAUDE_PLUGIN_ROOT}/templates/...`.

### 2.0 Project Inception

1. **Detect Project Maturity:**
   - **Brownfield indicators:** `.git` directory, `package.json`, `requirements.txt`, `go.mod`, source code dirs.
   - **Greenfield:** None of the above + empty or docs-only directory.

2. **Execute based on maturity:**

   **Brownfield — Dispatch Project Analyzer:**
   1. Use the **Agent tool** with `subagent_type: "conductor-project-analyzer"`.
   2. Description: `"Analyze brownfield project structure and tech stack"`.
   3. Pass prompt:
      ```
      ## Analysis Input
      - PROJECT_DIR: {project_root}
      - PROJECT_NAME: {project_name}
      ```
   4. Wait for the subagent to complete.
   5. Parse the `---ANALYSIS RESULT---` / `---END ANALYSIS RESULT---` JSON block.
   6. Use the analysis results to pre-fill tech stack, suggest style guides, and recommend workflow.

   **Greenfield — Interactive:**
   1. Announce new project.
   2. Ask "What do you want to build?"

3. **Initialize Git** (greenfield only): `git init` if no `.git`.

4. **Create conductor directory and state file.**

### 2.1 Generate Product Guide (Interactive)

- Ask up to 5 questions sequentially.
- Auto-generate option available.
- User confirmation loop.
- Write to `conductor/overview/product.md`.
- Commit state: `{"last_successful_step": "2.1_product_guide"}`

### 2.2 Generate Product Guidelines (Interactive)

- Same interactive pattern.
- Write to `conductor/overview/product-guidelines.md`.
- Commit state: `{"last_successful_step": "2.2_product_guidelines"}`

### 2.3 Generate Tech Stack & Style Guides (Interactive)

**Part A — Tech Stack:**
- Same interactive pattern.
- **Brownfield:** Pre-fill from `conductor-project-analyzer` results. Ask user to confirm detected stack.
- **Greenfield:** Ask from scratch.
- Write to `conductor/design/tech-stack.md`.

**Part B — Auto-Derive Style Guides:**
After tech stack is confirmed, derive style guides automatically using this mapping:

| Detected Language | Style Guides |
|---|---|
| JavaScript | `javascript.md` |
| TypeScript | `typescript.md` + `javascript.md` |
| Python | `python.md` |
| Go | `go.md` |
| C++ | `cpp.md` |
| C# | `csharp.md` |
| Dart | `dart.md` |
| HTML/CSS | `html-css.md` |
| *(any)* | `general.md` (always included) |

1. Parse languages from the confirmed tech stack.
2. Map each language to its style guide files (always include `general.md`).
3. Present the derived list to the user: *"Based on your tech stack, these style guides will be included: ..."*
4. User confirms or adjusts.
5. Copy selected guides from `${CLAUDE_PLUGIN_ROOT}/templates/code-styleguides/` to `conductor/workflow/code-styleguides/`.

- Commit state: `{"last_successful_step": "2.3_tech_stack_styleguides"}`

### 2.4 Generate Workflow

Compose the workflow from modular templates based on the confirmed tech stack.

**Step 1 — Core Template:**
Read `${CLAUDE_PLUGIN_ROOT}/templates/template.md` and write to `conductor/workflow/template.md`.

**Step 2 — Inject Dev Commands:**
For each language in the tech stack, read the corresponding file from `${CLAUDE_PLUGIN_ROOT}/templates/dev-commands/<lang>.md` (where `<lang>` is one of: `javascript`, `typescript`, `python`, `go`, `cpp`, `csharp`, `dart`).
- If a dev-commands file exists for the language, append its content into the `## Development Commands` section of `conductor/workflow/template.md`.
- If no dev-commands template matches, leave the section with the placeholder comment.

**Step 3 — Task Workflow & Phase Checkpoint:**
Copy these files directly:
- `${CLAUDE_PLUGIN_ROOT}/templates/task-workflow.md` → `conductor/workflow/task-workflow.md`
- `${CLAUDE_PLUGIN_ROOT}/templates/phase-checkpoint.md` → `conductor/workflow/phase-checkpoint.md`

**Step 4 — Generate index.md Dynamically:**
Write `conductor/workflow/index.md` with the following structure, populated with the actual files created:

```markdown
# Workflow Index

## Workflow Definition

| Document | Path | Purpose |
|----------|------|---------|
| Workflow Template | [template.md](./template.md) | Guiding principles, quality gates, dev commands, commit guidelines |
| Task Workflow | [task-workflow.md](./task-workflow.md) | 11-step standard task workflow with task selection protocol |
| Phase Checkpoint | [phase-checkpoint.md](./phase-checkpoint.md) | Phase completion verification and checkpointing protocol |

## Code Style Guides

<!-- List ONLY the guides actually copied in Section 2.3 -->
| Language | Document |
|----------|----------|
| General | [general.md](./code-styleguides/general.md) |
| <Language> | [<lang>.md](./code-styleguides/<lang>.md) |
```

**Step 5 — Verify:**
Confirm every file referenced in `index.md` exists under `conductor/workflow/`.

- Commit state: `{"last_successful_step": "2.4_workflow"}`

### 2.5 Finalization

1. **Generate CLAUDE.md TOC:** Create or update the project's `CLAUDE.md` with a Conductor TOC section:
   ```markdown
   # Conductor

   ## File Index

   Use this map when explicit links are missing. All new documents MUST be created in the following **RELEVANT** paths:

   | Category        | Document Type           | Default Path Pattern                                       | Creation Rule                         |
   | :-------------- | :---------------------- | :--------------------------------------------------------- | :------------------------------------ |
   | **Overview**    | Product Definition      | `./conductor/overview/product.md`                          | Create if missing.                    |
   |                 | Product Guidelines      | `./conductor/overview/product-guidelines.md`               | Create if missing.                    |
   | **Requirement** | PRD                     | `./conductor/requirement/prd/<name>.md`                    | **Create here** if missing.           |
   | **Design**      | Tech Stack              | `./conductor/design/tech-stack.md`                         | Create if missing.                    |
   |                 | UX/UI Spec              | `./conductor/requirement/ux-ui/design-spec.md`             | Create if missing.                    |
   |                 | Architecture            | `./conductor/design/architecture/system-architecture.md`   | Create if missing.                    |
   |                 | DB Design               | `./conductor/design/database/schema.md`                    | Create if missing.                    |
   |                 | API Specs               | `./conductor/design/api-specs/<endpoint>.md`               | **Strict Schema Adherence Required**. |
   | **Workflow**    | Workflow Index          | `./conductor/workflow/index.md`                            | Create if missing.                    |
   |                 | Code Patterns           | `./conductor/workflow/code-styleguides/<code-patterns>.md` | Create if missing.                    |
   |                 | Code Style              | `./conductor/workflow/code-styleguides/<language>.md`      | Create if missing.                    |
   |                 | Git Flow                | `./conductor/workflow/git-flow.md`                         | Create if missing.                    |
   |                 | Testing                 | `./conductor/workflow/testing/strategy.md`                 | Create if missing.                    |
   | **Resources**   | References/FAQ/Glossary | `./conductor/resource/<type>.md`                           | Create if needed.                     |
   | **Management**  | Track Spec/Plan/Meta    | `./conductor/tracks/<track_id>/`                           | Read/Update based on context.         |
   ```

   **Rules:**
   - If CLAUDE.md already exists, append the Conductor section (do not overwrite existing content).
   - If CLAUDE.md does not exist, create it with the Conductor section.
   - This TOC is the single source of truth for file paths. All Conductor skills resolve files via this TOC.

2. **Generate Index File:** `conductor/index.md` with all project context links.
3. **Summarize** all Phase 1 actions.

---

## 3.0 INITIAL PLAN AND TRACK GENERATION

### 3.1 Generate Product Requirements (Greenfield only)

- Ask up to 5 questions sequentially.
- Auto-generate option available.

### 3.2 Propose Initial Track

- Analyze context and requirements.
- Generate single track title.
- User confirmation.

### 3.3 Dispatch Spec & Plan Generator

Once requirements are gathered and track is confirmed, dispatch the `conductor-spec-planner` subagent. The subagent writes `spec.md` and `plan.md` directly to disk and returns a compact summary.

**Build the dispatch prompt:**

```
## Generation Input
- TRACK_DIR: {track_dir}
- TRACK_DESCRIPTION: {confirmed track description}
- TRACK_TYPE: {inferred type}
- USER_ANSWERS: {collected requirements or "N/A"}
- RELATED_DOCS: {comma-separated paths to product.md, tech-stack.md, etc.}
```

**Launch the subagent:**
1. Use the **Agent tool** with `subagent_type: "conductor-spec-planner"`.
2. Description: `"Generate spec and plan for initial track '<track_description>'"`.
3. Pass the dispatch prompt above as the prompt.
4. Wait for the subagent to complete.
5. Parse the `---SPEC PLAN RESULT---` / `---END SPEC PLAN RESULT---` block.
6. On **FAILURE** → announce error and halt.
7. On **SUCCESS** → extract `PLAN_STRUCTURE` for Step 3.4. The files are already on disk.

### 3.4 Create Track State Artifacts (Parent)

**The subagent already wrote `spec.md` and `plan.md` to disk. The parent only creates state/registry files.**

1. **Initialize Tracks File:** Create `conductor/tracks.md` if it does not exist.

2. **Generate track-state.json** using `PLAN_STRUCTURE` from the subagent result:
   ```json
   {
     "track_id": "<track_id>",
     "type": "<type>",
     "status": "new",
     "created_at": "<timestamp>",
     "updated_at": "<timestamp>",
     "description": "<description>",
     "current_phase_index": 0,
     "current_task_index": 0,
     "phases": [
       {
         "name": "Phase 1: ...",
         "status": "pending",
         "tasks": [
           { "name": "Task name", "status": "pending" }
         ]
       }
     ]
   }
   ```
   Map each entry in `PLAN_STRUCTURE.phases[]` to the `phases[]` array above. Set all statuses to `"pending"`.

3. **Write Track index.md:**
   ```markdown
   # Track <track_id> Context

   ## Track Files
   - [Specification](./spec.md)
   - [Implementation Plan](./plan.md)
   - [Track State](./track-state.json)
   - [Issues Log](./issues.md) (created lazily on first failure)
   ```

4. **Write Project index.md:**
   ```markdown
    # Project Context

    ## Product Overview

    - [Product Definition](./overview/product.md)
    - [Product Guidelines](./overview/product-guidelines.md)

    ## Product Requirement

    - [PRD Index](./requirement/prd/index.md)
       > ⚠️ **CRITICAL**: ONLY read the **matching or semantically similar** product requirement docs.

    ## Design System

    - [Tech Stack](./design/tech-stack.md)
    - [Design Specification](./requirement/ux-ui/design-spec.md)
    - [System Architecture](./design/architecture/system-architecture.md)
    - [Database Design](./design/database/index.md)
    - [API Specifications](./design/api-specs/index.md)
       > ⚠️ **CRITICAL**: ONLY read the **matching or semantically similar** API docs.

    ## Development Workflow

    - [Workflow Index](./workflow/index.md)

    ## Knowledge & Resources

    - [References](./resource/references/index.md)
    - [FAQ](./resource/faq/index.md)
    - [Glossary](./resource/glossary.md)

    ## Management

    - [Tracks Registry](./tracks.md)
   ```

5. **Update Tracks Registry:** Append the new track section to `conductor/tracks.md`.

6. **Write checkpoint state:** `{"last_successful_step": "3.4_track_artifacts_created"}`

### 3.5 Final Commit (Parent)

1. `git add` all conductor files.
2. `git commit -m "conductor(setup): Add conductor setup files"`
3. Announce completion with summary of created artifacts.
4. Inform user: "Run `/conductor:implement` to begin."
