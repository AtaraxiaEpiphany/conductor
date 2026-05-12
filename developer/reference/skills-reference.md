---
title: Skills Reference
audience: developer
status: stable
last_updated: 2026-05-12
related:
  - ./agents-reference.md
  - ./hooks-reference.md
  - ./plugins-reference.md
---

# Conductor Plugin: Skills Reference

> Complete developer reference for all 6 orchestrator skills. Covers frontmatter configuration, execution workflows, agent dispatch patterns, argument handling, and integration with hooks and state management.

---

## Table of Contents

- [Skills Architecture](#skills-architecture)
- [Skill Registry](#skill-registry)
- [Frontmatter Configuration Reference](#frontmatter-configuration-reference)
- [Skill Details](#skill-details)
  - [setup](#setup)
  - [new-track](#new-track)
  - [implement](#implement)
  - [status](#status)
  - [review](#review)
  - [revert](#revert)
- [Common Patterns](#common-patterns)
- [String Substitutions](#string-substitutions)
- [Skill-Agent Dispatch Reference](#skill-agent-dispatch-reference)
- [State Management Integration](#state-management-integration)
- [Content Lifecycle](#content-lifecycle)

---

## Skills Architecture

### How skills work in Claude Code

Skills are directories containing `SKILL.md` files. Each skill creates a `/plugin-name:skill-name` command (for plugin skills) or `/skill-name` (for project/user skills) that can be invoked by the user or automatically by Claude based on the description.

```
skills/
├── implement/
│   └── SKILL.md           # Orchestrator for task execution
├── new-track/
│   └── SKILL.md           # Track creation workflow
├── revert/
│   └── SKILL.md           # State-aware git revert
├── review/
│   └── SKILL.md           # Code review via subagent
├── setup/
│   └── SKILL.md           # Project initialization
└── status/
    └── SKILL.md           # Progress overview
```

### Scope and discovery

| Location | Scope | Applies to |
|----------|-------|------------|
| Plugin `skills/` | Where plugin enabled | Conductor (priority 5) |
| `.claude/skills/` | Project | This project only |
| `~/.claude/skills/` | Personal | All projects |

Conductor skills are **plugin-scoped**. They appear as `/conductor:skill-name` in the UI.

### Invocation paths

| Method | Example | When |
|--------|---------|------|
| Direct invocation | `/conductor:implement auth-feature` | User types the command |
| Natural language | "Implement the auth track" | Claude matches description |
| @-mention | `@conductor:implement auth-feature` | Guaranteed invocation |

### Content lifecycle

When a skill is invoked:
1. `SKILL.md` content enters the conversation as a single message
2. Content stays in context for the rest of the session
3. Auto-compaction carries invoked skills forward (first 5,000 tokens per skill, 25,000 combined budget)
4. Older skills can be dropped after compaction if many were invoked

---

## Skill Registry

| Skill | Invocation | Model | Tools | Description |
|-------|-----------|-------|-------|-------------|
| `setup` | `/conductor:setup` | sonnet | Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion | Project initialization and scaffolding |
| `new-track` | `/conductor:new-track [desc]` | sonnet | Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion | Create track with spec, plan, and state |
| `implement` | `/conductor:implement [track]` | sonnet | Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion | Orchestrate task execution via subagents |
| `status` | `/conductor:status [track]` | haiku | Read, Grep, Glob | Display project progress overview |
| `review` | `/conductor:review [track]` | sonnet | Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion | Review completed track work |
| `revert` | `/conductor:revert [scope]` | sonnet | Bash, Read, Edit, Write, Grep, Glob | State-aware git revert |

---

## Frontmatter Configuration Reference

All 6 skills use YAML frontmatter in `SKILL.md`:

```yaml
---
name: implement
description: Orchestrates track task execution via subagents with track-state.json synchronization
when_to_use: User wants to implement a track, execute pending tasks, or run the conductor implementation workflow
argument-hint: "[track_name]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion
model: sonnet
hooks:
  Stop:
    - matcher: ""
      hooks:
        - type: command
          command: "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/state-consistency-check.py\""
          timeout: 5
---
```

### Fields used by Conductor skills

| Field | Required | Description | Conductor usage |
|-------|----------|-------------|-----------------|
| `name` | No | Display name (directory name used if omitted) | All 6 skills |
| `description` | Recommended | What the skill does and when to use it | All 6 skills |
| `when_to_use` | No | Additional trigger context for Claude | All 6 skills |
| `argument-hint` | No | Autocomplete hint for arguments | implement, new-track, review, revert, status |
| `allowed-tools` | No | Pre-approved tools while skill is active | All 6 skills |
| `model` | No | Model override while skill is active | All 6 skills |
| `hooks` | No | Lifecycle hooks scoped to this skill | implement only |

### Fields not used

| Field | Description |
|-------|-------------|
| `disable-model-invocation` | All Conductor skills allow Claude auto-invocation |
| `user-invocable` | All skills are user-invocable (default) |
| `context` | No skills use `fork` context (subagents are dispatched via Agent tool instead) |
| `agent` | No forked subagent execution |
| `effort` | Inherits from session |
| `paths` | No path-gated activation |
| `arguments` | Uses `$ARGUMENTS` directly instead of named parameters |
| `shell` | Default bash |

### Description + when_to_use truncation

The combined `description` and `when_to_use` text is truncated at **1,536 characters** in the skill listing. Put the key use case first in `description`.

---

## Skill Details

### setup

```
skills/setup/SKILL.md (149 lines)
```

**Invocation**: `/conductor:setup`

**Model**: sonnet

**Purpose**: Scaffolds the project with Conductor environment, creates initial track with `track-state.json`.

**When to use**: User wants to initialize a new project with Conductor, or set up the conductor directory structure.

**Allowed tools**: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion

#### Execution workflow

```
Phase 1: PROJECT SETUP
  0.0 Resolve Paths (product.md, tech-stack.md, tracks registry, workflow index)
  1.0 Resume Check (existing setup_state.json?)
  2.0 Project Inception
      ├── Brownfield → Dispatch project-analyzer
      └── Greenfield → Interactive Q&A
  3.0 Product Guide (interactive requirements gathering)
  4.0 Product Guidelines (interactive UX/brand)
  5.0 Tech Stack & Style Guides
  6.0 Workflow (copy templates)
  7.0 Finalization (CLAUDE.md TOC, project index)

Phase 2: INITIAL TRACK GENERATION
  8.0 Product Requirements (greenfield only)
  9.0 Propose Track
  10.0 Dispatch spec-planner → generate spec.md + plan.md
  11.0 Dispatch spec-reviewer → interactive review
  12.0 Create State Artifacts (track-state.json)
  13.0 Final Commit
```

#### Key files created

| File | Description |
|------|-------------|
| `conductor/overview/product.md` | Product definition |
| `conductor/overview/product-guidelines.md` | UX and brand guidelines |
| `conductor/design/tech-stack.md` | Technology choices |
| `conductor/index.md` | Project documentation index |
| `CLAUDE.md` | Updated with Conductor TOC |
| `conductor/tracks.md` | Track registry |
| First track directory | spec.md, plan.md, track-state.json |

#### Agents dispatched

| Agent | Purpose |
|-------|---------|
| `project-analyzer` | Brownfield project tech stack detection |
| `spec-planner` | Generate spec.md and plan.md |
| `spec-reviewer` | Interactive review of spec and plan |

---

### new-track

```
skills/new-track/SKILL.md (122 lines)
```

**Invocation**: `/conductor:new-track [track_description]`

**Model**: sonnet

**Purpose**: Creates a new track with spec, plan, and `track-state.json` for orchestrator-driven execution.

**When to use**: User wants to create a new feature track, bug fix track, or chore track with specification and plan.

**Argument hint**: `[track_description]`

#### Execution workflow

```
0.0 Resolve Paths (product.md, tech-stack.md, tracks.md, workflow index)
1.0 Setup Check (verify conductor environment)
2.0 Track Initialization
    ├── Description & Type inference (feature/bugfix/chore/docs)
    ├── Context Discovery (paths only — agent reads files itself)
    ├── Dispatch spec-planner → generate spec.md + plan.md
    ├── Dispatch spec-reviewer → interactive review
    ├── Execution Mode Selection (interactive/continuous)
    ├── Create State Artifacts (track-state.json)
    └── Offer Auto-Start ("Start implementing now?")
```

#### Track type inference

| Keywords in description | Inferred type |
|------------------------|--------------|
| "fix", "bug", "issue", "patch" | `bugfix` |
| "document", "readme", "docs" | `docs` |
| "update", "refactor", "cleanup", "migrate" | `chore` |
| (default) | `feature` |

#### Context discovery pattern

The skill provides **file paths only** to the spec-planner agent — the agent reads and synthesizes content itself. This keeps business documents out of the orchestrator context:

```
1. Read conductor/index.md → discover documentation paths
2. Collect RELATED_DOCS paths from semantic scan
3. Pass paths as parameters to spec-planner
4. spec-planner reads files in its own context
```

#### Agents dispatched

| Agent | Purpose |
|-------|---------|
| `spec-planner` | Generate spec.md and plan.md |
| `spec-reviewer` | Interactive review of spec and plan |

---

### implement

```
skills/implement/SKILL.md (152 lines)
```

**Invocation**: `/conductor:implement [track_name]`

**Model**: sonnet

**Purpose**: Orchestrates track task execution via subagents with `track-state.json` synchronization.

**When to use**: User wants to implement a track, execute pending tasks, or run the conductor implementation workflow.

**Argument hint**: `[track_name]`

**Inline hooks**: `Stop` → `state-consistency-check.py` (converted to `SubagentStop` at runtime)

#### Orchestrator contract

The implement skill is a **thin state machine** that routes between subagents. It does NOT implement code itself — it reads `track-state.json`, determines the next action, and dispatches the appropriate agent.

#### Execution workflow

```
1.0 SETUP + TRACK SELECTION
    ├── Locate track (from argument or auto-detect)
    ├── Verify required files exist
    └── Initialize state (load track-state.json)

2.0 STATE RECOVERY
    ├── Validate state consistency
    ├── Recover from stale in_progress tasks
    ├── Sync plan.md → track-state.json
    └── Route by track status (active → dispatch loop, completed → finalize)

3.0 DISPATCH LOOP (main execution cycle)
    ├── Get Next Action
    │   ├── Has in_progress task → resume/retry
    │   ├── Has pending task → dispatch
    │   ├── Phase complete → dispatch phase-checker
    │   └── All tasks done → finalize
    │
    ├── dispatch_phase_checker
    │   └── Agent: phase-checker
    │
    ├── dispatch_explorer
    │   └── Agent: explorer (for [Explore] tasks)
    │
    ├── dispatch_executor
    │   └── Agent: task-executor (TDD workflow)
    │
    ├── defer_manual
    │   └── Defer [Manual] tasks (interactive only)
    │
    ├── Process Result
    │   ├── Update track-state.json
    │   ├── Sync plan.md markers
    │   └── Handle retry/failure
    │
    ├── Phase Boundary
    │   └── Route to phase-checker when phase completes
    │
    └── finalize
        └── Dispatch doc-syncer → mark track completed

4.0 POST-LOOP
    └── Execute post-loop workflow (cleanup, summary)
```

#### Dispatch loop action routing

| Condition | Action | Agent |
|-----------|--------|-------|
| Task tagged `[Explore]` | `dispatch_explorer` | explorer |
| Task tagged `[Manual]` | `defer_manual` | (none — user action) |
| Task tagged `[Docs]`/`[Config]`/`[Chore]` | `dispatch_executor` | task-executor |
| Untagged task (default) | `dispatch_executor` | task-executor |
| Phase's last task completed | Phase boundary | phase-checker |
| Task failed (max retries) | `dispatch_skip_analyst` | skip-analyst |
| Track's last phase completed | `finalize` → `dispatch_doc_syncer` | doc-syncer |

#### Retry and failure handling

| Scenario | Behavior |
|----------|----------|
| Task fails (1st time) | Retry immediately |
| Task fails (retry 2-3) | Retry with failure context in handoff |
| Task fails (max retries) | Dispatch skip-analyst for skip/skip analysis |
| skip-analyst recommends `pause_and_escalate` | Stop and ask user |
| skip-analyst recommends `skip` | Mark task as skipped, advance |

#### Agents dispatched

| Agent | When |
|-------|------|
| `explorer` | `[Explore]` tagged tasks |
| `task-executor` | All implementation tasks |
| `phase-checker` | Phase boundary verification |
| `skip-analyst` | Max retries exhausted |
| `doc-syncer` | Track completion |

---

### status

```
skills/status/SKILL.md (161 lines)
```

**Invocation**: `/conductor:status [track_name]`

**Model**: haiku (lightweight — read-only overview)

**Purpose**: Displays project progress by reading `track-state.json` as the authoritative source.

**When to use**: User wants to see track progress, check task status, or get a project overview.

**Allowed tools**: Read, Grep, Glob (read-only)

#### Execution workflow

```
1.0 Setup Check (verify conductor environment)
2.0 Status Overview Protocol
    ├── Read Track States
    │   ├── Filter by track_name argument (if provided)
    │   └── Read all track-state.json files
    │
    ├── Compute Status
    │   ├── Track-level: active/completed/blocked
    │   └── Phase-level: in_progress/pending/complete
    │
    ├── Present Status Overview
    │   ├── Summary (total tracks, active, completed)
    │   ├── Per-track breakdown
    │   └── Phase progress bars
    │
    ├── Highlight Issues
    │   ├── Failed tasks (with error details)
    │   ├── Blocked tasks
    │   ├── Stale in_progress tasks
    │   └── Deferred tasks
    │
    ├── Next Actions
    │   └── Recommend based on current state
    │
    └── Health Check
        └── Offer gc option for cleaning stale items
```

#### Status computation rules

| Track state | Condition |
|-------------|-----------|
| Active | Has pending or in_progress tasks |
| Completed | All phases complete |
| Blocked | Has blocked tasks |
| Stale | in_progress task older than 24 hours |

#### Agents dispatched

None — status is a read-only overview skill.

---

### review

```
skills/review/SKILL.md (105 lines)
```

**Invocation**: `/conductor:review [track_name]`

**Model**: sonnet

**Purpose**: Reviews completed track work using `track-state.json` for context and commit tracking.

**When to use**: User wants to review a track's implementation quality, check code compliance, or verify test coverage.

**Allowed tools**: Bash, Read, Edit, Write, Grep, Glob, Agent, NotebookEdit, AskUserQuestion

#### Execution workflow

```
1.0 Setup Check (verify conductor environment)
2.0 Review Protocol
    ├── Identify Scope
    │   ├── Auto-detect from tracks registry
    │   ├── Filter by track_name argument
    │   └── Select most recently completed track
    │
    ├── Retrieve Context
    │   ├── SHA range (first commit → last commit from track-state.json)
    │   ├── spec.md (requirements and acceptance criteria)
    │   └── Project context (tech stack, style guides)
    │
    ├── Dispatch code-reviewer subagent
    │   └── Agent receives: TRACK_DIR, TRACK_ID, TRACK_DESCRIPTION
    │
    └── Process Result
        ├── Format review report
        ├── Highlight critical findings
        └── Suggest fixes
3.0 Completion
    ├── Offer to apply fixes
    └── Offer cleanup options
```

#### SHA range computation

The review skill uses `track-state.json` to determine the exact commit range:
- First SHA: from the first task's commit in the track
- Last SHA: from the last task's commit in the track
- This provides a precise `git diff` scope for the code-reviewer

#### Agents dispatched

| Agent | Purpose |
|-------|---------|
| `code-reviewer` | Deep code analysis with plan compliance check |

---

### revert

```
skills/revert/SKILL.md (148 lines)
```

**Invocation**: `/conductor:revert [scope]`

**Model**: sonnet

**Purpose**: Reverts work with `track-state.json` state synchronization.

**When to use**: User wants to revert a task, phase, or entire track while keeping state consistent.

**Argument hint**: `[scope]` (task, phase, or track name)

**Allowed tools**: Bash, Read, Edit, Write, Grep, Glob

#### Execution workflow

```
1.0 System Directive (git-aware revert assistant)
1.1 Setup Check (verify conductor environment)

2.0 Phase 1: TARGET SELECTION & CONFIRMATION
    ├── Auto-detect from tracks registry
    ├── Parse scope argument:
    │   ├── "track <name>" → entire track
    │   ├── "phase <N>" → specific phase
    │   └── "task <desc>" → specific task
    └── Confirm target with user

3.0 Phase 2: GIT RECONCILIATION
    ├── Find ALL commits for target
    │   ├── Scan track-state.json for SHAs
    │   ├── Cross-reference git log
    │   └── Build ordered commit list
    └── Identify dependent commits

4.0 Phase 3: EXECUTION PLAN CONFIRMATION
    ├── Present revert plan
    │   ├── Commits to revert (in reverse order)
    │   ├── State changes (track-state.json)
    │   └── Plan.md marker changes
    └── Get explicit user confirmation

5.0 Phase 4: EXECUTION & STATE SYNC
    ├── Execute Reverts (git revert per commit)
    ├── Update track-state.json
    │   ├── Task revert: mark as [ ] pending
    │   ├── Phase revert: reset all phase tasks
    │   └── Track revert: archive or delete state
    ├── Sync plan.md markers
    ├── Commit State Sync
    └── Verify & Announce
```

#### Scope resolution

| Scope argument | What gets reverted |
|----------------|-------------------|
| `task <name>` | Single task + its commit |
| `phase <N>` | All tasks in phase N |
| `track <name>` | All commits in track |
| (no argument) | Interactive selection |

#### State synchronization rules

| Revert scope | track-state.json change | plan.md change |
|-------------|------------------------|----------------|
| Task | `[x]` → `[ ]`, remove SHA | `[x]` → `[ ]`, remove SHA |
| Phase | All tasks in phase → `[ ]` | All tasks in phase → `[ ]` |
| Track | Status → `cancelled` or delete | N/A |

#### Agents dispatched

None — revert is handled entirely in the main conversation context. This ensures the orchestrator has full control over the state machine during reverting.

---

## Common Patterns

### Setup check pattern

All skills begin with a setup check that verifies the Conductor environment:

```markdown
## 1.1 Setup Check

Verify these exist. If ANY are missing, inform the user:

1. `conductor/` directory exists
2. `conductor/tracks.md` exists (track registry)
3. Track directory exists with required files
4. `track-state.json` exists and is valid JSON

If setup check fails → tell user to run /conductor:setup first.
```

### Resolve paths pattern

Skills that need to discover documentation use a resolve paths step:

```markdown
## 0.0 Resolve Paths

Read conductor/index.md to discover:
- PRODUCT_DOC = conductor/overview/product.md
- TECH_STACK_DOC = conductor/design/tech-stack.md
- TRACKS_REGISTRY = conductor/tracks.md
- WORKFLOW_INDEX = conductor/workflow/index.md
```

### Agent dispatch pattern

Skills dispatch agents via the Agent tool with a structured prompt:

```
Dispatch [agent-name] subagent with these parameters:
- TRACK_DIR: {absolute path}
- TRACK_ID: {id}
- PHASE_INDEX: {n}
- TASK_INDEX: {n}
- TASK_NAME: "{name}"
- AC_CONTEXT: "{acceptance criteria from plan.md HTML comments}"
```

### Result processing pattern

After an agent returns, the skill processes its `---RESULT---` block:

```
Process [agent-name] Result:
1. Parse the ---RESULT--- block
2. On SUCCESS:
   - Update track-state.json with new SHA
   - Sync plan.md markers ([~] → [x] + SHA)
   - Commit state update
   - Return to dispatch loop
3. On FAILURE:
   - Update track-state.json with failure info
   - Write handoff content for retry
   - Return to dispatch loop for retry/skip decision
```

---

## String Substitutions

### Available variables

| Variable | Description | Conductor usage |
|----------|-------------|-----------------|
| `$ARGUMENTS` | All arguments passed at invocation | track name, description, scope |
| `${CLAUDE_SESSION_ID}` | Current session ID | Logging, file correlation |
| `${CLAUDE_EFFORT}` | Active effort level | Adapting workflow detail |
| `${CLAUDE_SKILL_DIR}` | Skill's directory path | Referencing skill files |
| `${CLAUDE_PLUGIN_ROOT}` | Plugin installation path | Referencing plugin resources |

### Argument handling by skill

| Skill | Argument | Usage |
|-------|----------|-------|
| `setup` | (none) | No arguments needed |
| `new-track` | `[track_description]` | Passed to spec-planner |
| `implement` | `[track_name]` | Track selection filter |
| `status` | `[track_name]` | Track filter (optional) |
| `review` | `[track_name]` | Track selection |
| `revert` | `[scope]` | Revert target specification |

---

## Skill-Agent Dispatch Reference

Complete mapping of which skills dispatch which agents:

| Skill | Agent | Context passed | Result expected |
|-------|-------|---------------|-----------------|
| `setup` | `project-analyzer` | PROJECT_DIR, PROJECT_NAME | `---ANALYSIS RESULT---` |
| `setup` | `spec-planner` | TRACK_DIR, TRACK_DESCRIPTION, TRACK_TYPE, USER_ANSWERS, RELATED_DOCS | `---SPEC PLAN RESULT---` |
| `setup` | `spec-reviewer` | TRACK_DIR | `---REVIEW RESULT---` |
| `new-track` | `spec-planner` | TRACK_DIR, TRACK_DESCRIPTION, TRACK_TYPE, USER_ANSWERS, RELATED_DOCS | `---SPEC PLAN RESULT---` |
| `new-track` | `spec-reviewer` | TRACK_DIR | `---REVIEW RESULT---` |
| `implement` | `explorer` | TRACK_DIR, EXPLORE_TARGET, AC_CONTEXT | `---TASK RESULT---` |
| `implement` | `task-executor` | TRACK_DIR, TRACK_ID, PHASE_INDEX, TASK_INDEX, TASK_NAME, AC_CONTEXT | `---TASK RESULT---` |
| `implement` | `phase-checker` | TRACK_DIR, TRACK_ID, PHASE_INDEX, PHASE_NAME | `---CHECKPOINT RESULT---` |
| `implement` | `skip-analyst` | TRACK_DIR, TRACK_ID, PHASE_INDEX, TASK_INDEX, TASK_NAME, RETRY_COUNT | `---SKIP ANALYSIS---` |
| `implement` | `doc-syncer` | TRACK_DIR, TRACK_ID, TRACK_DESCRIPTION | `---DOC SYNC RESULT---` |
| `review` | `code-reviewer` | TRACK_DIR, TRACK_ID, TRACK_DESCRIPTION | `---REVIEW RESULT---` |
| `status` | (none) | Read-only overview | N/A |
| `revert` | (none) | Handled in main context | N/A |

---

## State Management Integration

### track-state.json structure

All skills interact with `track-state.json` as the authoritative state source:

```json
{
  "track_id": "auth-feature_20260512",
  "type": "feature",
  "status": "active",
  "description": "Add authentication flow",
  "current_phase_index": 0,
  "current_task_index": 2,
  "execution_mode": "interactive",
  "updated_at": "2026-05-12T10:30:00Z",
  "phases": [
    {
      "name": "Phase 1: Foundation",
      "tasks": [
        {
          "name": "Set up auth models",
          "status": "completed",
          "sha": "a1b2c3d"
        },
        {
          "name": "Implement login",
          "status": "in_progress"
        },
        {
          "name": "Write auth tests",
          "status": "pending"
        }
      ]
    }
  ]
}
```

### State mutation rules

| Skill | Can mutate | Cannot mutate |
|-------|-----------|--------------|
| `implement` | task status, SHA, phase index | track type, description |
| `new-track` | Creates new track-state.json | Existing tracks |
| `revert` | task status (→ pending), removes SHA | track structure |
| `review` | (none — read-only) | All |
| `status` | (none — read-only) | All |
| `setup` | Creates initial track-state.json | Existing tracks |

### plan.md marker synchronization

When a task status changes, both `track-state.json` and `plan.md` must stay in sync:

| track-state.json | plan.md marker |
|-----------------|----------------|
| `"status": "pending"` | `- [ ] Task` |
| `"status": "in_progress"` | `- [~] Task` |
| `"status": "completed", "sha": "abc"` | `- [x] Task [abc]` |
| `"status": "failed", "sha": "abc"` | `- [!] Task [abc]` |
| `"status": "skipped", "sha": "abc"` | `- [>] Task [abc]` |
| `"status": "deferred", "sha": "abc"` | `- [d] Task [abc]` |
| `"status": "blocked", "sha": "abc"` | `- [#] Task [abc]` |

---

## Content Lifecycle

### Skill loading sequence

```
1. User invokes /conductor:implement auth-feature
2. Claude Code reads skills/implement/SKILL.md
3. $ARGUMENTS replaced with "auth-feature"
4. Content enters conversation as a single message
5. Claude follows skill instructions → dispatches agents
6. Skill content stays in context for session lifetime
```

### Auto-compaction behavior

During auto-compaction:
- First 5,000 tokens of each invoked skill are preserved
- Combined budget: 25,000 tokens across all skills
- Most recently invoked skills are preserved first
- Older skills may be dropped entirely

### Re-invocation

If a skill seems to stop influencing behavior after compaction, re-invoke it:
```
/conductor:implement
```
This restores the full content without losing session context.

---

**Last Updated**: 2026-05-12
