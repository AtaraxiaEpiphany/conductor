---
type: concept
sources:
  - agents/code-reviewer
  - skills/review
last_verified: 2026-07-03
---

# Review Result Schema

Canonical JSON shape written by `agents/code-reviewer.md` §4.1 to `{RESULT_PATH}`
(defaults to `{TRACK_DIR}/.conductor/review-result.json`). Reproduce this
structure verbatim via a Bash heredoc. Read by the `conductor:review` "Apply
Fixes" path and the post-loop auto-review, both of which parse this exact file.

```json
{
  "status": "SUCCESS",
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
  "stats": {"critical": 0, "high": 0, "medium": 0, "low": 0}
}
```

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

**Stdout is separate and terse** — the 4-line `---REVIEW RESULT---` block lives
inline in `agents/code-reviewer.md` §4.2; only the full findings live in this JSON.

**Related:** the lens each pass ran under is defined in [[code-reviewer-lens-matrix]].
