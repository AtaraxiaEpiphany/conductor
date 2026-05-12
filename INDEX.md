# Conductor Plugin Documentation Index

> Spec-Driven Development Orchestration Plugin for Claude Code

---

## Quick Start

| Document | Description |
|----------|-------------|
| [Getting Started](docs/user/getting-started.md) | Get up and running with Conductor in 5 minutes |
| [User Guide](docs/user/user-guide.md) | Complete usage guide |
| [Command Reference](docs/user/commands.md) | Detailed command reference |
| [Troubleshooting](docs/user/troubleshooting.md) | Common issues and solutions |

---

## Reference

| Document | Description |
|----------|-------------|
| [Subagent Reference](docs/reference/subagents.md) | All subagent definitions and purposes |
| [Hook Reference](docs/reference/hooks.md) | Hook events and configuration |
| [Quality Gates](docs/reference/quality-gates.md) | F1-F6 rules explained |

---

## Developer Documentation

> Internal implementation details for plugin developers

### Architecture

| Document | Description |
|----------|-------------|
| [Architecture Overview](developer/architecture/overview.md) | System architecture overview |
| [Interaction Mechanism](developer/architecture/INTERACTION_MECHANISM.md) | Deep dive into Skills, Subagents, and Hooks communication |
| [Interaction Flow](developer/architecture/INTERACTION_FLOW.md) | 8 visual flowcharts |
| [State Model](developer/architecture/state-model.md) | State machine and state management |

### Guides

| Document | Description |
|----------|-------------|
| [Extending Hooks](developer/guides/extending-hooks.md) | Hook script implementation details |

### Reference

| Document | Description |
|----------|-------------|
| [Interaction Reference](developer/reference/INTERACTION_REFERENCE.md) | Complete interaction reference |
| [track-state CLI](developer/reference/track-state-cli.md) | Complete CLI command reference |
| [Git Notes Audit](developer/reference/git-notes.md) | Audit system and query tools |
| [Hooks Reference](developer/reference/hooks-reference.md) | Hook scripts, I/O protocol, shared library API |
| [Agents Reference](developer/reference/agents-reference.md) | Subagent definitions, dispatch patterns, result formats |
| [Skills Reference](developer/reference/skills-reference.md) | Orchestrator skills, execution workflows, state management |
| [Plugins Reference](developer/reference/plugins-reference.md) | Manifest, directory structure, CLI management |
| [Harness Engineering](developer/reference/harness-engineering.md) | Anthropic/OpenAI/Martin Fowler best practices |

---

## Runtime (Injected)

| Document | Description | Injected By |
|----------|-------------|-------------|
| [Core Contract](runtime/core-contract.md) | Orchestrator agent core rules | session-start.py |
| [Orchestration Specification](runtime/orchestration-spec.md) | Dispatch loop, subagent registry | inline (implement skill) |
| [Reference Paths](runtime/reference-paths.md) | Default paths for project initialization | inline (setup/new-track skills) |

---

## Directory Structure

```
conductor-plugin/
├── README.md                  # Project homepage
├── INDEX.md                   # This document
├── CHANGELOG.md               # Changelog
│
├── docs/                      # User Documentation
│   ├── user/                  #   Getting Started, User Guide, Commands, Troubleshooting
│   └── reference/             #   Hook Reference, Subagent Reference, Quality Gates
│
├── developer/                 # Developer Documentation (internal)
│   ├── architecture/          #   Architecture docs
│   ├── guides/                #   Developer guides
│   └── reference/            #   Internal reference docs
│
├── runtime/                   # Runtime Injection
│   ├── core-contract.md
│   ├── orchestration-spec.md
│   └── reference-paths.md
│
├── agents/                    # Subagent definitions
├── skills/                    # Skill definitions
├── scripts/                   # Hook scripts and utilities
├── templates/                 # Workflow templates
└── hooks/                     # Hook configuration
```

---

## Component Overview

| Component | Count | Description |
|-----------|--------|-------------|
| **Skills** | 6 | User command interfaces: setup, implement, newTrack, status, review, revert |
| **Subagents** | 9 | Specialized execution agents |
| **Hooks** | 10 | Lifecycle event handlers (8 event types) |

---

**Last Updated**: 2026-05-12
