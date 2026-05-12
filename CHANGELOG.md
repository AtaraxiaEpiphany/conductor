# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- **Interrupt recovery: stale current indices after state transitions** ([699798f](https://github.com/anthropics/conductor-plugin/commit/699798f))
  - `_do_complete`, `_do_defer`, `_do_skip`, `_do_block`, `_do_fail` now update `current_phase_index`/`current_task_index`/`current_subtask_index` so recovery always points to the latest task
  - Previously only `_do_lock` updated indices, causing recovery to target stale positions after defer/complete/skip/block/fail
- **Interrupt recovery: checkpoint detection scans ALL phases** ([699798f](https://github.com/anthropics/conductor-plugin/commit/699798f))
  - `cmd_recover` now calls `_any_phase_needs_checkpoint` instead of `_phase_needs_checkpoint(pi)` — checks every phase for missing checkpoints, not just the current one
  - Previously only checked when current task status was `completed`/`skipped`/`cancelled`, missing `deferred`/`blocked`/`failed` states
- **Auto-completed subtasks inherit parent commit SHA** ([699798f](https://github.com/anthropics/conductor-plugin/commit/699798f))
  - `_do_complete` now sets `commit_sha` on auto-completed subtasks via `resolved_sha`
- **`cmd_finalize` correctly sets `failed` status** ([699798f](https://github.com/anthropics/conductor-plugin/commit/699798f))
  - Previously fell through to `"completed"` even when `failed` tasks existed
- **`cmd_gc` guards result.json against active-task race** ([699798f](https://github.com/anthropics/conductor-plugin/commit/699798f))
  - Only deletes orphaned `result.json` when no task is `in_progress`

### Added

- **Comprehensive developer reference documentation** ([6c88fdc](https://github.com/anthropics/conductor-plugin/commit/6c88fdc))
  - `developer/reference/hooks-reference.md`: 17 hook scripts, I/O protocol, shared library API, testing and debugging guides
  - `developer/reference/plugins-reference.md`: manifest, directory structure, 6 skills, 9 agents, environment variables, CLI management
  - `developer/reference/agents-reference.md`: 9 subagent definitions, frontmatter config, dispatch patterns, result formats, failure recovery
  - `developer/reference/skills-reference.md`: 6 orchestrator skills, execution workflows, agent dispatch mapping, state management
- **Harness engineering reference** ([db222cb](https://github.com/anthropics/conductor-plugin/commit/db222cb))
  - Moved `harness-engineering.md` from gitignored local-only to tracked developer reference
  - Consolidates Anthropic/OpenAI/Martin Fowler harness engineering best practices

- **Documentation reorganization** ([ebcbec3](https://github.com/anthropics/conductor-plugin/commit/ebcbec3))
  - Restructured documentation based on audience segmentation
  - New directory structure: `docs/user/`, `docs/reference/`, `developer/`, `runtime/`
  - Standard frontmatter added to all documentation files (title, audience, status, related docs)
  - Created `developer/REORGANIZATION.md` for migration notes
- **Documentation simplification** ([55c47d3](https://github.com/anthropics/conductor-plugin/commit/55c47d3))
  - Moved internal implementation docs to `developer/reference/`: INTERACTION_REFERENCE.md, git-notes.md, track-state-cli.md
  - Simplified user-facing reference docs to core essentials only (~1,168 lines removed)
  - `harness-engineering.md` moved to `developer/reference/` and gitignored (local developer reference only)
  - Docs/code ratio improved from 3:1 to 2.8:1

- **Comprehensive interaction mechanism documentation** ([a347c9d](https://github.com/anthropics/conductor-plugin/commit/a347c9d))
  - `INTERACTION_MECHANISM.md`: Core architecture with sequence diagrams, component definitions, communication flows, message formats, state authority, error recovery, and quality gate enforcement
  - `INTERACTION_FLOW.md`: 8 visual flowcharts covering master flow, hook lifecycle, subagent isolation, state authority, recovery scenarios, quality gates, hook communication, async/sync patterns, and timeline
  - `INTERACTION_REFERENCE.md`: Quick reference guide with component interaction matrix, hook event table, subagent dispatch formats, track-state CLI reference, hook decisions, context injection points, state marker mapping, firewall violations, recovery scenarios, performance, troubleshooting, extension points, and best practices
  - Updated `README.md` with new Documentation section linking to all three interaction mechanism documents

- **Test script for all Python hooks** ([7158b06](https://github.com/anthropics/conductor-plugin/commit/7158b06))
  - `scripts/test-all.py`: validates all 18 hook scripts with automated test cases
  - Tests hook input/output, CLI commands, and expected behavior
  - Ensures migration completeness and correctness

### Changed

- **Hook system streamlined for reduced context pressure** ([7be594c](https://github.com/anthropics/conductor-plugin/commit/7be594c))
  - Removed 5 redundant hooks: enhance-conductor-context, on-task-event, on-config-change, on-cwd-change, on-subagent-result
  - Merged on-subagent-result failure/recovery detection into filter-subagent-output
  - Extracted shared `FAILURE_PATTERNS` to `lib/constants.py` for single-source truth
  - Fixed test command detection in on-test-run: precise regex patterns replace broad substring match
  - Trimmed SubagentStart reminders to result-format delimiters only
  - Added `track-state write-result` CLI for atomic result.json writes
  - Repurposed task-executor Step Log as Interruption Log (only writes on failure/interruption)
  - Hooks registration reduced from 14 to 8 events; net change: -754 lines, +207 lines
- **Code quality improvements and hook timeout safety** ([1a6b239](https://github.com/anthropics/conductor-plugin/commit/1a6b239))
  - Improved regex precision: word boundaries for extract_tags() and phase matching patterns
  - Removed unused imports across 9 hook scripts
  - Added timeout settings to code-reviewer and phase-checker Stop hooks
- **All hooks migrated from Bash to pure Python** ([7158b06](https://github.com/anthropics/conductor-plugin/commit/7158b06), [785a2bb](https://github.com/anthropics/conductor-plugin/commit/785a2bb))
  - 18 scripts converted from Bash to Python with shebang `#!/usr/bin/env python3`
  - Eliminated 40% duplicate code through shared library architecture
  - Original Bash scripts backed up to `scripts/.backup/bash-originals/`
  - Full IDE support, type checking, and testability now available
  - All hook configurations updated in `hooks/hooks.json`, skills, and agents
  - Shared library modules (`scripts/lib/`):
    - `hook_io.py`: Hook JSON input/output handling
    - `logging.py`: Log directory initialization and entry writing
    - `env.py`: Environment variable utilities
    - `json_utils.py`: JSON loading/saving with safe defaults
    - `path_utils.py`: Path and directory operations
    - `validation.py`: Validation functions
    - `git_utils.py`: Git operation utilities

### Fixed

- **Comprehensive hook protocol compliance audit** ([f6e31fe](https://github.com/anthropics/conductor-plugin/commit/f6e31fe))
  - Rewrote `hook_io.py` to support hookSpecificOutput with all event-specific fields (permissionDecision, updatedToolOutput, additionalContext, retry)
  - Fixed pre-command-check.py TypeError that bypassed security checks for dangerous git operations
  - Fixed filter-subagent-output.py TypeError that prevented subagent output filtering
  - Fixed state-consistency-check.py hardcoded hookEventName breaking SubagentStop auto-convert
  - Redesigned on-subagent-stop.py: use decision:"block" + reason for subagent self-recovery
  - Fixed on-test-run.py overly broad failure patterns (r'fail' matched "successfully")
  - Fixed hooks.json: InstructionsLoaded matcher, PostToolBatch timeout, ConfigChange matcher
  - Fixed validation.py wrong field names in validate_track_state
  - Fixed track-state: stack overflow from recursive dispatch, stale state reads, IndexError in _safe_task_name
- **HookSpecificOutput omission for observability-only events** ([f07cce9](https://github.com/anthropics/conductor-plugin/commit/f07cce9))
  - write_hook_output previously generated hookSpecificOutput whenever event name was present, causing JSON validation failures
  - Now only emits hookSpecificOutput when actual event-specific fields are provided
- **Harness engineering reliability issues** ([a6f9674](https://github.com/anthropics/conductor-plugin/commit/a6f9674))
  - Added file locking to track-state load/save for concurrent access safety
  - Added server-side coverage verification to on-batch-complete.py (F3 gate)
  - Improved subagent failure detection with context-aware pattern matching
  - Replaced bare except Exception blocks with specific exception types
  - Updated task-executor.md with atomic result.json write pattern
- **Gitignore pattern scope** ([92cda8f](https://github.com/anthropics/conductor-plugin/commit/92cda8f))
  - Changed broad `lib/` pattern to `/lib/` to prevent `scripts/lib/` from being ignored

### Added

- **Feature Verification File (feature-checklist.json)** (Harness Engineering P1)
  - Generated during `track-state init` with one entry per task/subtask, all `passes: false`
  - Updated to `passes: true` by `track-state process-result` on task success, with coverage and SHA evidence
  - Verified by `track-state finalize` — reports unverified items
  - New command: `track-state checklist-verify <track-dir>` for manual status check
  - Prevents premature "done" declarations: every task must have evidence of verification
- **F2/F3 Mechanical Enforcement** (Harness Engineering P2)
  - F3 Coverage Gate: `process-result` checks `coverage_pct < 80%` and reports `coverage_gate: FAILED`
  - F2 TDD Gate: `process-result` verifies test files exist in commit, reports `tdd_gate: NO_TESTS_FOUND`
  - Gate exemptions: `[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, `[Manual]` tasks bypass TDD/coverage checks
- **Track Quality Score** (Harness Engineering P3)
  - `track-state finalize` computes a 0-100 quality score: completion 40% + checklist 30% + coverage 20% + retry penalty 10%
  - Score stored in `track-state.json` and reported in finalize output
- **P1/P2 hooks for comprehensive lifecycle coverage** ([b6a604f](https://github.com/anthropics/conductor-plugin/commit/b6a604f))
  - `PostToolBatch` hook: batch-level validation after parallel tool calls
  - Prompt hook on implement Stop: LLM-based state audit (uses haiku model)
  - `asyncRewake` on critical subagent Stop: auto-recover on task-executor/explorer/phase-checker failure
  - `InstructionsLoaded` hook: progressive disclosure for conductor context
  - `ConfigChange` hook: hook configuration validation and audit logging
  - `CwdChanged` hook: conductor state awareness across directory changes
- **PreToolUse state protection** ([e1c824f](https://github.com/anthropics/conductor-plugin/commit/e1c824f))
  - Blocks dangerous git operations (push --force, reset --hard, clean -f) during active tracks
  - Prevents state lock violations before tool execution
- **SessionEnd cleanup hook** ([e1c824f](https://github.com/anthropics/conductor-plugin/commit/e1c824f))
  - Session cleanup: validates handoff files, logs session metrics
  - Writes session summary for cross-session recovery
- **Phase checkpoint resume** ([8947d21](https://github.com/anthropics/conductor-plugin/commit/8947d21))
  - `track-state recover` detects interrupted phase-checker via `phase_checkpoint_pending` field
  - `track-state dispatch-next` returns `dispatch_phase_checker` instead of `finalize` when checkpoint is pending
  - `_phase_needs_checkpoint()` / `_any_phase_needs_checkpoint()` scan plan.md for missing `[checkpoint: <sha>]` markers
  - Implement SKILL.md Section 2.1: phase-checker resume flow
- **Track archive system** ([3b9450f](https://github.com/anthropics/conductor-plugin/commit/3b9450f))
  - `track-state archive` command: transitions completed tracks to `archived` status with `archived_at` timestamp
  - `archived` status added to track-state schema with `[@]` marker in tracks.md
  - Full archive/keep active/delete cleanup flow in implement skill Section 8.0
  - Archived tracks grouped separately in status report
  - Review skill cleanup options reference `track-state archive`
- **Execution mode selection** ([3b9450f](https://github.com/anthropics/conductor-plugin/commit/3b9450f))
  - `execution_mode` field (interactive/continuous) in track-state.json schema
  - Mode selection UI in new-track skill before track init
  - `track-state init --execution-mode` flag
  - Mode propagation: recover/next output → implement → phase-checker dispatch
  - Documented storage location and lifecycle in conductor-orchestration.md
- **Session handoff file** ([3b9450f](https://github.com/anthropics/conductor-plugin/commit/3b9450f))
  - `state-consistency-check` Stop hook writes `session-handoff.md` with active track positions
  - `session-start` injects previous session state for cross-session recovery
- **Boundary enforcement linter** ([3b9450f](https://github.com/anthropics/conductor-plugin/commit/3b9450f))
  - `scripts/lint-track-state`: mechanically enforces F1 (State Lock) + F4 (SHA) + state consistency
  - Error messages include remediation instructions (Harness Engineering principle)
- **Garbage collection** ([3b9450f](https://github.com/anthropics/conductor-plugin/commit/3b9450f))
  - `track-state gc` command: cleans orphaned result.json, detects stale in_progress tasks (>24h)
  - `--health`/`--gc` flags in status skill for health check reporting
- **Enhanced validation messages** ([3b9450f](https://github.com/anthropics/conductor-plugin/commit/3b9450f))
  - `track-state validate` error messages now include remediation guidance

### Changed

- **Documentation directory structure** ([ebcbec3](https://github.com/anthropics/conductor-plugin/commit/ebcbec3))
  - `internal/` → `runtime/` (clearer intent for injected content)
  - `architecture/` → `developer/architecture/` (developer documentation)
  - `docs/` → `docs/user/` (user-facing documentation)
  - `reference/` → `docs/reference/` (consolidated reference manuals)
  - 21 files renamed with git history preserved
- **Runtime injection paths** ([ebcbec3](https://github.com/anthropics/conductor-plugin/commit/ebcbec3))
  - `session-start.py`: `internal/conductor-core.md` → `runtime/core-contract.md`
  - `test-all.py`: `internal/conductor-core.md` → `runtime/core-contract.md`
  - `task-executor.md`: `internal/conductor-core.md` → `runtime/core-contract.md`
  - All internal documentation links updated to reflect new structure
- **Hook configuration centralized** ([b6a604f](https://github.com/anthropics/conductor-plugin/commit/b6a604f))
  - All hooks now registered in `hooks/hooks.json` (skill-scoped Stop hook remains in SKILL.md frontmatter)
  - `SubagentStop` split: critical agents (task-executor, explorer, phase-checker) use `asyncRewake`, others use `async`
- **track-state code consolidation** ([d0b65fc](https://github.com/anthropics/conductor-plugin/commit/d0b65fc))
  - Extracted `_find_next_task()` helper, eliminating 49 lines of duplicated task-finding logic in `cmd_next` and `cmd_dispatch_next`
- **Implement skill compression section removed** ([b6a604f](https://github.com/anthropics/conductor-plugin/commit/b6a604f))
  - Removed `COMPRESSION PRIORITY` section from SKILL.md (PreCompact hook handles this automatically)
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

- **Subtask SHA visibility** ([73335d6](https://github.com/anthropics/conductor-plugin/commit/73335d6))
  - Subtask SHA now shown in plan.md even when identical to parent SHA
  - Previously, completed subtasks appeared without commit reference
- **Phase-checker skip on interruption** ([8947d21](https://github.com/anthropics/conductor-plugin/commit/8947d21))
  - Resumed interrupted phase-checker when all tasks terminal but no checkpoint in plan.md
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
