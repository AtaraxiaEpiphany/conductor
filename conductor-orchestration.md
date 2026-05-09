## Orchestration Layer

Loaded by `/conductor:implement` and other execution skills.

### Orchestrator Contract

1. **Zero File Reads**: The orchestrator NEVER reads `spec.md`, `plan.md`, or workflow templates. All business context is loaded by subagents.
2. **CLI-Only State**: All state mutations go through `track-state` CLI. The orchestrator NEVER reads/writes `track-state.json` directly.
3. **Compact Dispatch**: Subagent prompts contain only task identity + file paths (~100 tokens). Subagents self-load all context.
4. **Result Minimalism**: Parse only `status`, `sha`, `deviations` from results. Implementation details stay in subagent context.

### Dispatch Loop

```
RECOVER → SELECT → PRE_DISPATCH → DISPATCH → PROCESS → PHASE_BOUNDARY → FINALIZE
```

### Subagent Registry

| Subagent | Dispatch Tag | Purpose |
|----------|-------------|---------|
| `conductor:task-executor` | default | TDD implementation (Steps 3-9) |
| `conductor:explorer` | `[Explore]` | Read-only codebase investigation |
| `conductor:skip-analyst` | on failure (retries exhausted) | Skip safety analysis |
| `conductor:phase-checker` | on phase completion | Phase checkpoint verification |
| `conductor:doc-syncer` | on track completion | Project documentation sync |
| `conductor:code-reviewer` | on track completion (auto-review) | Deep code review with diff analysis |
| `conductor:spec-planner` | setup/newTrack | spec.md and plan.md generation |
| `conductor:spec-reviewer` | setup/newTrack | Interactive spec/plan review (keeps full files out of orchestrator context) |
| `conductor:project-analyzer` | setup | Brownfield project analysis |

### Execution Modes

| Mode | Storage | Behavior |
|------|---------|----------|
| `interactive` | `track-state.json` `execution_mode` field (default) | Pauses for user confirmation at checkpoints. `[Manual]` tasks are always auto-deferred. |
| `continuous` | `track-state.json` `execution_mode` field | Auto-proceeds through checkpoints. `[Manual]` tasks are always auto-deferred. |

**Default:** If `execution_mode` field is absent from `track-state.json`, the implement skill defaults to `interactive`.
**Set during:** `/conductor:new-track` Section 2.5 (mode selection before state init).
**Read during:** `/conductor:implement` Section 3.0 (extracted from `track-state recover` output).

### Task Lifecycle

```
pending → in_progress → completed | failed → (retry) → in_progress
                                     → skip_analysis → skipped | blocked
pending → deferred (auto, by [Manual] tag) → completed (human verifies later)
blocked → pending (human reset) | Any → cancelled
```
