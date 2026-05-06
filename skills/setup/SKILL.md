---
name: conductor-setup
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
   - `"2.3_tech_stack"` → Resume at Section 2.4
   - `"2.4_code_styleguides"` → Resume at Section 2.5
   - `"2.5_workflow"` → Resume at Section 3.0
   - `"3.3_initial_track_generated"` → Already complete. Announce and halt.
3. If file doesn't exist → new setup. Proceed to 1.2.

---

## 1.2 PRE-INITIALIZATION OVERVIEW

Present the setup overview:
> "Welcome to Conductor. I will guide you through:
> 1. **Project Discovery** — Analyze current directory
> 2. **Product Definition** — Define vision, guidelines, tech stack
> 3. **Configuration** — Select style guides and workflow
> 4. **Track Generation** — Define initial track with state file"

---

## 2.0 PHASE 1: PROJECT SETUP

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

### 2.3 Generate Tech Stack (Interactive)

- Same interactive pattern.
- **Brownfield:** Pre-fill from `conductor-project-analyzer` results. Ask user to confirm detected stack.
- **Greenfield:** Ask from scratch.
- Write to `conductor/design/tech-stack.md`.
- Commit state: `{"last_successful_step": "2.3_tech_stack"}`

### 2.4 Select Code Style Guides (Interactive)
**Conductor installation**: `ls ~/.claude/plugins/marketplaces/conductor`
- List the available style guides by running `ls ~/.claude/plugins/marketplaces/conductor/templates/code_styleguides/`.
- **Brownfield:** Recommend based on detected languages from analyzer results.
- **Greenfield:** Recommend based on user's described tech stack.
- Copy selected guides to `conductor/workflow/code-styleguides/`.
- Commit state: `{"last_successful_step": "2.4_code_styleguides"}`

### 2.5 Select Workflow (Interactive)

1. **Generate Workflow:** Copy workflow template to `conductor/workflow/`:
   - Source: the `templates/*.md` from the Conductor installation.
2. **Generate Workflow Index:** Copy `templates/workflow/index.md` from the Conductor installation to `conductor/workflow/index.md`:
   - Source: the `workflow/index.md` from the Conductor installation.
3. **Verify Generation:** Confirm both files were written:
   - `conductor/workflow/index.md` contains links to all workflow resources.
4. **Verify Linked Files:** Read `conductor/workflow/index.md` and confirm every linked file exists in `conductor/workflow/`. If any are missing, report the discrepancy.
5. Commit state: `{"last_successful_step": "2.5_workflow"}`

### 2.6 Finalization

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
   | **Workflow**    | Dev Workflow            | `./conductor/workflow/workflow.md`                         | Create if missing.                    |
   |                 | Workflow Index          | `./conductor/workflow/index.md`                            | Create if missing.                    |
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

### 3.3 Create Track Artifacts

1. **Initialize Tracks File:** Create `conductor/tracks.md`.
2. **Generate Track Artifacts:**
   - Generate `spec.md` and `plan.md` automatically.
   - Inject phase completion tasks per workflow.
   - Include status markers `[ ]` for every task.

3. **Create track-state.json:**
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

4. **Write all files:** `spec.md`, `plan.md`, `track-state.json`, `index.md`.
   - `index.md` template:
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
5. **Commit state:** `{"last_successful_step": "3.3_initial_track_generated"}`

### 3.4 Final Announcement

1. Announce completion.
2. Commit all files: `conductor(setup): Add conductor setup files`
3. Inform user: "Run `/conductor:implement` to begin."
