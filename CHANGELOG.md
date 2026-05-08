# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.1.0] - 2026-05-08

### Added

- **SDD Orchestration Workflow**: Full Spec-Driven Development pipeline with orchestrator-subagent pattern ([bc63495](https://github.com/anthropics/conductor-plugin/commit/bc63495))
- **6 CLI Commands**: `setup`, `newTrack`, `implement`, `status`, `review`, `revert` ([5366795](https://github.com/anthropics/conductor-plugin/commit/5366795))
- **8 Subagents**: task-executor, explorer, spec-planner, project-analyzer, code-reviewer, skip-analyst, phase-checker, doc-syncer ([5366795](https://github.com/anthropics/conductor-plugin/commit/5366795))
- **track-state CLI**: Context-isolated state management with commands: next, recover, lock, complete, fail, skip, block, sync-plan, phase-done, finalize, process-result ([46a97b2](https://github.com/anthropics/conductor-plugin/commit/46a97b2))
- **Context-Isolated SDD Workflow**: Subagent dispatch and result pipeline keeping orchestrator context minimal ([bc63495](https://github.com/anthropics/conductor-plugin/commit/bc63495))
- **Deferred State, [Manual] Tag, and Continuous Execution Mode**: Enhanced task lifecycle for manual and continuous workflows ([0b154d9](https://github.com/anthropics/conductor-plugin/commit/0b154d9))
- **Auto-Review and Shared Templates**: Automated review flow and shared workflow templates across agents ([e14c343](https://github.com/anthropics/conductor-plugin/commit/e14c343))
- **SessionStart Hook**: Automatically injects conductor.md as context on session start ([e8f8d14](https://github.com/anthropics/conductor-plugin/commit/e8f8d14))
- **References Directory**: Dedicated branch for reference documentation ([3b7630f](https://github.com/anthropics/conductor-plugin/commit/3b7630f))
- **Subtask Support**: Hierarchical task structure with parent-child relationships ([f988033](https://github.com/anthropics/conductor-plugin/commit/f988033))
- **Global/Scoped Doc Classification**: Per-agent doc filtering with Global and Scoped document categories ([1c6f670](https://github.com/anthropics/conductor-plugin/commit/1c6f670))
- **Doc-Syncer Expansion**: Extended doc-syncer to cover all project documentation ([3d1c0a6](https://github.com/anthropics/conductor-plugin/commit/3d1c0a6))

### Changed

- **Layered Conductor Injection**: Split conductor.md into layered injection to optimize SDD context footprint ([6753ed7](https://github.com/anthropics/conductor-plugin/commit/6753ed7))
- **Compressed Skill Narratives**: Reduced context footprint across all skill definitions ([e14c343](https://github.com/anthropics/conductor-plugin/commit/e14c343))
- **Optional Skill Arguments**: Made arguments optional with auto-detection and AskUserQuestion ([fb2863d](https://github.com/anthropics/conductor-plugin/commit/fb2863d))
- **Spec-Planner Extraction**: Track generation extracted from setup to dedicated spec-planner subagent ([c3a8324](https://github.com/anthropics/conductor-plugin/commit/c3a8324))
- **Unified Agent Naming**: Added `conductor:` prefix to all subagent names for consistent namespacing ([e0a3e28](https://github.com/anthropics/conductor-plugin/commit/e0a3e28))
- **Restructured Plugin Layout**: Modular and portable plugin structure with standard directories ([570b658](https://github.com/anthropics/conductor-plugin/commit/570b658))
- **SHA Format Fix**: Standardized SHA format across all skills ([f988033](https://github.com/anthropics/conductor-plugin/commit/f988033))

### Fixed

- **Duplicate SHA Markers**: Strip all trailing SHAs in sync-plan to prevent duplicate markers in plan.md ([6e74be3](https://github.com/anthropics/conductor-plugin/commit/6e74be3))
- **Stale Workflow References**: Fixed stale workflow.md refs and enhanced spec-to-test traceability ([55d69eb](https://github.com/anthropics/conductor-plugin/commit/55d69eb))

### Removed

- **Old Plugin Config**: Removed plugin config, keeping only references directory ([214335d](https://github.com/anthropics/conductor-plugin/commit/214335d))
- **Conductor- Prefix**: Removed redundant `conductor-` prefix from skill and agent names ([2041025](https://github.com/anthropics/conductor-plugin/commit/2041025), [7777fd0](https://github.com/anthropics/conductor-plugin/commit/7777fd0), [ce756d5](https://github.com/anthropics/conductor-plugin/commit/ce756d5))

[Unreleased]: https://github.com/anthropics/conductor-plugin/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/anthropics/conductor-plugin/releases/tag/v0.1.0
