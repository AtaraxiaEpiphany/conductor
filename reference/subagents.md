# Subagent Reference

> Complete reference for all subagents in Conductor

---

## Overview

Subagents are specialized AI agents that execute specific tasks in isolated context. They inherit:
- **System Prompt**: From their `.md` definition file
- **Tools**: Restricted/allowlisted per agent
- **Hooks**: Agent-scoped lifecycle hooks
- **Permissions**: Pre-approved where specified

---

## Subagent Registry

| Subagent | Model | Tools | Purpose | Dispatched By |
|-----------|--------|--------|---------|---------------|
| **task-executor** | Sonnet | Bash, Read, Edit, Write, Grep, Glob, NotebookEdit | TDD workflow execution (Steps 3-9) | `implement` |
| **explorer** | Haiku | Bash, Read, Grep, Glob | Read-only codebase investigation | `implement` (`[Explore]` tasks) |
| **phase-checker** | Sonnet | Bash, Read, Edit, Write, Grep, Glob, AskUserQuestion | Phase verification protocol | `implement` (phase boundaries) |
| **code-reviewer** | Sonnet | Bash, Read, Grep, Glob | Deep code review | `review` |
| **skip-analyst** | Haiku | Read, Grep, Glob | Failed task analysis | `implement` (retry exhausted) |
| **spec-planner** | Sonnet | Bash, Read, Edit, Write | Spec/plan generation | `setup`, `newTrack` |
| **spec-reviewer** | Sonnet | Read, Write | Interactive spec/plan review | `setup`, `newTrack` |
| **doc-syncer** | Sonnet | Bash, Read, Edit, Write | Documentation sync | `implement` (track completion) |
| **project-analyzer** | Sonnet | Bash, Read, Grep, Glob | Project structure analysis | `setup` |

---

## Context Loading Pattern

All subagents use the same layered context loading pattern:

### Layer 0: Exploration Map (Optional)
- Reads `exploration.md` if exists
- Pre-computed by explorer agent
- Contains: architecture, gotchas, file inventory

### Layer 1: Task Identity
- Reads `plan.md`
- Finds task at Phase P, Task T
- Extracts: task_description, ac_ids, tc_ids

### Layer 2: Acceptance Criteria
- Reads `spec.md`
- Extracts only relevant ACs/TCs for the current task

### Layer 3: Workflow & Style
- Loads `task-workflow.md` for execution steps
- Loads language-specific `code-styleguides/*.md`
- Loads `testing/strategy.md` for test conventions

---

## Subagent Details

### task-executor

**Purpose**: Execute TDD workflow for implementation tasks

**Workflow Steps**:
1. Read spec.md and plan.md
2. Extract ACs and TCs for current task
3. Write failing test (Red)
4. Implement minimal code (Green)
5. Refactor code (Refactor)
6. Run coverage check
7. Commit with conventional message
8. Write `result.json` with outcomes

**Result Format**:
```
---TASK RESULT---
STATUS: SUCCESS
COMMIT_SHA: a1b2c3d
FILES_CHANGED: test/auth.test.ts, src/auth.ts
SUMMARY: Implemented OAuth2 login flow
TC_COVERAGE: TC-1.1, TC-1.2, TC-2.1
SPEC_DEVIATION: NONE
---END RESULT---
```

**Hook Priority**: `asyncRewake: true` (critical agent)

---

### explorer

**Purpose**: Read-only codebase investigation

**Use Cases**:
- Understanding architecture
- Mapping dependencies
- Finding relevant code paths
- Generating `exploration.md`

**Output**: Writes `exploration.md` with findings

**Hook Priority**: `asyncRewake: true` (critical agent)

---

### phase-checker

**Purpose**: Verify phase completion

**Checklist**:
1. All tasks in phase are terminal
2. Tests pass
3. Coverage meets threshold
4. Manual verification plan created
5. Checkpoint commit created

**Output**: Writes checkpoint result

**Hook Priority**: `asyncRewake: true` (critical agent)

---

### code-reviewer

**Purpose**: Deep code review with diff analysis

**Checks**:
- Plan compliance
- Style guide adherence
- Test coverage
- Code quality issues
- Security vulnerabilities

**Output**: `---REVIEW RESULT---` block

**Hook Priority**: `async: true` (non-critical)

---

### skip-analyst

**Purpose**: Analyze if a task can be safely skipped

**Analysis**:
- Downstream impact assessment
- Dependency chain evaluation
- Risk assessment

**Output**: Skip recommendation with reasoning

**Hook Priority**: `async: true` (non-critical)

---

### spec-planner

**Purpose**: Generate spec.md and plan.md from requirements

**Process**:
1. Analyze requirements
2. Create spec.md with ACs and TCs
3. Break down into phases and tasks
4. Assign tags to tasks
5. Write plan.md

**Output**: Complete spec.md and plan.md files

**Hook Priority**: `async: true` (non-critical)

---

### spec-reviewer

**Purpose**: Interactive spec/plan review

**Features**:
- Presents summaries
- Handles revisions
- Keeps full files out of orchestrator context

**Output**: Review result with changes

**Hook Priority**: `async: true` (non-critical)

---

### doc-syncer

**Purpose**: Update project documentation after track completion

**Updates**:
- `product.md`
- `tech-stack.md`
- `product-guidelines.md`

**Output**: Sync confirmation

**Hook Priority**: `async: true` (non-critical)

---

### project-analyzer

**Purpose**: Brownfield project analysis

**Detection**:
- Technology stack
- Architecture patterns
- Project structure
- Existing conventions

**Output**: Analysis report

**Hook Priority**: `async: true` (non-critical)

---

## Dispatch Formats

### Minimal Dispatch Prompt

Orchestrator sends minimal prompts (~100 tokens):
```
TRACK_DIR=/conductor/tracks/user-login
PHASE=0
TASK=1
NAME=Implement login form
TAGS=[]
```

Subagent self-loads all context from files.

---

## Hook Integration

### SubagentStart Hook

Injects role-specific reminders:
```json
{
  "additionalContext": "[Conductor] task-executor reminder: Follow TDD workflow strictly. Write tests before implementation."
}
```

### SubagentStop Hook

- Logs lifecycle events
- Checks for failure patterns
- Critical agents trigger asyncRewake on failure

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| Subagent not found | Agent name mismatch | Verify agent name in hooks.json |
| Context too large | Not using layered loading | Ensure subagent self-loads context |
| No result block | Missing result format | Subagent must write `---TASK RESULT---` |
| Recovery failure | No handoff file | Check `.conductor/handoff/` directory |

---

## Next Steps

- [Hook Reference](hooks.md) - Hook events and configuration
- [Interaction Mechanism](../architecture/INTERACTION_MECHANISM.md) - Communication flows
- [Quality Gates](quality-gates.md) - F1-F6 rules

---

**Last Updated**: 2026-05-11
