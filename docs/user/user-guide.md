---
title: User Guide
audience: user
status: stable
last_updated: 2026-05-11
related:
  - getting-started.md
  - commands.md
  - troubleshooting.md
---

# User Guide

> Complete guide to using Conductor for spec-driven development

---

## Table of Contents

1. [Introduction](#introduction)
2. [Project Setup](#project-setup)
3. [Track Management](#track-management)
4. [Task Execution](#task-execution)
5. [Quality Gates](#quality-gates)
6. [Best Practices](#best-practices)
7. [Advanced Topics](#advanced-topics)

---

## Introduction

Conductor is a spec-driven development orchestration plugin that helps you:

- **Define specifications** with clear acceptance criteria
- **Break down work** into manageable phases and tasks
- **Execute tasks** with enforced TDD workflow
- **Track progress** with state management
- **Review quality** with automated checks
- **Maintain audit trail** with git notes

### Key Concepts

- **Track**: A complete feature development unit
- **Phase**: A logical grouping of related tasks
- **Task**: A single unit of work with clear acceptance criteria
- **Subagent**: Specialized AI agent for task execution
- **Hook**: Event-driven automation script

---

## Project Setup

### First-Time Setup

```
> /conductor:setup
```

The setup wizard guides you through:

#### 1. Project Analysis

Conductor analyzes your project:
- Detects existing tech stack
- Identifies architecture patterns
- Scans for existing documentation

#### 2. Product Definition

Define your product:
- Product name and purpose
- Target users and use cases
- Product guidelines and constraints

#### 3. Technology Stack

Select or confirm:
- Programming languages
- Frameworks and libraries
- Build and deployment tools
- Testing frameworks

#### 4. Workflow Configuration

Configure:
- Code style guides per language
- Testing strategy and conventions
- Development commands

#### 5. Initial Track

Create your first track:
- Define initial feature scope
- Generate spec.md and plan.md
- Initialize track-state.json

---

## Track Management

### Creating a New Track

```
> /conductor:newTrack User registration flow
```

The interactive workflow:

1. **Document Context Loading**
   - Conductor scans your project
   - Loads relevant documentation
   - Prepares context for spec generation

2. **Requirements Gathering**
   - Functional requirements (what it does)
   - Non-functional requirements (how well it does it)
   - Constraints and dependencies

3. **Specification Generation**
   - Automatically generates spec.md
   - Includes acceptance criteria (ACs)
   - Includes test cases (TCs)
   - Defines out-of-scope items

4. **Interactive Review**
   - Review generated specification
   - Make revisions as needed
   - Approve or regenerate

5. **Plan Generation**
   - Breaks spec into phases
   - Creates task breakdown
   - Assigns tags to tasks

6. **Execution Mode Selection**

| Mode | Behavior |
|------|----------|
| **interactive** | Pauses for confirmation at phase checkpoints |
| **continuous** | Auto-proceeds through all phases |

7. **Track Initialization**
   - Creates track-state.json
   - Commits all artifacts

### Track Status

```
> /conductor:status
```

Displays:
- All tracks with their status
- Phase progress for active tracks
- Task-level details
- Issues and warnings

### Track Lifecycle

```
new → in_progress → completed → archived
         ↓            ↓
       blocked      cancelled
```

---

## Task Execution

### Running Implement

```
> /conductor:implement
```

If multiple tracks are active, you'll be prompted to select one.

### The Execution Loop

```
┌─────────────────────────────────┐
│  1. Load State              │
│     track-state recover       │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  2. Select Next Task       │
│     track-state next         │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  3. Dispatch Subagent      │
│     Based on task type      │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  4. Process Result         │
│     track-state process-result│
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  5. Sync Plan             │
│     Update plan.md markers   │
└──────────────┬──────────────┘
               │
               ▼
         Continue or Done?
         /            \
      Yes              No
       │                │
       ▼                ▼
    To Step 1      Finalize Track
```

### Task Types and Their Subagents

| Task Type | Tag | Subagent | Workflow |
|-----------|------|-----------|-----------|
| Investigation | `[Explore]` | explorer | Read-only code analysis |
| Implementation | (default) | task-executor | TDD workflow |
| Verification | (phase end) | phase-checker | Checkpoint protocol |
| Documentation | `[Docs]` | task-executor | Doc-only changes |
| Configuration | `[Config]` | task-executor | Config-only changes |
| Maintenance | `[Chore]` | task-executor | Maintenance tasks |
| Manual | `[Manual]` | (deferred) | Human verification |

---

## Quality Gates

Conductor enforces 6 quality gates:

### F1 - Global State Lock

**Rule**: Only one task can be in progress at a time.

**Why**: Prevents concurrent conflicts and state corruption.

**Enforcement**: `pre-command-check.py` validates before git operations.

---

### F2 - TDD Gate

**Rule**: Write failing test before implementation code.

**Why**: Ensures test-driven development and comprehensive test coverage.

**Exemptions**: `[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, `[Manual]` tasks.

**Enforcement**: `track-state process-result` checks commit for test files.

---

### F3 - Coverage Gate

**Rule**: No commit if code coverage < 80%.

**Why**: Maintains code quality and regression prevention.

**Exemptions**: Tasks producing no code.

**Enforcement**: `track-state process-result` runs coverage tool.

---

### F4 - SHA Must Exist

**Rule**: All completed tasks must have commit SHA appended.

**Why**: Ensures traceability and auditability.

**Format**: `- [x] Task description [a1b2c3d]`

**Enforcement**: `lint-track-state.py` validates plan.md.

---

### F5 - Checkpoint Integrity

**Rule**: Phase checkpoint required when phase completes.

**Why**: Provides natural review points and ensures quality.

**Enforcement**: `track-state phase-done` detects phase completion.

---

### F6 - Context Guard

**Rule**: Never skip workflow steps.

**Why**: Prevents incomplete implementation and quality gaps.

**Enforcement**: Orchestrator validates before execution.

---

## Best Practices

### Specification Writing

1. **Be Specific**: Clear, unambiguous requirements
2. **Define ACs**: Acceptance criteria that can be verified
3. **Include TCs**: Test cases for each AC
4. **Scope Boundaries**: Explicitly state out-of-scope items

### Plan Structuring

1. **Logical Phases**: Group related tasks together
2. **Task Granularity**: Tasks should take 1-2 hours
3. **Use Tags**: Mark tasks with appropriate types
4. **Dependencies**: Order tasks by dependency

### Task Execution

1. **Let Subagents Work**: Don't intervene unless necessary
2. **Review Results**: Check subagent output for issues
3. **Handle Failures**: Use `/conductor:status` to diagnose
4. **Complete Phase**: Always complete checkpoints

### Code Review

1. **Run Before Merge**: Always run `/conductor:review` before merging
2. **Address Issues**: Fix all critical and major issues
3. **Update Specs**: If implementation changed requirements, update spec.md

---

## Advanced Topics

### Recovery After Interruption

If a session is interrupted:

1. **Run `/conductor:implement`**
2. Session handoff is loaded automatically
3. Stale locks are detected and reported
4. Choose to recover or clean up

### Handling Blocked Tasks

When a task is blocked:

1. **Identify Blocker**: Review blocking reason
2. **Resolve Blocker**: Remove dependency or find workaround
3. **Reset Task**: Use `/conductor:revert task <name>`
4. **Resume**: Run `/conductor:implement`

### Customizing Workflow

Modify workflow templates in `conductor/workflow/`:

- `task-workflow.md`: Custom task execution steps
- `phase-checkpoint.md`: Custom verification protocol
- `code-styleguides/`: Add language-specific guides

### Extending Conductor

Add custom hooks in `hooks/hooks.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/custom-hook.py\""
          }
        ]
      }
    ]
  }
}
```

---

## Next Steps

- [Commands Reference](commands.md) - Command details
- [Architecture Overview](../architecture/overview.md) - System architecture
- [Troubleshooting](troubleshooting.md) - Common issues

---

**Last Updated**: 2026-05-11
