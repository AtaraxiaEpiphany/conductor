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
- `conductor:strategy-writer` — optional project-specific testing strategy (§2.4 step 3), when the user picks generation over the filtered template
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

   Parse `---ANALYSIS RESULT---` block and **persist the full detection tree to `conductor/.conductor/analysis.json` via the tolerant helper** (a weak model's block may carry trailing commas, code fences, or smart quotes that strict `json.loads` rejects — the helper repairs them so setup never crashes or persists garbage):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/persist-analysis.py" \
     --output conductor/.conductor/analysis.json --result "<paste the full project-analyzer output>"
   ```
   Exit 0 + `OK:` line → persisted; proceed. Exit 1 + `HALT:` → the block was unparseable even after tolerant repair: **re-dispatch `conductor:project-analyzer` ONCE** with the tightened prompt *"emit STRICT raw JSON only — no ``` fences, no trailing commas, ASCII double-quotes only"*, then retry the helper. If it still fails → halt → announce the unreadable output (do NOT persist garbage). This is the durable record for later consumers (e.g. corpus-writer seeding, future `/conductor:wiki` queries about the stack), so the analyzer's one-pass detection is not lost. Subsequent steps (§2.3 Tech Stack pre-fill, §3.2 description) operate on the live fields (`languages`, `frameworks`) — recovered from `analysis.json` on resume, or from the result block on first run.
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
| Java | `java.md` |
| C++ | `cpp.md` |
| C# | `csharp.md` |
| Dart | `dart.md` |
| HTML/CSS | `html-css.md` |
| *(any)* | `general.md` (always) |

Confirm the set with the user, then copy the selected guides into place with one Bash call. These are pure copies (no placeholders), so `cp` keeps the guide bodies out of the orchestrator context:
```bash
mkdir -p conductor/workflow/code-styleguides
cp "${CLAUDE_PLUGIN_ROOT}/templates/code-styleguides/"{general,<detected-langs>}.md conductor/workflow/code-styleguides/
```
`general.md` is always included; `<detected-langs>` is the brace-expansion list of detected languages' basenames (e.g. `python,typescript`).
Save state: `2.3_tech_stack_styleguides`.

### 2.4 Workflow

Copy the workflow templates into `conductor/workflow/` with Bash (`cp`/`sed`) rather than Read+Write. These are pure file copies — `phase-checkpoint.md` and `post-loop.md` carry runtime tokens (`{TRACK_DIR}`/`{PHASE_INDEX}`/`{CLAUDE_PLUGIN_ROOT}`) that agents substitute later, so they pass through **verbatim and must NOT be sed'd**. Routing them through `cp` keeps their contents out of the orchestrator context.

1. **Core workflow files** (pure copies):
   ```bash
   mkdir -p conductor/workflow/testing
   cp "${CLAUDE_PLUGIN_ROOT}/templates/"{task-workflow,phase-checkpoint,post-loop}.md conductor/workflow/
   ```

2. **Dev commands:** copy the detected languages' dev-command files into `conductor/workflow/dev-commands/` — the path task-runner / build-runner / refactorer / apply-fixes / phase-checker read at runtime (pure Bash keeps the lang files out of context). No `general.md` (unlike styleguides) — detected languages only:
   ```bash
   mkdir -p conductor/workflow/dev-commands
   cp "${CLAUDE_PLUGIN_ROOT}/templates/dev-commands/"{<lang1>,<lang2>}.md conductor/workflow/dev-commands/
   ```

3. **Testing strategy:** `AskUserQuestion` — **"How should I create the testing strategy?"** with options:
   - **"Use the filtered template (Recommended)"** (default) → run the scaffold script below. Language-filtered, deterministic, contract-correct.
   - **"Generate a project-specific strategy"** → dispatch `conductor:strategy-writer` (inspects the project's real test layout/frameworks, asks follow-up questions, writes the file). Best for projects whose actual conventions diverge from the generic template.

   **Default branch (filtered template):** run the scaffold script — it resolves the test root (`conductor/.conductor/analysis.json` → `structure.test_dirs[0]`; greenfield → `tests`), filters the per-language rows/examples/cache-rules to the detected languages (`analysis.json` → `languages[].name`; no detection → keeps all), writes `conductor/workflow/testing/strategy.md` byte-exact modulo the token + filter, and self-verifies (non-zero exit + remediation hint on any failure). Promoted to code so the `{TEST_ROOT}` substitution and language filter can't be skipped or drifted:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scaffold-strategy.py"
   ```
   Override the detected root with `--test-root <path>` if the scan missed it, or the language set with `--languages python,typescript` to force a filter manually.

   **Generate branch (project-specific):** resolve the test root the same way the script would (`analysis.json` → `structure.test_dirs[0]`, else `tests`), then:
   ```
   Dispatch `conductor:strategy-writer`, prompt:
   PROJECT_DIR={project root}
   TEST_ROOT={resolved root}
   ```
   The agent inspects the live project, asks the user questions interactively, writes `conductor/workflow/testing/strategy.md`, and self-verifies via `scripts/verify-strategy.py` (the deterministic invariant backstop for the generated doc). Parse the `---STRATEGY RESULT---` block; on `STATUS: FAILURE` → halt → announce.

4. **Workflow index:** generate `conductor/workflow/index.md` listing the created files (task-workflow, phase-checkpoint, post-loop, code-styleguides/, dev-commands/, testing/strategy.md — per-project content, not a template copy). Include one line documenting the **task-type override hook**: a project MAY drop `conductor/workflow/task-type-profiles.json` here to add project-specific task-type tags or override a built-in tag's semantics — it merges over the plugin baseline (project wins conflicts; absent = plugin defaults). Do **not** auto-create the file — it is opt-in by presence; a project creates it only when it actually wants an override (auto-creating a full-baseline copy would shadow future plugin-shipped tags).

5. **Verify** every referenced file exists before continuing.

6. **Wiki overview/purpose/log** — copy each to its renamed target, then stamp the current ISO-8601 timestamp over `{TIMESTAMP}`:
   ```bash
   ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
   cp "${CLAUDE_PLUGIN_ROOT}/templates/wiki-overview.md" conductor/overview.md
   cp "${CLAUDE_PLUGIN_ROOT}/templates/wiki-purpose.md"  conductor/purpose.md
   cp "${CLAUDE_PLUGIN_ROOT}/templates/wiki-log.md"      conductor/log.md
   sed -i "s/{TIMESTAMP}/$ts/g" conductor/overview.md conductor/purpose.md conductor/log.md
   ```

7. **Seed `conductor/purpose.md` Goals** — the one content edit in this phase: `Edit` `conductor/purpose.md` to replace the Goals placeholder with the goals gathered in §2.1. The other sections start as self-documenting placeholders (the template annotates who fills each and when): **Key Questions** and **Out of Scope** are seeded from the first track's Brief by `/conductor:new-track` §2.2b (additive, intersection-only); **Evolving Thesis** and **Active Decisions** are deliberately empty until wiki-synthesizer Phase 2 harvests them from completed tracks. This file is the wiki's directional intent — *why* the project exists, distinct from the structural overview.
Save state: `2.4_workflow`.

### 2.5 Finalization

1. **CLAUDE.md TOC (idempotent, sentinel-guarded append — NOT a `cp`, which would clobber a brownfield `CLAUDE.md`):** the template carries the `<!-- conductor:toc begin -->` … `<!-- conductor:toc end -->` sentinels bracketing its block. If the project's `CLAUDE.md` already contains the `<!-- conductor:toc begin -->` sentinel, skip the append — a setup re-run must never duplicate the block; otherwise append the template (create `CLAUDE.md` if missing). The template has no placeholders, so a single Bash guard keeps it out of context:
   ```bash
   grep -q '<!-- conductor:toc begin -->' CLAUDE.md 2>/dev/null \
     || cat "${CLAUDE_PLUGIN_ROOT}/templates/claude-md-toc.md" >> CLAUDE.md
   ```

2. **`.gitignore` conductor block (idempotent, sentinel-guarded append — NOT a clobber):** `/conductor:wiki-doctor` lint/diff write transient scratch (`wiki-lint-findings-*.json`, `wiki-diff-findings-*.json`, `wiki-diff-report.md`) to the **project-root** `.conductor/` — distinct from the per-track `.conductor/` that track commits own. That scratch is never `git add`-ed, so without this rule it shows as untracked noise in every `git status`. The rule is **root-anchored** (`/.conductor/`) so it does NOT match the committed per-track `conductor/tracks/*/.conductor/`. If `.gitignore` already carries the `# conductor:gitignore begin` sentinel → skip the append (a re-run must never duplicate the block); otherwise append (create `.gitignore` if missing). The template has no placeholders, so a single Bash guard keeps it out of context:
   ```bash
   grep -q '# conductor:gitignore begin' .gitignore 2>/dev/null \
     || cat "${CLAUDE_PLUGIN_ROOT}/templates/conductor-gitignore.md" >> .gitignore
   ```

3. **Project index** (pure copy, no placeholders):
   ```bash
   cp "${CLAUDE_PLUGIN_ROOT}/templates/project-index.md" conductor/index.md
   ```
   The template's **Status** column classifies every row `seeded` (setup creates it — exists now), `auto` (a skill creates it later), or `on-demand` (user/agent creates it when needed). This is why some referenced paths don't exist after setup — they are intentional slots, not broken links. `scripts/check-index-maps.py` (run by the test suite) enforces that every `seeded` row is actually written by setup, so the two can't drift.

4. **Wiki cold-start (brownfield only — optional, never blocks).** The scaffolded wiki (§2.4 step 6) is placeholder text until a track runs or sources are filed — which is why a fresh project's wiki reads as empty. If this is brownfield (analyzer ran / `conductor/.conductor/analysis.json` exists) AND the project has pre-existing docs worth filing, offer to populate the wiki from them now so it compounds from day one. Detect candidates with `Glob`: `README.md`, `docs/**/*.md`, and root `*.md` — excluding anything under `conductor/` (never re-ingest the wiki into itself). If candidates exist → `AskUserQuestion`: "Populate the wiki from your existing docs now via `/wiki build`?" → **Yes** → invoke `/conductor:wiki build <path>` (`docs/` if a docs tree exists, else the project root, else `README.md`). **No, or no candidates** → skip. `/wiki build` is idempotent, so a setup re-run that re-offers it is a safe no-op; the wiki can also be built any time later via `/conductor:wiki build`.

5. **Tracks Registry:** create `conductor/tracks.md` if missing (header `# Tracks Registry`):
   ```bash
   [ -f conductor/tracks.md ] || printf '# Tracks Registry\n' > conductor/tracks.md
   ```

6. Save state: `2.5_finalization`.
7. Ask user: "Create an initial track now, or later?" If later → **commit Phase 1 scoped (never `git add -A`)**, then HALT. The staged scope is the same setup-owned tree §3.6 commits — `conductor/` (incl. `setup_state.json` and `.conductor/analysis.json`), the `CLAUDE.md` TOC append, the `.gitignore` conductor block — staged **after** `2.5_finalization` is saved so the marker is included:
   ```bash
   git add conductor/ CLAUDE.md .gitignore
   git diff --cached --quiet || git commit -m "chore(conductor): Scaffold conductor setup (no initial track)"
   ```
   On re-entry (user later picks "create track now"), §3.0 → `/conductor:new-track` owns the track commit; setup's §3.6 then re-runs this same scoped commit idempotently (the `--quiet` guard makes it a no-op if already committed). A defensive re-run must never duplicate the commit and must never leave `setup_state.json` uncommitted.
8. Summarize Phase 1 actions.

---

## 3.0 INITIAL TRACK (delegates to /conductor:new-track)

setup delegates the entire track lifecycle to `/conductor:new-track`, which owns derive-name, spec-planner, spec-reviewer, `init-from-plan`, registry-update, the track commit, announce, and auto-start (resuming any partial track via its own §0.5 marker). setup's only unique responsibilities here are the greenfield product requirements (§3.1), delegating (§3.2), and its own final commit (§3.6).  On re-entry after an interruption, **do not** re-derive a track id, re-init state, or pass a `--plan-structure` — let new-track resume the partial track.

### 3.1 Product Requirements (Greenfield only)

Interactive (up to 5 questions).

### 3.2 Delegate to /conductor:new-track

1. If the user chose "later" at §2.5 step 7 → Phase 1 is already committed → HALT.
2. Gather the track description: greenfield → synthesize from the §3.1 answers; brownfield → one short description (the analyzer's top recommendation). Pass the greenfield product answers as context.
3. Invoke `/conductor:new-track <description>`. new-track does the rest — including resuming a partial track if one exists at the derived `track_dir`, and validating any pre-existing `plan.md` (its §2.3 guard).
4. On return, fall through to §3.6.

### 3.6 Final Commit

1. **Save the terminal resume key BEFORE committing.** Saving first means the
   scoped stage below includes the completed marker:
   Save state: `3.6_setup_complete`.
2. Commit setup artifacts — **scoped, never `git add -A`**. A brownfield project may carry unrelated WIP that must not be swept into the scaffold commit, so stage only what setup owns: the `conductor/` tree (incl. `setup_state.json` and `.conductor/analysis.json`), the `CLAUDE.md` TOC append, and the `.gitignore` conductor block. The `git diff --cached --quiet ||` guard makes the commit a no-op **only** when those artifacts are already committed (a defensive re-run) — it does NOT skip this step:
   ```bash
   git add conductor/ CLAUDE.md .gitignore
   git diff --cached --quiet || git commit -m "chore(conductor): Scaffold conductor setup"
   ```
3. Announce: `"Setup complete. Run /conductor:implement to begin."`