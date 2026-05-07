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
   │  (6 cmds)  │    │  (6 agents)│      │  + Styles  │
   └────────────┘    └────────────┘      └────────────┘
```

### Design Principles

| Principle | Description |
|-----------|-------------|
| **Orchestrator-Subagent Pattern** | The orchestrator manages state and dispatches tasks; subagents focus on single-responsibility execution |
| **TDD Enforcement** | Mandatory Red-Green-Refactor cycle — no implementation code without a failing test |
| **Single State Lock** | Only one task may be `in_progress` globally, eliminating concurrent conflicts |
| **Audit Trail** | Every state transition is accompanied by a git commit + git note for full traceability |
| **Spec-Driven** | From PRD to spec.md to plan.md — specifications drive every line of implementation code |

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
| **task-executor** | TDD workflow execution (Steps 3-9): write tests, implement code, verify coverage, commit | `implement` |
| **explorer** | Read-only code exploration: architecture analysis, dependency mapping, codebase investigation | `implement` (for `[Explore]` tasks) |
| **spec-planner** | Generate spec.md and plan.md: transform requirements into specifications and implementation plans | `newTrack` |
| **project-analyzer** | Brownfield project analysis: detect tech stack, architecture patterns, project structure | `setup` |
| **code-reviewer** | Deep code review: diff analysis, plan compliance, style check, test execution | `review` |
| **skip-analyst** | Failed task analysis: evaluate whether a task can be safely skipped and assess downstream impact | `implement` (retry exhausted) |

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
          │   └─────────┘ └──┬───┘ └─────────┘  │
          │                  │                    │
          │         ┌────────┼────────┐          │
          │         ▼                  ▼          │
          │    ┌──────────┐     ┌─────────┐      │
          │    │  skipped  │     │ blocked │──────┘
          │    └──────────┘     └─────────┘
          │                           │
          └───────────────────────────┘
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

1. Clone this repository to your Claude Code plugins directory:

```bash
git clone <repo-url> ~/.claude/plugins/conductor
```

2. Add to your Claude Code settings (`.claude/settings.json` or `.claude/settings.local.json`):

```json
{
  "projects": {
    "/path/to/your/project": {
      "allowedTools": ["Agent", "Bash", "Read", "Edit", "Write", "Grep", "Glob"]
    }
  }
}
```

3. In your project, run `/conductor:setup` to initialize the Conductor environment.

---

## Project Structure

```
conductor-plugin/
├── CLAUDE.md                          # System prompt & orchestration rules
├── README.md                          # This file
├── .claude_plugin/
│   └── plugin.json                    # Plugin metadata
├── skills/                            # User-facing commands
│   ├── setup/SKILL.md                 # Project initialization
│   ├── implement/SKILL.md             # Track execution orchestrator
│   ├── newTrack/SKILL.md              # New track creation
│   ├── status/SKILL.md                # Progress overview
│   ├── review/SKILL.md                # Code review
│   └── revert/SKILL.md                # Safe rollback
├── agents/
│   └── subagents/                     # Specialized execution agents
│       ├── task-executor.md           # TDD implementation (Steps 3-9)
│       ├── explorer.md                # Read-only codebase investigation
│       ├── spec-planner.md            # Spec & plan generation
│       ├── project-analyzer.md        # Brownfield project detection
│       ├── code-reviewer.md           # Deep code analysis
│       └── skip-analyst.md            # Failure impact analysis
├── templates/                         # Workflow & style guide templates
│   ├── template.md                    # Full workflow template
│   ├── task-workflow.md               # 11-step task workflow
│   ├── phase-checkpoint.md            # Phase verification protocol
│   ├── index.md                       # Workflow index
│   └── code-styleguides/              # Language-specific style guides
│       ├── general.md
│       ├── javascript.md
│       ├── typescript.md
│       ├── python.md
│       ├── go.md
│       ├── cpp.md
│       ├── csharp.md
│       ├── dart.md
│       └── html-css.md
└── .gitignore
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
│   │   └── code-styleguides/          # Selected language guides
│   ├── tracks.md                      # Tracks registry
│   └── tracks/
│       └── <track_id>/
│           ├── index.md               # Track context
│           ├── spec.md                # Feature specification
│           ├── plan.md                # Implementation plan
│           ├── track-state.json       # Authoritative state
│           └── issues.md              # Failure reports (lazy)
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
1. Scans for related documents in your project
2. Collects requirements through guided Q&A
3. Dispatches `spec-planner` to generate spec.md and plan.md
4. Creates `track-state.json` with full task hierarchy

### 3. Implement

```
> /conductor:implement
```

The orchestrator:
1. Loads track state and recovers from interruptions
2. Selects next pending task (global state lock)
3. Dispatches appropriate subagent:
   - `[Explore]` tasks → `explorer` (read-only investigation)
   - Default tasks → `task-executor` (TDD workflow)
4. Processes result: success → advance, failure → retry/skip analysis
5. Executes phase checkpoint protocol at phase boundaries
6. Syncs project documentation upon track completion

### 4. Monitor Progress

```
> /conductor:status
```

Outputs a comprehensive status report with track progress, phase status, task-level details, and issue highlights.

### 5. Review & Cleanup

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
| `[Explore]` | N/A | Read-only code investigation. Dispatched to `explorer` subagent |
| `[Docs]` | Skipped | Documentation-only changes |
| `[Config]` | Skipped | Configuration file changes |
| `[Chore]` | Skipped | Maintenance tasks (dependencies, tooling) |

---

## Quality Standards

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

```
track-state.json          plan.md
┌──────────────┐         ┌──────────────┐
│  Authoritative│──sync──►│  Projection  │
│  State        │         │  (markers)   │
└──────────────┘         └──────────────┘
```

Status marker mapping:

| track-state.json | plan.md |
|------------------|---------|
| `pending` | `[ ]` |
| `in_progress` | `[~]` |
| `completed` | `[x] [<sha>]` |
| `failed` | `[!] [<sha>]` |
| `skipped` | `[>] [<sha>]` |
| `blocked` | `[#] [<sha>]` |
| `cancelled` | `[-] [<sha>]` |

---

## Requirements

- [Claude Code](https://claude.ai/code) CLI or IDE extension
- Git repository
- No additional dependencies — Conductor operates entirely through Claude Code's agent system

---

## License

MIT
