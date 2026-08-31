---
type: resource
sources:
  - conductor/design/dispatch-manifest
  - conductor/design/planning-as-data
  - conductor/design/task-type-ownership
  - conductor/design/extensibility-review-2026-08
  - runtime/contracts/grill-discipline
last_verified: 2026-08-31
---

# Glossary

Settled vocabulary for conductor grills and docs
([[runtime/contracts/grill-discipline]] §7). Each entry: the term, a tight
definition, and an **Avoid-list** of the rejected synonyms. Once recorded, a
term is shared-known — don't re-ask it, don't rephrase it. Append-only; the
doc-sync pass merges alongside these entries, never duplicates or clobbers
them ([[runtime/contracts/doc-sync-procedure]]).

## dispatch manifest

The per-task pointer artifact `<track>/.conductor/dispatch-manifest.md`,
composed deterministically by pure code at dispatch-prepare from the two
registries — task identity, resolved gates/exemptions, the path decision
(fast-path | bespoke docfile | default-TDD), and docfile pointers — and
reaped at finalize. It is the executor's derived view of **where its
workflow lives**, never the workflow itself. Transient and gitignored;
byte-identical on retry.

**Avoid:** *agent-brief* (a cached restatement of the environment — caches
drift; the manifest points, it does not restate), *prompt-file* (suggests
an LLM generates it per dispatch; composition is pure code over registries).

## workflow docfile

A markdown file in the steps library
(`templates/workflow/steps/<name>.md`, project-overridable at
`conductor/workflow/steps/<name>.md`, project wins) carrying **one
task-type's step prose**, named by its registry row's `workflow_doc` field.
Docfiles carry steps only; gates, exemptions, and verifiers stay
registry-owned shape fields. A project adds a full custom workflow = one
docfile + one registry row, zero plugin edits.

**Avoid:** *step_sequence* (declined 1B: a machine-readable step list would
be a second representation of step semantics — a new drift surface),
*task-steps fragment* (underspecifies who owns gate semantics — the
registry does, not the fragment).

## planning docfile

A markdown file in the planning library
(`templates/planning/<name>.md`, project-overridable at
`conductor/planning/<name>.md`, project wins) carrying **one shape's
planning procedure** — an orchestrator-facing Prelude (pre-planning steps,
e.g. research-first's explorer dispatch) and a planner-facing body
(spec/plan authoring guidance). Named by the shape row's `planning_doc`
field; docfiles carry procedure only, policy (signals, gates, verifiers,
grounding) stays registry-owned. A project adds a custom planning workflow
= one docfile + one shape row, zero plugin edits.

**Avoid:** *play / playbook* (rejected coinage — breaks the settled
workflow-docfile parallel), *planning manifest* (a per-track composed
artifact explicitly declined — planning has no replay concern; the pointer
is the envelope field, not an artifact).

## shape proposal

The pure, deterministic `track-state propose-shape` emission: shapes ranked
by registry `signals` matched over the track description ⊕ brief, with
per-candidate rationale and a confirm-required flag for non-default shapes.
Selection is control flow (code); the choice is the user's (one confirm).

**Avoid:** *shape inference* (implies silent, unconfirmed selection — the
proposal is always surfaced, and consequential shapes are confirmed).

## agent roster

The third registry (`templates/workflow/agent-roster.json` baseline ⊕
`conductor/workflow/agent-roster.json` overlay, project rows added/wins):
one row per scaffolded agent — `class` (executor / verifier / reviewer /
advisory, deriving the single-writer default), `fence` (the exact
result-format reminder string), and override flags. The roster contracts
**named** agents; it never selects them. An agent absent from the merged
roster is *unrostered*: it dispatches fine (the harness resolves three
name homes) and runs fail-open with no scaffold — the declared-names lint
is the guard, not a runtime deny.

**Avoid:** *agent registry* (reads as a fourth task-semantics registry —
this one contracts dispatch behavior), *scaffold map* (it is merged
overlay data, not a generated artifact).

## scaffold contract

The bundle the roster grants a dispatched agent: the result fence
(SubagentStart reminder + `filter-subagent-output` extraction), the
registry-vocab injection, prior-failure retry context, and the
single-writer dedupe guard. Registry owns the contract; the agent body
owns behavior — the docfile invariant applied to dispatch.

**Avoid:** *agent contract* (ambiguous — sounds like the body), *result
contract* (names one facet of the bundle).

## probe

A named, registered, read-only, side-effect-free, cheap context snapshot an
agent fetches on demand (`track-state probe <name>`) — tier B's dynamic arm
(see [[conductor/design/probes]]). The fourth registry
(`templates/workflow/probes.json` ⊕ `conductor/workflow/probes.json`): rows
are `builtin` (a parser in `track_state/probes.py`; the exemplar `test-state`
reads the on-test-run ledger) or `command` (registered argv, shlex-split, no
shell, hard timeout). The five adjectives are the contract: dynamic context
is fine, undisciplined context is not — ad-hoc scraping from agent prose
stays out.

**Avoid:** *dynamic context hook* (sounds tier-A and hardcoded), *context
scraper* (the exact thing the registry replaces), *tmux probe* (interaction
is not context — the rejected heuristic).

## task-type ownership

The split that governs task tags: **the planner authors the label; code owns
the vocab and validation.** A task's leading tag is authored content, like the
task name — written by spec-planner from the resolved registry vocab by
judgment. The keyword matcher is only the advisory lint inside
`init-from-plan --check` (declared-vs-signals disagreements = telemetry); the
unknown-tag hard error and the TAG_CONFIRM relay stay contract. A wrong label
in flight is fixed by the misroute verdict amending the tag into plan.md —
never by a dispatch-time override.

**Avoid:** *tag inference* (the matcher never infers the label anymore — it
advises), *route override* (an ephemeral patch reconcile silently reverts;
the label is the defect, amend the label).

## findings edge

The envelope-pointer contract that carries cross-phase knowledge to a
dispatched task: a `FINDINGS_FILE` line in the executor/explorer envelope
naming `.conductor/track-findings.md` (absent line = none recorded), plus
compile-at-checkpoint regardless of verdict. The pointer rides the prompt
body exactly as `WORKFLOW_FILE` does — fetch-side, no new injection channel;
the artifact, its caps, and its archive-time supersession by corpus
graduation are unchanged.

**Avoid:** *findings injection* (tier-A restatement — the edge points, it
never carries content), *explore-findings file* (a second artifact beside
track-findings — one bridge, one home).

## phase-gate replanning

The dynamism ceiling: at each PASSED phase checkpoint, a confirm-gated
re-derive pass (spec ⊕ track-findings ⊕ remaining plan → amendment
proposals through the existing plan-amendment machinery, one
AskUserQuestion, reconcile's SHA-preserving semantics). Completed phases
never reopen; shape stays immutable mid-track; within-phase dispatch stays
frozen — authored content changes at boundaries, control flow never does.

**Avoid:** *rolling replan* (implies unbounded re-derivation; one pass per
checkpoint), *dynamic spine* (the declined D2 — runtime judgment over
control flow, which this explicitly is not).

## skill distillation

The reverse direction of the improvement loop: harvest recurrence from
completed tracks (handoffs, manifest lanes, failure verdicts, tag usage),
route each candidate to its home (job family → tag + workflow docfile;
role → roster wrapper; knowledge → wiki; planning pattern → planning
docfile), refute it, and adopt it only through a human gate landing via
the validating generators. Recurrence-before-extraction and
human-as-adoption-anchor are the two invariants.

**Avoid:** *auto-skill* (nothing auto-adopts — the gate is the anchor),
*skill mining* (sounds like transcript scraping; the harvester reads
conductor's own durable artifacts).

