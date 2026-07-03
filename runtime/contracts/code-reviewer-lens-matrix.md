---
type: concept
sources:
  - agents/code-reviewer
  - skills/review
last_verified: 2026-07-03
---

# Code-Reviewer Lens Matrix

Canonical map from each review **lens** → the §3.4 checklist items it runs AND the
§3.1 global sources it loads. Consumed by `agents/code-reviewer.md` §2.6: a lensed
pass loads only its row's sources — the load-bearing context gate that keeps an
N-lens fan-out at roughly 1× a single full pass instead of N×. The lens set is
also the fan-out dimension set `conductor:review` dispatches per-lens producers
over, so the skill's inline lens names and this matrix must stay in sync.

| LENS              | §3.4 items run                                              | §3.1 sources loaded (the gate)                                     |
| ----------------- | ---------------------------------------------------------- | ------------------------------------------------------------------ |
| `bugs`            | 4 — correctness side (bugs, races, null-pointer, error handling) | plan.md, spec.md, track-state.json, tech-stack.md                  |
| `security`        | 4 — security side (injection, XSS, auth, OWASP top 10)     | plan.md, spec.md, tech-stack.md                                    |
| `spec-compliance` | 1 (Plan Compliance) + 7 (Design Doc Consistency)           | plan.md, spec.md, track-state.json, handoff.md, scoped design docs |
| `tests`           | 5 (Testing)                                                | plan.md, spec.md, track-state.json                                 |
| (omitted)         | all 7 (§3.4.1–§3.4.7)                                      | all §3.1 sources (full context)                                    |

**LENS × MODE intersection:** a lensed `refute` re-confirms only findings whose
dimension matches the lens; a lensed `critique` hunts missed classes only within
the lens dimension.

**Documented scope limit, not a silent gap:** §3.4 items 2 (State Consistency),
3 (Style Compliance), and 6 (Skipped/Blocked) are mapped to no lens, so a lensed
pass does not run them — the conductor enforces state-consistency and
skipped-task justification deterministically (track-state lint, phase-checker),
and style is obtainable via a no-lens `full` review.

**Related:** the result JSON each pass emits (including its `"lens"` field) is
defined in [[review-result-schema]].
