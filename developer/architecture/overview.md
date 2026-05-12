---
title: Architecture Overview
audience: developer
status: stable
last_updated: 2026-05-11
related:
  - state-model.md
  - INTERACTION_FLOW.md
  - INTERACTION_MECHANISM.md
---

# Architecture Overview

> System architecture and design principles of Conductor

---

## High-Level Architecture

Conductor follows a **Three-Tier Interaction Model**:

```
┌─────────────────────────────────────────────────────────┐
│                  Orchestrator Layer                    │
│              (Skills + State Management)                │
└──────────────────────┬────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼             ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  Skills      │ │  Subagents   │ │  Templates   │
│  (Commands)  │ │  (Agents)    │ │  + Styles    │
└──────────────┘ └──────────────┘ └──────────────┘
       │               │               │
       └──────────────┴──────────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
┌─────────────┐ ┌──────────┐ ┌──────────┐
│   Hooks     │ │   State   │ │   Git    │
│ (Lifecycle) │ │  (track- │ │  Notes   │
│             │ │  state)  │ │ (Audit)  │
└─────────────┘ └──────────┘ └──────────┘
```

---

## Design Principles

| Principle | Description |
|-----------|-------------|
| **Orchestrator-Subagent Pattern** | Orchestrator manages state and dispatches tasks; subagents focus on single-responsibility execution |
| **Context Isolation** | State mutations via CLI scripts; subagents self-extract ACs/specs; phase checkpoints run in isolated subagent context |
| **TDD Enforcement** | Mandatory Red-Green-Refactor cycle - no implementation code without a failing test |
| **Single State Lock** | Only one task may be `in_progress` globally |
| **Audit Trail** | Every task commit gets a human-readable git note |
| **Spec-Driven** | From PRD to spec.md to plan.md - specifications drive implementation |

---

## Component Layers

### Layer 1: Skills (User Interface)

Skills are the primary user-facing command interface.

| Skill | Purpose | Key Responsibilities |
|--------|---------|-------------------|
| `setup` | Initialize project environment | Scaffolding, track creation, subagent dispatch |
| `newTrack` | Create new feature track | Requirements gathering, spec/plan generation |
| `implement` | Orchestrate task execution | State machine loop, subagent dispatch, result processing |
| `status` | Display project progress | State aggregation, status computation |
| `review` | Code review orchestration | Subagent dispatch, quality verification |
| `revert` | Safe rollback | State synchronization, git operations |

### Layer 2: Subagents (Execution)

Subagents are specialized AI agents that execute specific tasks in isolated context.

| Subagent | Model | Tools | Purpose |
|-----------|--------|--------|---------|
| `task-executor` | Sonnet | Bash, Read, Edit, Write, Grep, Glob | TDD workflow execution |
| `explorer` | Haiku | Bash, Read, Grep, Glob | Read-only codebase investigation |
| `phase-checker` | Sonnet | Bash, Read, Edit, Write, Grep, Glob, AskUserQuestion | Phase verification protocol |
| `code-reviewer` | Sonnet | Bash, Read, Grep, Glob | Deep code review |
| `skip-analyst` | Haiku | Read, Grep, Glob | Failed task analysis |
| `spec-planner` | Sonnet | Bash, Read, Edit, Write | Spec/plan generation |
| `spec-reviewer` | Sonnet | Read, Write | Interactive spec review |
| `doc-syncer` | Sonnet | Bash, Read, Edit, Write | Documentation sync |
| `project-analyzer` | Sonnet | Bash, Read, Grep, Glob | Project structure analysis |

### Layer 3: Hooks (Lifecycle Management)

Hooks are event-driven scripts that execute at specific lifecycle points.

| Hook Type | Trigger | Key Scripts |
|-----------|---------|-------------|
| `SessionStart` | Session begins/resumes | `session-start.py` |
| `SessionEnd` | Session terminates | `session-end.py` |
| `PreToolUse` | Before tool execution | `pre-command-check.py` |
| `PostToolUse` | After tool success | `filter-subagent-output.py`, `on-test-run.py` |
| `PostToolBatch` | After parallel tools resolve | `on-batch-complete.py` |
| `SubagentStart` | Subagent spawns | `on-subagent-start.py` |
| `SubagentStop` | Subagent finishes | `on-subagent-stop.py` |

---

## State Authority Model

```
┌─────────────────────────────────────────┐
│     Authoritative Source               │
│    track-state.json                   │
└────────────┬────────────────────────┘
             │
    ┌────────┼─────────┐
    ▼        ▼         ▼
┌────────┐ ┌──────┐ ┌─────────┐
│ plan.md │ │check-│ │tracks.md│
│(projection)│list│ │registry │
└────────┘ └──────┘ └─────────┘
```

**Principles:**
1. `track-state.json` is always the source of truth
2. Orchestrator never reads/writes state JSON directly
3. All mutations go through `track-state` CLI
4. Plan.md is a human-readable projection, synced automatically
5. Git notes provide immutable audit trail

---

## Communication Flow

### Session Initialization

```
User Command
    ↓
SessionStart Hook
    ↓
Load conductor-core.md + session handoff
    ↓
Skill dispatches
```

### Task Execution

```
Orchestrator selects task
    ↓
track-state next (get next task)
    ↓
PreToolUse Hook (validate command)
    ↓
SubagentStart Hook (inject reminder)
    ↓
Subagent executes (self-loads context)
    ↓
SubagentStop Hook (check failures)
    ↓
PostToolUse Hook (filter output)
    ↓
track-state process-result (update state)
    ↓
Sync plan.md + write git notes
```

---

## Quality Gates

| Gate | Rule | Exempt Tags |
|------|------|-------------|
| **F1** Global State Lock | Only one `[~]` task allowed globally | - |
| **F2** TDD | Test files must exist in commit | `[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, `[Manual]` |
| **F3** Coverage | `coverage_pct >= 80%` | `[Docs]`, `[Config]`, `[Chore]`, `[Manual]` |
| **F4** SHA Must Exist | All terminal markers must have commit SHA | `[ ]`, `[~]` |
| **F5** Checkpoint Integrity | Phase checkpoint mandatory at phase boundaries | - |
| **F6** Context Guard | Never skip workflow steps | - |

---

## Next Steps

- [Interaction Mechanism](INTERACTION_MECHANISM.md) - Deep dive into three-tier model
- [Interaction Flow](INTERACTION_FLOW.md) - Visual flowcharts
- [Interaction Reference](INTERACTION_REFERENCE.md) - Quick reference

---

**Last Updated**: 2026-05-11
