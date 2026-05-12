---
title: Plugin System Reference
audience: developer
status: stable
last_updated: 2026-05-12
related:
  - ./hooks-reference.md
  - ./INTERACTION_REFERENCE.md
  - ../../docs/reference/hooks.md
---

# Conductor Plugin: Plugin System Reference

> Comprehensive reference mapping the Claude Code plugin system to Conductor's implementation. Covers manifest, skills, agents, hooks, output styles, monitors, environment variables, directory structure, and CLI management.

---

## Table of Contents

- [Plugin Manifest](#plugin-manifest)
- [Plugin Directory Structure](#plugin-directory-structure)
- [Skills](#skills)
- [Agents](#agents)
- [Hooks](#hooks)
- [Output Styles](#output-styles)
- [Monitors](#monitors)
- [Environment Variables](#environment-variables)
- [Persistent Data Directory](#persistent-data-directory)
- [Plugin Loading and Caching](#plugin-loading-and-caching)
- [CLI Management](#cli-management)
- [Component Interaction Matrix](#component-interaction-matrix)
- [Debugging and Troubleshooting](#debugging-and-troubleshooting)

---

## Plugin Manifest

The manifest is at `.claude-plugin/plugin.json`:

```json
{
  "name": "conductor",
  "description": "Spec-Driven Development Orchestration Plugin for Claude Code",
  "version": "1.0.0",
  "author": {
    "name": "Hannibal"
  }
}
```

### Manifest fields used

| Field | Value | Notes |
|-------|-------|-------|
| `name` | `"conductor"` | Unique kebab-case identifier. Components are namespaced as `conductor:skill-name` in the UI. |
| `description` | `"Spec-Driven Development Orchestration Plugin for Claude Code"` | Shown in `/plugin` UI. |
| `version` | `"1.0.0"` | Semantic version. Users only get updates when this is bumped. |
| `author.name` | `"Hannibal"` | Author metadata. |

### Manifest fields not used

The following fields are omitted — Claude Code uses defaults:

| Field | Default behavior |
|-------|-----------------|
| `skills` | Auto-discovers `skills/` directory |
| `commands` | Auto-discovers `commands/` directory |
| `agents` | Auto-discovers `agents/` directory |
| `hooks` | Auto-discovers `hooks/hooks.json` |
| `mcpServers` | No MCP servers configured |
| `lspServers` | No LSP servers configured |
| `outputStyles` | Auto-discovers `output-styles/` directory |
| `experimental.monitors` | Auto-discovers `monitors/monitors.json` |
| `dependencies` | No plugin dependencies |
| `userConfig` | No user-configurable options at enable time |
| `channels` | No message channels |

---

## Plugin Directory Structure

```
conductor-plugin/
├── .claude-plugin/
│   └── plugin.json              # Plugin manifest
│
├── agents/                       # 9 subagent definitions
│   ├── task-executor.md
│   ├── code-reviewer.md
│   ├── explorer.md
│   ├── phase-checker.md
│   ├── doc-syncer.md
│   ├── skip-analyst.md
│   ├── spec-planner.md
│   ├── spec-reviewer.md
│   └── project-analyzer.md
│
├── commands/                     # Empty (using skills/ instead)
│   └── .gitkeep
│
├── hooks/                        # Hook configuration
│   └── hooks.json                # 12 event bindings
│
├── monitors/                     # Background monitors (empty)
│   └── monitors.json
│
├── output-styles/                # Output styles (empty)
│   └── .gitkeep
│
├── scripts/                      # 17 hook scripts + shared libraries
│   ├── lib/
│   │   ├── hook_io.py
│   │   ├── logging.py
│   │   ├── env.py
│   │   ├── validation.py
│   │   ├── json_utils.py
│   │   ├── git_utils.py
│   │   └── path_utils.py
│   ├── session-start.py
│   ├── session-end.py
│   ├── ... (see hooks-reference.md)
│   └── test-all.py
│
├── skills/                       # 6 orchestrator skills
│   ├── implement/
│   │   └── SKILL.md
│   ├── new-track/
│   │   └── SKILL.md
│   ├── revert/
│   │   └── SKILL.md
│   ├── review/
│   │   └── SKILL.md
│   ├── setup/
│   │   └── SKILL.md
│   └── status/
│       └── SKILL.md
│
├── templates/                    # Template files for track scaffolding
├── runtime/                      # Runtime reference files
│   └── core-contract.md          # Core Conductor rules loaded at session start
├── schemas/                      # JSON schemas
├── themes/                       # Color themes (none)
│
├── bin/                          # Executables on PATH (none currently)
├── .claude/                      # Claude Code project settings
├── .data/                        # Runtime data (logs, session state)
│   └── logs/
├── developer/                    # Developer documentation
│   ├── guides/
│   └── reference/
├── docs/                         # User documentation
│   ├── reference/
│   └── user/
│
├── README.md
├── INDEX.md
├── CHANGELOG.md
├── LICENSE
└── settings.json                 # Plugin default settings
```

### Path behavior

Per the Claude Code plugin protocol, component path fields have different merge behavior:

| Conductor field | Behavior | Notes |
|----------------|----------|-------|
| `skills/` | Auto-discovered (adds to default) | Default `skills/` is always scanned |
| `commands/` | Auto-discovered (replaces default) | Currently empty |
| `agents/` | Auto-discovered (replaces default) | All 9 agents discovered automatically |
| `hooks/hooks.json` | Auto-discovered | Single hooks config file |
| `output-styles/` | Auto-discovered (replaces default) | Currently empty |
| `monitors/monitors.json` | Auto-discovered | Empty array `[]` |

---

## Skills

Skills are directories containing `SKILL.md` that create `/conductor:skill-name` shortcuts.

### Skill inventory

| Skill | Invocation | Model | Description |
|-------|-----------|-------|-------------|
| `implement` | `/conductor:implement [track_name]` | sonnet | Orchestrates track task execution via subagents |
| `new-track` | `/conductor:new-track [description]` | sonnet | Creates a new track with spec, plan, and state |
| `revert` | `/conductor:revert [scope]` | sonnet | Reverts work with state synchronization |
| `review` | `/conductor:review [track_name]` | sonnet | Reviews completed track work |
| `setup` | `/conductor:setup` | sonnet | Initializes project with Conductor environment |
| `status` | `/conductor:status [track_name]` | haiku | Displays project progress overview |

### Skill frontmatter format

Each `SKILL.md` uses this frontmatter:

```yaml
---
name: implement                          # Invocation name (becomes /conductor:implement)
description: Orchestrates track task...   # When Claude should invoke this skill
when_to_use: User wants to implement...   # Additional context for auto-dispatch
argument-hint: "[track_name]"             # Hint for user arguments
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet                             # Model override
---
```

### Skill-agent dispatch relationships

Skills dispatch agents via the `Agent` tool. The primary dispatcher is `implement`:

| Skill | Dispatches agents | Purpose |
|-------|-------------------|---------|
| `implement` | `task-executor`, `explorer`, `phase-checker`, `skip-analyst` | Core execution loop |
| `review` | `code-reviewer` | Post-implementation review |
| `new-track` | `spec-planner`, `spec-reviewer` | Spec and plan generation |
| `setup` | `project-analyzer`, `spec-planner`, `spec-reviewer` | Project initialization |

### Skill hooks

Skills can define inline hooks scoped to their lifecycle. Currently used by:

**`implement`** — defines a `Stop` hook:
```yaml
hooks:
  Stop:
    - matcher: ""
      hooks:
        - type: command
          command: "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/state-consistency-check.py\""
          timeout: 5
```

This hook is automatically converted to `SubagentStop` when the skill runs inside a subagent.

---

## Agents

Agents are Markdown files in `agents/` that define specialized subagents. Claude invokes them automatically via the `Agent` tool based on task context.

### Agent inventory

| Agent | Model | Effort | Max turns | Tools | Description |
|-------|-------|--------|-----------|-------|-------------|
| `task-executor` | sonnet | high | 50 | Bash, Read, Edit, Write, Grep, Glob, NotebookEdit | Executes a single track task via TDD workflow |
| `code-reviewer` | sonnet | xhigh | 30 | Bash, Read, Grep, Glob | Deep code analysis on track implementation |
| `explorer` | haiku | medium | 25 | Bash, Read, Grep, Glob | Read-only code exploration, produces `exploration.md` |
| `phase-checker` | sonnet | high | 30 | Bash, Read, Edit, Write, Grep, Glob, AskUserQuestion | Phase checkpoint verification protocol |
| `doc-syncer` | haiku | medium | 40 | Bash, Read, Edit, Write, Grep, Glob, AskUserQuestion | Documentation synchronization after track completion |
| `skip-analyst` | haiku | low | 15 | Read, Grep, Glob | Analyzes whether a failed task can be safely skipped |
| `spec-planner` | haiku | medium | 30 | Read, Write, Grep, Glob | Generates `spec.md` and `plan.md` |
| `spec-reviewer` | haiku | medium | 30 | Read, Edit, Write, AskUserQuestion | Interactive review of spec and plan |
| `project-analyzer` | sonnet | — | — | Bash, Read, Grep, Glob | Brownfield project tech stack analysis |

### Agent frontmatter format

```yaml
---
name: task-executor
description: Executes a single track task via TDD workflow (Steps 3-8)...
tools: Bash, Read, Edit, Write, Grep, Glob, NotebookEdit
model: sonnet
effort: high
maxTurns: 50
permissionMode: acceptEdits
hooks:
  PostToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/on-test-run.py\""
---
```

### Supported frontmatter fields

| Field | Description | Conductor usage |
|-------|-------------|-----------------|
| `name` | Agent identifier (used for dispatch) | All agents |
| `description` | When Claude should invoke this agent | All agents |
| `model` | Model override (`haiku`, `sonnet`, `opus`) | All agents |
| `effort` | Effort level (`low`, `medium`, `high`, `xhigh`) | Most agents |
| `maxTurns` | Maximum tool-use turns | Most agents |
| `tools` | Allowed tools (whitelist) | All agents |
| `permissionMode` | Permission mode override | `task-executor` (acceptEdits), `explorer` (plan) |
| `hooks` | Inline hooks scoped to agent lifecycle | `task-executor`, `code-reviewer`, `phase-checker` |

### Unsupported fields for plugin agents

Per the Claude Code plugin protocol, the following fields are **ignored** for plugin agents (they generate warnings at load time):

| Ignored field | Warning message |
|---------------|-----------------|
| `permissionMode` | `Plugin agent file ... sets permissionMode, which is ignored for plugin agents` |
| `hooks` | `Plugin agent file ... sets hooks, which is ignored for plugin agents` |

These fields work for agents in `.claude/agents/` but not for plugin-shipped agents. Despite the warnings, the hooks still function when defined in `hooks/hooks.json` with `SubagentStart`/`SubagentStop` matchers.

### Agent isolation levels

| Agent | Access level | Can modify files |
|-------|-------------|-----------------|
| `task-executor` | Full (acceptEdits) | Yes — implementation code |
| `code-reviewer` | Read-only for app code | Only review artifacts |
| `explorer` | Read-only | No — produces `exploration.md` only |
| `phase-checker` | Full | Yes — creates tests, writes checkpoints |
| `doc-syncer` | Full (with confirmation) | Yes — updates documentation |
| `skip-analyst` | Read-only | No |
| `spec-planner` | Write only | Yes — creates `spec.md`, `plan.md` |
| `spec-reviewer` | Edit/Write | Yes — revises spec and plan |
| `project-analyzer` | Read-only | No |

### Agent result formats

Each agent produces a structured result block that the `filter-subagent-output.py` hook extracts:

| Agent | Result delimiter | Format |
|-------|-----------------|--------|
| `task-executor` | `---TASK RESULT---` / `---END RESULT---` | Task outcome, SHA, status |
| `code-reviewer` | `---REVIEW RESULT---` / `---END REVIEW RESULT---` | Findings, verdict |
| `explorer` | `---TASK RESULT---` / `---END RESULT---` | Exploration summary |
| `phase-checker` | `---CHECKPOINT RESULT---` / `---END RESULT---` | Checkpoint status |
| `doc-syncer` | `---DOC SYNC RESULT---` / `---END RESULT---` | Doc update summary |
| `spec-planner` | `---SPEC PLAN RESULT---` / `---END SPEC PLAN RESULT---` | Spec/plan paths |
| `spec-reviewer` | `---REVIEW RESULT---` / `---END REVIEW RESULT---` | Review outcome |
| `project-analyzer` | `---ANALYSIS RESULT---` / `---END RESULT---` | Project analysis |

---

## Hooks

Hooks are event handlers that respond to Claude Code lifecycle events. The full reference is in [hooks-reference.md](./hooks-reference.md).

### Configuration location

```
hooks/hooks.json
```

### Event coverage

Conductor registers hooks for 12 of the 25 available Claude Code events:

| Event | Script | Async | Purpose |
|-------|--------|-------|---------|
| `SessionStart` | `session-start.py` | No | Load core-contract.md, session handoff |
| `SessionEnd` | `session-end.py` | No | Cleanup, validation, metrics |
| `InstructionsLoaded` | `enhance-conductor-context.py` | No | Audit logging |
| `PreToolUse` | `pre-command-check.py` | No | Block dangerous git ops |
| `PostToolUse` (Agent) | `filter-subagent-output.py` | No | Filter subagent output |
| `PostToolUse` (Agent) | `on-subagent-result.py` | No | Recovery context injection |
| `PostToolUse` (Bash) | `on-test-run.py` | No | TDD guidance on test failure |
| `PostToolBatch` | `on-batch-complete.py` | No | Batch analysis, coverage gate |
| `SubagentStart` | `on-subagent-start.py` | No | Role-specific reminders |
| `SubagentStop` | `on-subagent-stop.py` | Mixed | Failure detection and recovery |
| `SubagentStop` | `on-phase-checkpoint-stop.py` | No | Checkpoint logging |
| `SubagentStop` | `on-review-stop.py` | Yes | Review logging |
| `TaskCreated` | `on-task-event.py` | Yes | Lifecycle logging |
| `TaskCompleted` | `on-task-event.py` | Yes | Lifecycle logging |
| `ConfigChange` | `on-config-change.py` | No | Config validation |
| `CwdChanged` | `on-cwd-change.py` | No | Conductor awareness |
| `PreCompact` | `on-compact.py` | No | Compression priority |
| `Stop` | `state-consistency-check.py` | No | State consistency, handoff |

### Hook output protocol

All scripts follow the Claude Code hook protocol:

- **Input**: JSON on stdin with `hook_event_name`, `session_id`, `cwd`, and event-specific fields
- **Output**: JSON on stdout with optional `hookSpecificOutput`, `decision`, `systemMessage`
- **Exit 0**: Success, stdout parsed
- **Exit 2**: Blocking error, stderr shown as error

Key rule: **Only emit `hookSpecificOutput` when event-specific fields are present**. Events like `InstructionsLoaded`, `SessionEnd`, and `CwdChanged` do not support it — output `{}` instead.

---

## Output Styles

Output styles are Markdown files in `output-styles/` that define how Claude formats responses.

**Current status**: Empty (`.gitkeep` only). No custom output styles defined.

To add an output style, create a `.md` file:

```
output-styles/
└── terse.md          # A terse output style definition
```

---

## Monitors

Monitors are background processes that run for the lifetime of a session and deliver stdout lines to Claude as notifications.

**Current status**: Empty array `[]` in `monitors/monitors.json`. No monitors defined.

To add a monitor:

```json
[
  {
    "name": "conductor-state-watch",
    "command": "inotifywait -m -e modify -e create -e delete --format '%w%f %e' conductor/",
    "description": "Watch conductor directory for state changes"
  }
]
```

Monitor fields:

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique identifier within the plugin |
| `command` | Yes | Shell command run as persistent background process |
| `description` | Yes | Short summary shown in task panel |
| `when` | No | `"always"` (default) or `"on-skill-invoke:<skill-name>"` |

Monitors require Claude Code v2.1.105 or later.

---

## Environment Variables

### Variables provided by Claude Code

These are available to all hook scripts, MCP servers, and monitor commands:

| Variable | Description | Conductor usage |
|----------|-------------|-----------------|
| `CLAUDE_PLUGIN_ROOT` | Absolute path to plugin installation directory | All hook scripts (via `${CLAUDE_PLUGIN_ROOT}` in `hooks.json`) |
| `CLAUDE_PLUGIN_DATA` | Persistent data directory (`~/.claude/plugins/data/conductor/`) | `session-end.py`, `state-consistency-check.py` |
| `CLAUDE_PROJECT_DIR` | Project root directory | Available but not directly used |
| `CLAUDE_SESSION_ID` | Current session identifier | Logging |
| `CLAUDE_CODE_REMOTE` | `"true"` in remote web environments | `env.py` detection |
| `CLAUDE_EFFORT` | Active effort level | `env.py` compact mode detection |
| `CLAUDE_ENV_FILE` | File for persisting env vars across Bash commands | Available for SessionStart hooks |

### Variables used by conductor scripts

Scripts access these via `lib/env.py`:

```python
from lib.env import get_plugin_root, get_data_dir, get_logs_dir

plugin_root = get_plugin_root()   # ${CLAUDE_PLUGIN_ROOT} or fallback
data_dir = get_data_dir()         # ${CLAUDE_PLUGIN_DATA} or .data/
logs_dir = get_logs_dir()         # .data/logs/
```

### Variable substitution in hooks.json

Hook commands support inline variable substitution:

```json
{
  "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/session-start.py\""
}
```

| Variable | Substituted by | Where |
|----------|---------------|-------|
| `${CLAUDE_PLUGIN_ROOT}` | Plugin installation path | All hook commands |
| `${CLAUDE_PLUGIN_DATA}` | Persistent data path | Hook commands, MCP configs |
| `${CLAUDE_PROJECT_DIR}` | Project root | Hook commands |

---

## Persistent Data Directory

`${CLAUDE_PLUGIN_DATA}` resolves to `~/.claude/plugins/data/conductor/`. This directory survives plugin updates and is deleted on uninstall.

### Directory layout

```
~/.claude/plugins/data/conductor/
├── logs/
│   ├── session-lifecycle.log       # Session start/end events
│   ├── session-metrics.log         # Session duration tracking
│   ├── subagent-failures.log       # Subagent failure detection
│   ├── on-batch-complete.log       # Batch analysis metrics
│   ├── on-task-event.log           # Task lifecycle events
│   ├── on-config-change.log        # Configuration changes
│   ├── on-cwd-change.log           # Directory changes
│   ├── on-test-run.log             # Test command results
│   ├── on-subagent-stop.log        # Subagent stop events
│   ├── on-phase-checkpoint-stop.log # Checkpoint completions
│   ├── on-review-stop.log          # Review completions
│   ├── enhance-conductor-context.log # Instruction load audit
│   └── cleanup.log                 # Temp file cleanup
├── tmp/                            # Temporary files (auto-cleaned after 24h)
└── session-handoff.md              # State for next session recovery
```

### Plugin root vs data directory

| Directory | Path | Survives updates | Purpose |
|-----------|------|-------------------|---------|
| `${CLAUDE_PLUGIN_ROOT}` | `~/.claude/plugins/cache/conductor/.../` | No | Scripts, configs, templates |
| `${CLAUDE_PLUGIN_DATA}` | `~/.claude/plugins/data/conductor/` | Yes | Logs, state, cached data |

The plugin root changes on every update. Never write persistent state there — use `${CLAUDE_PLUGIN_DATA}` instead.

---

## Plugin Loading and Caching

### Inline plugin (current mode)

Conductor is loaded via `claude --plugin-dir`:

```bash
claude --plugin-dir /path/to/conductor-plugin
```

Inline plugins load directly from the specified directory without copying to cache. This is the development mode — changes to scripts are reflected immediately without reinstalling.

### Marketplace plugin (future)

When installed from a marketplace, Claude Code copies the plugin to `~/.claude/plugins/cache/`. Each version gets a separate directory. Old versions are cleaned up after 7 days.

### Path traversal limitation

Installed (marketplace) plugins cannot reference files outside their directory. This does not apply to inline plugins loaded with `--plugin-dir`.

### Session startup sequence

1. Claude Code discovers `.claude-plugin/plugin.json`
2. Auto-scans `skills/`, `agents/`, `hooks/hooks.json`, `output-styles/`, `monitors/monitors.json`
3. Registers skills (6), agents (9), and hooks (17 from 5 plugins merged)
4. Fires `SessionStart` hooks — `session-start.py` loads `runtime/core-contract.md`
5. Fires `InstructionsLoaded` hooks — `enhance-conductor-context.py` logs loads
6. Plugin is ready

---

## CLI Management

### Inline plugin (development)

```bash
# Load as inline plugin for current session
claude --plugin-dir /path/to/conductor-plugin

# Load with debug logging
claude --plugin-dir /path/to/conductor-plugin --debug-file /tmp/debug.txt
```

### Marketplace plugin (installed)

```bash
# Install from marketplace
claude plugin install conductor@marketplace-name

# Install to project scope (shared with team)
claude plugin install conductor@marketplace-name --scope project

# Check status
claude plugin list

# Update to latest version
claude plugin update conductor

# Disable without uninstalling
claude plugin disable conductor

# Re-enable
claude plugin enable conductor

# Uninstall (deletes data directory)
claude plugin uninstall conductor

# Uninstall but keep data
claude plugin uninstall conductor --keep-data

# Create release tag
claude plugin tag --push

# Validate manifest and components
claude plugin validate
```

### Installation scopes

| Scope | Settings file | Use case |
|-------|--------------|----------|
| `user` | `~/.claude/settings.json` | Personal plugins (default) |
| `project` | `.claude/settings.json` | Team plugins (committable) |
| `local` | `.claude/settings.local.json` | Project-specific (gitignored) |

---

## Component Interaction Matrix

How Conductor's plugin components communicate:

```
User prompt ("implement feature X")
       │
       ▼
┌─────────────────────┐
│  Skill: implement    │  Reads track-state.json, dispatches agents
│  /conductor:implement│
└─────────┬───────────┘
          │ Agent tool calls
          ▼
┌─────────────────────┐
│  Agent: task-executor│  TDD workflow, modifies code
└─────────┬───────────┘
          │ Hook events fire
          ▼
┌─────────────────────────────────────────────┐
│  Hooks (automatic, event-driven)            │
│                                             │
│  PreToolUse  → Block dangerous commands     │
│  PostToolUse → Filter output, log tests     │
│  PostToolBatch → Coverage gate (F3)         │
│  SubagentStart → Inject role reminders      │
│  SubagentStop  → Detect failures, recover   │
│  Stop → State consistency, write handoff    │
└─────────────────────────────────────────────┘
          │ File I/O
          ▼
┌─────────────────────────────────────────────┐
│  State Layer                                │
│                                             │
│  track-state.json  → Authoritative state    │
│  plan.md           → Task checklist         │
│  session-handoff.md → Cross-session state   │
│  .data/logs/       → Audit trail            │
└─────────────────────────────────────────────┘
```

### Component communication protocols

| From | To | Protocol | Direction |
|------|----|----------|-----------|
| Skills | Subagents | Agent tool dispatch | Skill → Agent |
| Subagents | Skills | `---RESULT---` blocks | Agent → Skill |
| Hooks | Runtime | JSON stdin/stdout | Runtime ↔ Hook |
| Hooks | Filesystem | File read/write | Hook → Files |
| Skills | `track-state` CLI | Shell commands | Skill → CLI |
| All | `track-state.json` | File I/O | All → State |

---

## Debugging and Troubleshooting

### Debug mode

```bash
claude --plugin-dir /path/to/conductor-plugin --debug-file /tmp/debug.txt
```

Look for these log lines:

```
[DEBUG] Loaded hooks from standard location for plugin conductor: .../hooks/hooks.json
[DEBUG] Loaded 9 agents from plugin conductor default directory
[DEBUG] Loaded 6 skills from plugin conductor default directory
[DEBUG] Registered 17 hooks from 5 plugins
```

### Common issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Skills not appearing | Wrong directory structure | Ensure `skills/` is at plugin root, not inside `.claude-plugin/` |
| Agents showing warnings | `permissionMode` or `hooks` in frontmatter | These fields are ignored for plugin agents. Use `hooks/hooks.json` instead. |
| Hooks not firing | Script not executable | `chmod +x scripts/*.py` |
| Hooks validation failing | `hookSpecificOutput` for unsupported event | Only emit `hookSpecificOutput` when event-specific fields are present. See [hooks-reference.md](./hooks-reference.md). |
| `CLAUDE_PLUGIN_ROOT` not resolving | Missing from environment | Ensure plugin is loaded via `--plugin-dir` or marketplace install |
| Stale hook scripts after update | Mid-session update keeps old paths | Run `/reload-plugins` or restart session |
| `core-contract.md` not loading | File missing from `runtime/` | Check `runtime/core-contract.md` exists |

### Validate plugin

```bash
claude plugin validate
```

Checks `plugin.json` syntax, skill/agent/command frontmatter, and `hooks/hooks.json` schema.

### Manual hook testing

See [hooks-reference.md > Testing](./hooks-reference.md#testing) for complete instructions on testing individual hook scripts via stdin.

### Log inspection

```bash
# View session lifecycle
cat .data/logs/session-lifecycle.log

# Check for subagent failures
cat .data/logs/subagent-failures.log

# Monitor test results
tail -f .data/logs/on-test-run.log
```

---

**Last Updated**: 2026-05-12
