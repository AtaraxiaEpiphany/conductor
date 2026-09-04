---
type: concept
sources:
  - scripts/track_state/misc.py
  - scripts/track_state/reconcile.py
last_verified: 2026-09-04
---

# Phase-Gate Replanning

The single home for the re-derive pass a Conductor track offers at a PASSED
phase checkpoint. **Read this on demand and follow it** — the skills that drive
phase boundaries (`implement` §3.7, `implement-step`'s `dispatch_phase_checker`
row) reference it rather than restating the procedure, so the two never silently
diverge (see [[runtime/contracts/prose-style]] Bucket B). The offer marker lives
in `scripts/track_state/misc.py` (`_write_replan_marker_fail_open`,
`cmd_replan`); the apply mechanics are `reconcile-plan`'s existing name-keyed
sync.

## The model: rolling-wave planning at phase boundaries only

A track's plan is authored up front and then *fixed* — the spine replays it, it
does not re-derive it mid-flight (dynamism at phase gates only, confirm-gated).
But each completed phase produces real information: track-findings from the
phase's tasks, verdicts from its gates, surprises its executors hit. Throwing
that information away and dispatching remaining phases verbatim wastes it;
re-deriving continuously would make the plan unauditable and the replay
non-durable. The seam is the phase gate: **at each PASSED checkpoint, exactly
one bounded re-derive pass may propose amendments to the remaining rows.**

The pass runs in the orchestrating session (the main conversation that drives
the skill) — no new agent. It is judgment over *authored content* (remaining
plan rows), which is safe at a boundary; it is never judgment over *control
flow* (shape, mode, recovery policy, the spine itself).

## The handshake (once per checkpoint, mechanically enforced)

1. **Staging.** A PASSED checkpoint stamp writes `.conductor/replan-pass.json`
   = `{"phase": N}` — single-homed in `misc._stamp_checkpoint_in_plan`, so both
   stamp paths (Rail A `add-checkpoint` via the phase-checker agent, Rail B
   `phase-checkpoint-review`) stage exactly one offer. A last-phase stamp stages
   nothing (no remaining work to re-derive). A waived checkpoint (checkpoint
   policy skip) stages nothing — no verdict event happened.
2. **Polling.** At the phase boundary, the orchestrator runs
   `track-state replan "<track_dir>"`. `replan_due: true` carries `phase` (the
   stamped phase) and `remaining_phases`. Not due (`replan_due: false`) →
   continue to the next phase; nothing to announce.
3. **Acking.** Whatever the pass concludes, it ends with
   `track-state replan "<track_dir>" --ack` (consumes the marker; idempotent —
   an ack with no pending offer is `acked: null`, never an error). The ack is
   the mechanical "one pass per checkpoint" guarantee: a re-offer for the same
   checkpoint is impossible until a phase is re-verified and re-stamped.

## The pass (when `replan_due: true`)

**Inputs, read in this order:**

- `spec.md` — read-only. The spec is the contract; if the pass believes the
  *spec* is wrong, that is the failure-analyst amendment machinery
  ([[runtime/contracts/plan-amendment]]), not this pass.
- `.conductor/track-findings.md` — just compiled by the stamp itself. The
  phase's durable findings: what was learned, what surprised, what broke.
- The remaining plan rows — every task in phases after the stamped one.

**Derive proposals.** Compare what the findings say against what the remaining
rows assume. Amendment classes (any, all, or none):

- **Add** an `[Explore]` task — findings surfaced ground nobody mapped (a new
  integration surface, an unknown data format, a dependency nobody probed).
- **Split** a task that grew — a remaining row now clearly spans two
  deliverables with different verification stories.
- **Reorder** — a dependency flipped; a later row must land before an earlier
  one.
- **Drop** — a remaining row is mooted (the finding shows its outcome already
  exists, or its premise was wrong).
- **None** — the common case. Findings consistent with remaining rows.

**Confirm.** One `AskUserQuestion`, the recommended option first, `No
amendments — continue as planned` always among the options. An empty proposal
(`None` derived) skips the question entirely — silent continue, straight to the
ack. No batching of questions, no follow-up confirms.

**Apply.** On confirmation: edit the remaining rows in `plan.md` (add the task
line, split the row, swap the order, delete the row), then
`track-state reconcile-plan "<track_dir>"` (dry-run) to read the buckets, then
`--apply`. `reconcile-plan` is name-keyed and SHA-preserving — completed work
keeps its SHAs, the amendment lands as a committed plan edit. Do not hand-edit
`track-state.json`; sync flows plan → state, never backwards.

**Then ack** (`track-state replan "<track_dir>" --ack`) and continue to the
next phase.

## Constraints (invariant under every amendment)

1. **Completed phases never reopen.** Edits touch only rows in phases after
   the stamped one. A completed phase's rows, SHAs, and findings are settled
   history.
2. **One pass per checkpoint.** The marker + ack enforce it; there is no
   second bite even if the pass found nothing.
3. **The shape is immutable mid-track.** No amendment changes
   `workflow_shape`, execution mode, or recovery policy. Those are control
   flow (the twice-declined D2 line); a track that needs a different shape is
   a new track.
4. **Within-phase dispatch is frozen.** The pass runs at the boundary, after
   the stamp, before the next phase's first dispatch — never between tasks of
   a live phase.
5. **The spec is an input, not an output.** This pass amends plan rows. Spec
   text changes go through the amendment machinery with its own confirm, never
   by shadowing the spec inside the plan.
