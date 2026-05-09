# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **handoff.md system** ([a6bcbac](https://github.com/anthropics/conductor-plugin/commit/a6bcbac))
  - Replaced issues.md with indexed handoff system for better context management
  - `track-state get-handoff`, `sync-handoff`, `append-handoff` commands
  - Task-level isolation with support for exploration notes, technical decisions, and risk tracking
- **SDD Layer 0 context loading** ([ed1f7b1](https://github.com/anthropics/conductor-plugin/commit/ed1f7b1))
  - task-executor reads `exploration.md` before task details ("map before manual" principle)
  - `Out-of-Scope` section in spec.md with boundary enforcement
  - explorer contributes `Out-of-Scope Notes` to exploration.md
- **`spec-reviewer` subagent** ([521f56e](https://github.com/anthropics/conductor-plugin/commit/521f56e))
  - Interactive review in isolated context, keeps full files out of orchestrator
  - Saves ~200-500 lines of main session context per track creation
- **`track-state init` command** ([521f56e](https://github.com/anthropics/conductor-plugin/commit/521f56e))
  - One-call track initialization from PLAN_STRUCTURE
  - Eliminates duplicate JSON generation in `new-track` and `setup`
- **`track-state validate --fix` command** ([d35d1d6](https://github.com/anthropics/conductor-plugin/commit/d35d1d6))
  - Auto-repair flag for state inconsistencies
  - Semantic checks for state consistency and plan cross-checks
- **`track-state start` command** ([580d729](https://github.com/anthropics/conductor-plugin/commit/580d729))
  - Transitions track from `new` to `in_progress`
- **`track-state registry-update` command** ([580d729](https://github.com/anthropics/conductor-plugin/commit/580d729))
  - Syncs track status from track-state.json to tracks.md
  - Supports section-based and checkbox formats
- **Testing Placement Strategy** ([7563d4a](https://github.com/anthropics/conductor-plugin/commit/7563d4a))
  - Language-specific test file placement policies and naming conventions
  - `templates/testing/strategy.md` with coverage thresholds
- **JSON Schema for track-state.json** ([580d729](https://github.com/anthropics/conductor-plugin/commit/580d729))
  - Formal schema at `schemas/track-state.schema.json`
- **Harness Engineering reference** ([ed1f7b1](https://github.com/anthropics/conductor-plugin/commit/ed1f7b1))
  - Added `references/Harness-engineering.md` with best practices from Anthropic, OpenAI, Martin Fowler

### Changed

- **Git notes audit trail moved from agent to CLI** ([efc2b14](https://github.com/anthropics/conductor-plugin/commit/efc2b14))
  - `track-state process-result` now writes human-readable git notes after consuming result.json
  - task-executor Step 9 removed — agent no longer writes git notes, reducing context pressure
  - `result.json` gains `coverage_pct` and `coverage_tool` fields for audit completeness
  - `track-state recover` adds best-effort git notes recovery: full note if result.json exists, basic note from git + track-state.json otherwise
  - result.json lifecycle: created by agent, consumed by process-result, deleted after processing
- **explorer file-bridge structure** ([ed1f7b1](https://github.com/anthropics/conductor-plugin/commit/ed1f7b1))
  - Enhanced with `Out-of-Scope Notes`, `Related Docs`, detailed `Architecture` section
- **Reference layer inlined** ([521f56e](https://github.com/anthropics/conductor-plugin/commit/521f56e))
  - Replaced `conductor-reference.md` Read call with inline path resolution
- **Context discovery simplified** ([521f56e](https://github.com/anthropics/conductor-plugin/commit/521f56e))
  - `new-track` collects file paths only, no content reads
- **State artifact creation via CLI** ([521f56e](https://github.com/anthropics/conductor-plugin/commit/521f56e))
  - Both `new-track` and `setup` use `track-state init`
- **Layered Conductor Injection** ([6753ed7](https://github.com/anthropics/conductor-plugin/commit/6753ed7))
  - Split into `conductor-core.md` + `conductor-orchestration.md` + `conductor-reference.md`
  - Reduced per-session context footprint by ~40%
- **Compressed Skill Narratives** ([e14c343](https://github.com/anthropics/conductor-plugin/commit/e14c343))
  - Reduced skill definitions by ~526 lines across 5 skills
- **SHA Format Standardization** ([f988033](https://github.com/anthropics/conductor-plugin/commit/f988033))
  - SHA always appended at end of task line, after HTML comments

### Fixed

- **track-state command not found** ([74542da](https://github.com/anthropics/conductor-plugin/commit/74542da))
  - Added `bin/track-state` wrapper so bare command resolves via plugin PATH
  - Normalized all invocations to bare `track-state` across skills and agents
- **track-state invocation** ([be83cdd](https://github.com/anthropics/conductor-plugin/commit/be83cdd))
  - Changed `bash` to `python3` for Python script
- **track-state sync-plan marker cleanup** ([afdf535](https://github.com/anthropics/conductor-plugin/commit/afdf535))
  - Fixed regex for duplicate trailing markers
  - Handles comma-separated SHA format
- **track-state parent→child status propagation** ([dc25272](https://github.com/anthropics/conductor-plugin/commit/dc25272))
  - Subtasks inherit parent status on direct completion
- **SessionStart hook context injection** ([aded7ff](https://github.com/anthropics/conductor-plugin/commit/aded7ff))
  - Changed to `hookSpecificOutput.additionalContext` format
- **SUBTASK dispatch pipeline** ([afdf535](https://github.com/anthropics/conductor-plugin/commit/afdf535))
  - Fixed subtask index passing to prevent auto-completing siblings
- **Deferred marker regex** ([afdf535](https://github.com/anthropics/conductor-plugin/commit/afdf535))
  - Now matches `d` (deferred) marker in sync operations
- **Parent auto-completion check** ([afdf535](https://github.com/anthropics/conductor-plugin/commit/afdf535))
  - Uses `TERMINAL_STATUSES` constant for correctness
- **Manual task auto-defer** ([580d729](https://github.com/anthropics/conductor-plugin/commit/580d729))
  - `[Manual]` tasks now always auto-defer regardless of execution mode
- **Track registry sync** ([580d729](https://github.com/anthropics/conductor-plugin/commit/580d729))
  - tracks.md stays in sync with track-state.json
- **Path Resolution** ([7563d4a](https://github.com/anthropics/conductor-plugin/commit/7563d4a))
  - Fixed `project-index.md` paths from `./` to `conductor/`
- **Duplicate SHA Markers** ([6e74be3](https://github.com/anthropics/conductor-plugin/commit/6e74be3))
  - `sync-plan` strips trailing SHAs before re-projecting
- **Stale Workflow References** ([55d69eb](https://github.com/anthropics/conductor-plugin/commit/55d69eb))
  - Fixed references across skills

### Removed

- **Enrichment pipeline** ([74542da](https://github.com/anthropics/conductor-plugin/commit/74542da))
  - Removed `on-task-executor-stop` hook, `enrich-git-notes` script, and Stop hook from task-executor
  - The two-phase "marker → async enrichment" pattern was fragile: result.json is never committed so git diff cannot discover it, and the information is redundant since task-executor already has all context at Step 9
- **Stale V2 Tags** ([7563d4a](https://github.com/anthropics/conductor-plugin/commit/7563d4a))
  - Removed from `status` and `revert` skills
- **Old Plugin Config** ([214335d](https://github.com/anthropics/conductor-plugin/commit/214335d))
  - Removed plugin config files
- **Conductor- Prefix** ([2041025](https://github.com/anthropics/conductor-plugin/commit/2041025))
  - Removed from directory names and filenames

---

## [0.1.1] - 2026-05-08

### Added

- **Hooks ↔ Skills ↔ Subagents Integration** ([83834b3](https://github.com/anthropics/conductor-plugin/commit/83834b3))
  - `SubagentStart` hook: injects role-specific execution reminders
  - `SubagentStop` hook: async logging to `logs/subagent-lifecycle.log`
  - `TaskCreated` / `TaskCompleted` hooks: async logging to `logs/task-lifecycle.log`
  - `Stop` hook on `implement`: state consistency guard
  - `PostToolUse` hook on `task-executor`: test monitoring and TDD context injection
  - `Stop` hooks on `phase-checker` and `code-reviewer`: completion logging
  - 7 new hook scripts

---

## [0.1.0] - 2026-05-08

Initial release of the Conductor plugin — a Spec-Driven Development orchestration system for Claude Code.

### Added

- **6 CLI Commands**: `setup`, `new-track`, `implement`, `status`, `review`, `revert`
- **8 Specialized Subagents**: task-executor, explorer, spec-planner, spec-reviewer, project-analyzer, code-reviewer, skip-analyst, phase-checker, doc-syncer
- **`track-state` CLI** with 15+ commands for state management
- **TDD Enforcement** with Execution Firewall (F1-F6)
- **Global State Lock** for conflict-free concurrent execution
- **Task Type Tags**: `[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, `[Manual]`
- **Subtask Support** with hierarchical task structure
- **Retry and Skip Analysis** for failed tasks
- **Auto-Review Flow** after track completion
- **Phase Checkpoint Protocol** for verification at phase boundaries
- **Doc-Syncer** for post-completion documentation synchronization
- **9 Language-Specific Style Guides**
- **Development Command Templates** per language
- **Project Artifacts**: index.md, track index.md, CLAUDE.md TOC, setup checkpointing

### Changed

- **Layered Conductor Injection** — split into 3 layers, ~40% context reduction
- **Compressed Skill Narratives** — ~526 lines reduced across 5 skills
- **Optional Skill Arguments** — auto-detection with fallback to user prompt
- **Spec-Planner Extraction** — unified subagent for setup and newTrack
- **Unified Agent Naming** — `conductor:` prefix for all subagents
- **Restructured Plugin Layout** — modular and portable structure

[Unreleased]: https://github.com/anthropics/conductor-plugin/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/anthropics/conductor-plugin/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/anthropics/conductor-plugin/releases/tag/v0.1.0
