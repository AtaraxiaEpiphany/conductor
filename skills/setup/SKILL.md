---
name: setup
description: Scaffolds the project with Conductor environment, creates initial track with track-state.json
when_to_use: User wants to initialize a new project with Conductor, or set up the conductor directory structure
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet
---

# Conductor Setup

## 0.0 RESOLVE PATHS

Key paths (resolve via `conductor/index.md` if non-default):
- Product: `conductor/overview/product.md`
- Tech Stack: `conductor/design/tech-stack.md`
- Tracks Registry: `conductor/tracks.md`
- Workflow Index: `conductor/workflow/index.md`

## 1.0 RESUME CHECK

1. Read `conductor/setup_state.json` if exists.
2. Resume from `last_successful_step` (keys: `2.1_product_guide` → `2.2_product_guidelines` → `2.3_tech_stack_styleguides` → `2.4_workflow` → `2.5_finalization` → `3.4_track_artifacts_created` → `3.5_setup_complete`).
3. If `3.5_setup_complete` → announce complete → HALT.
4. No file → new setup → proceed.

**Subagents:**
- `conductor:project-analyzer` — brownfield project analysis
- `conductor:spec-planner` — spec.md and plan.md generation
- `conductor:spec-reviewer` — interactive spec/plan review

CRITICAL: Validate every tool call. On failure → halt → announce.

---

## 2.0 PHASE 1: PROJECT SETUP

### 2.0 Project Inception

1. **Detect maturity:** Brownfield (`.git`, `package.json`, `go.mod`, etc.) vs Greenfield.
2. **Brownfield:** Dispatch `conductor:project-analyzer`. Parse `---ANALYSIS RESULT---` block.
3. **Greenfield:** Ask "What do you want to build?"
4. Init git if needed. Create `conductor/` directory.

### 2.1 Product Guide

Interactive (up to 5 questions). Write to `conductor/overview/product.md`. Save state: `2.1_product_guide`.

### 2.2 Product Guidelines

Interactive. Write to `conductor/overview/product-guidelines.md`. Save state: `2.2_product_guidelines`.

### 2.3 Tech Stack & Style Guides

**Tech Stack:**
- Brownfield: pre-fill from analyzer results, confirm.
- Greenfield: ask from scratch.
- Write to `conductor/design/tech-stack.md`.

**Style Guides (auto-derive):**

| Language | Guides |
|----------|--------|
| JavaScript | `javascript.md` |
| TypeScript | `typescript.md` + `javascript.md` |
| Python | `python.md` |
| Go | `go.md` |
| C++ | `cpp.md` |
| C# | `csharp.md` |
| Dart | `dart.md` |
| HTML/CSS | `html-css.md` |
| *(any)* | `general.md` (always) |

Confirm with user. Copy from `${CLAUDE_PLUGIN_ROOT}/templates/code-styleguides/` to `conductor/workflow/code-styleguides/`.
Save state: `2.3_tech_stack_styleguides`.

### 2.4 Workflow

1. Copy `${CLAUDE_PLUGIN_ROOT}/templates/template.md` → `conductor/workflow/template.md`
2. Inject dev commands: for each language, append `${CLAUDE_PLUGIN_ROOT}/templates/dev-commands/<lang>.md` into the `## Development Commands` section.
3. Copy `task-workflow.md`, `phase-checkpoint.md`, and `post-loop.md` from templates.
4. **Testing strategy:** Copy `${CLAUDE_PLUGIN_ROOT}/templates/testing/strategy.md` → `conductor/workflow/testing/strategy.md`. Replace `{TEST_ROOT}` with the detected test directory (scan project for `tests/`, `__tests__/`, `test/`; default: `tests/`).
5. Generate `conductor/workflow/index.md` listing all created files.
6. Verify all referenced files exist.
Save state: `2.4_workflow`.

### 2.5 Finalization

1. **CLAUDE.md TOC:** Read `${CLAUDE_PLUGIN_ROOT}/templates/claude-md-toc.md`, append to project's `CLAUDE.md` (create if missing).
2. **Project index:** Read `${CLAUDE_PLUGIN_ROOT}/templates/project-index.md`, write to `conductor/index.md`.
3. **Tracks Registry:** Create `conductor/tracks.md` if missing (empty registry with header `# Tracks Registry`).
4. Save state: `2.5_finalization`.
5. Ask user: "Create an initial track now, or later?" If later → commit Phase 1 → HALT.
6. Summarize Phase 1 actions.

---

## 3.0 INITIAL TRACK GENERATION

### 3.1 Product Requirements (Greenfield only)

Interactive (up to 5 questions).

### 3.2 Propose Track

Analyze context → generate track title → user confirms.

### 3.3 Dispatch Spec-Planner

`Agent` tool, `subagent_type: "conductor:spec-planner"`. Description: `"Generate spec/plan for '<track_desc>'"`.

```
TRACK_DIR={track_dir}
TRACK_DESCRIPTION={desc}
TRACK_TYPE={type}
USER_ANSWERS={answers or N/A}
RELATED_DOCS={paths or N/A}
```

Parse `---SPEC PLAN RESULT---` block. Extract `PLAN_STRUCTURE`. Files are on disk.

### 3.4 Dispatch Spec-Reviewer

`Agent` tool, `subagent_type: "conductor:spec-reviewer"`. Description: `"Review spec/plan for '<desc>'"`.

```
TRACK_DIR={track_dir}
```

Parse `---REVIEW RESULT---` block. If `STATUS: CANCELLED` → halt. If `STRUCTURE_CHANGED: true` → note for init.

### 3.5 Create State Artifacts

1. **Tracks Registry:** Create `conductor/tracks.md` if missing.
2. **Initialize track:**
   ```bash
   track-state init "<track_dir>" \
     --plan-structure '<PLAN_STRUCTURE json>' \
     --track-id <id> \
     --type <type> \
     --description '<desc>'
   ```
3. **Update Tracks Registry:** Append new entry.
4. Save state: `3.5_track_artifacts_created`.

### 3.6 Final Commit

```bash
git add -A && git commit -m "conductor(setup): Add conductor setup files"
```

Announce: `"Setup complete. Run /conductor:implement to begin."`
