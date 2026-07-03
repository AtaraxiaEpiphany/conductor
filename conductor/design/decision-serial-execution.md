---
type: concept
sources:
  - runtime/core-contract
last_verified: 2026-07-01
---

# Decision: Serial Execution Model (single active task)

Status: **Accepted** — serial by default, globally locked. An opt-in **within-track
wave parallelism** escape hatch (worktree-isolated, deps-gated) has been added on
top of the serial spine; per-track locking remains deferred. Recorded so the
tradeoff is conscious rather than accidental.

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

**Keep serial as the default.** The plugin runs one active task globally; F1
stays a global lock for the serial spine. Parallel execution is **opt-in**:
`conductor:parallel` (`skills/parallel/SKILL.md`) relaxes F1 to a *wave lock*
**only** while a sidecar ledger (`.conductor/parallel.json`) records in-flight
members, and only for tasks the plan author declared file-disjoint via
`<!-- deps: -->`. Everything else stays strictly serial.

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

### Within-track wave parallelism (shipped)

The rung-2 → rung-3 step the deps substrate unlocked: real within-track
throughput for phases that decompose into genuinely independent tasks, **without**
abandoning the serial spine or the linear audit trail. Opt-in via
`conductor:parallel`:

- **Worktree isolation, not shared-state parallelism.** Each wave member runs in
  its own `git worktree` (its own index, its own `track-state.json` checkout, its
  own `result.json`). No two agents ever touch the same git index or state file.
  The orchestrator integrates each member's commit back serially via squash-merge,
  so the main branch keeps one-conductor-commit-per-task linear history.
- **F1 relaxes to a wave lock, not removed.** While a ledger records in-flight
  members, F1's "one `[~]`" count exempts those members (the ledger, not F1,
  authorizes their parallel `in_progress`). The moment the wave drains, strict
  serial resumes. Three guard sites (`validate`, `lint-track-state`,
  `dispatch`) reconcile the two modes; the serial spine refuses to interleave
  with an active wave.
- **Conservative ready-set.** Only pending, flat, executor-routed tasks with a
  `<!-- deps: -->` comment whose every declared dependency is satisfied
  (completed/skipped/deferred) enter a wave. Tasks with no deps comment are
  assumed serial-order-dependent and stay on the spine. The author opts each task
  in by declaring deps.
- **Serial fallback + reuse.** When no ready-set exists, the skill falls to the
  serial spine (`dispatch-next`), so serial tasks make progress and may satisfy
  deps for the next wave. Failed members drain as `failed` and are handled by the
  serial retry/skip/block path — no separate parallel recovery machinery.

This realizes the scheduler the `collect_deps` substrate was built for, at a
fraction of the per-track-locking complexity, because each worktree is a full
isolated checkout rather than a coordinated shared-state writer.

### Per-track locking (still deferred)

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
globally locked, the within-track wave escape hatch ships in worktree-isolated
form, and per-track locking remains a clear (unexercised) path if and when
cross-track throughput justifies the added complexity.

## See Also

- [[runtime/core-contract]] — F1 Global State Lock; the invariant this decision preserves (relaxed to a wave lock only while `.conductor/parallel.json` is active).
- [[runtime/contracts/plan-format-contract]] — the `<!-- deps: -->` annotation the wave ready-set consumes.
- [[runtime/contracts/doc-conventions]] — corpus-authoring conventions.
- [[conductor/design/decision-pattern-realization]] — extends this model with analysis-side patterns (adversarial review, tournament) on the Workflow-tool rail; construction stays on the prose-skill rail.
