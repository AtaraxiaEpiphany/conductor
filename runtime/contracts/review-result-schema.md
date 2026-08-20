---
type: concept
sources:
  - agents/code-reviewer
  - skills/review
last_verified: 2026-08-20
---

# Review Result Schema

Canonical JSON shape written by `agents/code-reviewer.md` §4.1 to `{RESULT_PATH}`
(defaults to `{TRACK_DIR}/.conductor/review-result.json`). Reproduce this
structure verbatim via a Bash heredoc. Read by the `conductor:review` "Apply
Fixes" path and the post-loop auto-review, both of which parse this exact file.

```json
{
  "status": "SUCCESS",
  "verdict": "APPROVE|APPROVE_WITH_COMMENTS|CHANGES_REQUESTED",
  "summary": "<single sentence>",
  "mode": "full|refute|critique",
  "lens": "bugs|security|spec-compliance|tests|null",
  "checks": {
    "plan_compliance": "Yes|No|Partial",
    "state_consistency": "Consistent|Inconsistent",
    "style_compliance": "Pass|Fail",
    "design_doc_consistency": "Yes|No|N/A",
    "new_tests": "Yes|No",
    "test_coverage": "Yes|No|Partial",
    "test_results": "Passed|Failed|Not_Run",
    "skipped_tasks": "None|N_skipped"
  },
  "findings": [
    {"severity": "Critical|High|Medium|Low", "title": "...", "file": "path", "lines": "L1-L2", "context": "why", "suggestion": "fix"}
  ],
  "state_issues": "None|<description>",
  "stats": {"critical": 0, "high": 0, "medium": 0, "low": 0},
  "lens_verdicts": {"<lens>": {"verdict": "APPROVE|APPROVE_WITH_COMMENTS|CHANGES_REQUESTED", "critical": 0, "high": 0, "medium": 0, "low": 0}}
}
```

**`status` vs `verdict` (do not confuse them):** `status` is the **agent-run**
status (`SUCCESS` the review completed / `FAILURE` it crashed) — it says nothing
about the code. `verdict` is the **review judgment** of the code
(`APPROVE` / `APPROVE_WITH_COMMENTS` / `CHANGES_REQUESTED`) — the same value the
inline `---REVIEW RESULT---` block carries as `STATUS:`. Persisting `verdict`
here (mirroring the stdout block) closes the gap where the review judgment lived
only in ephemeral stdout; the post-loop spine also stamps it to the committed
sidecar (`review_verdict`) so a completed track's verdict is auditable on disk.

**`mode`-specific `findings` semantics:**

- **`full`** — the complete findings list (all severities the analysis surfaced).
- **`refute`** — `findings` holds the **survivors**: producer findings that held
  up under re-examination against the actual code. `stats` reflects survivor
  counts; include a `"refuted": <count>` field for transparency. Default to
  refuted when uncertain — a finding that cannot be positively re-confirmed does
  not survive (the cure for producer self-certification / self-preferential bias).
- **`critique`** — `findings` holds **only newly-discovered** defect classes the
  producer pass plausibly missed (may be empty — an honest "nothing missed").

In `refute` and `critique` the `checks` block is optional (the narrower modes may
not exercise every checklist item); always emit `"mode"` (and `"lens"`) so the
orchestrator's synthesis step knows which pass wrote the file.

**`lens_verdicts` (optional — finalized file only):** the orchestrator's
finalize step (`conductor:review` §2.3) adds one entry per lens —
`{"verdict": <review judgment>, "critical": n, "high": n, "medium": n,
"low": n}` — the per-axis record §2.4 renders **side by side**. The two axes
(Standards: `bugs`/`security`/`tests` — Spec: `spec-compliance`) are never
merged or re-ranked into one list; `stats` stays the merged totals the
"Apply Fixes" path consumes. Per-lens producer/refute files do NOT carry this
field — it is a synthesis product, written once at finalize.

**Stdout is separate and terse** — the 4-line `---REVIEW RESULT---` block lives
inline in `agents/code-reviewer.md` §4.2; only the full findings live in this JSON.

**Related:** the lens each pass ran under is defined in [[code-reviewer-lens-matrix]].
