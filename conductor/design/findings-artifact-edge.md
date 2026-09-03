---
type: concept
sources:
  - conductor/design/extensibility-review-2026-08
  - conductor/design/decision-artifact-ledger
  - scripts/track_state/result
  - scripts/track_state/handoff
  - scripts/track_state/plan_parse
  - scripts/track_state/task_context
  - scripts/track_state/dispatch
  - scripts/track_state/wave
  - agents/task-executor
  - agents/explorer
  - agents/phase-checker
  - runtime/contracts/plan-format-contract
last_verified: 2026-09-03
---

# Findings & Artifact Edge — Track D extended (the dataflow contract)

Status: **Shipped** (campaign C1–C7, `56e1c3d..69a70ff`, 2026-09-03, 2950
tests green). Extends the agreed Track D (Finding 2 of
[[conductor/design/extensibility-review-2026-08]]) from the compiled-findings
channel to the broader class it generalizes: task-produced durable files.
Decision record: [[conductor/design/decision-artifact-ledger]].

## Problem

A Conductor plan is a control-flow artifact; its dataflow rides phase order
and prose. When a task produces a durable file a later task needs — a
migration baseline, a mapping table, a coverage number — three channels all
fail to carry it:

- `result.json` is transient (reaped at dispatch-finalize);
- handoffs are per-task keyed (a later task cannot see an earlier one's
  record except through the findings compile, which harvests findings
  *sections* only, not files);
- the envelope names `WORKFLOW_FILE` but nothing names a task artifact —
  the consumer must recall a path it was never given.

Live instance: track `fastjson2jackson_20260902` wrote a pre-migration
baseline at P1.T1.S1 whose only future consumer (P6.T2, TC-1.3 regression
comparison) has no delivered pointer. Same class incoming in that plan: an
interface mapping table (P4.T1), a recorded coverage number (P5.T1).

This is gap class #1 of the extensibility review — *name-the-data,
deliver-by-side-channel* — extended from handoff artifacts and registry
facts to task artifacts.

## Locked decisions (grill 2026-09-03)

- **Deliver + surface, never deny.** Every artifact a later task needs is
  delivered by contract (pointer injection, not path recall); artifacts
  with no declared consumer are surfaced (lint + advisory), never forbidden.
  Forced consumption breeds fake reads.
- **One campaign** — the findings edge (Track D as agreed) and the artifact
  ledger share every seam they touch.
- **Declaration is both runtime truth and planner intent** — result.json
  fields (what the task actually wrote) and plan annotations (what the plan
  promises); verification diffs them.
- **Attestation closes the loop** — the consuming task names what it
  actually read, so the checkpoint can diff should-read vs did-read.
- **No live-track edits** in this campaign; verdict-only for the observed
  track.

## Mechanism — four layers, all on existing seams

1. **Declare (producer).** `write-result` gains repeatable
   `--artifacts '{"path": "...", "role": "..."}'` and `--artifacts-used
   <path>` (the `--deviation` pattern — `flags_all`, not the single-value
   flag table). On finalize SUCCESS, `_append_execution_record` rolls them
   into the task's handoff as a `## Task Artifacts` block (`### Produced` /
   `### Used` bullets). One roll site covers the dispatch hot path, wave
   members, and the legacy process-result path (all funnel through
   `_append_execution_record`; wave finalizes via `_finalize_task` with the
   main track dir).
2. **Plan edges (intent).** The plan-format contract's Inter-Task
   Dependencies section gains the couplet `<!-- produces: path -->` on the
   producing task row and `<!-- uses: path -->` on the consuming row —
   paths repo-relative, forward references allowed (produces precedes
   uses). `parse_plan` extracts them beside `deps:`; malformed annotations
   deny at the plan lint (same class as malformed AC/TC refs); dangling
   `uses` and orphan `produces` warn.
3. **Deliver (consumer).** `compute_task_context` gains an `artifacts`
   section: this task's `uses` refs resolved to absolute paths, plus every
   ledgered artifact from strictly-earlier tasks (lexicographic
   `(phase, task)` on the handoff stem — same-phase serial edges included;
   self, future, and later same-phase tasks excluded). The executor already
   fetches task-context at Layer 1 — pointers arrive by contract, no new
   envelope line. Same-phase *parallel* (wave) edges are deliberately not
   delivered pre-completion; that is exactly what the checkpoint advisory
   surfaces.
4. **Verify (checkpoint + authoring).** A pure, fail-open
   `artifact_advisory` folds into the phase-checker envelope
   (`ARTIFACT_ADVISORY=...`, report-only — never gates a verdict): (a)
   *orphan* — a produced artifact with no `uses` edge anywhere; (b)
   *unattested* — a `uses` edge whose consumer completed without an
   attestation. The plan lint carries the same signals at authoring time
   (full-content writes only; edit fragments are blind — the runtime
   advisory covers that residual).

Plus the agreed Track D items, unchanged: the `FINDINGS_FILE` envelope line
on both executor and explorer arms (emitted only when the compiled doc
exists; absent line = none recorded), compile-at-checkpoint on the FAILED
arm as well (one fail-open wrapper, two call sites), and the wave copy of
track-findings.md into member worktrees (the file is gitignored; worktrees
check out at the wave base — without the copy the mirror would be a
permanent silent no-op).

## Invariants

- **No new injection channel.** Pointers ride the existing envelope
  (`FINDINGS_FILE`, like `WORKFLOW_FILE`) and the existing task-context
  fetch. Tier-A/B/C discipline of the context model is untouched.
- **Fail-open everywhere.** A ledger, lint, or advisory failure never
  blocks a dispatch, finalize, or checkpoint.
- **Nothing new persists into track-state.json.** result.json fields are
  transient as today; the durable copy is the handoff block and the
  findings catalog; the state schema stays closed.
- **Determinism.** The advisory is a pure function of file contents and
  state (sorted walks, no mtimes, no clock in the string) — dispatch
  replay stays byte-identical.
- **Caps and staleness unchanged.** Eight bullets per kind per handoff
  file; `_source_age_label` ages each catalog entry; corpus graduation
  supersedes at archive as today.

## Terminology (see glossary)

**Task artifacts** are durable files a task produced for later consumption
— distinct from *Artifact Anchors* (spec-side AC grounding for review
shapes) and from *build artifacts* (forbidden outputs like `dist/`).

## Observation for Track E (root cause, not fixed here)

The live track that exposed this gap also mis-selected its workflow shape:
its brief and description are Chinese, and shape `signals` are English-only
keywords — zero hits, so the confirm defaulted. Bilingual signal matching
(or a language-aware ranking step) is labeling work: Track E owns it. This
campaign records the observation only.

## Seeds

- Envelope: FINDINGS_FILE present-when-exists on both arms; explorer arm
  still carries no WORKFLOW_FILE.
- Ledger: repeatable flags (both `--flag val` and `--flag=val` forms, JSON
  with spaces); SUCCESS-only roll; harvest capped + deduped; catalog
  rendered with age labels; the findings-render emptiness check extended
  to the new buckets.
- Grammar: extractor triples beside deps; warnings never errors; refs
  never reach track-state.json.
- Delivery: earlier-task filter matrix; fail-open on missing plan/handoffs.
- Advisory: orphan, unattested, clean, and exception cases; purity pinned.
