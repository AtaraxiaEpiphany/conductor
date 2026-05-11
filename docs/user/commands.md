---
title: Command Reference
audience: user
status: stable
last_updated: 2026-05-11
related:
  - ../reference/commands/
  - user-guide.md
---

# Command Reference

> Complete reference for all Conductor commands

---

## Overview

Conductor provides 6 main commands for spec-driven development:

| Command | Purpose | Usage |
|----------|---------|--------|
| `/conductor:setup` | Initialize project | First-time setup |
| `/conductor:newTrack <desc>` | Create new track | Feature development |
| `/conductor:implement [track]` | Execute tasks | Task execution |
| `/conductor:status` | View progress | Status monitoring |
| `/conductor:review [track]` | Code review | Quality verification |
| `/conductor:revert <scope>` | Safe rollback | Undo operations |

---

## /conductor:setup

Initialize Conductor environment for a project.

### Usage

```
> /conductor:setup
```

### What It Does

1. **Detects Project Type**
   - Brownfield (existing codebase)
   - Greenfield (new project)

2. **Project Analyzer**
   - Detects tech stack
   - Analyzes architecture
   - Identifies conventions

3. **Product Definition**
   - Product name and description
   - Product guidelines
   - Success criteria

4. **Technology Stack**
   - Languages and frameworks
   - Build tools
   - Testing frameworks

5. **Workflow Configuration**
   - Code style guides
   - Testing strategy
   - Dev commands

6. **Initial Track**
   - Creates first track
   - Generates spec.md and plan.md
   - Initializes track-state.json

### Generated Structure

```
your-project/
├── CLAUDE.md
├── conductor/
│   ├── index.md
│   ├── overview/
│   │   ├── product.md
│   │   └── product-guidelines.md
│   ├── design/
│   │   └── tech-stack.md
│   ├── workflow/
│   │   ├── index.md
│   │   ├── task-workflow.md
│   │   ├── phase-checkpoint.md
│   │   ├── testing/strategy.md
│   │   └── code-styleguides/
│   ├── tracks.md
│   └── tracks/
│       └── initial-track/
│           ├── spec.md
│           ├── plan.md
│           └── track-state.json
```

---

## /conductor:newTrack

Create a new feature track with spec and plan.

### Usage

```
> /conductor:newTrack user authentication with OAuth2
```

### Workflow

1. **Document Scan**
   - Scans project for related docs
   - Loads context from conductor/index.md

2. **Requirements Gathering**
   - Interactive Q&A
   - Captures functional requirements
   - Captures non-functional requirements

3. **Spec Generation**
   - `conductor:spec-planner` generates spec.md
   - Includes ACs and TCs
   - Defines out-of-scope items

4. **Spec Review**
   - `conductor:spec-reviewer` presents summary
   - Interactive revision
   - Keeps full files out of main context

5. **Plan Generation**
   - Breaks spec into phases
   - Creates task breakdown
   - Assigns tags to tasks

6. **Plan Review**
   - Interactive plan review
   - Task count validation
   - Phase distribution check

7. **Execution Mode Selection**
   - `interactive`: Pause for confirmation at checkpoints
   - `continuous`: Auto-proceed through all phases

8. **Track Initialization**
   - `track-state init` creates track-state.json
   - Commits all artifacts

### Task Type Tags

| Tag | TDD Gate | Description |
|-----|----------|-------------|
| (none) | Required | Standard TDD workflow |
| `[Explore]` | N/A | Read-only investigation |
| `[Manual]` | Skipped | Human verification (auto-deferred) |
| `[Docs]` | Skipped | Documentation changes |
| `[Config]` | Skipped | Configuration changes |
| `[Chore]` | Skipped | Maintenance tasks |

---

## /conductor:implement

Execute track tasks through orchestrator.

### Usage

```
> /conductor:implement [track_id]
```

If no track_id specified, finds active track.

### Execution Flow

```
Session Start
    ↓
track-state recover (load state)
    ↓
Select next task (track-state next)
    ↓
Check for interrupted phase-checker
    ↓
Dispatch subagent
    ↓
Process result (track-state process-result)
    ↓
Update plan.md (sync-plan)
    ↓
Phase boundary? → phase-checker
    ↓
Continue or Finalize
```

### Task Dispatch

| Task Type | Subagent | Notes |
|-----------|-----------|--------|
| `[Explore]` | `explorer` | Read-only investigation |
| Default | `task-executor` | TDD workflow |
| Phase end | `phase-checker` | Verification protocol |
| Track end | `doc-syncer` | Documentation sync |

### Recovery

If interrupted:
1. Session handoff saved
2. Next `/conductor:implement` resumes from handoff
3. Stale locks detected and reported

---

## /conductor:status

Display project progress overview.

### Usage

```
> /conductor:status [--health]
```

### Output Format

```
Conductor Status Report
=====================

Active Tracks (3)
-----------------
1. user-login [in_progress]
   Phase: 1/3 | Tasks: 3/10
   Last Update: 2h ago

2. checkout-flow [completed]
   Phase: 3/3 | Tasks: 15/15
   Quality Score: 92%

3. api-refactoring [blocked]
   Blocked By: Database migration pending

Archived Tracks (5)
-------------------
...

Issues
-------
- user-login: Stale lock detected (>24h)
- checkout-flow: Manual tasks pending (2)
```

### Health Check (`--health`)

```bash
> /conductor:status --health
```

Performs garbage collection:
- Detects orphaned locks
- Validates state consistency
- Offers cleanup options

---

## /conductor:review

Review completed track for quality compliance.

### Usage

```
> /conductor:review [track_id]
```

### Review Process

1. **Diff Analysis**
   - Compares implementation to plan.md
   - Checks for plan deviations
   - Validates commit history

2. **Code Quality**
   - Style guide compliance
   - Test coverage
   - Lint check results

3. **Specification Compliance**
   - All ACs implemented
   - All TCs covered
   - No out-of-scope changes

4. **Output**

```
---REVIEW RESULT---
STATUS: PASS
QUALITY_SCORE: 92
PLAN_COMPLIANCE: 100%
SPEC_COMPLIANCE: 95%
COVERAGE: 85%

ISSUES:
- 1 minor style violation in auth.ts:24
- Missing type annotation in utils.ts:12

RECOMMENDATIONS:
- Consider extracting auth middleware to separate module
- Add integration tests for login flow
---END RESULT---
```

---

## /conductor:revert

Safe rollback with state synchronization.

### Usage

```
> /conductor:revert <scope>
```

### Scopes

| Scope | Description |
|--------|-------------|
| `task <name>` | Revert specific task |
| `phase <number>` | Revert entire phase |
| `track <id>` | Revert entire track |
| `all` | Revert all conductor changes |

### What It Does

1. **Reads current state** via `track-state recover`
2. **Performs git revert** operations
3. **Syncs state** via `track-state sync`
4. **Updates plan.md** markers
5. **Verifies consistency**

### Example

```
> /conductor:revert task "Implement login form"
```

Reverts:
- Git commit for the task
- Task status to pending
- Plan.md marker to `[ ]`
- Handoff files for the task

---

## Global Options

All commands support:

| Option | Description |
|--------|-------------|
| `--help` | Show command help |
| `--verbose` | Verbose output |
| `--dry-run` | Show what would happen without executing |

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|---------|
| `Ctrl+C` | Cancel current operation |
| `Enter` | Confirm default option |
| `?` | Show available options |

---

## Next Steps

- [Getting Started](getting-started.md) - Quick start guide
- [User Guide](user-guide.md) - Complete usage guide
- [Troubleshooting](troubleshooting.md) - Common issues

---

**Last Updated**: 2026-05-11
