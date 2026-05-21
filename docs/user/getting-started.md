---
title: Getting Started
audience: user
status: stable
last_updated: 2026-05-11
related:
  - user-guide.md
  - commands.md
  - troubleshooting.md
---

# Getting Started

> Get started with Conductor for Spec-Driven Development in 5 minutes

---

## Prerequisites

- [Claude Code](https://claude.ai/code) CLI or IDE extension
- Git repository

---

## Installation

### Method 1: Install from Marketplace

```bash
claude plugin install conductor
```

### Method 2: Local Installation

```bash
git clone <repo-url> ~/.claude/plugins/conductor
claude plugin install ~/.claude/plugins/conductor
```

---

## Initialize Project

Run the setup command in your project directory:

```
> /conductor:setup
```

The setup wizard will guide you through:
1. Project type detection (brownfield/greenfield)
2. Product definition
3. Technology stack selection
4. Code style guide configuration
5. Initial track creation

After initialization, the following directory structure is created:

```
your-project/
├── CLAUDE.md                          # Project instructions
├── conductor/
│   ├── index.md                       # Project context index
│   ├── overview/
│   │   ├── product.md                 # Product definition
│   │   └── product-guidelines.md      # Product guidelines
│   ├── design/
│   │   └── tech-stack.md              # Technology stack
│   ├── workflow/
│   │   ├── index.md                   # Workflow index
│   │   ├── task-workflow.md           # Task workflow
│   │   ├── phase-checkpoint.md        # Phase checkpoint protocol
│   │   └── code-styleguides/         # Code style guides
│   ├── tracks.md                      # Tracks registry
│   └── tracks/
│       └── <track_id>/
│           ├── spec.md                # Feature specification
│           ├── plan.md                # Implementation plan
│           └── track-state.json       # State file
```

---

## Create Your First Track

```
> /conductor:newTrack user login
```

Interactive workflow:
1. Scans your project for related documents
2. Collects requirements through guided Q&A
3. Auto-generates `spec.md` and `plan.md`
4. Interactive spec and plan review
5. Select execution mode (interactive/continuous)
6. Creates track-state.json and commits all artifacts

---

## Implement Feature

```
> /conductor:implement
```

The orchestrator will:
1. Load track state and recover from interruptions
2. Select the next pending task
3. Dispatch appropriate subagent
4. Execute TDD workflow
5. Update state and sync plan
6. Execute checkpoint at phase boundaries

---

## View Progress

```
> /conductor:status
```

Displays:
- Progress overview for all tracks
- Phase status
- Task-level details
- Issue highlights

---

## Core Concepts

### Track

A track is a complete development unit for a feature, containing:
- **spec.md**: Feature specification document
- **plan.md**: Implementation plan (broken into phases and tasks)
- **track-state.json**: Authoritative source of state (includes verification evidence)

### Task

A task is the smallest unit of work, with the following status markers:

| Marker | Status | Description |
|--------|--------|-------------|
| `[ ]` | pending | To be processed |
| `[~]` | in_progress | Currently running |
| `[x]` | completed | Finished |
| `[!]` | failed | Failed |
| `[>]` | skipped | Skipped |
| `[d]` | deferred | Deferred |
| `[#]` | blocked | Blocked |
| `[-]` | cancelled | Cancelled |

### Execution Firewall

All task execution must follow 6 mandatory rules:

| Rule | Severity | Description |
|------|----------|-------------|
| **F1** | Critical | Global State Lock - Only one `[~]` task allowed |
| **F2** | Critical | TDD Gate - Write failing test before implementation |
| **F3** | Warning | Coverage Gate - No commit if coverage < 80% |
| **F4** | Critical | SHA Must Exist - All terminal markers need commit SHA |
| **F5** | Warning | Checkpoint Integrity - Phase checkpoint required when phase completes |
| **F6** | Critical | Context Guard - Never skip workflow steps |

---

## Next Steps

- Read the complete [User Guide](user-guide.md)
- View [Command Reference](commands.md)
- Learn about [Architecture Overview](../developer/architecture/overview.md)

---

## FAQ

**Q: What types of projects is Conductor suitable for?**

A: Any software project using Git, especially projects requiring strict quality control and traceability.

**Q: Is TDD mandatory?**

A: TDD gate is automatically exempted for `[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, and `[Manual]` task types.

**Q: How do I skip a task?**

A: Add a marker like `[> skip reason]` to the task in plan.md, then run implement.

**Q: How to recover after interruption?**

A: Simply run `/conductor:implement`. The system will automatically resume from where it stopped.

---

**Last Updated**: 2026-05-11
