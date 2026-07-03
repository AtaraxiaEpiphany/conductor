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
- Product: `conductor/product/product.md`
- Tech Stack: `conductor/design/tech-stack.md`
- Tracks Registry: `conductor/tracks.md`
- Workflow Index: `conductor/workflow/index.md`

## 1.0 RESUME CHECK

1. Read `conductor/setup_state.json` if exists.
2. Resume from `last_successful_step` (keys: `2.1_product_guide` → `2.2_product_guidelines` → `2.3_tech_stack_styleguides` → `2.4_workflow` → `2.5_finalization` → `3.6_setup_complete`). Resume at the **first section whose key is NOT yet saved** — i.e. re-run the step that follows `last_successful_step`. Do not treat a mid-chain key (e.g. `2.5_finalization`) as complete; only `3.6_setup_complete` is terminal.
3. If `3.6_setup_complete` → announce complete → HALT.
4. No file → new setup → proceed.

**Subagents:**
- `conductor:project-analyzer` — brownfield project analysis (§2.0)
- `/conductor:new-track` — owns the entire initial-track lifecycle (§3.2): derive-name, spec-planner, spec-reviewer, `init-from-plan`, registry-update, commit, announce, auto-start. It also resumes any partial track via its own §0.5 marker.

CRITICAL: Validate every tool call. On failure → halt → announce.

---

## 2.0 PHASE 1: PROJECT SETUP

### 2.0 Project Inception

1. **Detect maturity:** Brownfield (`.git`, `package.json`, `go.mod`, etc.) vs Greenfield.
2. **Brownfield:** **Resumability guard** — if `conductor/.conductor/analysis.json` already exists (a prior setup pass already ran the analyzer), **Read it to recover the detection fields** (`languages`, `frameworks`, etc.) and skip the dispatch: the analyzer's one-pass detection is durable and must not be re-run on resume. Otherwise dispatch `conductor:project-analyzer`, prompt:

   ```
   PROJECT_DIR={project root}
   ```

   Parse `---ANALYSIS RESULT---` block. **Persist the full detection tree** to `conductor/.conductor/analysis.json` (create `.conductor/` if absent) — this is the durable record for later consumers (e.g. corpus-writer seeding, future `/conductor:wiki` queries about the stack), so the analyzer's one-pass detection is not lost. Subsequent steps (§2.3 Tech Stack pre-fill, §3.2 description) operate on the live fields (`languages`, `frameworks`) — recovered from `analysis.json` on resume, or from the result block on first run.
3. **Greenfield:** Ask "What do you want to build?"
4. Init git if needed. Create `conductor/` directory.

### 2.1 Product Guide

Interactive (up to 5 questions). Write to `conductor/product/product.md`. Save state: `2.1_product_guide`.

### 2.2 Product Guidelines

Interactive. Write to `conductor/product/product-guidelines.md`. Save state: `2.2_product_guidelines`.

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
7. **Wiki Overview:** Read `${CLAUDE_PLUGIN_ROOT}/templates/wiki-overview.md`, write to `conductor/overview.md`. Replace `{TIMESTAMP}` with current ISO-8601 timestamp.
8. **Wiki Purpose:** Read `${CLAUDE_PLUGIN_ROOT}/templates/wiki-purpose.md`, write to `conductor/purpose.md`. Replace `{TIMESTAMP}`. Seed the **Goals** section from the product guide (§2.1) — the other sections (Key Questions, Thesis, Decisions) start as placeholders and are co-evolved by the user (`/conductor:wiki purpose`) and wiki-synthesizer (Phase 2 of the doc-sync split) over time. This is the wiki's directional intent — *why* the project exists, distinct from the structural overview.
9. **Wiki Log:** Read `${CLAUDE_PLUGIN_ROOT}/templates/wiki-log.md`, write to `conductor/log.md`.
Save state: `2.4_workflow`.

### 2.5 Finalization

1. **CLAUDE.md TOC (idempotent):** Read `${CLAUDE_PLUGIN_ROOT}/templates/claude-md-toc.md`. If the project's `CLAUDE.md` already contains the `<!-- conductor:toc begin -->` sentinel, skip the append — a setup re-run must never duplicate the block. Otherwise append the template (it carries the `<!-- conductor:toc begin -->` … `<!-- conductor:toc end -->` sentinels bracketing the block); create `CLAUDE.md` if missing.
2. **Project index:** Read `${CLAUDE_PLUGIN_ROOT}/templates/project-index.md`, write to `conductor/index.md`.
3. **Tracks Registry:** Create `conductor/tracks.md` if missing (empty registry with header `# Tracks Registry`).
4. Save state: `2.5_finalization`.
5. Ask user: "Create an initial track now, or later?" If later → commit Phase 1 → HALT.
6. Summarize Phase 1 actions.

---

## 3.0 INITIAL TRACK (delegates to /conductor:new-track)

setup no longer creates the track itself — the entire track lifecycle lives in
`/conductor:new-track`, which owns derive-name, spec-planner, spec-reviewer,
`init-from-plan` (mechanical, from plan.md — no large `--plan-structure` arg),
registry-update, the track commit, announce, and auto-start. It also resumes any
partial track via its §0.5 marker (issue #3). setup's only unique responsibilities
here are the greenfield product requirements (§3.1), delegating (§3.2), and its
own final commit (§3.6).

Re-entering §3.0 after an interruption lets new-track resume the partial track
automatically — **do not** re-derive a track id, re-init state, or pass a
`--plan-structure` from setup.

### 3.1 Product Requirements (Greenfield only)

Interactive (up to 5 questions).

### 3.2 Delegate to /conductor:new-track

1. If the user chose "later" at §2.5 step 5 → Phase 1 is already committed → HALT.
2. Gather the track description: greenfield → synthesize from the §3.1 answers;
   brownfield → one short description (the analyzer's top recommendation). Pass
   the greenfield product answers as context.
3. Invoke `/conductor:new-track <description>`. new-track does the rest —
   including resuming a partial track if one exists at the derived `track_dir`,
   and validating any pre-existing `plan.md` (its §2.3 guard).
4. On return, fall through to §3.6. (The old §3.3 spec-planner / §3.4
   spec-reviewer / §3.5 `init --plan-structure` steps are now owned by new-track
   — hence the numbering gap. This also retires the large CLI arg of issue #6 and
   the parser-bypass of issue #4a.)

### 3.6 Final Commit

1. **Save the terminal resume key BEFORE committing** (issue #1: the old order
   committed first, then saved `setup_state.json`, leaving it dirty on the
   working tree). Saving first means the scoped stage below includes the
   completed marker:
   Save state: `3.6_setup_complete`.
2. Commit setup artifacts — **scoped, never `git add -A`**. A brownfield project
   may carry unrelated WIP that must not be swept into the scaffold commit, so
   stage only what setup owns: the `conductor/` tree (incl. `setup_state.json`
   and `.conductor/analysis.json`) plus the `CLAUDE.md` TOC append. The
   `git diff --cached --quiet ||` guard makes the commit a no-op **only** when
   those artifacts are already committed (a defensive re-run) — it does NOT skip
   this step:
   ```bash
   git add conductor/ CLAUDE.md
   git diff --cached --quiet || git commit -m "chore(conductor): Scaffold conductor setup"
   ```
3. Announce: `"Setup complete. Run /conductor:implement to begin."`

