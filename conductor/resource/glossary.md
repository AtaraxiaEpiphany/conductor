---
type: resource
sources:
  - conductor/design/dispatch-manifest
  - conductor/design/planning-as-data
  - runtime/contracts/grill-discipline
last_verified: 2026-08-20
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
