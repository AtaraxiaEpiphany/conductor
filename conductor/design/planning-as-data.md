---
type: concept
sources:
  - runtime/contracts/grill-discipline
  - skills/new-track
  - agents/spec-planner
  - templates/workflow/workflow-shapes.json
  - conductor/design/dispatch-manifest
  - conductor/design/decision-workflow-as-data
  - "~/Documents/wiki (external; agent-engineering + mattpocock notes read at 2026-08-20)"
last_verified: 2026-08-20
---

# Planning-as-Data (planning docfile seam)

Status: **Agreed** (2026-08-20, grill-resolved) — design + ADR recorded, not
yet implemented. The Phases below are the campaign plan for whenever it is
scheduled. Decision record:
[[conductor/design/decision-planning-as-data]].

## Context

The ask — make the plan → implement-step workflow more extendable and
generic — had its executor half shipped the same day
([[conductor/design/dispatch-manifest]]: workflow docfiles + `workflow_doc` +
the dispatch manifest; a project adds a custom task workflow = one docfile +
one registry row, zero plugin edits). The grill's premise challenge
redirected the remaining work to the **planning front door** — the last
prose-welded layer:

1. **Shape selection is keyword prose in a skill.** `skills/new-track` §2.1
   step 3 hardcodes the migration/deliverable keyword lists and their confirm
   questions — an unwatched restatement of registry intent (the drift class
   every prior campaign hunted; the contract-sync WATCHED list does not
   cover it).
2. **Shape-specific authoring branches live in the agent.**
   `agents/spec-planner` §4.1 branches on `AC_GROUNDING` (test → Test
   Scenarios; review → Artifact Anchors) and §4.2 carries shape-flavored
   decomposition guidance — the same shape the task-executor's §1.5/§4.0
   branching had before the manifest collapse.
3. **A third partial home.** The shape registry's `instruction` fields
   restate planner-facing procedure as display prose; `when_to_use` restates
   selection rationale.
4. **`research-first` is unreachable.** The shape row exists, documents
   explore-before-plan intent — and nothing selects it. A declared
   capability with no front door.

What already works and stays: the brief grill → authoritative `brief.md`;
spec-planner's fetch-don't-inject discipline (§3.1 already fetches
`registry-doc`); the three deterministic plan validations
(`init-from-plan --check`, `spec-anchors`, `spec-integrity`); the §2.3b
refuter and §2.4 spec-reviewer gates; the two-registry separation
(task-types own node behavior, shapes own topology + gates); the mid-track
machinery (amend / split / replan / recovery routers) — dynamism stays
plan-time selection plus the existing amendment path, per the grill.

Constraint history honored: 1B's declined `step_sequence` (no second
representation of step semantics); D2 dynamic spine and D1 native Workflow
stay deferred — this seam changes **planning**, never dispatch; the
single-source Non-goal "no new injection channels" — the planning docfile is
fetched (Tier C self-load); `PLAY_FILE` rides the envelope exactly as
`WORKFLOW_FILE` does.

Wiki principles that shaped it (external notes, cited above): the ladder —
loop before graph, nodes only for real specialties; the deterministic seam —
selection is control flow, so it becomes pure code, not a model judgment;
flows call primitives — new-track stays a thin flow over `track-state`
primitives; OpenSpec's artifact ladder — plays make the proposal/specs
stages data-driven the way docfiles made the tasks stage data-driven.

## Decisions

### D1 — Planning docfiles (the artifact)

Each shape's **planning procedure** lives in a docfile
`templates/planning/<name>.md` (project-overridable at
`conductor/planning/<name>.md`, project wins — the registry overlay rule),
named by the shape row's new `planning_doc` field. A docfile carries
**planning procedure only**: an orchestrator-facing **Prelude** (pre-planning
steps — e.g. research-first's explorer dispatch) and a planner-facing body
(spec/plan authoring guidance for that shape). Selection signals, gates,
verifiers, grounding stay registry fields — the executor seam's invariant,
mirrored: *docfiles carry procedure; the registry owns policy*. **A project
adds a custom planning workflow = one docfile + one shape row, zero plugin
edits.**

**Rejected:** restating planning per skill/agent (the three-homes status
quo); a per-track *planning manifest* (no replay concern justifies a
composed artifact — the dispatch manifest's purity rationale does not
transfer; pointer envy is not a requirement).

### D2 — Shape selection becomes code: `track-state propose-shape`

A pure function over (track description ⊕ `brief.md` when present) × the
resolved shape registry. Shapes gain a machine `signals` field (keyword
list, mirroring task-type `signals` — the same matcher-data shape
`derive_task_tag` consumes). Output: ranked candidates with per-candidate
rationale (signal hits), `default` as the fail-open fallback, and a
`confirm_required` flag (true for every non-default shape). Deterministic —
no model call (the deterministic seam: selection is control flow).

**Rejected:** keyword prose in the skill (today's unwatched drift surface);
a model-judgment selection call (relocates the fragility rather than
removing it).

### D3 — Confirmation UX preserved (decisions stay the user's)

`default` proceeds silently; a non-default top proposal (migration /
deliverable / research-first — each consequential: gates drop, grounding
shifts, or an exploration phase inserts) asks **one** AskUserQuestion with
the proposal recommended-first — today's §2.1 behavior, now data-driven.
`set-workflow-shape` remains the override. Rationale is ephemeral (printed
by the proposal; not persisted — no schema change).

### D4 — `research-first` goes live through the planning layer

Its planning docfile's Prelude instructs the orchestrator to dispatch
`explorer` **before** spec-planner and pass the Exploration Notes forward as
planning input; the docfile body then plans with findings in hand. The
dispatch spine is untouched — `nodes` stays advisory, no emit-site change,
the deferred-D2 conditions are unchanged. This converts the false
declaration into honest behavior at the layer where it is cheap:
**planning-side ordering, not topology honesty.**

**Rejected:** making `nodes` load-bearing (re-opens deferred spine work; the
1B bar — ≥2 task-types with bespoke step *semantics* — remains unmet).

### D5 — Relocation, not duplication (the three homes collapse)

The shape-planning prose MOVES home (Delete→Point rungs):

- new-track §2.1 step-3 keyword block is DELETED — replaced by the
  `propose-shape` call + the D3 confirm;
- spec-planner §4.1/§4.2 shape branches MOVE into the docfile bodies;
  spec-planner keeps the universal procedure (context load, tag matching,
  deps discipline, output format) and gains "read your planning docfile"
  via `PLAY_FILE` (Tier C self-load);
- shape `instruction` fields whose content moved are DELETED. `when_to_use`
  stays as the human-facing rationale `registry-doc --shape` renders — the
  gloss for the machine `signals`.

### D6 — Drift gates mirror the executor seam

`registry_validate` knows `planning_doc` + `signals` and cross-checks
declared docfiles exist; the contract-sync WATCHED list gains the planning
docfile glob; the dangling-doctrine lint covers `templates/planning/` refs;
a propose-shape golden test pins the ranking over a fixed description
corpus; README sync picks up the new subcommand + library.

## Phases

- **Phase A — registry + library.** `planning_doc` + `signals` fields; the
  four docfiles seeded verbatim from the three current homes
  (move-not-copy); `resolve_planning_doc` (project wins, fail-open → the
  default play); `registry-doc --shape` renders the docfile; the validator
  cross-check lands BEFORE any consumer points at it.
- **Phase B — propose-shape + new-track swap.** The pure subcommand (all
  command-surface drift sites: `COMMAND_GROUPS`, the sanctioned set, README
  regen); new-track §2.1 keyword block deleted, confirm UX per D3;
  research-first prelude wiring (explorer dispatch + notes hand-off in
  §2.2).
- **Phase C — spec-planner collapse.** `PLAY_FILE` envelope field
  (`COMPACT_FIELDS` allowlist); §4.1/§4.2 shape branches deleted; migrated
  `instruction` fields deleted from the shipped shapes.
- **Phase D — drift gates + docs.** WATCHED glob, dangling lint, the golden
  propose-shape corpus test, README re-sync, this doc's Status →
  Implemented.

Per-phase conventional commits; full suite green each phase; scratch-track
smoke at the end: one default, one migration, one research-first
end-to-end.

## Non-goals

- **No dispatch/spine change** — `nodes` stays advisory; deferred-D2
  conditions unchanged.
- **No planning manifest artifact** (D1's rejected alternative, recorded so
  it is not re-litigated).
- **No new injection channel** — `PLAY_FILE` rides the existing envelope
  (fetch-side, Tier C).
- **No mid-track shape mutation** — amend/split/replan remain the whole
  dynamism story (grill decision: plan-time selection only).
- **No archetype axis** (greenfield/brownfield/hotfix) — re-proposal bar
  mirrors 1B's: revisit when ≥2 shapes need the same play, or one shape
  needs two.
- **No change to `brief`** — the brief stays shape-agnostic; it *feeds*
  propose-shape.

## See Also

- [[conductor/design/decision-planning-as-data]] — the ADR recording the direction decision + gate check
- [[conductor/design/dispatch-manifest]] — the executor-side seam this mirrors one layer up
- [[conductor/design/decision-workflow-as-data]] — the prior ADR whose derive-and-point discipline this inherits
- [[conductor/design/single-source-authority]] — the ladder D5's relocations follow
- [[runtime/contracts/grill-discipline]] — §4 premise challenge, §7 crystallization writes (this doc IS one)
- [[runtime/contracts/context-model]] — Tier C (docfile self-load) placement
- [[conductor/resource/glossary]] — **planning docfile**, **shape proposal** entries
