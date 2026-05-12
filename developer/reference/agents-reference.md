---
title: Agents Reference
audience: developer
status: stable
last_updated: 2026-05-12
related:
  - ./hooks-reference.md
  - ./plugins-reference.md
  - ../../docs/reference/subagents.md
---

# Conductor Plugin: Agents Reference

> Complete developer reference for all 9 subagent definitions, frontmatter configuration, dispatch patterns, result formats, and hook integration.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Agent Registry](#agent-registry)
- [Agent Configuration Reference](#agent-configuration-reference)
- [Agent Details](#agent-details)
  - [task-executor](#task-executor)
  - [code-reviewer](#code-reviewer)
  - [explorer](#explorer)
  - [phase-checker](#phase-checker)
  - [spec-planner](#spec-planner)
  - [spec-reviewer](#spec-reviewer)
  - [doc-syncer](#doc-syncer)
  - [skip-analyst](#skip-analyst)
  - [project-analyzer](#project-analyzer)
- [Result Format Reference](#result-format-reference)
- [Dispatch Patterns](#dispatch-patterns)
- [Hook Integration](#hook-integration)
- [Output Filtering](#output-filtering)
- [Failure Recovery](#failure-recovery)
- [Plugin Agent Limitations](#plugin-agent-limitations)

---

## Architecture Overview

Conductor agents are Markdown files in `agents/` with YAML frontmatter. Each agent runs in its own context window with a custom system prompt, specific tool access, and independent permissions.

### Scope and priority

| Location | Scope | Priority |
|----------|-------|----------|
| `.claude/agents/` | Project-level | 3 |
| `~/.claude/agents/` | User-level | 4 |
| Plugin `agents/` | Where plugin enabled | 5 (lowest) |

Conductor agents are **plugin-scoped** (priority 5). They appear in `/agents` as `conductor:<agent-name>` and are available wherever the conductor plugin is enabled.

### Context isolation

Each agent invocation creates a new instance with:
- Fresh context window (no parent conversation history)
- Custom system prompt from the agent's Markdown body
- Restricted tool access per the `tools` frontmatter
- Independent permission mode
- Current working directory from parent session

### Agent hierarchy

```
Orchestrator (main session)
├── task-executor        # Implementation (TDD workflow)
├── explorer             # Read-only codebase investigation
├── phase-checker        # Phase checkpoint verification
├── code-reviewer        # Deep code analysis
├── spec-planner         # Spec & plan generation
├── spec-reviewer        # Interactive spec review
├── doc-syncer           # Documentation synchronization
├── skip-analyst         # Failed task analysis
└── project-analyzer     # Brownfield project discovery
```

---

## Agent Registry

| Agent | Model | Effort | Max turns | Tools | Access | Result delimiter |
|-------|-------|--------|-----------|-------|--------|-----------------|
| `task-executor` | sonnet | high | 50 | Bash, Read, Edit, Write, Grep, Glob, NotebookEdit | Full (acceptEdits) | `---TASK RESULT---` |
| `code-reviewer` | sonnet | xhigh | 30 | Bash, Read, Grep, Glob | Read-only (app code) | `---REVIEW RESULT---` |
| `explorer` | haiku | medium | 25 | Bash, Read, Grep, Glob | Read-only | `---TASK RESULT---` |
| `phase-checker` | sonnet | high | 30 | Bash, Read, Edit, Write, Grep, Glob, AskUserQuestion | Full | `---CHECKPOINT RESULT---` |
| `spec-planner` | haiku | medium | 30 | Read, Write, Grep, Glob | Write only | `---SPEC PLAN RESULT---` |
| `spec-reviewer` | haiku | medium | 30 | Read, Edit, Write, AskUserQuestion | Edit/Write | `---REVIEW RESULT---` |
| `doc-syncer` | haiku | medium | 40 | Bash, Read, Edit, Write, Grep, Glob, AskUserQuestion | Full (with confirmation) | `---DOC SYNC RESULT---` |
| `skip-analyst` | haiku | low | 15 | Read, Grep, Glob | Read-only | `---SKIP ANALYSIS---` |
| `project-analyzer` | sonnet | — | — | Bash, Read, Grep, Glob | Read-only | `---ANALYSIS RESULT---` |

---

## Agent Configuration Reference

### Supported frontmatter fields

| Field | Type | Description | Conductor usage |
|-------|------|-------------|-----------------|
| `name` | string | Unique identifier (lowercase, hyphens) | All agents |
| `description` | string | When Claude should delegate to this agent | All agents |
| `tools` | list | Tool allowlist (comma-separated) | All agents |
| `disallowedTools` | list | Tool denylist | Not used |
| `model` | string | Model alias or ID | All agents |
| `effort` | string | Effort level | Most agents |
| `maxTurns` | number | Maximum agentic turns | Most agents |
| `permissionMode` | string | Permission mode override | `task-executor` (acceptEdits), `explorer` (plan) |
| `hooks` | object | Inline hooks scoped to agent lifecycle | `task-executor`, `code-reviewer`, `phase-checker` |
| `memory` | string | Persistent memory scope | Not used |
| `background` | boolean | Always run as background task | Not used |
| `isolation` | string | Run in isolated worktree | Not used |
| `skills` | list | Skills to preload | Not used |
| `mcpServers` | list | MCP server access | Not used |
| `color` | string | Display color | Not used |
| `initialPrompt` | string | Auto-submitted first turn | Not used |

### Model resolution order

When an agent is dispatched, Claude Code resolves the model in this order:

1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable
2. Per-invocation `model` parameter from the Agent tool call
3. Agent definition's `model` frontmatter
4. Main conversation's model

### Permission mode inheritance

| Parent mode | Subagent `permissionMode` | Effective mode |
|-------------|---------------------------|----------------|
| `bypassPermissions` | Any | `bypassPermissions` (parent wins) |
| `acceptEdits` | Any | `acceptEdits` (parent wins) |
| `auto` | Any | `auto` (parent wins, subagent field ignored) |
| `default` | `acceptEdits` | `acceptEdits` |
| `default` | `plan` | `plan` |
| `default` | (not set) | `default` |

---

## Agent Details

### task-executor

```
agents/task-executor.md
```

| Field | Value |
|-------|-------|
| **Model** | sonnet |
| **Effort** | high |
| **Max turns** | 50 |
| **Tools** | Bash, Read, Edit, Write, Grep, Glob, NotebookEdit |
| **Permission mode** | acceptEdits |
| **Dispatched by** | `conductor:implement` |
| **Access level** | Full — implementation code |

**Purpose**: Executes a single track task via TDD workflow (Steps 3-8). Self-loads all context from files.

**Input parameters**:

| Parameter | Description |
|-----------|-------------|
| `TRACK_DIR` | Absolute path to the track directory |
| `TRACK_ID` | Track identifier |
| `PHASE_INDEX` | Phase index of the task |
| `TASK_INDEX` | Task index within the phase |
| `TASK_NAME` | Name of the task |
| `AC_CONTEXT` | Acceptance criteria from plan.md HTML comments |
| `SUBTASK_INDEX` | Subtask index (if executing a subtask) |

**TDD Workflow**:

1. Read `spec.md` and `plan.md` from track directory
2. **Red** — Write a failing test
3. **Green** — Implement minimal code to pass
4. **Refactor** — Clean up while keeping tests green
5. Run coverage check
6. Commit with conventional message (`<type>(<scope>): <description>`)
7. Write result block

**Inline hooks**:

```yaml
hooks:
  PostToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/on-test-run.py\""
```

This hook detects test commands and provides TDD guidance context on failure.

**Task type exemptions**: The orchestrator may tag tasks with `[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, or `[Manual]` in `plan.md`. These tags skip the TDD requirement and follow simplified workflows.

---

### code-reviewer

```
agents/code-reviewer.md
```

| Field | Value |
|-------|-------|
| **Model** | sonnet |
| **Effort** | xhigh |
| **Max turns** | 30 |
| **Tools** | Bash, Read, Grep, Glob |
| **Permission mode** | (not set) |
| **Dispatched by** | `conductor:review` |
| **Access level** | Read-only for application code |

**Purpose**: Performs deep code analysis on a track's implementation. Analyzes diffs, verifies plan compliance, checks style, runs tests, and produces structured findings.

**Input parameters**:

| Parameter | Description |
|-----------|-------------|
| `TRACK_DIR` | Absolute path to the track directory |
| `TRACK_ID` | Track identifier |
| `TRACK_DESCRIPTION` | Human-readable track description |

**Review checklist**:

- Plan compliance (does implementation match spec?)
- Style guide adherence
- Test coverage adequacy
- Code quality issues (duplication, complexity, naming)
- Security vulnerabilities

**Inline hooks**:

```yaml
hooks:
  Stop:
    - matcher: ""
      hooks:
        - type: command
          command: "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/on-review-stop.py\""
          timeout: 5
```

The `Stop` hook is automatically converted to `SubagentStop` when the agent runs as a subagent.

---

### explorer

```
agents/explorer.md
```

| Field | Value |
|-------|-------|
| **Model** | haiku |
| **Effort** | medium |
| **Max turns** | 25 |
| **Tools** | Bash, Read, Grep, Glob |
| **Permission mode** | plan (read-only) |
| **Dispatched by** | `conductor:implement` (for `[Explore]` tagged tasks) |
| **Access level** | Read-only |

**Purpose**: Read-only code exploration agent. Produces `exploration.md` as a file-bridge for downstream task-executor.

**Input parameters**:

| Parameter | Description |
|-----------|-------------|
| `TRACK_DIR` | Absolute path to the track directory |
| `EXPLORE_TARGET` | What to investigate |
| `AC_CONTEXT` | Acceptance criteria for context |

**Output**: Writes `exploration.md` to the track directory. This file is read by `task-executor` as Layer 0 context ("map before manual" principle).

---

### phase-checker

```
agents/phase-checker.md
```

| Field | Value |
|-------|-------|
| **Model** | sonnet |
| **Effort** | high |
| **Max turns** | 30 |
| **Tools** | Bash, Read, Edit, Write, Grep, Glob, AskUserQuestion |
| **Permission mode** | (not set) |
| **Dispatched by** | `conductor:implement` |
| **Access level** | Full |

**Purpose**: Executes phase checkpoint verification protocol. Handles test coverage verification, missing test creation, test execution, manual verification plan, and checkpoint commit.

**Checkpoint protocol**:

1. Verify all tasks in phase are terminal (completed, skipped, or deferred)
2. Run all tests — verify they pass
3. Check code coverage meets threshold (80%)
4. Create missing tests if needed
5. Create manual verification plan for `[Manual]` tasks
6. Create checkpoint commit

**Input parameters**:

| Parameter | Description |
|-----------|-------------|
| `TRACK_DIR` | Absolute path to the track directory |
| `TRACK_ID` | Track identifier |
| `PHASE_INDEX` | Phase index to verify |
| `PHASE_NAME` | Phase name for commit message |

**Inline hooks**:

```yaml
hooks:
  Stop:
    - matcher: ""
      hooks:
        - type: command
          command: "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/on-phase-checkpoint-stop.py\""
          timeout: 5
```

---

### spec-planner

```
agents/spec-planner.md
```

| Field | Value |
|-------|-------|
| **Model** | haiku |
| **Effort** | medium |
| **Max turns** | 30 |
| **Tools** | Read, Write, Grep, Glob |
| **Permission mode** | (not set) |
| **Dispatched by** | `conductor:setup`, `conductor:new-track` |
| **Access level** | Write only |

**Purpose**: Generates `spec.md` and `plan.md` from user requirements and project context. Writes files directly, returns compact summary to minimize parent context pressure.

**Input parameters**:

| Parameter | Description |
|-----------|-------------|
| `TRACK_DIR` | Absolute path for output files |
| `TRACK_DESCRIPTION` | User's description of what the track should accomplish |
| `TRACK_TYPE` | Inferred type: `feature`, `bugfix`, `chore`, `docs` |
| `USER_ANSWERS` | Collected answers from interactive Q&A |
| `RELATED_DOCS` | Paths to semantically related documents |

**Context loading** (self-load pattern):

1. Read `conductor/index.md` to discover documentation paths
2. Read global docs (Product Definition, Tech Stack)
3. Semantic scan for related documents
4. Read `RELATED_DOCS` paths if provided

**Output artifacts**:
- `{TRACK_DIR}/spec.md` — Specification with FRs, NFRs, ACs, TCs, constraints, references
- `{TRACK_DIR}/plan.md` — Implementation plan with phases, tasks, subtasks, AC traceability

**Task type tags** (in plan.md):

| Tag | TDD required | Use for |
|-----|-------------|---------|
| `[Explore]` | No | Code investigation |
| `[Docs]` | No | Documentation changes |
| `[Config]` | No | Configuration files |
| `[Chore]` | No | Maintenance tasks |
| `[Manual]` | No | Human verification required |
| *(no tag)* | Yes | Standard implementation (default) |

---

### spec-reviewer

```
agents/spec-reviewer.md
```

| Field | Value |
|-------|-------|
| **Model** | haiku |
| **Effort** | medium |
| **Max turns** | 30 |
| **Tools** | Read, Edit, Write, AskUserQuestion |
| **Permission mode** | (not set) |
| **Dispatched by** | `conductor:setup`, `conductor:new-track` |
| **Access level** | Edit/Write |

**Purpose**: Interactive reviewer for spec.md and plan.md. Presents summaries to user, handles revisions, returns compact result. Keeps full file contents out of the orchestrator context.

**Input parameters**:

| Parameter | Description |
|-----------|-------------|
| `TRACK_DIR` | Absolute path to the track directory |

**Review workflow**:

1. Read `spec.md` and `plan.md`
2. Present structured **summary** of spec (not full content) to user
3. User can: Approve, Request Changes, or Read Full
4. If changes requested → apply edits → re-present → repeat until approved
5. Present structured **summary** of plan
6. Same approval cycle
7. Return compact result

**Key design**: The spec-reviewer is an **interactive** agent that uses `AskUserQuestion` to present summaries and collect feedback. This keeps full file contents in the subagent's context, not the parent's.

---

### doc-syncer

```
agents/doc-syncer.md
```

| Field | Value |
|-------|-------|
| **Model** | haiku |
| **Effort** | medium |
| **Max turns** | 40 |
| **Tools** | Bash, Read, Edit, Write, Grep, Glob, AskUserQuestion |
| **Permission mode** | (not set) |
| **Dispatched by** | `conductor:implement` (after track completion) |
| **Access level** | Full (with user confirmation per change) |

**Purpose**: Synchronizes all project documentation after track completion. Analyzes spec.md against product docs, design docs, API specs, database schema, architecture, and resource files.

**Input parameters**:

| Parameter | Description |
|-----------|-------------|
| `TRACK_DIR` | Absolute path to the track directory |
| `TRACK_ID` | Track identifier |
| `TRACK_DESCRIPTION` | Human-readable track description |

**Documents analyzed** (resolved via `conductor/index.md`):

| Document | Analysis |
|----------|----------|
| Product Definition | New/removed features |
| Tech Stack | New/removed technologies |
| Product Guidelines | Branding/voice changes (skip if no UX impact) |
| System Architecture | Component/data flow changes |
| Database Schema | Table/column changes |
| API Specifications | Endpoint changes |
| UX/UI Design Spec | UI component changes (skip if no UI impact) |
| Glossary | New domain terms |

**Confirmation pattern**: For each document needing update, the agent presents proposed changes via `AskUserQuestion` and only applies confirmed updates.

**Commit format**: `docs(conductor): Synchronize docs for track '{TRACK_DESCRIPTION}'`

---

### skip-analyst

```
agents/skip-analyst.md
```

| Field | Value |
|-------|-------|
| **Model** | haiku |
| **Effort** | low |
| **Max turns** | 15 |
| **Tools** | Read, Grep, Glob |
| **Permission mode** | (not set) |
| **Dispatched by** | `conductor:implement` (when retry count exhausted) |
| **Access level** | Read-only |

**Purpose**: Analyzes whether a repeatedly failed track task can be safely skipped without breaking downstream work.

**Input parameters**:

| Parameter | Description |
|-----------|-------------|
| `TRACK_DIR` | Absolute path to the track directory |
| `TRACK_ID` | Track identifier |
| `PHASE_INDEX` | Phase index of the failed task |
| `TASK_INDEX` | Task index within the phase |
| `TASK_NAME` | Name of the failed task |
| `RETRY_COUNT` | Number of failed attempts |

**Analysis questions**:

1. What downstream tasks depend on this task's output?
2. Can downstream tasks still be completed without this task?
3. What is the scope of impact if skipped?
4. Is there an alternative implementation approach?

**Recommendation values**: `skip`, `pause_and_escalate`, or `retry_with_modification`.

The agent is explicitly **conservative** — when in doubt, it recommends `pause_and_escalate`.

---

### project-analyzer

```
agents/project-analyzer.md
```

| Field | Value |
|-------|-------|
| **Model** | sonnet |
| **Effort** | (not set) |
| **Max turns** | (not set) |
| **Tools** | Bash, Read, Grep, Glob |
| **Permission mode** | (not set) |
| **Dispatched by** | `conductor:setup` (brownfield projects) |
| **Access level** | Read-only |

**Purpose**: Analyzes a brownfield project to detect tech stack, architecture, and structure.

**Input parameters**:

| Parameter | Description |
|-----------|-------------|
| `PROJECT_DIR` | Absolute path to the project root |
| `PROJECT_NAME` | Name of the project |

**Detection capabilities**:

| Category | What it detects |
|----------|----------------|
| Project type | `web_app`, `api`, `cli`, `library`, `mobile`, `desktop`, `other` |
| Languages | TypeScript, Python, Go, Rust, Java, C#, Dart, Ruby, PHP |
| Frameworks | React, FastAPI, Express, Django, Rails, etc. |
| Architecture | MVC, Modular, Component-based, Monolith, Microservices |
| Build tools | npm, pip, go, cargo, make, cmake, webpack, vite |
| Test frameworks | jest, pytest, go test, cargo test |
| Linters | eslint, prettier, black, gofmt |
| CI/CD | GitHub Actions, GitLab CI, Jenkins |
| Containers | Docker, docker-compose |

---

## Result Format Reference

All agents return structured result blocks delimited by `---TYPE---` / `---END---` markers. The `filter-subagent-output.py` hook extracts only these blocks from subagent output.

### task-executor

```
---TASK RESULT---
STATUS: SUCCESS|FAILURE
COMMIT_SHA: <hash or N/A>
FILES_CHANGED: <comma-separated or N/A>
SUMMARY: <one-line>
TC_COVERAGE: <IDs or N/A>
SPEC_DEVIATION: NONE|<description>
---END RESULT---
```

### phase-checker

```
---CHECKPOINT RESULT---
STATUS: PASSED|FAILED
CHECKPOINT_SHA: <hash or N/A>
MISSING_TESTS_CREATED: <count or 0>
TESTS_PASSED: <true|false>
USER_CONFIRMED: <true|skipped_continuous>
FAILURE_REASON: <description if FAILED>
---END RESULT---
```

### code-reviewer

```
---REVIEW RESULT---
STATUS: APPROVED|CHANGES_REQUESTED
FINDINGS:
- [CRITICAL] <finding>
- [WARNING] <finding>
- [SUGGESTION] <finding>
SUMMARY: <one-line summary>
---END REVIEW RESULT---
```

### spec-planner

```
---SPEC PLAN RESULT---
STATUS: SUCCESS|FAILURE
FILES_WRITTEN:
- {TRACK_DIR}/spec.md
- {TRACK_DIR}/plan.md
PLAN_STRUCTURE:
{
  "phases": [
    {
      "name": "Phase 1: ...",
      "tasks": [
        { "name": "Task name" },
        { "name": "Task with subtasks", "subtasks": ["Sub 1", "Sub 2"] }
      ]
    }
  ]
}
SUMMARY: <one-line summary>
---END SPEC PLAN RESULT---
```

### spec-reviewer

```
---REVIEW RESULT---
STATUS: APPROVED|CANCELLED
TRACK_DIR: {TRACK_DIR}
CHANGES_MADE: true|false
STRUCTURE_CHANGED: true|false
SUMMARY: <one-line summary>
---END REVIEW RESULT---
```

### doc-syncer

```
---DOC SYNC RESULT---
STATUS: COMPLETED|SKIPPED
UPDATED_FILES: <comma-separated or NONE>
SUMMARY: <one-line summary>
---END RESULT---
```

### skip-analyst

```
---SKIP ANALYSIS---
```json
{
  "can_skip": true|false,
  "impact": "description",
  "recommendation": "skip|pause_and_escalate|retry_with_modification",
  "reasoning": "detailed reasoning"
}
```
---END ANALYSIS---
```

### project-analyzer

```
---ANALYSIS RESULT---
```json
{
  "project_type": "web_app|api|cli|library|mobile|desktop|other",
  "maturity": "brownfield",
  "languages": [{"name": "...", "percentage": N}],
  "frameworks": [{"name": "...", "version": "...", "category": "..."}],
  "architecture": {"pattern": "...", "description": "..."},
  "build_tools": ["..."],
  "test_frameworks": ["..."],
  "linters": ["..."],
  "ci_cd": ["..."],
  "containers": ["..."],
  "structure": {
    "source_dirs": ["..."],
    "test_dirs": ["..."],
    "config_files": ["..."]
  },
  "code_volume": {"size": "...", "file_counts": {...}},
  "suggested_styleguides": ["..."],
  "suggested_workflow": "standard_tdd"
}
```
---END ANALYSIS RESULT---
```

---

## Dispatch Patterns

### Skill-to-agent mapping

```
conductor:setup
  ├── project-analyzer    # Brownfield discovery
  ├── spec-planner        # Generate spec & plan
  └── spec-reviewer       # Interactive review

conductor:new-track
  ├── spec-planner        # Generate spec & plan
  └── spec-reviewer       # Interactive review

conductor:implement
  ├── explorer            # [Explore] tagged tasks
  ├── task-executor       # Implementation tasks (TDD)
  ├── phase-checker       # Phase checkpoint verification
  ├── skip-analyst        # Failed task analysis (retry exhausted)
  └── doc-syncer          # Documentation sync (after completion)

conductor:review
  └── code-reviewer       # Deep code analysis

conductor:revert
  (no subagents)          # Revert handled in main context

conductor:status
  (no subagents)          # Read-only status display
```

### Execution flow

```
/conductor:implement auth-feature
│
├─ Read track-state.json → find next pending task
│
├─ If [Explore] task:
│   └─ Dispatch explorer → write exploration.md
│
├─ Dispatch task-executor → TDD workflow
│   ├── PreToolUse hook → check for dangerous commands
│   ├── PostToolUse hook → TDD guidance on test failure
│   └── SubagentStop hook → failure recovery
│
├─ If phase complete:
│   └─ Dispatch phase-checker → checkpoint verification
│
├─ If task failed (max retries):
│   └─ Dispatch skip-analyst → skip/skip analysis
│
└─ If track complete:
    └─ Dispatch doc-syncer → documentation updates
```

---

## Hook Integration

Agents interact with hooks at three levels:

### 1. Plugin-level hooks (`hooks/hooks.json`)

These hooks fire for all agents matching the matcher pattern:

| Hook event | Matcher | Fires for |
|------------|---------|-----------|
| `SubagentStart` | `task-executor\|code-reviewer\|...` | All 9 agents |
| `SubagentStop` | `task-executor\|explorer\|phase-checker` | Critical agents (sync) |
| `SubagentStop` | `code-reviewer\|doc-syncer\|...` | Non-critical agents (async) |
| `PostToolUse` (Agent) | `Agent` | Filter output + recovery context |

### 2. Agent-level inline hooks (frontmatter)

These hooks fire only while the specific agent is active:

| Agent | Hook event | Script |
|-------|-----------|--------|
| `task-executor` | `PostToolUse` (Bash) | `on-test-run.py` |
| `code-reviewer` | `Stop` → `SubagentStop` | `on-review-stop.py` |
| `phase-checker` | `Stop` → `SubagentStop` | `on-phase-checkpoint-stop.py` |

**Note**: `Stop` hooks in agent frontmatter are automatically converted to `SubagentStop` at runtime.

### 3. Hook processing pipeline

When `task-executor` runs a test command:

```
1. PreToolUse (Bash) → pre-command-check.py
   └─ Checks for dangerous git operations

2. Bash tool executes test command

3. PostToolUse (Bash) → on-test-run.py (inline hook from frontmatter)
   └─ Detects test failure → injects TDD guidance context

4. PostToolBatch → on-batch-complete.py
   └─ Coverage gate verification after git commits
```

---

## Output Filtering

The `filter-subagent-output.py` hook runs on every `PostToolUse` event for the `Agent` tool. It extracts only the `---RESULT---` delimited blocks, discarding narrative text to reduce context pressure.

### Recognized block types

| Start delimiter | End delimiter | Produced by |
|----------------|---------------|-------------|
| `---TASK RESULT---` | `---END RESULT---` | task-executor, explorer |
| `---CHECKPOINT RESULT---` | `---END RESULT---` | phase-checker |
| `---SKIP ANALYSIS---` | `---END ANALYSIS---` | skip-analyst |
| `---DOC SYNC RESULT---` | `---END RESULT---` | doc-syncer |
| `---REVIEW RESULT---` | `---END REVIEW RESULT---` | code-reviewer, spec-reviewer |
| `---SPEC PLAN RESULT---` | `---END SPEC PLAN RESULT---` | spec-planner |
| `---ANALYSIS RESULT---` | `---END RESULT---` | project-analyzer |

### When no result block found

If the subagent completes without producing a result block, the filter replaces the output with:

```
[Conductor] Subagent completed. No structured result block found. Check .conductor/ for artifacts.
```

---

## Failure Recovery

### Detection

The `on-subagent-stop.py` hook detects failure patterns in the subagent's last message:

- `Traceback (most recent call last):`
- `Error:`
- `Permission denied`
- `File not found`
- `Command failed`
- `BUILD FAILED`
- `test.*failed`
- `AssertionError`

False positive exclusions: `error handling`, `error message`, `errors?: none`, `error code`, `catch error`.

### Recovery mechanism

For critical agents (task-executor, explorer, phase-checker):

1. `SubagentStop` hook detects failure pattern
2. Returns `decision: "block"` with recovery instructions
3. Subagent **stays running** and receives recovery instructions as its next prompt
4. Subagent attempts to self-correct

```json
{
  "decision": "block",
  "reason": "[Conductor Recovery] Failure detected (pattern: ...). Review the error above, correct the issue, and retry."
}
```

For non-critical agents (code-reviewer, doc-syncer, etc.): async logging only.

### Parent session notification

The `on-subagent-result.py` hook runs as a `PostToolUse` on the Agent tool. It detects failure indicators in the final subagent output and injects recovery context into the **parent** session:

```
[Conductor] Subagent reported failure. If retries remain, the orchestrator will re-dispatch.
```

---

## Plugin Agent Limitations

Per the Claude Code plugin protocol, the following frontmatter fields are **ignored** for plugin-shipped agents:

| Field | Behavior |
|-------|----------|
| `permissionMode` | Generates warning at load time. Ignored in practice unless parent mode is `default`. |
| `hooks` | Generates warning at load time. However, hooks defined in `hooks/hooks.json` still work via `SubagentStart`/`SubagentStop` matchers. |

### Workaround

Instead of relying on inline agent hooks, register hooks in `hooks/hooks.json` with matchers targeting specific agent types:

```json
{
  "SubagentStart": [{
    "matcher": "task-executor|code-reviewer|explorer|...",
    "hooks": [{"type": "command", "command": "..."}]
  }],
  "SubagentStop": [{
    "matcher": "task-executor|explorer|phase-checker",
    "hooks": [{"type": "command", "command": "..."}]
  }]
}
```

This is the approach Conductor uses — all hook logic is in `hooks/hooks.json`, not in agent frontmatter (despite some agents also declaring inline hooks for redundancy).

---

**Last Updated**: 2026-05-12
