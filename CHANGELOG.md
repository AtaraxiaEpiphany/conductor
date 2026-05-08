# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **`spec-reviewer` subagent**: Interactive review of spec.md and plan.md in isolated context. Presents summaries, handles revisions, returns compact result. Keeps full file contents out of the orchestrator context, saving ~200-500 lines of main session context per track creation
- **`track-state init` command**: One-call track initialization — creates `track-state.json` and `index.md` from PLAN_STRUCTURE. Eliminates duplicate JSON generation logic in `new-track` and `setup` SKILLs
- **newTrack commit step**: Added `git commit` after track artifact creation, aligning with `setup`'s behavior
- **spec-planner self-discovery**: When `RELATED_DOCS` is not provided by the orchestrator, spec-planner now scans the project for relevant files itself using Glob/Grep, removing the need for the orchestrator to read business documents during context discovery

### Changed

- **Reference layer inlined**: Replaced `conductor-reference.md` Read call in `new-track` and `setup` SKILLs with inline path resolution rules (~5 lines). Saves one Read call and ~26 lines of context per invocation
- **Context discovery simplified**: `new-track` Section 2.2 now collects file paths only (no content reads). All business document content is loaded by subagents. Estimated saving: 50-200 lines of orchestrator context per track creation
- **Review delegated to subagent**: `new-track` and `setup` now dispatch `spec-reviewer` instead of reading spec.md/plan.md directly. Full file contents never enter the orchestrator context
- **State artifact creation via CLI**: Both `new-track` and `setup` now use `track-state init` instead of manually building `track-state.json` JSON. Removes ~20 lines of duplicate schema definitions across both SKILLs

### Added (Previous)

- **Testing Placement Strategy**: Language-specific test file placement policies, naming conventions, coverage thresholds, and cache management via `templates/testing/strategy.md` ([7563d4a](https://github.com/anthropics/conductor-plugin/commit/7563d4a))
  - `testing/strategy.md` template: test directory conventions for JavaScript, TypeScript, Python, Go, C++, C#, and Dart with naming patterns and coverage thresholds
  - Environment sections in all 7 dev-command templates for cache/artifact directory redirection
  - Testing references in all 9 code-styleguides linking to `testing/strategy.md`
  - `task-executor` Layer 3 updated to load `testing/strategy.md` before Step 3
  - `task-workflow.md` Step 3 enhanced with explicit test directory guidance
  - Setup skill updated to deploy testing strategy with `test_root` detection
  - Testing strategy entry added to workflow index

### Fixed

- **Path Resolution**: Fixed `project-index.md` paths from `./` to `conductor/` for correct CWD resolution; added path resolution notes to `track-index.md` ([7563d4a](https://github.com/anthropics/conductor-plugin/commit/7563d4a))

### Removed

- **Stale V2 Tags**: Removed stale V2 tags from `status` and `revert` skills ([7563d4a](https://github.com/anthropics/conductor-plugin/commit/7563d4a))

---

## [0.1.1] - 2026-05-08

### Added

- **Hooks ↔ Skills ↔ Subagents Integration**: Cross-cutting lifecycle monitoring connecting all three plugin components ([83834b3](https://github.com/anthropics/conductor-plugin/commit/83834b3))
  - `SubagentStart` hook in `hooks.json`: injects role-specific execution reminders (TDD protocol, read-only constraints, result format requirements) into every subagent at dispatch time
  - `SubagentStop` hook in `hooks.json`: async logging of subagent completions to `logs/subagent-lifecycle.log`
  - `TaskCreated` / `TaskCompleted` hooks in `hooks.json`: async logging of task lifecycle events to `logs/task-lifecycle.log`
  - `Stop` hook on `implement` skill: state consistency guard — detects stale `in_progress` tasks after orchestrator finishes and warns the user
  - `PostToolUse` hook on `task-executor` (Bash matcher): detects test command executions, logs results to `logs/test-results.log`, and injects TDD phase context on failure (reminds whether Red phase expects failure or Green phase needs a fix)
  - `Stop` hook on `phase-checker`: logs checkpoint completion events
  - `Stop` hook on `code-reviewer`: logs review completion events
  - 7 new hook scripts: `on-subagent-start`, `on-subagent-stop`, `on-task-event`, `on-test-run`, `on-phase-checkpoint-stop`, `on-review-stop`, `state-consistency-check`

---

## [0.1.0] - 2026-05-08

Initial release of the Conductor plugin — a Spec-Driven Development orchestration system for Claude Code.

### Added

#### Core Architecture

- **SDD Orchestration Engine**: Full Spec-Driven Development pipeline using the orchestrator-subagent pattern. The orchestrator is a thin state machine that routes between specialized subagents, never loading business context itself ([bc63495](https://github.com/anthropics/conductor-plugin/commit/bc63495))
- **Context Isolation Model**: All state mutations handled by the `track-state` CLI script; subagents self-extract ACs/specs from files; step logs write to disk; results piped through `process-result`. Main session context stays minimal ([bc63495](https://github.com/anthropics/conductor-plugin/commit/bc63495))
- **6 CLI Commands (Skills)**:
  - `/conductor:setup` — Project initialization with brownfield/greenfield detection, interactive product definition, tech stack selection, style guide setup, and first track generation ([5366795](https://github.com/anthropics/conductor-plugin/commit/5366795))
  - `/conductor:new-track` — Create new feature/bugfix/chore tracks with spec and plan generation ([5366795](https://github.com/anthropics/conductor-plugin/commit/5366795))
  - `/conductor:implement` — Execute track tasks via subagent dispatch with state machine recovery ([5366795](https://github.com/anthropics/conductor-plugin/commit/5366795))
  - `/conductor:status` — Display project progress overview from track-state.json ([5366795](https://github.com/anthropics/conductor-plugin/commit/5366795))
  - `/conductor:review` — Code review with diff analysis, plan compliance, and style verification ([5366795](https://github.com/anthropics/conductor-plugin/commit/5366795))
  - `/conductor:revert` — Safe git rollback with track-state.json synchronization ([5366795](https://github.com/anthropics/conductor-plugin/commit/5366795))
- **8 Specialized Subagents**:
  - `conductor:task-executor` — TDD workflow (Steps 3-9): writes failing tests, implements minimum code, refactors, verifies coverage, commits with git notes
  - `conductor:explorer` — Read-only codebase investigation for `[Explore]` tasks; produces `exploration.md` as file-bridge for downstream task-executors
  - `conductor:spec-planner` — Generates `spec.md` and `plan.md` from user requirements and project context
  - `conductor:project-analyzer` — Brownfield project detection: tech stack, architecture patterns, project structure
  - `conductor:code-reviewer` — Deep code analysis: diff review, plan compliance, style check, test execution, structured findings
  - `conductor:skip-analyst` — Evaluates whether failed tasks can be safely skipped; analyzes downstream dependencies and impact
  - `conductor:phase-checker` — Phase checkpoint verification: test coverage, automated test execution, manual verification plan, checkpoint commit
  - `conductor:doc-syncer` — Synchronizes project documentation after track completion

#### State Management

- **`track-state` CLI**: Context-isolated state management Python CLI with 11 commands ([46a97b2](https://github.com/anthropics/conductor-plugin/commit/46a97b2))
  - `next` — Find next dispatchable task (prioritizes in_progress > pending)
  - `recover` — Get recovery context for interrupted tasks
  - `lock` — Set task to in_progress with global state lock enforcement
  - `complete` — Mark task completed with commit SHA; detect parent completion
  - `fail` — Mark task failed with retry count increment and summary
  - `skip` — Mark task skipped with reason
  - `defer` — Mark task deferred with reason
  - `block` — Mark task blocked with reason
  - `sync-plan` — Re-project all markers from track-state.json to plan.md
  - `phase-done` — Check if all tasks in a phase reached terminal state
  - `finalize` — Set indices to -1, compute track-level status
  - `process-result` — Read `.conductor/result.json`, update state + plan + issues.md in one call
  - `shas` — List first and last commit SHAs for a track
  - `deferred-report` — List all deferred tasks for verification

#### Execution Model

- **TDD Enforcement**: Mandatory Red-Green-Refactor cycle — no implementation code without a failing test. Exempted task types: `[Docs]`, `[Config]`, `[Chore]`, `[Explore]`, `[Manual]` ([bc63495](https://github.com/anthropics/conductor-plugin/commit/bc63495))
- **Execution Firewall**: 6 mandatory pre-action checks (F1-F6) with Critical and Warning severity levels ([bc63495](https://github.com/anthropics/conductor-plugin/commit/bc63495))
- **Global State Lock (F1)**: Only one `[~]` task allowed globally — eliminates concurrent state conflicts
- **Task Type Tags**: `[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, `[Manual]` tags modify workflow behavior and TDD gate requirements
- **Continuous and Interactive Execution Modes**: Continuous mode auto-defers `[Manual]` tasks and auto-proceeds through checkpoints; interactive mode pauses for user confirmation ([0b154d9](https://github.com/anthropics/conductor-plugin/commit/0b154d9))
- **Deferred Task Lifecycle**: `[Manual]` tasks auto-defer in continuous mode, tracked for later human verification via `deferred-report` command ([0b154d9](https://github.com/anthropics/conductor-plugin/commit/0b154d9))
- **Subtask Support**: Hierarchical task structure with parent-child relationships; subtasks inherit parent's AC context and task type tags ([f988033](https://github.com/anthropics/conductor-plugin/commit/f988033))
- **Retry and Skip Analysis**: Failed tasks retry up to max count; exhausted retries trigger `skip-analyst` for impact assessment ([bc63495](https://github.com/anthropics/conductor-plugin/commit/bc63495))

#### Review and Documentation

- **Auto-Review Flow**: Automatically dispatches `code-reviewer` subagent after track completion, presenting findings with severity-based decisions (Critical/High → CHANGES REQUESTED, Medium/Low → APPROVE WITH COMMENTS) ([e14c343](https://github.com/anthropics/conductor-plugin/commit/e14c343))
- **Phase Checkpoint Protocol**: Mandatory verification at phase boundaries — test coverage check, missing test creation, automated test execution, manual verification plan, checkpoint commit with git notes ([bc63495](https://github.com/anthropics/conductor-plugin/commit/bc63495))
- **Doc-Syncer**: Post-completion documentation synchronization covering product definition, tech stack, product guidelines, system architecture, database schema, API specs, UX/UI design spec, and glossary ([3d1c0a6](https://github.com/anthropics/conductor-plugin/commit/3d1c0a6))
- **Global/Scoped Document Classification**: Per-agent doc filtering — Global docs (product, tech stack) loaded by all agents; Scoped docs (architecture, database, API, UX) loaded only when relevant files change ([1c6f670](https://github.com/anthropics/conductor-plugin/commit/1c6f670))
- **Spec-Test Traceability**: AC-to-TC mapping with `<!-- AC-n, TC-n.n -->` annotations in plan.md tasks, enabling task-executors to self-extract precise acceptance criteria ([f988033](https://github.com/anthropics/conductor-plugin/commit/f988033))

#### Templates and Style Guides

- **Shared Workflow Templates**: `template.md`, `task-workflow.md`, `phase-checkpoint.md`, `workflow/index.md` auto-copied during setup ([e14c343](https://github.com/anthropics/conductor-plugin/commit/e14c343))
- **Language-Specific Style Guides**: 9 built-in guides — `general.md`, `javascript.md`, `typescript.md`, `python.md`, `go.md`, `cpp.md`, `csharp.md`, `dart.md`, `html-css.md` ([570b658](https://github.com/anthropics/conductor-plugin/commit/570b658))
- **Development Command Templates**: Per-language dev command templates for build, test, lint, and format commands ([570b658](https://github.com/anthropics/conductor-plugin/commit/570b658))

#### Hooks and Context Injection

- **SessionStart Hook**: Automatically injects `conductor-core.md` (Execution Firewall, Anti-Patterns, Task State Model) into every session via `scripts/session-start` ([e8f8d14](https://github.com/anthropics/conductor-plugin/commit/e8f8d14))
- **Layered Context Injection**: Split `conductor.md` into three layers — `conductor-core.md` (session start), `conductor-orchestration.md` (implement skill), `conductor-reference.md` (setup/newTrack skills) — to minimize per-session context footprint ([6753ed7](https://github.com/anthropics/conductor-plugin/commit/6753ed7))

#### Project Artifacts

- **Project Index**: `conductor/index.md` generated during setup listing all documentation paths and categories ([e14c343](https://github.com/anthropics/conductor-plugin/commit/e14c343))
- **Track Index**: Per-track `index.md` with context links to spec, plan, and project-level docs ([e14c343](https://github.com/anthropics/conductor-plugin/commit/e14c343))
- **CLAUDE.md TOC**: Project CLAUDE.md extended with Conductor table of contents for file resolution ([e14c343](https://github.com/anthropics/conductor-plugin/commit/e14c343))
- **Setup State Checkpointing**: `conductor/setup_state.json` tracks setup progress, enabling resume from last successful step ([e14c343](https://github.com/anthropics/conductor-plugin/commit/e14c343))
- **References Directory**: Claude Code documentation (hooks, skills, subagents, plugins) for offline reference ([3b7630f](https://github.com/anthropics/conductor-plugin/commit/3b7630f))

### Changed

- **Layered Conductor Injection**: Split monolithic `conductor.md` into `conductor-core.md` + `conductor-orchestration.md` + `conductor-reference.md`. Core layer injected at session start; orchestration and reference layers loaded on-demand by individual skills. Reduced per-session context footprint by ~40% ([6753ed7](https://github.com/anthropics/conductor-plugin/commit/6753ed7))
- **Compressed Skill Narratives**: Reduced all skill definitions from verbose instructions to compact dispatch protocols. `implement` skill reduced from ~500 lines to ~200 lines by extracting subagent dispatch details into subagent definitions. Total savings: ~526 lines across 5 skills ([e14c343](https://github.com/anthropics/conductor-plugin/commit/e14c343))
- **Optional Skill Arguments**: Made all skill arguments optional with auto-detection logic — skills now auto-detect track name from registry state and fall back to `AskUserQuestion` only when ambiguous ([fb2863d](https://github.com/anthropics/conductor-plugin/commit/fb2863d))
- **Spec-Planner Extraction**: Track generation logic extracted from `setup` skill into dedicated `conductor:spec-planner` subagent. Both `setup` and `newTrack` now dispatch the same subagent, eliminating duplication ([c3a8324](https://github.com/anthropics/conductor-plugin/commit/c3a8324))
- **Unified Agent Naming**: Added `conductor:` prefix to all subagent names for consistent namespacing across the plugin ecosystem ([e0a3e28](https://github.com/anthropics/conductor-plugin/commit/e0a3e28))
- **Restructured Plugin Layout**: Modular and portable plugin structure — moved agents from `agents/subagents/` to `agents/`, moved skills to flat namespace, added `templates/` directory with dev-commands and code-styleguides ([570b658](https://github.com/anthropics/conductor-plugin/commit/570b658))
- **SHA Format Standardization**: Fixed SHA placement to always append at end of task line, after any HTML comments. Correct: `- [x] Task description [a1b2c3d]`. Wrong: `- [x] [a1b2c3d] Task description` ([f988033](https://github.com/anthropics/conductor-plugin/commit/f988033))

### Fixed

- **Duplicate SHA Markers**: `sync-plan` now strips all trailing SHAs before re-projecting, preventing duplicate markers when called multiple times ([6e74be3](https://github.com/anthropics/conductor-plugin/commit/6e74be3))
- **Stale Workflow References**: Fixed stale `workflow.md` references across skills and enhanced spec-to-test traceability with AC/TC annotations ([55d69eb](https://github.com/anthropics/conductor-plugin/commit/55d69eb))

### Removed

- **Old Plugin Config**: Removed plugin config files, keeping only the references directory for documentation ([214335d](https://github.com/anthropics/conductor-plugin/commit/214335d))
- **Conductor- Prefix**: Removed redundant `conductor-` prefix from skill directory names and agent filenames for cleaner filesystem layout. Namespaced `conductor:` prefix in subagent frontmatter remains ([2041025](https://github.com/anthropics/conductor-plugin/commit/2041025), [7777fd0](https://github.com/anthropics/conductor-plugin/commit/7777fd0), [ce756d5](https://github.com/anthropics/conductor-plugin/commit/ce756d5))

[Unreleased]: https://github.com/anthropics/conductor-plugin/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/anthropics/conductor-plugin/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/anthropics/conductor-plugin/releases/tag/v0.1.0
