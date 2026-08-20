---
type: concept
sources:
  - agents/task-executor
last_verified: 2026-07-13
---

# Step 5 — Refactor (under green)

The procedure reference for the task-executor's **Step 5 (Refactor)**. The
inline §4.0 binding states the **boundary** (the four invariants below); this
doc holds the *how* and *why*. It is loaded on-demand only when Step 5 actually
runs — it is **not** resident in the agent's context otherwise (progressive
disclosure: the executor is a small-window model and context budget is the
scarce resource, so the procedure lives one Read away, not inline).

**Position in the cycle:** Red (3) → Green (4) → **Refactor (5)** → Coverage (6)
→ Deviations (7) → Commit (8). Refactor happens *under* the passing Step-3
tests; Step 6's `PURPOSE=coverage` dispatch doubles as its green-confirm, so
Step 5 adds no extra dispatch.

## The four invariants (the boundary — all binding)

1. **behavior-preserving.** Refactor changes structure, not behavior. If the
   diff touches a public API/signature or anything recorded in `tech-stack.md`
   or a design doc, that is a **Step 7 `SPEC_DEVIATION`**, not a refactor —
   stop and route it there.
2. **diff-scoped.** Touch only files you changed this task
   (`git diff --name-only HEAD~1`). Never "improve" neighboring code — that is
   scope creep that bloats the diff and the context budget.
3. **green-guarded.** The Step-3 tests must stay green. Step 6's
   `PURPOSE=coverage` dispatch is the green-confirm. On `STATUS: green` →
   proceed. On `STATUS: failure` → `git revert <refactor-sha>` and re-run Step 6
   on the Green commit. **Never fix forward** through a refactor regression — a
   small-window model spends more rounds "fixing" a broken refactor than
   reverting it.
4. **bounded.** Cap refactor at **~6 rounds**, counted against the §7.0 tripwire
   (`TRIPWIRE_HARD`, owned by `on-pre-tool-tripwire.py`). If refactor would trip
   the tripwire, **skip it** — a working Green commit outranks refactor. (A
   small-window model can't self-assess "percent of budget," so count rounds,
   exactly as §7.0 does.)

## Procedure

**Mechanical tier (deterministic — do this first).** Resolve the project's
lint/format command from `conductor/workflow/dev-commands/<lang>.md` (`<lang>`
via `conductor/design/tech-stack.md` or `conductor/.conductor/analysis.json` —
the same resolution Step 6 uses for the test command). Run it on your changed
files only; fix what it flags, one concern per fix. If the project documents no
lint command, skip this tier. **If the lint exits clean on your diff, the
mechanical tier is done in one round** — don't manufacture work.

**Tactical tier (judgment — optional, target-bearing).** Under green, make ONE
bounded improvement to the code you just wrote: remove duplication you
introduced, simplify a function you added, apply the loaded styleguide. **State a
one-line target before editing** ("extract the duplicated X", "reduce fn Y's
complexity") — a refactor without a stated target is an open-ended license to
churn, which a small-window model spends poorly.

Refactor only regions your Step-3 tests exercise. For an uncovered region, **add
a test first or skip** — refactoring through a holey net is how refactor
introduces bugs.

## Commit hygiene

Commit the refactor as its own `refactor(<area>): <title>` — never fold it into
the Green commit. The separate commit is what makes `git revert` trivial
(invariant 3) and keeps the diff independently reviewable.

## Nesting fence (no widening)

Step 5 spawns **no subagent**. The lint run is inline Bash (small output); the
green-confirm reuses Step 6's existing `PURPOSE=coverage` dispatch. It therefore
adds no `Agent`-tool dispatch kind, and the §5.0 nesting fence
(`command-digester` per Step 3/6 + opt-in `doc-probe`) is deliberately **not**
widened for it.
