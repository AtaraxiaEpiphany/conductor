---
type: concept
sources:
  - conductor/design/task-type-ownership
  - conductor/design/planning-as-data
  - conductor/design/agent-roster
  - conductor/design/probes
  - conductor/design/extensibility-review-2026-08
  - conductor/design/decision-phase-gate-replanning
  - "~/Documents/wiki (external; ai/agent-engineering + ai/claude-code-extensions + ai/practice, read at 2026-09-04)"
last_verified: 2026-09-04
---

# Any-Job Extensibility — 2026-09 Campaign (grounding inversion · persona binding · telemetry · replanning)

Status: **Shipped** (2026-09-04; Tracks 1–4, commits `ab5edfc..f2c74f1`).
The ask: *the conductor must do any job reliably — task types, workflow
shapes, skill-integration are the means.* This campaign was a fresh
first-principles pass (user's explicit choice over the standing 2026-08-31
evolve-not-redesign verdict), grilling twelve direction decisions to shared
understanding per [[runtime/contracts/grill-discipline]].

The fresh pass **converges near current architecture** — independently. The
corpus confirms authored-plan + fixed-spine + registry-data axes as canonical
and re-declines per-dispatch runtime judgment (D2, third time, see below).
What it changes is three inversions, each fixing a real generality gap for
non-code work, plus the already-designed phase-gate replanning seam:

1. **Class-declared grounding** — negative-space exemption booleans
   (`tdd_exempt`/`coverage_exempt`) die; every task class positively declares
   what "done, verified" means for its deliverable (`gates` subset of
   {tdd, coverage, checkpoint} + `grounding` ∈ {test, review, data-check,
   human-attest}). Code becomes one deliverable class among peers.
2. **Persona binding** — tag rows gain `agent: <roster-name>`, roster-validated;
   `[Tag]` tasks dispatch that rostered wrapper as executor. The missing
   plan→skill seam.
3. **Telemetry leg** — the fourth reliability leg (measurement loop): one
   substrate (the probes registry), three feeds (label-accuracy, skill-fires,
   gate-outcomes).
4. **Phase-gate replanning** — at each PASSED checkpoint, one bounded
   re-derive pass over remaining rows, confirm-gated, reconcile-plan applied.
   Procedure single-homed in
   [[runtime/contracts/phase-gate-replanning]].

## The five-axis derivation

The fresh pass starts from five questions a conductor for *any digital work*
must answer, derives each from first principles, and lands on the current
architecture every time:

1. **Job scope** — what can be asked for? *Any digital work whose done-state
   is attestable*: code, docs, config, data pipelines, research digests.
   Code loses its default-seat status: it is one deliverable class among
   peers, each declaring its own witness. This is the axis the three
   inversions serve.
2. **Authorship** — who writes the plan? A human-authored plan + fixed spine,
   with dynamism confined to phase gates and confirm-gated there. An
   LLM-authored-per-dispatch plan is unauditable and un-replayable; a fully
   static plan wastes what finished phases learn. The phase gate is the only
   seam where new information may amend remaining rows (Track 4).
3. **Reliability** — how is a finished thing trusted? Four legs: replay
   (durable state + SHAs), gates (class-declared, not code-assumed), confirms
   (one informed AskUserQuestion at each irreversible point), and measurement
   (the telemetry leg — Track 3 — closing the loop: what the gates actually
   caught, whether labels agree with signals, which skills fired).
4. **Skill role** — how do integrations ride? As **executor personas**: a
   skill enters by wrapping an agent (`.claude/agents/<name>.md` with
   `skills:` preload) that a *class* binds to by name (Track 2). The skill is
   the executor's procedure, not a parallel orchestration path.
5. **Topology** — how are agents arranged? A fixed two-rail spine (Rail A
   implement, Rail B implement-step) whose variation axes are data (registries:
   task-type, shape, roster, probes). Custom node sequences (registry-composed
   shapes) are a designed ceiling, deferred until a real job cannot fit.

## Corpus mapping

Each conductor concept instantiates a pattern the external corpus
independently canonizes:

| Conductor concept | Corpus pattern |
|---|---|
| Authored `plan.md` + `track-state.json` + replay spine | Durable-execution engine (workflow systems: state outlives any one runner; resume = re-read state, not re-derive) |
| `[Tag]` typed plan rows + task-type registry | Typed activity contracts (the label is data with semantics, not prose) |
| spec.md (what/why, human-owned) vs plan.md (how, amenable) split | OpenSpec-style content/code-contract split |
| Frozen hooks as the anchor points (`hooks/hooks.json`) | Hooks as frozen anchors around pluggable intelligence |
| Gates per class + phase checkpoints | Verify-at-boundary; the plan's definition of done is checkable, not vibes |
| Registries (baseline ⊕ project overlay) | Data-driven extension; schema migrations via overlays, not forks |
| Executor persona (wrapper agent + `agent:` binding) | Skill-as-executor-persona (the agent IS the integration surface) |
| Phase-gate replanning | Rolling-wave planning: replan detail near execution, freeze far work |
| Telemetry feeds on probes | Measurement loop: instruments over claims (dead gates, label drift, unfired skills are observable) |

## The grill ledger (2026-09-04, all twelve locked)

| # | Decision | Resolution |
|---|---|---|
| 1 | Direction | Fresh first-principles pass (user overrode standing evolve verdict) |
| 2 | Job scope | Any digital work; deliverable classes; code one among peers |
| 3 | Authorship | Authored plan + fixed spine; dynamism at phase gates only, confirm-gated |
| 4 | Reliability | Four legs: replay + gates + confirms + measurement |
| 5 | Skill role | Executor persona |
| 6 | Gate model | Class-declared positive grounding; exemption booleans die |
| 7 | Persona bind | Class-level `agent` field on tag row, roster-validated |
| 8 | Telemetry | One substrate, three feeds |
| 9 | Landing | Incremental inversion (delete→point→lint→derive ladder) |
| 10 | Topology | Fixed spine; data axes carry; custom node sequences deferred |
| 11 | Studio | Editor parity rides each schema change; dashboards deferred |
| 12 | Sequence | Grounding → persona → telemetry → replanning |

## D2, third declinal

**Per-dispatch runtime judgment over the plan's control flow stays declined.**
First declined in the harness-optimization review, again in the 2026-08
extensibility review; re-examined from zero this pass and declined again. The
reason is unchanged and now load-bearing in three places: a spine that
re-derives routing per dispatch cannot replay (durable execution dies), cannot
be audited (the authored plan becomes a suggestion), and cannot confirm (every
dispatch is a new irreversible point). Judgment is welcome over *authored
content* — planner labels at init, remaining rows at phase gates — never over
*control flow*. The three inversions widen what the fixed spine can carry
(more classes, more personas, more evidence); they do not loosen the spine.

## Shipped map

- **Track 1 — grounding inversion** (`ab5edfc..bd69f2a`): positive `gates` +
  `grounding` on every baseline row; `tdd_exempt`/`coverage_exempt` derived
  (never stored); validator guards (tdd/coverage require grounding=test;
  two-homes XOR; merged-level re-check); fast-path tightened to both-exempt;
  tag add CLI speaks the positive form; full render + prose sweep.
- **Track 2 — persona binding** (`421f4d4..0c02e6e`): `agent` field
  roster-validated on tag rows; dispatch chokepoint `_build_executor`;
  serial-rail surfaces slot-aware (manifest gate, telemetry, shape_allows
  occupancy, registry injection, tripwire + clean-tree hooks); wave scope
  serial-only, pinned; wrapper maxTurns headroom; roster generator parity.
- **Track 3 — telemetry substrate** (`a9fa820`): `gate-outcomes.json`
  persisted at both review arms; probes `label-accuracy` / `skill-fires` /
  `gate-outcomes` (the lifecycle `agent=` field self-records feed 2 — no new
  stamping); three registry rows.
- **Track 4 — phase-gate replanning** (`f2c74f1`): `replan-pass.json` staged
  at the stamp home (both rails), `track-state replan` poll/`--ack`
  (exactly-once), procedure in
  [[runtime/contracts/phase-gate-replanning]].

## Deferred ceilings (designed, not blocking)

- `data-check` / `human-attest` ship as vocabulary + class semantics; a
  dedicated data-check verifier agent, if ever needed, is an overlay adoption
  (one row + one docfile).
- Studio telemetry dashboards (grill #11).
- Custom node sequences / registry-composed shapes (grill #10).
- Revisit triggers for Track 4 ([[conductor/design/decision-phase-gate-replanning]]):
  empty-proposal streaks demote the pass to ask-mode opt-in; confirm friction
  allows an auto-apply allowlist for tag adds only — never splits/reorders.
