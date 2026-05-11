# Conductor

> Spec-Driven Development Orchestration Plugin for Claude Code

Conductor is a Claude Code plugin built on **Spec-Driven Development** and **Subagent-Driven Development** paradigms. It coordinates specialized subagents through an orchestrator to execute software development tasks, enforcing TDD workflows, quality gates, and auditable state management.

---

## Core Architecture

```
                    ┌──────────────────────────┐
                    │     Orchestrator Agent    │
                    │  (State, FSM, Dispatch)   │
                    └─────────┬────────────────┘
                              │
            ┌─────────────────┼──────────────────┐
            │                 │                   │
            ▼                 ▼                    ▼
   ┌────────────┐    ┌────────────┐      ┌────────────┐
   │   Skills   │    │  Subagents │      │ Templates  │
   │  (6 cmds)  │    │  (9 agents)│      │  + Styles  │
   └────────────┘    └────────────┘      └────────────┘
```

### Design Principles

| Principle | Description |
|-----------|-------------|
| **Orchestrator-Subagent Pattern** | The orchestrator manages state and dispatches tasks; subagents focus on single-responsibility execution |
| **Context Isolation** | State mutations via CLI scripts; subagents self-extract ACs/specs; phase checkpoints and doc sync run in isolated subagent context. Git notes written by `process-result` (not agents). Step logs write to files, results via `process-result`. Main session context stays minimal |
| **TDD Enforcement** | Mandatory Red-Green-Refactor cycle — no implementation code without a failing test |
| **Single State Lock** | Only one task may be `in_progress` globally, eliminating concurrent conflicts |
| **Audit Trail** | Every task commit gets a human-readable git note with quality gate evidence (coverage %, spec deviations, TC coverage). Written by `track-state process-result` — zero agent context cost. Best-effort recovery on interruption via `track-state recover` |
| **Spec-Driven** | From PRD to spec.md to plan.md — specifications drive every line of implementation code |

---

## Hooks System

Conductor uses Claude Code's hook system for lifecycle automation. Hooks are configured in `hooks/hooks.json`.

### Hook Events

| Event | Script | Purpose |
|-------|--------|---------|
| `InstructionsLoaded` | `enhance-conductor-context` | Progressive conductor context disclosure |
| `SessionStart` | `session-start` | Inject runtime/core-contract.md, session handoff |
| `SessionEnd` | `session-end` | Cleanup, handoff validation, metrics logging |
| `PreToolUse` (Bash) | `pre-command-check` | Block dangerous git ops, enforce state lock |
| `PostToolBatch` | `on-batch-complete` | Batch-level validation after parallel tool calls |
| `PostToolUse` (Agent) | `filter-subagent-output` | Filter subagent output for context pressure |
| `SubagentStart` | `on-subagent-start` | Inject role-specific execution reminders |
| `SubagentStop` | `on-subagent-stop` | Lifecycle logging; `asyncRewake` for critical agents |
| `TaskCreated/Completed` | `on-task-event` | Async logging to `logs/task-lifecycle.log` |
| `ConfigChange` | `on-config-change` | Configuration validation & audit logging |
| `CwdChanged` | `on-cwd-change` | Conductor state awareness across directory changes |

### Subagent Stop Priority

Critical subagents (`task-executor`, `explorer`, `phase-checker`) use `asyncRewake: true` — the orchestrator auto-recovers on failure. Other subagents use `async: true` for fire-and-forget logging.

---

## Commands

| Command | Description |
|---------|-------------|
| `/conductor:setup` | Initialize project: product definition, tech stack, workflow config, first track |
| `/conductor:newTrack <desc>` | Create a new track: interactive requirements gathering, auto-generates spec.md and plan.md |
| `/conductor:implement [track]` | Execute track tasks: orchestrator dispatches subagents through TDD workflow |
| `/conductor:status` | Project progress overview: reads track-state.json to display all track statuses |
| `/conductor:review [track]` | Code review: verify implementation quality, style compliance, test coverage against spec |
| `/conductor:revert <scope>` | Safe rollback: git revert with track-state.json state synchronization |

---

## Subagents

| Subagent | Role | Dispatched By |
|----------|------|---------------|
| **conductor:task-executor** | TDD workflow execution (Steps 3-9): write tests, implement code, verify coverage, commit | `implement` |
| **conductor:explorer** | Read-only code exploration: architecture analysis, dependency mapping, codebase investigation | `implement` (for `[Explore]` tasks) |
| **conductor:spec-planner** | Generate spec.md and plan.md: transform requirements into specifications and implementation plans | `setup`, `newTrack` |
| **conductor:spec-reviewer** | Interactive spec/plan review: presents summaries, handles revisions, keeps full files out of orchestrator context | `setup`, `newTrack` |
| **conductor:project-analyzer** | Brownfield project analysis: detect tech stack, architecture patterns, project structure | `setup` |
| **conductor:code-reviewer** | Deep code review: diff analysis, plan compliance, style check, test execution | `review` |
| **conductor:skip-analyst** | Failed task analysis: evaluate whether a task can be safely skipped and assess downstream impact | `implement` (retry exhausted) |
| **conductor:phase-checker** | Phase checkpoint verification: test coverage, automated tests, manual verification plan, checkpoint commit | `implement` (phase boundary) |
| **conductor:doc-syncer** | Project documentation sync: update product.md, tech-stack.md, product-guidelines.md after track completion | `implement` (track completion) |

---

## Task Lifecycle

Every task follows a strict 11-step lifecycle with a finite state machine:

```
                        ┌──────────┐
          ┌────────────►│ pending  │◄───────────┐
          │             └────┬─────┘            │
          │                  │ dispatch          │ human reset
          │                  ▼                   │
          │            ┌──────────┐              │
          │            │in_progress│              │
          │            └────┬─────┘              │
          │        ┌────────┼────────┐           │
          │        │        │        │           │
          │        ▼        ▼        ▼           │
          │   ┌─────────┐ ┌──────┐ ┌─────────┐  │
          │   │completed│ │failed│ │cancelled│  │
          │   └────┬────┘ └──┬───┘ └─────────┘  │
          │        │         │                    │
          │        │  ┌──────┼────────┐          │
          │        │  ▼      ▼        ▼          │
          │        │ ┌──────┐ ┌─────────┐        │
          │        │ │skipped│ │ blocked │────────┘
          │        │ └──────┘ └─────────┘
          │        │
          │        ▼
          │   ┌─────────┐
          │   │ deferred │ (auto, by [Manual] tag)
          │   └─────────┘
          │
          └─── ┌─────────┐
              │ archived │ (after completed, via cleanup)
              └─────────┘
```

### Execution Firewall

Six mandatory pre-action checks. Violating any Critical rule is a terminal error:

| Rule | Severity | Description |
|------|----------|-------------|
| **F1** Global State Lock | Critical | Only one `[~]` task allowed globally |
| **F2** TDD Gate | Critical | No implementation code without a failing test |
| **F3** Coverage Gate | Warning | No commit if coverage < 80% |
| **F4** SHA Must Exist | Critical | All terminal markers must have a commit SHA |
| **F5** Checkpoint Integrity | Warning | Phase checkpoint protocol is mandatory when all phase tasks complete |
| **F6** Context Guard | Critical | Never skip workflow steps |

---

## Installation

### As a Claude Code Plugin

1. Install the plugin:

```bash
claude plugin install <repo-url>
```

Or clone and install locally:

```bash
git clone <repo-url> ~/.claude/plugins/conductor
claude plugin install ~/.claude/plugins/conductor
```

2. In your project, run `/conductor:setup` to initialize the Conductor environment.

---

## Plugin Structure

```
conductor-plugin/
├── .claude-plugin/
│   └── plugin.json                    # Plugin manifest (name, version, author)
├── CLAUDE.md                          # System prompt & orchestration rules
├── README.md                          # This file
├── .gitignore
├── settings.json                      # Default plugin settings
├── .mcp.json                          # MCP server configurations
├── .lsp.json                          # LSP server configurations
│
├── skills/                            # Skill-based commands (SKILL.md per directory)
│   ├── setup/SKILL.md                 #   Project initialization
│   ├── implement/SKILL.md             #   Track execution orchestrator
│   ├── newTrack/SKILL.md              #   New track creation
│   ├── status/SKILL.md                #   Progress overview
│   ├── review/SKILL.md                #   Code review
│   └── revert/SKILL.md                #   Safe rollback
│
├── commands/                          # Flat .md command skills
│
├── agents/                            # Subagent definitions (markdown + frontmatter)
│   ├── task-executor.md               #   TDD implementation (Steps 3-9)
│   ├── explorer.md                    #   Read-only codebase investigation
│   ├── spec-planner.md                #   Spec & plan generation
│   ├── spec-reviewer.md               #   Interactive spec/plan review
│   ├── project-analyzer.md            #   Brownfield project detection
│   ├── code-reviewer.md               #   Deep code analysis
│   ├── skip-analyst.md                #   Failure impact analysis
│   ├── phase-checker.md               #   Phase checkpoint verification
│   └── doc-syncer.md                  #   Project documentation sync
│
├── hooks/
│   └── hooks.json                     # Hook event configurations (10 hook types)
│
├── monitors/
│   └── monitors.json                  # Background monitor definitions
│
├── bin/                               # Executables (added to PATH)
├── scripts/                           # Hook & utility scripts
│   ├── session-start                  #   SessionStart hook (injects runtime/core-contract.md)
│   ├── session-end                    #   SessionEnd hook (cleanup, handoff validation, metrics)
│   ├── enhance-conductor-context      #   InstructionsLoaded hook (progressive context disclosure)
│   ├── on-subagent-start              #   SubagentStart hook (injects agent reminders)
│   ├── on-subagent-stop               #   SubagentStop hook (lifecycle logging, asyncRewake for critical agents)
│   ├── on-task-event                  #   TaskCreated/TaskCompleted hooks (async logging)
│   ├── on-test-run                    #   PostToolUse hook for test monitoring
│   ├── on-batch-complete              #   PostToolBatch hook (batch-level validation)
│   ├── pre-command-check              #   PreToolUse hook (blocks dangerous git ops, state lock violations)
│   ├── on-config-change               #   ConfigChange hook (configuration validation & audit)
│   ├── on-cwd-change                  #   CwdChanged hook (conductor state awareness)
│   ├── state-consistency-check        #   implement Stop hook (detects stale locks)
│   ├── git-notes-query                #   Query tool for audit data
│   ├── track-state                    #   State management CLI (Python 3)
│       # Commands: next, recover, lock, complete,
│       #   fail, skip, block, defer, sync-plan,
│       #   registry-update, start, validate,
│       #   phase-done, add-checkpoint, finalize,
│       #   process-result, init, shas, deferred-report,
│       #   get-handoff, sync-handoff, append-handoff,
│       #   checklist-verify, archive, gc
│   └── lint-track-state               #   Boundary enforcement linter (F1/F4/state)
├── schemas/                           # JSON Schema definitions
│   └── track-state.schema.json        #   track-state.json schema (documentation reference)
├── output-styles/                     # Output formatting styles
├── themes/                            # Color theme definitions
│
├── templates/                         # Workflow & style guide templates
│   ├── template.md                    #   Full workflow template
│   ├── task-workflow.md               #   11-step task workflow
│   ├── phase-checkpoint.md            #   Phase verification protocol
│   ├── index.md                       #   Workflow index
│   ├── dev-commands/                  #   Development command templates
│   ├── testing/
│   │   └── strategy.md               #   Test placement & naming conventions
│   └── code-styleguides/              #   Language-specific style guides
│       ├── general.md
│       ├── javascript.md
│       ├── typescript.md
│       ├── python.md
│       ├── go.md
│       ├── cpp.md
│       ├── csharp.md
│       ├── dart.md
│       └── html-css.md
│
└── references/                        # Reference documentation (gitignored)
```

---

## Generated Project Layout

When `/conductor:setup` initializes a project, it creates:

```
your-project/
├── CLAUDE.md                          # Project instructions + Conductor TOC
├── conductor/
│   ├── index.md                       # Project context index
│   ├── overview/
│   │   ├── product.md                 # Product definition
│   │   └── product-guidelines.md      # Product guidelines
│   ├── design/
│   │   └── tech-stack.md              # Technology stack
│   ├── workflow/
│   │   ├── index.md                   # Workflow index
│   │   ├── template.md                # Full workflow template
│   │   ├── task-workflow.md           # 11-step task workflow
│   │   ├── phase-checkpoint.md        # Phase verification protocol
│   │   ├── testing/strategy.md        # Test placement & naming conventions
│   │   └── code-styleguides/          # Selected language guides
│   ├── tracks.md                      # Tracks registry
│   └── tracks/
│       └── <track_id>/
│           ├── index.md               # Track context
│           ├── spec.md                # Feature specification
│           ├── plan.md                # Implementation plan
│           ├── track-state.json       # Authoritative state
│           ├── feature-checklist.json # Feature verification (passes=false → true)
│           ├── handoff.md             # Handoff index (created on first execution)
│           └── .conductor/handoff/    # Per-task handoff files
```

---

## Usage Workflow

### 1. Initialize Project

```
> /conductor:setup
```

Setup wizard will:
- Detect brownfield/greenfield project
- Guide you through product definition, guidelines, tech stack selection
- Configure code style guides and workflow
- Generate initial track

### 2. Create a Feature Track

```
> /conductor:newTrack user authentication with OAuth2
```

Interactive workflow:
1. Scans for related document paths in your project (content loaded by subagent)
2. Collects requirements through guided Q&A
3. Dispatches `conductor:spec-planner` to generate spec.md and plan.md
4. Dispatches `conductor:spec-reviewer` for interactive review (keeps full files out of main session)
5. Select execution mode (interactive or continuous)
6. Creates `track-state.json` via `track-state init --execution-mode` and commits all artifacts

### 3. Implement

```
> /conductor:implement
```

The orchestrator:
1. Loads track state and recovers from interruptions via `track-state recover`
2. If interrupted phase-checker detected (`phase_checkpoint_pending`), resumes it before dispatching new tasks
3. Selects next pending task (global state lock) via `track-state next`
4. Dispatches appropriate subagent:
   - `[Explore]` tasks → `conductor:explorer` (read-only investigation)
   - Default tasks → `conductor:task-executor` (TDD workflow, self-extracts ACs from spec.md)
5. Processes result via `track-state process-result` (state update + plan sync + handoff)
6. Dispatches `conductor:phase-checker` at phase boundaries
7. Dispatches `conductor:doc-syncer` upon track completion

All state mutations are performed by the `track-state` CLI script — the orchestrator never reads or edits `track-state.json` directly. Subagents self-extract ACs from spec.md and write step logs to files, keeping orchestrator context minimal.

### 4. Archive & Cleanup

After implementation, the orchestrator offers cleanup options:
- **Archive** (recommended) — marks track as archived, keeps files for reference
- **Keep Active** — leaves track in completed status
- **Delete** — removes track files entirely (irreversible)

### 5. Monitor Progress

```
> /conductor:status
```

Outputs a comprehensive status report with track progress, phase status, task-level details, issue highlights, and archived track grouping. Supports `--health` flag for garbage collection and stale state detection.

### 6. Review & Rollback

```
> /conductor:review
> /conductor:revert task <name>
```

---

## Task Type Tags

Tasks in `plan.md` can be annotated with type tags that modify workflow behavior:

| Tag | TDD Gate | Description |
|-----|----------|-------------|
| (none) | **Required** | Standard TDD workflow: Red → Green → Refactor → Coverage → Commit |
| `[Explore]` | N/A | Read-only code investigation. Dispatched to `conductor:explorer` subagent |
| `[Manual]` | Skipped | Human verification tasks. Always auto-deferred, verified at track finalization |
| `[Docs]` | Skipped | Documentation-only changes |
| `[Config]` | Skipped | Configuration file changes |
| `[Chore]` | Skipped | Maintenance tasks (dependencies, tooling) |

---

## Quality Standards

### Feature Verification

Every track gets a `feature-checklist.json` generated from the plan structure at init time. Each task/subtask starts with `passes: false`. The system mechanically updates the checklist as tasks complete:

1. **Init**: `track-state init` generates checklist with all items `passes: false`
2. **Process**: `track-state process-result` flips completed tasks to `passes: true` with evidence (SHA, coverage, TC IDs)
3. **Finalize**: `track-state finalize` verifies all items and computes a 0-100 quality score

Quality score weights: completion (40%) + checklist verification (30%) + coverage (20%) + retry penalty (10%).

### Quality Gates (F2/F3)

Mechanically enforced in `process-result`:

| Gate | Rule | Exempt Tags |
|------|------|-------------|
| **F2** TDD | Test files must exist in commit | `[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, `[Manual]` |
| **F3** Coverage | `coverage_pct >= 80%` | `[Docs]`, `[Config]`, `[Chore]`, `[Manual]` |

### Pre-Commit Checklist

- All tests pass
- Code coverage > 80%
- Code follows style guides (`conductor/workflow/code-styleguides/`)
- Public APIs documented
- Type safety enforced
- No lint errors
- No security vulnerabilities

### Commit Format

```
<type>(<scope>): <description>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Conductor prefixes: `conductor(plan)`, `conductor(checkpoint)`, `chore(conductor)`

---

## State Authority Model

`track-state.json` is **always** the source of truth. `plan.md` is a synchronized human-readable projection.

### Track Status Lifecycle

Track-level status is stored as plain text in `track-state.json` and synced to `tracks.md` via `track-state registry-update`. Supported formats in `tracks.md`: section-based (`- **Status:** in_progress`), checkbox (`- [~] description`), or table row.

| Status | Description |
|--------|-------------|
| `new` | Track created, not yet started |
| `in_progress` | Track actively being implemented |
| `completed` | All tasks finished, review done |
| `archived` | Track archived for reference |
| `blocked` | Track has unresolvable blockers |
| `cancelled` | Track cancelled |

### Execution Modes

| Mode | Behavior |
|------|----------|
| `interactive` (default) | Pauses for user confirmation at phase checkpoints |
| `continuous` | Auto-proceeds through all phases, only stops on failures |

```
track-state.json          plan.md
┌──────────────┐         ┌──────────────┐
│  Authoritative│──sync──►│  Projection  │
│  State        │         │  (markers)   │
└──────────────┘         └──────────────┘
```

Task status marker mapping (plan.md only):

| track-state.json | plan.md |
|------------------|---------|
| `pending` | `[ ]` |
| `in_progress` | `[~]` |
| `completed` | `[x] ... [sha]` (SHA appended at line end) |
| `failed` | `[!] ... [sha]` (SHA appended at line end) |
| `skipped` | `[>] ... [sha]` (SHA appended at line end) |
| `blocked` | `[#] ... [sha]` (SHA appended at line end) |
| `cancelled` | `[-] ... [sha]` (SHA appended at line end) |

---

## Git Notes Audit System

Every task-executor commit gets a human-readable git note for comprehensive auditability. Notes are written by `track-state process-result` — zero agent context cost.

### Note Structure

```json
{
  "conductor": {
    "version": "1.0",
    "timestamp": "2026-05-09T12:34:56Z",
    "session_id": "abc123",
    "track_id": "user-login",
    "track_dir": "conductor/tracks/user-login"
  },
  "task": {
    "phase": 0,
    "task": 1,
    "subtask": null,
    "name": "Implement login form",
    "attempt": 1,
    "tags": []
  },
  "requirements": {
    "tc_implemented": ["TC-1.1", "TC-1.2", "TC-2.1"],
    "spec_deviation": "NONE"
  },
  "implementation": {
    "commit_sha": "a1b2c3d",
    "summary": "Implemented user login",
    "diff_stats": "3 files changed, 127 insertions(+), 5 deletions(-)",
    "files_added": ["test/login.test.ts", "src/login.ts"],
    "files_modified": ["src/index.ts"],
    "files_deleted": [],
    "lines_added": 127,
    "lines_deleted": 5
  }
}
```

### How It Works

1. **task-executor**: Completes task, writes `result.json`, commits code

2. **Orchestrator**: Calls `track-state process-result` which:
   - Reads `result.json` (coverage, spec deviations, TC coverage)
   - Updates track-state.json state
   - Syncs plan.md markers
   - Updates handoff.md index
   - Writes human-readable git note to the commit

3. **Recovery**: `track-state recover` performs best-effort git notes recovery on interruption

### Query Tool

```bash
# View audit data for a specific commit
git-notes-query --sha <commit-hash>

# View all commits for a track
git-notes-query --track <track-id>

# View all activity in a session
git-notes-query --session <session-id>

# Show test coverage trend
git-notes-query --coverage-trend

# Show all changed files
git-notes-query --files

# Show specification deviations
git-notes-query --deviations
```

### Benefits

- **Zero subagent overhead**: Notes written by CLI, not by agents
- **Queryable**: Human-readable notes viewable via `git log --notes`
- **Complete traceability**: Links commits to requirements, tests, and state
- **Session tracking**: All work in a session can be reconstructed

---

## `track-state` CLI Reference

All `track-state.json` mutations are handled by the `scripts/track-state` Python CLI. The orchestrator calls it via bash — never reads/edits the JSON directly.

```
track-state <command> <track-dir> [options]
```

| Command | Description | Output |
|---------|-------------|--------|
| `next` | Find next dispatchable task (in_progress > pending); returns `phase_checkpoint_pending` if checkpoint resume needed | `{phase, task, subtask, name, type, tags, phase_checkpoint_pending?}` |
| `recover` | Get recovery context for current task; includes `phase_checkpoint_pending` if interrupted phase-checker detected | `{status, phase, task, subtask, name, type, retry_count, phase_checkpoint_pending, ...}` |
| `lock <p> <t> [<s>]` | Set task to in_progress, update indices | `{ok}` |
| `complete <p> <t> [<s>] --sha <sha>` | Set task to completed, check parent completion | `{ok, parent_completed}` |
| `fail <p> <t> [<s>] --summary <text>` | Set task to failed, increment retry_count | `{retry_count}` |
| `skip <p> <t> [<s>] --reason <text>` | Set task to skipped | `{ok}` |
| `block <p> <t> [<s>] --reason <text>` | Set task to blocked | `{ok}` |
| `defer <p> <t> [<s>] --reason <text>` | Set task to deferred | `{ok, parent_deferred}` |
| `sync-plan` | Re-project all markers to plan.md from state | `{synced}` |
| `registry-update <tracks-md>` | Update track entry in tracks.md based on track-state.json status (handles section and checkbox formats) | `{updated, marker, status}` |
| `start` | Transition track from `new` to `in_progress` | `{ok, status}` |
| `validate [--fix]` | Validate track-state.json structural + semantic integrity and cross-check plan.md consistency. `--fix` auto-repairs: parent→subtask status propagation, phase status sync | `{valid, errors, warnings, fixes}` |
| `phase-done <p>` | Check if all tasks in phase are terminal | `{complete, terminal, total}` |
| `add-checkpoint <p> <sha>` | Add or update checkpoint SHA for a phase in plan.md | `{ok, phase, sha}` |
| `finalize` | Set indices to -1, compute track-level status, verify checklist, compute quality score | `{status, quality_score, checklist?}` |
| `process-result` | Read `.conductor/result.json`, update state + plan + handoff + checklist + git notes. Enforces F2/F3 gates | `{status, sha, parent_completed, deviations, coverage_gate, tdd_gate}` or `{status, retry_count, summary}` |
| `init --plan-structure <json> --track-id <id> --type <type> --description <desc>` | Create track-state.json + index.md + handoff.md + feature-checklist.json from plan structure in one call | `{ok, track_id, phases, tasks}` |
| `shas` | List all commit SHAs for a track | `{shas, first, last, count}` |
| `deferred-report` | List all deferred tasks for verification | `{deferred, count}` |
| `get-handoff <p> <t> [--subtask <s>]` | Get handoff content for a specific task/subtask | `{content, path}` |
| `sync-handoff` | Sync handoff.md index with current state | `{ok, updated}` |
| `append-handoff <p> <t> --type <explore|decision|risk|deviation> --content <json> [--subtask <s>]` | Append content to a task's handoff file | `{ok, type, handoff_file}` |
| `checklist-verify` | Check feature-checklist.json verification status | `{exists, total, verified, unverified}` |

---

## Documentation

- **[Core README](README.md)** — This file, plugin overview and usage
- **[Interaction Mechanism](INTERACTION_MECHANISM.md)** — Deep dive into Skills, Subagents, and Hooks communication
- **[Interaction Flow](INTERACTION_FLOW.md)** — Visual flowcharts showing interaction patterns
- **[Interaction Reference](INTERACTION_REFERENCE.md)** — Quick reference for all interaction points
- **[CHANGELOG.md](CHANGELOG.md)** — Complete history of changes

### In-Depth Architecture

For detailed understanding of the three-tier interaction model:

1. **[INTERACTION_MECHANISM.md](INTERACTION_MECHANISM.md)** — Comprehensive documentation covering:
   - System architecture with Mermaid diagrams
   - Component definitions and responsibilities
   - Communication flows (5 phases with sequence diagrams)
   - Message formats and schemas
   - State authority model
   - Error recovery mechanisms
   - Quality gate enforcement

2. **[INTERACTION_FLOW.md](INTERACTION_FLOW.md)** — Visual reference with 8 flowcharts:
   - Master flow from user command to task completion
   - Detailed hook flow (tool execution lifecycle)
   - Subagent isolation patterns
   - State authority architecture
   - Recovery flow diagrams
   - Quality gate enforcement flow
   - Hook communication protocol
   - Async vs sync execution patterns
   - Complete implementation timeline

3. **[INTERACTION_REFERENCE.md](INTERACTION_REFERENCE.md)** — Quick reference guide:
   - Component interaction matrix
   - Complete hook event reference (20+ events)
   - Subagent dispatch formats and output schemas
   - track-state CLI command reference
   - Hook decision matrix and exit codes
   - Context injection points
   - State marker mapping (plan.md ↔ track-state.json)
   - Firewall violation detection
   - Recovery scenarios
   - Performance characteristics
   - Troubleshooting guide
   - Extension points and best practices

---

## Requirements

- [Claude Code](https://claude.ai/code) CLI or IDE extension
- Git repository
- No additional dependencies — Conductor operates entirely through Claude Code's agent system

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a complete history of changes.

## License

MIT
