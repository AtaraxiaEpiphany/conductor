# Conductor Plugin Documentation Index

> Spec-Driven Development Orchestration Plugin for Claude Code

---

## 🚀 Quick Start

| Document | Description |
|----------|-------------|
| [Getting Started](docs/user/getting-started.md) | Get up and running with Conductor in 5 minutes |
| [User Guide](docs/user/user-guide.md) | Complete usage guide |
| [Command Reference](docs/user/commands.md) | Detailed command reference |
| [Troubleshooting](docs/user/troubleshooting.md) | Common issues and solutions |

---

## 🏗️ Architecture (Developer)

| Document | Description |
|----------|-------------|
| [Architecture Overview](developer/architecture/overview.md) | System architecture overview |
| [Interaction Mechanism](developer/architecture/INTERACTION_MECHANISM.md) | Deep dive into Skills, Subagents, and Hooks communication |
| [Interaction Flow](developer/architecture/INTERACTION_FLOW.md) | 8 visual flowcharts |
| [State Model](developer/architecture/state-model.md) | State machine and state management |

### Developer Guides

| Document | Description |
|----------|-------------|
| [Extending Hooks](developer/guides/extending-hooks.md) | Hook script implementation details |

---

## 📖 Reference

| Document | Description |
|----------|-------------|
| [Interaction Reference](docs/reference/INTERACTION_REFERENCE.md) | Quick reference for all interaction points |
| [Hook Reference](docs/reference/hooks.md) | Hook events, configuration, and implementation |
| [Subagent Reference](docs/reference/subagents.md) | All subagent definitions and purposes |
| [track-state CLI](docs/reference/track-state-cli.md) | Complete CLI command reference |
| [Quality Gates](docs/reference/quality-gates.md) | F1-F6 rules explained |
| [Git Notes Audit](docs/reference/git-notes.md) | Audit system and query tools |

---

## 🤖 Runtime (Injected)

| Document | Description | Injected By |
|----------|-------------|-------------|
| [Core Contract](runtime/core-contract.md) | Orchestrator agent core rules (Execution Firewall, Anti-Patterns) | session-start.py |
| [Orchestration Specification](runtime/orchestration-spec.md) | Dispatch loop, subagent registry, execution modes | inline (implement skill) |
| [Reference Paths](runtime/reference-paths.md) | Default paths for project initialization | inline (setup/new-track skills) |

---

## 📂 Directory Structure

```
conductor-plugin/
├── README.md                  # Project homepage
├── INDEX.md                   # This document
├── CHANGELOG.md               # Changelog
│
├── docs/                      # 👤 User Documentation
│   ├── user/                  #   Getting Started, User Guide, Commands, Troubleshooting
│   └── reference/             #   Hook Reference, Subagent Reference, CLI Reference
│
├── developer/                 # 🛠️ Developer Documentation
│   ├── architecture/          #   Architecture docs (moved from architecture/)
│   └── guides/                #   Developer guides (moved from internal/)
│
├── runtime/                   # 🤖 Runtime Injection (moved from internal/)
│   ├── core-contract.md       #   Injected by session-start.py
│   ├── orchestration-spec.md  #   Referenced by implement skill
│   └── reference-paths.md     #   Referenced by setup/new-track skills
│
├── agents/                    # Subagent definitions
├── skills/                    # Skill definitions
├── scripts/                   # Hook scripts and utilities
├── templates/                 # Workflow templates and style guides
└── hooks/                     # Hook configuration
```

---

## 📊 Three-Layer Architecture Overview

```
┌─────────────────────────────────────────────────┐
│              Orchestrator Agent                │
│         (State, FSM, Dispatch)                │
└────────────────┬──────────────────────────────┘
                 │
      ┌──────────┼──────────┐
      ▼          ▼           ▼
   Skills    Subagents    Templates
   (6 cmds)  (9 agents)  + Styles
```

### Components

| Component | Count | Description |
|-----------|--------|-------------|
| **Skills** | 6 | User command interfaces: setup, implement, newTrack, status, review, revert |
| **Subagents** | 9 | Specialized execution agents: task-executor, explorer, phase-checker, etc. |
| **Hooks** | 10+ | Lifecycle event handlers: session-start, subagent-stop, etc. |
| **Templates** | 24+ | Workflow templates, code style guides, testing strategies |

---

## 🔗 Related Resources

- [Claude Code Plugins Reference](references/claude-docs/plugins-reference.md)
- [Claude Code Hooks Documentation](https://code.claude.com/docs/en/hooks)
- [Claude Code Subagents Documentation](https://code.claude.com/docs/en/subagents)

---

**Last Updated**: 2026-05-11
