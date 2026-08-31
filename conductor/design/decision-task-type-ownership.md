---
type: concept
sources:
  - conductor/design/task-type-ownership
last_verified: 2026-08-31
---

# Decision: Task-Type Ownership (planner-authored labels)

Status: **Accepted** (2026-08-31, grill-resolved) — task-tag assignment flips
from matcher-owned to planner-authored; the matcher demotes to an advisory
lint; a misroute verdict closes the loop for in-flight plans. Full decision
set: [[conductor/design/task-type-ownership]].

## Context

Task types in plan.md were mostly empty or wrong; untagged exploration work
routed to task-executor, which executed it as a code task. The grill's premise
challenge redirected the ask from "redesign task types for dynamic execution"
to "re-home label authorship": the registry-driven tag mechanism (route ×
workflow × exemptions × docfile) is sound; the defect was that a zero-context
keyword matcher owned a content judgment while the context-rich planner was
forbidden from making it, and nothing recovered a wrong label once dispatch
started.

## Decision

1. Labels are **authored content**: spec-planner writes each top-level task's
   tag from the registry vocab by judgment (no matcher round-trip).
2. The matcher (`rank_tags` / `derive_task_tag`) survives only as the
   **advisory lint** inside `init-from-plan --check` — declared-vs-signals
   disagreements are telemetry, not gates. The `propose-tags` subcommand is
   deleted.
3. Recovery is a **verdict, not an override**: task-executor self-reports
   exploration-shaped work with a deterministic MISROUTE signature (tagged or
   not); failure-analyst's `misrouted_explore` → `reroute_explorer` arm
   amends the `[Explore]` tag into plan.md + state and re-dispatches —
   durable across reconcile because the label, the actual defect, was fixed.
4. Runtime per-dispatch classification stays **declined** (D2): determinism
   and byte-identical replay govern control flow; task labels are authored
   content, which is exactly why moving them to the planner is safe.

## Gate check (all three hold)

- **Hard to reverse:** changes the plan-authoring contract (spec-planner body,
  new-track relay prose), deletes a command-surface subcommand, and adds a
  recovery verdict arm with plan-amendment semantics.
- **Surprising without context:** it inverts the mechanical-match rule the
  same agent body used to mandate, and it resembles the deferred D2
  "model-judgment selection" while being its opposite — judgment over
  authored content, never over control flow.
- **A real trade-off was rejected:** dispatch-time route overrides (silently
  reverted by reconcile — rejected), keeping `propose-tags` as an advisory
  query (a dead route implying the matcher still owns labels — rejected), and
  the broader runtime-classifier redesign (D2 territory — rejected).

## When to revisit

- Lint telemetry showing planner labels systematically disagreeing with
  signals on real tracks → fix vocab/`when_to_use` prose, not ownership.
- A second misroute class appearing in the wild → extend the same
  signature + verdict + tag-amendment pattern.

## See Also

- [[conductor/design/task-type-ownership]] — decisions, seams, test inventory
- [[conductor/design/decision-planning-as-data]] — the vocab render R1 reads
- [[conductor/resource/glossary]] — **task-type ownership** entry
