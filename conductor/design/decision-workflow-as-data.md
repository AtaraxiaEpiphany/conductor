---
type: concept
sources:
  - conductor/design/dispatch-manifest
  - conductor/design/decision-pattern-realization
  - conductor/design/single-source-authority
last_verified: 2026-08-20
---

# Decision: Workflow-as-Data (dispatch-manifest seam)

Status: **Accepted** (2026-08-20, grill-resolved) — per-task workflow
becomes registry-derived data pointed at per dispatch; the agent roster
keeps harness-shell specialization. Full decision set and implementation
campaign: [[conductor/design/dispatch-manifest]].

## Context

An extendability grill asked whether conductor should move to "one general
subagent + a dynamically generated prompt file." The premise challenge
unbundled that into two pains — workflow prose welded into shared
templates/registry JSON strings, and executors re-deriving per-dispatch
path decisions from branching prose — neither of which requires collapsing
the roster.

The decision sits in a crowded prior-decision space and must not be
confused with its neighbors: 1B declined a `step_sequence` axis (second
representation of step semantics = drift surface); D2 (dynamic spine) and
D1 (native Workflow) are deferred, not rejected; the phase-verify
apparatus was unwound to a single path. "Workflow-as-data" here means
something narrower than all of those: **the step prose's home moves into
registry-indexed docfiles, and each dispatch gets a pure-composed pointer
artifact. Nothing else about dispatch changes.**

## Decision

1. **Keep role shells.** Agents stay specialized at the harness level
   (model tier, tool fences, maxTurns, permissionMode) — dimensions a
   prompt cannot express. The floated general-executor collapse is
   rejected.
2. **Workflow becomes data.** Each task-type's step prose lives in a
   docfile (`templates/workflow/steps/<name>.md`, project-overridable),
   named by its registry row's `workflow_doc`. Extending = one docfile +
   one registry row; zero plugin edits.
3. **Derive-and-point, never restate.** A pure `compose_manifest` writes a
   per-dispatch pointer artifact (path decision, resolved gates, docfile
   pointers); executors read the manifest and self-load their docfile. No
   `step_sequence`; no agent-brief cache; no new injection channel.

## Rationale

1. **The node test separates the axes.** Harness shells differ where the
   harness differs (model, tools, turns, permissions); workflow prose
   differs where the *process* differs. The general-executor bundle
   crossed the axes — it traded real harness specialization for workflow
   flexibility that data alone provides. Keeping shells while making
   workflow data gets both.
2. **Single-source discipline.** Restating workflow per agent or per task
   is the cache anti-pattern ("a document restating the environment is a
   cache" — caches drift). 1B already declined one restatement surface;
   an agent-brief cache is the same shape at larger scale. Pointing keeps
   exactly one representation: the docfile, registry-indexed.
3. **Extensibility lands where the ask pointed.** Today a project wanting
   a bespoke workflow edits plugin templates and executor branching. After:
   one docfile + one overlay row — the same project-wins rule the
   registries already use.
4. **Purity preserves replay.** `compose_manifest` is pure over
   (track state ⊕ registries), so retries and resumes get byte-identical
   manifests across plugin upgrades — the same property that makes
   `build_dispatch_prompt` safe to replay.

## Gate check (all three hold)

- **Hard to reverse:** moves the workflow prose's home (template → steps
  library), adds a registry field with a validator cross-check, retargets
  every executor dispatch path, and adds envelope/marker/gitignore seams.
- **Surprising without context:** it resembles the declined 1B and the
  deferred D2 while being neither — derive-and-point vs. a step axis;
  per-dispatch pointer artifact vs. a dynamic spine.
- **A real trade-off was rejected:** the general-executor collapse (loses
  harness-shell enforcement) and the agent-brief cache (restatement drift)
  were both live options with named costs.

## When to revisit

- **≥2 task-types need bespoke step *semantics*** (not just prose) — that
  is 1B's re-proposal bar; revisit the step-axis question then.
- **Manifests grow beyond pointers** (status, progress, results) — they
  would be duplicating `track-state.json`; promotion to state is the
  signal the seam was wrong.
- **D2/D1 conditions unchanged** — see
  [[conductor/design/single-source-authority]] Non-goals.

## See Also

- [[conductor/design/dispatch-manifest]] — the full decision set (D1–D7)
  and the phased campaign this record governs
- [[conductor/design/decision-pattern-realization]] — the two-rail
  assignment; construction stays welded to the state machine (unchanged by
  this decision)
- [[conductor/design/single-source-authority]] — the Delete→Point ladder
  D2's relocation follows, and the deferred-items Non-goals honored here
- [[conductor/resource/glossary]] — **dispatch manifest**, **workflow
  docfile** settled vocabulary
