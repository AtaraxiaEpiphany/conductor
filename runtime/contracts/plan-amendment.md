---
type: concept
sources:
  - scripts/track_state/spec_amend.py
  - scripts/track_state/dispatch.py
last_verified: 2026-08-08
---

# Plan Amendment

The single home for how a Conductor track amends its spec when a failure proves
the spec wrong. **Read this on demand and follow it** — the skills that drive the
replan arm (`implement` §3.6) reference it rather than restating the format, so
the two never silently diverge (see [[runtime/contracts/prose-style]] Bucket B).
The splice lives in `scripts/track_state/spec_amend.py`; the staging + ask in
`scripts/track_state/dispatch.py`.

## When an amendment fires

A failure-analyst `replan` verdict means the **spec/plan is wrong**, not the
implementation. The analyst returns the AC details — which criterion is
superseded (`ac_superseded`) and the corrected criterion (`ac_prime_text`). With
those, the spine stages an **in-place amendment** and emits a single informed
confirm (`Apply amendment` / `Edit manually` / `Halt`). Without them, it halts —
the analyst must give the AC specifics; the governing invariant (below) forbids
silently rewriting an AC a downstream gate already measured against.

`Apply amendment` runs `track-state amend-apply`: append a `## Amendment N`
section to spec.md, reactivate the failing task (retry history preserved), inject
a `[Conductor Amendment]` nudge for the re-dispatch, and commit. The track then
resumes — the reactivated task re-dispatches against the amended spec.

## The format: additive only

An amendment is **appended**, never spliced into existing sections:

```markdown
## Amendment 1

- **Supersedes:** AC-2 (the original line is preserved verbatim above; this
  amendment narrows or corrects it).
- **Adds:** AC-2′ — <ac_prime_text>
- **Reason:** <root_cause>
- **Affected tasks:** P1.T2

> Staged by conductor:failure-analyst (replan verdict). Additive: the original
> acceptance criteria are untouched; re-verify affected tasks against AC-2′.
```

The number `N` is `max(existing ## Amendment k) + 1`. The original `- AC-N:` line
is **never touched**. This is load-bearing: `parse_spec`
(`spec_parse.py`) collects ACs only while inside the `## Acceptance Criteria`
section — a `## Amendment N` heading ends that section, so the amendment prose is
not parsed as a duplicate AC. Every downstream "verified against AC-N" stamp
therefore stays truthful, because the supersede is *recorded*, not *executed*.
`cmd_spec_anchors` and `compute_ac_integrity` read the original ACs unchanged.

## The governing invariant

Every freedom added must declare its integrity substitute. Superseding an AC is
the most delicate freedom — it can rewrite what "done" means for work already
completed against the old AC. The amendment preserves soundness three ways:

1. **Additive splice** — the original AC line and every prior stamp stay literal.
2. **One informed confirm** — the only human touchpoint in the whole recovery
   router (see [[runtime/contracts/recovery-policy]]); the amendment is never
   silent.
3. **Re-verify flag** — the `[Conductor Amendment]` injection reaches the
   re-dispatched task, and the `Affected tasks` line scopes which other tasks
   owe a re-verification pass against the new criterion.

## See Also

- [[runtime/contracts/recovery-policy]] — the replan arm's place in the router.
- [[runtime/contracts/prose-style]] — Bucket B: why this has one home.
