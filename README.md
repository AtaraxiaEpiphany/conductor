# Conductor

**Spec-Driven Development Orchestration Plugin for [Claude Code](https://claude.ai/code)**

Conductor coordinates software construction by managing the full lifecycle of development *tracks* — from specification and planning through TDD implementation, code review, documentation sync, and archival. It enforces quality gates, state consistency, and workflow discipline through an extensive system of hooks, subagents, and a state machine CLI.

## Features

- **Track-based project management** — Work is organized into tracks (feature / bugfix / chore / docs), each with spec, plan, state, and handoff files
- **TDD enforcement** — Mandatory test-driven development with an 80 % coverage gate (server-side verification, not agent self-report)
- **Subagent orchestration** — A main orchestrator dispatches 9 specialized AI agents for isolated, focused work
- **State machine CLI** — `track-state` manages all state mutations atomically; `plan.md` stays in sync as the human-readable mirror
- **Execution firewall** — 6 mandatory pre-action checks (F1–F6) and 11 anti-patterns (V1–V11) prevent workflow violations
- **Session continuity** — Handoff files, state recovery on resume, and compression priority hints for context management
- **Git integration** — Conventional commits, git notes for audit trail, checkpoint commits per phase, SHA tracking on every task

## Requirements

- **Python 3** (standard library only — no pip dependencies)
- **Claude Code** CLI
- **Git**

## Installation

Place this project inside your Claude Code plugins directory. The plugin root is identified by the `CLAUDE_PLUGIN_ROOT` environment variable.

## Usage

All interaction happens through Claude Code slash commands:

| Command | Description |
|---------|-------------|
| `/conductor:setup` | Scaffold a new project with Conductor environment |
| `/conductor:new-track [description]` | Create a new track (generates spec, plan, state) |
| `/conductor:implement [track]` | Execute the dispatch loop to implement a track |
| `/conductor:status [track]` | View project / track progress |
| `/conductor:review [track]` | Code review of completed work |
| `/conductor:revert [scope]` | Revert work with state consistency |

### `track-state` CLI

The `bin/track-state` command provides direct state management:

```
bin/track-state <command> [options]

Commands:
  init              Create a new track state file
  start             Transition a task to in_progress
  lock              Lock a task for exclusive access
  complete          Mark a task completed
  fail              Mark a task failed
  skip              Skip a task with justification
  block             Block a task on a dependency
  defer             Defer a task to a future phase
  reset             Reset a task or phase
  validate          Validate state consistency
  recover           Recover from an interrupted state
  dispatch-next     Determine the next action
  dispatch-prepare  Prepare for dispatch
  dispatch-finalize Finalize a dispatch cycle
  sync-plan         Synchronise plan.md from state
  archive           Archive a completed track
  gc                Garbage-collect stale state
```

## Architecture

```
conductor-plugin/
├── agents/                 9 specialised agent definitions (.md)
├── bin/track-state         Shell wrapper for the state CLI
├── hooks/hooks.json        10 hook event definitions
├── runtime/core-contract.md  System prompt injected into every session
├── schemas/                JSON Schema for track-state.json
├── scripts/
│   ├── lib/                Shared library (hook_io, logging, validation …)
│   ├── track_state/        State machine CLI package
│   └── *.py                Hook scripts (session start/end, subagent, batch …)
├── skills/                 6 skill definitions (implement, new-track, setup …)
└── templates/              Templates copied into target projects
    ├── code-styleguides/   9 language style guides
    ├── dev-commands/       7 language dev-command templates
    └── testing/            Testing strategy template
```

### Subagents

| Agent | Model | Purpose |
|-------|-------|---------|
| `task-executor` | sonnet | TDD implementation (steps 3–8) |
| `explorer` | haiku | Read-only code investigation |
| `phase-checker` | sonnet | Phase checkpoint verification |
| `code-reviewer` | sonnet | Deep code review against spec/plan |
| `spec-planner` | haiku | Generate spec.md + plan.md |
| `spec-reviewer` | haiku | Interactive spec/plan review |
| `doc-syncer` | haiku | Documentation synchronisation |
| `skip-analyst` | haiku | Failed-task skip analysis |
| `project-analyzer` | sonnet | Brownfield project detection |

### Execution Firewall

Before every code-modifying action, Conductor enforces six checks:

1. **F1** — State consistency: `track-state.json` matches `plan.md`
2. **F2** — Task lock: current task is locked and owned
3. **F3** — Coverage gate: test coverage ≥ 80 %
4. **F4** — No stale in_progress: no tasks stuck in progress
5. **F5** — Phase boundary: all tasks complete before advancing
6. **F6** — Anti-pattern scan: no V1–V11 violations

## Testing

```bash
python3 -m pytest tests/
```

## License

[MIT](LICENSE)
