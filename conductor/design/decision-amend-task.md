---
type: concept
sources:
  - conductor/design/any-job-extensibility-2026-09
last_verified: 2026-09-04
---

# Decision: amend-task (the sanctioned mid-flight class mutation)

Status: **Accepted** (2026-09-04) — one new sanctioned mutation
(`track-state amend-task`) closes the gap between "the planner owns labels"
and "the user can correct a running track"; a per-task persona override field
is **DECLINED** in favor of composition. Context: the any-job extensibility
campaign's dynamic-mutation ask — "can I modify task metadata mid-flight so
the workflow changes?"

## Context

Mid-flight mutation surfaces existed for topology (`set-workflow-shape`,
`set-mode`), retry budget (`set-max-retries`), and class vocabulary
(`tag add`). The missing one was the per-task class: a task dispatched with
the wrong `[Tag]` could only be corrected by the *misroute reroute* (a
failure-analyst verdict hardwired to `[Explore]`) or by hand-editing plan.md —
which `reconcile-plan` then flags. The user-visible question "can I change a
task's workflow?" had no sanctioned answer at task granularity.

## Decision

1. **`track-state amend-task <td> --phase P --task T --tag <Tag>`** is the
   sanctioned per-task class mutation. It validates `--tag` against the LIVE
   registry vocab (hard-reject unknown — the same contract `tag add`
   enforces), amends the plan.md task line via the position-keyed
   `_amend_plan_task_tag` (the same helper the reroute path uses — one write
   path, not two), mirrors the name into state with `task_type` re-derived,
   subtasks inheriting the parent's type, and syncs the plan. Top-level only:
   subtasks never carry their own tag.
2. **Per-task persona override (`agent:` field on a task) — DECLINED.** The
   ask was a first-class per-task persona field so a single task could
   dispatch to a different agent. Composition covers it without a new field:
   `track-state tag add <NewTag> --agent <persona>` (exists, roster-validated)
   creates the class whose persona you want, then `amend-task --tag <NewTag>`
   moves the task onto it.

## Why (the three-part test)

- **Hard to reverse:** a per-task persona field would add a fifth override
  site to the persona-binding chain (tag row → roster validation → wave-scope
  pinning → PreToolUse hooks), each of which must learn the exception; the
  serial-rail-only scope pin was settled days earlier and a task-level carve-out
  would reopen it.
- **Surprising without context:** "declining a feature by composing two
  existing ones" looks like an omission until the single-binding-site rule
  (one place maps a class to a persona) is visible.
- **A real trade-off was rejected:** the direct field (zero composition cost
  for a one-off, but N override sites and per-task drift) vs. composition
  (one extra command for a rare operation, zero new binding sites).

## When to revisit

- A project whose tasks routinely need one-off personas (not classes) — if
  `tag add` compositions pile up as single-use classes, the field earns a
  revisit with the wave-scope question re-opened alongside it.

## See Also

- [[conductor/design/task-type-ownership]] — why the name is authoritative
  and `task_type` is re-derived, never overridden
- [[conductor/design/any-job-extensibility-2026-09]] — the campaign this
  closes
