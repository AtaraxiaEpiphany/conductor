# Conductor Plugin Documentation Index

> Spec-Driven Development Orchestration Plugin for Claude Code

---

## 🚀 Quick Start

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Get up and running with Conductor in 5 minutes |
| [User Guide](docs/user-guide.md) | Complete usage guide |
| [Command Reference](docs/commands.md) | Detailed command reference |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and solutions |

---

## 🏗️ Architecture

| Document | Description |
|----------|-------------|
| [Architecture Overview](architecture/overview.md) | System architecture overview |
| [Interaction Mechanism](architecture/INTERACTION_MECHANISM.md) | Deep dive into Skills, Subagents, and Hooks communication |
| [Interaction Flow](architecture/INTERACTION_FLOW.md) | 8 visual flowcharts |
| [State Model](architecture/state-model.md) | State machine and state management |

---

## 📖 Reference

| Document | Description |
|----------|-------------|
| [Interaction Reference](architecture/INTERACTION_REFERENCE.md) | Quick reference for all interaction points |
| [Hook Reference](reference/hooks.md) | Hook events, configuration, and implementation |
| [Subagent Reference](reference/subagents.md) | All subagent definitions and purposes |
| [track-state CLI](reference/track-state-cli.md) | Complete CLI command reference |
| [Quality Gates](reference/quality-gates.md) | F1-F6 rules explained |
| [Git Notes Audit](reference/git-notes.md) | Audit system and query tools |

---

## 🔒 Internal

| Document | Description |
|----------|-------------|
| [System Prompt](internal/conductor-core.md) | Orchestrator agent core rules |
| [Orchestration Layer](internal/conductor-orchestration.md) | Orchestration Layer specification |
| [Reference Layer](internal/conductor-reference.md) | Reference Layer specification |
| [Hook Implementation](internal/hooks-implementation.md) | Hook script implementation details |

---

## 📂 Directory Structure

```
conductor-plugin/
├── README.md              # Project homepage
├── INDEX.md               # This document
├── CHANGELOG.md           # Changelog
│
├── docs/                  # 👤 User Documentation
│   ├── getting-started.md
│   ├── user-guide.md
│   ├── commands.md
│   └── troubleshooting.md
│
├── architecture/          # 🏗️ Architecture Documentation
│   ├── overview.md
│   ├── INTERACTION_MECHANISM.md
│   ├── INTERACTION_FLOW.md
│   └── state-model.md
│
├── reference/             # 📖 Reference Manual
│   ├── hooks.md
│   ├── subagents.md
│   ├── track-state-cli.md
│   ├── quality-gates.md
│   └── git-notes.md
│
└── internal/             # 🔒 Internal Documentation
    ├── conductor-core.md
    ├── conductor-orchestration.md
    ├── conductor-reference.md
    └── hooks-implementation.md
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
