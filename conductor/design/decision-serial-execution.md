---
type: concept
sources:
  - runtime/core-contract
last_verified: 2026-06-25
---

# Decision: Serial Execution Model (single active task)

Status: **Accepted** — serial, globally locked. Recorded so the tradeoff is
conscious rather than accidental.

## Context

Conductor dispatches work one subagent at a time. The dispatch loop is
`RECOVER → DISPATCH (one task) → PROCESS → dispatch-finalize`, gated by **F1 —
Global State Lock** ([[runtime/core-contract]]): exactly one `[~]` task may be
active in the whole project at any moment (one flat task, or one parent + one
child subtask). `track-state.json` is single-writer; the orchestrator is a thin
state machine; each subagent self-loads its own business context.

The harness-engineering literature this plugin draws from champions the
alternative: worktree-isolated parallel agents — many tasks `in_progress` at
once, each in its own checkout, coordinated by per-track or per-task locking.
That model trades coherence for throughput (the "rung-4" ceiling).

## Decision

**Keep serial.** The plugin as shipped runs one active task globally; F1 stays a
global lock. Parallel execution is not added.

## Rationale

1. **Coherence over throughput for spec-driven TDD.** Each task builds on a
   verified predecessor — F2 (failing test first) and F5 (phase checkpoint)
   enforce that the prior unit is done and green before the next starts. Two
   tasks mutating the same spec/code/test artifact in parallel risk divergence
   and merge conflicts exactly where TDD demands a single linear sequence.
2. **Linear, replayable audit trail.** A single writer makes `track-state.json`
   + git notes an unambiguous history. Parallel writers would need state merge
   or arbitration, eroding the audit property the recovery spine depends on.
3. **Cheap enforceability.** F1 is a single global invariant, checked by one
   transaction context manager (the #6 work). Per-track locking needs
   track-scoped transactions, inter-track dependency resolution, and N-way
   result aggregation — real complexity for a generic plugin.
4. **Context budget.** Each dispatched agent already self-loads substantial
   context. Fanning out N concurrent agents multiplies token cost and forces the
   orchestrator to hold N `result.json` handoffs at once.

## When to revisit (the documented escape hatch)

Move to per-track locking only if **both** hold: rung-4 throughput becomes a
goal *and* work decomposes into genuinely independent tracks (disjoint code
areas, no shared spec coherence). The migration path, building on the #6
transaction context manager:

- Relax F1 from global to per-track — at most one `[~]` per `track_id`,
  enforced by a track-scoped lock rather than a global one.
- Scope the transaction's state lock to `track_id`.
- Add a result-aggregation step to `dispatch-finalize` to merge N `result.json`
  handoffs into one orchestrator decision.
- Resolve inter-track ordering / dependencies (a task in track B blocked on an
  artifact track A produces).

The serial choice is therefore by design, not by oversight: the state model is
globally locked with a clear (unexercised) path to per-track locking if and when
throughput justifies the added complexity.

## See Also

- [[runtime/core-contract]] — F1 Global State Lock; the invariant this decision preserves.
- [[conductor/design/doc-conventions]] — corpus-authoring conventions.
