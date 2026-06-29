---
type: concept
sources:
  - runtime/core-contract
last_verified: 2026-06-25
---

# Decision: Loop Heartbeat (deterministic-event-driven, opt-in)

Status: **Accepted** — no default wall-clock cron; housekeeping rides
deterministic hooks, with an opt-in user-invoked pulse.

## Context

Conductor is **invoke-driven**: you run `/conductor:implement` and the dispatch
loop pulses until the track completes or you stop. Nothing drives the system on
a wall clock between invocations — there is no daemon. The opt-in self-review
loop (the "Ralph Wiggum" pass, §3.6b of the `implement` skill) is the only
construct with a pulse, and it is default-off.

The harness-engineering literature notes that adding a triage / gc /
doc-garden cron is the *cheapest* step toward a truly autonomous loop. This
record decides whether Conductor should take that step.

## Decision

**Do not add a wall-clock cron as a default.** Keep the system
deterministic-event-driven, and expose housekeeping as an **opt-in
user-invoked pulse** (the user is the clock).

## Rationale

1. **A cron grants no real autonomy here.** A scheduled job only fires while a
   Claude session is open and idle. With no background process, its entire value
   is "housekeeping while you are at the keyboard but not actively dispatching"
   — marginal, and easily replaced by running the pulse on demand.
2. **Wall-clock churn surprises users of a generic plugin.** Spurious gc runs or
   commits appearing on a schedule are unpredictable and hard to attribute.
   Deterministic triggers fire at attributable moments the user already
   initiated.
3. **The housekeeping seam already exists on deterministic events.** The right
   move is to ensure gc / quality-snapshot / doc-garden *ride* those events,
   not to invent a clock:

   | Event | Housekeeping already wired |
   |-------|----------------------------|
   | `SessionStart` | state-consistency check; stale-temp GC; wiki-drift scan; comprehension-debt nudge (latest active track's Critical/High review findings) |
   | `SessionEnd` | `track-state gc`; session-handoff written |
   | `SubagentStop` | result validation / recovery gating |
   | `dispatch-finalize` | per-cycle accounting — the natural seat for a quality-snapshot |

## Opt-in pulse (the autonomous-loop capability, without default churn)

A user-invoked `/conductor:loop` skill (or a `monitors/`-driven trigger) that,
when run, executes the housekeeping bundle below. This is "loop on demand" —
full autonomous-loop capability available to users who want it, invisible to
those who do not.

### Heartbeat bundle spec (what either trigger runs)

1. **gc** — `track-state gc`: archive completed tracks, drop orphaned
   `result.json`, clear stale `in_progress` locks.
2. **quality-snapshot** — aggregate the per-track quality grading already
   emitted in `track-state` output (`quality-snapshot`).
3. **doc-linter drift** — scan the `conductor/design` corpus for provenance
   (`last_verified`) stale against the code.
4. *(optional)* **self-review loop** — the §3.6b pass, default off.

## When a true cron *would* be justified

If Conductor ever runs as a **long-lived background service** — a durable
scheduler / daemon draining a work queue across sessions — a wall-clock
heartbeat becomes the natural driver and this decision should be revisited.
Until then, deterministic events plus the opt-in pulse are strictly better for a
generic, invoke-driven plugin.

## See Also

- [[runtime/core-contract]] — the invoke-driven model this decision preserves.
- [[conductor/design/decision-serial-execution]] — single-active-task model; the loop pulses one task at a time.
