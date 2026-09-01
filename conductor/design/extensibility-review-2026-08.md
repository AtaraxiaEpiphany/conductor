---
type: concept
sources:
  - conductor/design/task-type-ownership
  - conductor/design/planning-as-data
  - conductor/design/dispatch-manifest
  - conductor/design/decision-pattern-realization
  - conductor/design/hygiene-audit-2026-08
  - scripts/track_state/dispatch
  - scripts/track_state/handoff
  - scripts/track_state/task_profiles
  - agents/spec-planner
  - agents/task-executor
  - agents/explorer
  - skills/adopt-skill/SKILL
  - "~/Documents/wiki (external; dynamic-workflows + graph-engineering + mattpocock-skills read at 2026-08-31)"
last_verified: 2026-09-01
---

# Extensibility Review — 2026-08 (dynamic workflow · task-type matching · skill distillation)

Status: **Advisory** (2026-08-31, grill-resolved; nothing here is implemented —
except the **incident addendum** below, shipped 2026-09-01).
Answers the ask — *task types, workflow shapes, skill-integration: are they
really best practice for the any-job extensibility goal?* — with a verdict on
the current design, three seam designs the grill selected, and a sequenced
track menu. Each Tier-1 finding is pre-shaped to seed a brief without
re-derivation.

**Grill-resolved direction decisions** (this session, per
[[runtime/contracts/grill-discipline]]):

| Decision | Resolution |
| :--- | :--- |
| Architecture direction | **Evolve, not redesign** — deterministic spine + authored plans stay; per-dispatch runtime judgment (D2) stays declined |
| Task-type pain timing | Unsure whether observed pre- or post-`3e3780a` → recommendation includes an instrument so evidence accumulates |
| Explore-findings flow | **Envelope pointer + always-compile** (mirror the dispatch-manifest seam; no parser change) |
| Task-type match optimization | **Plan-time authored, corrected post-hoc** — raise planner labeling accuracy; no new runtime mechanism |
| Skill distillation trigger | **System-proposed at track completion + human adoption gate** |
| Dynamic workflow appetite | **Phase-gate replanning, confirm-gated** — the dynamism ceiling; within-phase dispatch stays frozen |

## Method

Three read-only mapping passes over the live code (task-type pipeline;
checkpoint/findings flow; extensibility surfaces), the full design/decision
corpus, and the external wiki's agent-engineering cluster
(dynamic-workflows, graph-engineering, mattpocock-skills). The grill's
premise challenge redirected the ask from "redesign the mechanisms" to
"close three seams"; the frontier rounds then selected the fix shapes
recorded above.

## Verdict on the current design

**The architecture is the right class.** Conductor is a durable-execution
engine for LLM agents, and its load-bearing choices independently land on
the canonical patterns for that class:

| Conductor concept | Canonical pattern | Where pinned |
| :--- | :--- | :--- |
| plan.md + track-state.json + dispatch spine | Durable workflow engine (Temporal lineage): authored definition, deterministic interpreter, byte-identical replay | [[conductor/design/decision-workflow-as-data]] |
| Task-type tags (registry rows) | Typed activity contracts — route, exemptions, docfile from one leading label | [[conductor/design/task-type-ownership]] |
| Workflow shapes | Topology/policy declaration over a fixed walker (OpenSpec: "the walker owns topology; the agents own intelligence") | [[runtime/contracts/plan-format-contract]] |
| planner-authored tags, code-owned vocab + validation | AI owns content, code owns contract — the OpenSpec split | [[conductor/design/decision-task-type-ownership]] |
| Hooks (tripwires, gates, fences) | Frozen, non-tunable anchor nodes — guarantees the optimizer cannot rewrite | [[conductor/design/hygiene-audit-2026-08]] §excellent |
| failure-analyst + verdict routers | Compensation/retry policies with judgment at the failure point | [[conductor/design/recovery-policy]] sibling contracts |
| phase-checker + compile-track-findings | Checkpointing + a cross-phase state bridge | scripts/track_state (handoff compile) |
| registries ⊕ overlays + docfile libraries | Capability tables behind a stable interpreter — one row + one docfile, zero plugin edits (the deep-module property) | [[conductor/design/dispatch-manifest]] D2 |
| grill → brief → spec → plan → execute → check → archive | The mattpocock `grill → spec → tickets → implement → review` chain with the process contract hardened into code | [[runtime/contracts/grill-discipline]] |
| wiki corpus + harvest/graduation | Compounding knowledge layer (LLM-wiki pattern); state on disk, never in context | skills/wiki |

Against the wiki's own ladders the design also holds: loop-before-graph
(serial spine default, waves opt-in behind deps), determinism-where-it-matters
(the D2 declinature), verification asymmetry (adversarial lensed review fan-out
+ refuter + convergence loop shipped in skills/review), no-silent-caps
(ineligible lists, advisory disclosures, age labels), and anchors (human
confirm gates at every consequential branch).

**The gaps are seams, not structure** — ranked:

1. **Dataflow edges are implicit.** The plan is a control-flow artifact; data
   dependency rides phase order and prose. The one cross-phase artifact that
   exists — `track-findings.md` — is consumed by a Tier C self-load sentence
   in the executor's body, not by an envelope pointer like `WORKFLOW_FILE` /
   `PLAY_FILE` / `RESEARCH_NOTES`. This is the root of the reported
   "explore findings never reach next phases" pain (Finding 2).
2. **The improvement loop runs one direction.** Capabilities are installed
   (adopt-skill: external skill → registry) but never distilled (completed
   track → new capability). Knowledge graduates to the wiki (prose), but
   procedures never re-enter the executable rails (Finding 4).
3. **Labeling accuracy is unmeasured.** `tag_advisories` are stdout-only,
   unpersisted, unconsumed — "revisit after a few tracks" has no instrument
   (Finding 1, method 5).
4. **Extension axes are undocumented.** Six surfaces (tag, shape, roster,
   probe, planning docfile, steps docfile) plus hooks; choosing an axis is
   folk knowledge. Generalizes hygiene-audit finding 4 (Finding 5).
5. **dispatch.py cohesion** — hygiene-audit finding 1 stands; every seam below
   touches that file.

## Incident addendum — the refuter-registry incident (2026-09-01, SHIPPED)

Four days after this review's verdict, a live track confirmed gap #1's *class*
with a transcript. On a fresh plan (`git_visualizer_20260812`, project
`git-visualizer`), the §2.3b refuter was dispatched and went sideways: "Let me
find the tag vocabulary to verify which tags are tdd_exempt" → searched the
project → checked CLAUDE.md → searched the conductor plugin → announced
"The TAG_VOCAB registry isn't in the project" → **reconstructed the tag→
exemption mapping from memory** and audited against the guess.

**Root cause — two stacked defects, not a design failure:**

1. **Namespace miss (mechanical).** Installed-plugin projects dispatch skills'
   agents as `conductor:refuter`; roster keys are bare (`roster_add` validates
   letters/digits/-/_ — a key can never contain `:`). Every name-keyed lookup
   in five consumers (start-hook floor/reminder/registry/retry, stop-hook
   gates, dispatch-dedupe single-writer) silently no-ops on the namespaced
   form. The transcript's line 0 is the raw dispatch prompt — zero injection
   of any kind reached the agent.
2. **Dangling pointer (the Finding-2 class, generalized).** Agent prose named
   registry data ("the `[Conductor Registry]` block") delivered by a
   fail-open side channel the agent could neither cite nor grep — so when the
   channel failed, the pointer dangled and the agent hunted for the artifact
   it was told existed, then fabricated it. This is the same
   *name-the-data, deliver-by-side-channel* shape as Finding 2's findings
   edge; the incident extends the class from handoff artifacts to **registry
   facts**, and adds the missing-agent-side-tripwire observation.

**Shipped fix (hotfix, this session — precedes Track D, does not replace it):**

| Item | Change |
| :--- | :--- |
| Namespace normalization | `agent_roster.canonical_name()` — single-home tail-strip before membership; `row_for`, start/stop hooks, dedupe all consume it |
| Deterministic delivery | `track-state plan-refute-prompt` — §2.3b prompt assembled in code: CLAIM + recomputed `AC_EVIDENCE` + **resolved `TAG_VOCAB` rows embedded in the prompt itself** (skip-refute D3 precedent); skill pastes verbatim |
| Agent-side tripwire | refuter §4.0: missing registry = `STATUS: FAILURE` ("registry vocab not delivered"), never a hunt or a guessed audit; §5.0 failure list updated; spec-reviewer §3.4 parity (skip-with-`ADVISORY`) |
| Pointer wording | refuter §3.1 / spec-reviewer §3.4: vocab is GIVEN, "never search the project, CLAUDE.md, or the conductor plugin for it, and never reconstruct it from memory" |
| Evidence rule | §4.0 exception: registry rows delivered by the two channels are citable as given (ground truth by construction) — closes the grep-for-grounding pressure the hunt came from |

Tests: +26 (canonical_name unit, namespaced dispatch through start/stop/dedupe
hooks, prompt-builder pins moved to the code per the skip-refute pattern,
tripwire prose pins in both agent bodies). Suite 2767 green.

**Menu unchanged.** Track D (findings edge) still first — the incident's
generalized class argues for it more strongly, not less; D2 stays declined.

## Finding 1 — optimizing task-type match occurrence (S · doctrine + data)

The reported pain (tasks mistyped, defaulting to task-executor where explore
belonged) was root-caused and fixed the same day this review started
(`3e3780a`: planner-authored labels, advisory lint, misroute recovery).
What remains is *raising how often the plan-time label is right the first
time* — under the resolved stance: plan-time authored, corrected post-hoc.
Six methods, layered cheapest-first; all are authored-content or
registry-data changes, none touch routing code:

1. **Vocab/signals tuning, gated on telemetry.** The recorded revisit rule
   stands: if lint disagreements on real tracks concentrate in one tag
   family, edit that family's `when_to_use` / `signals` — not the ownership
   model.
2. **The complexity rule (fog test) in spec-planner §4.2.** Today's doctrine
   — "tags are exemptions; when unsure leave untagged" — is the right
   fail-safe for *exemption* tags but wrongly silences `[Explore]`: unsure
   about which exemption applies means default TDD; unsure about the
   *ground* means exploration. Add the rule: **for each area the plan
   touches where the planner cannot name the concrete files/modules
   involved, insert an `[Explore]` task ahead of the building tasks** (or
   propose the research-first shape). The F2 asymmetry supports the split:
   a wrongly-untagged explore task now costs one MISROUTE round (recoverable
   by design); a wrongly-explored ordinary task costs one cheap explorer
   pass whose findings still feed the bridge. Neither is the silent
   TDD-skip that motivated F2 — that risk is unchanged for exemption tags.
3. **Widen research-first's shape signals (data-only).** `propose-shape`
   ranks on keyword `signals`; complexity-flavored wording
   ("unfamiliar", "integrate", "cross-cutting", "investigate",
   "multi-system") is absent, so complex-but-not-"research"-worded tracks
   never rank it. One registry edit; the matcher is already pure code.
4. **Few-shot exemplars in the registry-doc render.** Judgment transfers
   through examples better than keyword lists. Add an optional `examples`
   field to tag rows — one canonical and one borderline case each
   ("map the auth flow across services → `[Explore]`" vs "bump the retry
   limit → `[Chore]`") — rendered into the `## Tag Signals` block
   spec-planner fetches in §3.1. Code owns the render; content owns the
   data; the `tag add` generator gains the flag.
5. **Persist the labeling telemetry.** `tag_advisories` today die on stdout.
   Persist per-track samples (a probes-readable store, or a probe over the
   init log) so the cross-track read exists: disagreement rate per tag,
   false-untagged rate, MISROUTE frequency. This is the instrument the
   "not sure / both" answer to the pain-timing question requires — and the
   feed for methods 1 and 4.
6. **Keep the post-hoc nets as shipped.** Manifest advisory at dispatch,
   MISROUTE self-report + `reroute_explorer` verdict in flight, TAG_CONFIRM
   relay at init. Extend the misroute pattern to a second class only when
   observed in the wild (the recorded revisit rule).

**Rejected:** per-dispatch runtime classification (D2 — re-declined at this
review's premise challenge); per-shape default tags (mutates the meaning of
untagged per shape, touching the F2 rationale for all tags to fix one).

## Finding 2 — the findings edge (S–M · the reported pain's mechanism)

Verified state: explore output *does* flow — explorer writes rich
Exploration Notes into `.conductor/handoff/P{n}T{m}.md`; a PASSED checkpoint
compiles Key Findings / Gotchas into `.conductor/track-findings.md`; the
next phase's tasks are *supposed* to self-load it (task-executor Layer 0(c),
explorer §3.2). Three defects make the edge unreliable:

- **The pointer is prose, not envelope.** `WORKFLOW_FILE`, `PLAY_FILE`,
  `RESEARCH_NOTES` are named in the envelope; track-findings is "if this
  path exists, Read it" body prose — the weakest contract class in the
  three-tier model.
- **Compile fires only on PASSED.** A FAILED phase's exploration notes never
  reach the bridge — and failed phases are often where the learning is.
- **Upstream starvation.** When no explore tasks were planned (Finding 1's
  bias), there is nothing to harvest — the pain compounds.

**Design (grill-selected: envelope pointer + always-compile):**

1. **`FINDINGS_FILE` envelope line** on both `_build_executor` arms
   (task-executor and explorer), emitted when
   `{TRACK_DIR}/.conductor/track-findings.md` exists, absent line = none
   recorded yet (no dangling pointer). It rides the prompt body exactly as
   `WORKFLOW_FILE` does — compact strips top-level keys, not prompt lines,
   so no COMPACT_FIELDS change (verify with the envelope golden test
   anyway; that is the known gotcha class). The Layer 0(c) / §3.2 prose
   retargets from path-recall to "read the file your envelope names."
2. **Always-compile at the checkpoint.** The compile call is single-homed
   after the stamp today; add the FAILED arm (phase verdict FAILED still
   compiles its handoffs before recovery routing). Fail-open unchanged.
3. **Finding 1's complexity rule** closes the starvation end.

**Invariant preserved:** no new injection channel — the pointer rides the
existing envelope (fetch-side, Tier C discipline); the artifact, its caps
(eight bullets per kind per file), its age labels, and its archive-time
supersession by corpus graduation are all unchanged.

**Seams:** `_build_executor` + the checkpoint PASSED/FAILED arms in
[dispatch.py](../scripts/track_state/dispatch.py), the compile call site in
[helpers-adjacent misc/handoff modules](../scripts/track_state/handoff.py),
executor/explorer body prose, `test_track_findings_wiring.py` + envelope
goldens.

## Finding 3 — phase-gate replanning, confirm-gated (M · the dynamism ceiling)

The grill accepted one new dynamism seam: **plans become living artifacts at
phase boundaries only.** At each PASSED checkpoint, a re-derive pass reads
the spec (unchanged) ⊕ `track-findings.md` ⊕ the remaining plan rows and
proposes amendments — add an `[Explore]` task for newly-discovered ground,
split a task that grew, reorder, drop — through the **existing**
plan-amendment machinery, then one AskUserQuestion confirm (recommended
first), applied with reconcile-plan's name-keyed SHA-preserving semantics.

Constraints that keep it inside the declined-D2 boundary:

- **Completed phases never reopen** (SHA preservation is the invariant
  reconcile already guarantees).
- **Confirm always** — the human is the anchor; an unconfirmed amendment is
  a no-op. This is what distinguishes the seam from auto-replanning.
- **One pass per checkpoint, empty proposal = silent pass** — no thrash
  loop, no token burn on quiet phases.
- **Shape immutable mid-track** — the planning-as-data non-goal stands;
  this extends the *cadence* of amendment, never shape mutation.
- **Within-phase dispatch stays frozen** — byte-identical replay is
  untouched; dynamism lives at authoring boundaries, which is where
  verification is cheap and artifacts are durable.

This is rolling-wave planning at track scale — and the same answer the
corpus gives twice already: judgment over authored content (planner labels,
R1) is safe; judgment over control flow (D2) is not. Phase gates are where
authored content legitimately changes.

**Seeds:** phase-gate-replan track · decision record below · AC = amendment
proposals surface at checkpoints, confirm-gated, reconcile semantics, replay
unchanged.

## Finding 4 — the distillation loop (M–L · skill enrichment)

The ask: *once agents walk a path for some jobs, summarize it into a skill
so the system compounds.* Today only the install direction exists
(adopt-skill). The reverse direction — completed work becoming capability —
has all its parts present and no loop connecting them. Design
(grill-selected: system-proposed + human gate):

1. **Harvest — `track-state distill-candidates`** (pure, read-only): reads
   `.conductor/handoff/` attempts, the dispatch-manifest path decisions,
   failure-analyst verdicts, tag usage, and MISROUTE classes across tracks;
   emits recurrence candidates. Skill-worthiness signals: the same
   task-shape ≥N times across tracks; the same improvised recovery twice; a
   docfile lane hit repeatedly; a repeating misroute class; graduation
   themes repeating in the wiki harvest.
2. **Propose — `skill-distiller` agent** (advisory class, sonnet — the
   adopt-skill precedent for distillation reliability): drafts each
   candidate and, critically, **routes it to the right home**:

   | Recurrence is a… | Home | Rail |
   | :--- | :--- | :--- |
   | job family (a kind of task tracks keep containing) | task-type tag + workflow docfile | deterministic, dispatch-time |
   | role needing its own context/tools | roster wrapper (`skills:` preload) | context-time |
   | reference or knowledge (not procedure) | wiki corpus graduation (existing) | fetch-time |
   | interaction/planning pattern | planning docfile | plan-time |

   The routing rule is the node test plus the invocation axis: if dispatch
   should be deterministic, it is a tag; if the model should reach for it,
   it is a preload; if a human reads it, it is the wiki.
3. **Verify — adversarial, before any adoption.** A refuter pass per
   candidate argues the counterfactual (would this skill have improved the
   source tracks, or is it a crystallized one-off?). Recurrence-before-
   extraction is the gate: one traversal is noise, not a skill.
4. **Gate — the human is the adoption anchor.** One AskUserQuestion round
   per candidate set. Nothing auto-adopts.
5. **Land — existing validating generators only** (`tag add`, `roster add`,
   wiki graduation). The zero-plugin-edits invariant is preserved; the
   `examples` data from Finding 1 method 4 rides along as first content.
6. **GC — the third harness pillar.** Usage probes (tag fire counts,
   docfile lane hits are already manifest-derived); the distill pass surfaces
   retire candidates (unfired for M tracks). Skills that never fire are
   sediment, not capability.

**Surfaces:** the harvester subcommand (all four command-surface drift
sites), one roster row + agent body, one skill (`/conductor:distill`, the
manual road on top of the system-proposed hook), a post-loop phase beside
corpus-writer/wiki-synthesizer.

**Rejected:** user-invoked only (relies on human memory for recurrence —
the asymmetry the system is better positioned to see); fully automatic
adoption (loses the anchor; unverified crystallization of noise compounds).

## Finding 5 — secondary recommendations

- **Extension-point map.** One doc (or README fragment): "I want conductor
  to do X — which seam?" across tag / shape / roster / probe / planning
  docfile / steps docfile / wiki / hook-for-always-never. The external
  wiki's extension-mechanism decision matrix, transposed to conductor's
  axes. Pairs with the honesty pass (hygiene Track B); kills the
  folk-knowledge problem and the mirage the shape-axis labeling gap
  represents.
- **Stale Rail B prose.** [[conductor/design/decision-pattern-realization]]
  assigns P0–P3 analysis patterns to the Workflow JS tool; they actually
  shipped as prose rails (skills/review's adversarial fan-out, the
  wiki-doctor dry loop). Correct the record in the honesty pass — a
  roadmap that misstates where patterns live is the same drift class the
  audit hunts.
- **Sequencing with the hygiene menu.** Track A (CLI/README derivation)
  anytime; Track B (honesty pass) alongside Finding 1 (both touch registry
  renders and stale prose); Track C (dispatch.py split) *after* Findings 2
  and 3 land — splitting first means rebasing both seams through the move.

## Track menu (sequenced)

1. **Track D — findings edge** (Finding 2 · S–M). Envelope pointer +
   always-compile + wiring tests. First: the acute reported pain, the
   smallest diff, immediate relief for every complex track.
2. **Track E — labeling optimization** (Finding 1 · S). Complexity rule +
   research-first signals + `examples` field + telemetry persistence.
   Doctrine and data only; can run alongside D.
3. **Track F — distillation loop** (Finding 4 · M–L). Harvester +
   skill-distiller + `/conductor:distill` + refuter verify + GC probes.
   After D (reads the same handoff/manifest substrate D stabilizes).
4. **Track G — phase-gate replanning** (Finding 3 · M). Last of the new
   seams: it consumes the findings edge and wants E's telemetry showing
   where mid-track re-derivation would have paid.
5. **Hygiene A / B / C** interleave as noted in Finding 5.

## Decision records

- [[conductor/design/decision-phase-gate-replanning]] — the dynamism
  ceiling decision (extends a recorded non-goal).
- [[conductor/design/decision-skill-distillation]] — the improvement-loop
  direction decision.

## See Also

- [[conductor/design/task-type-ownership]] — the fix Finding 1 tunes; its
  revisit rules are quoted by methods 1 and 6.
- [[conductor/design/planning-as-data]] — the plan-time selection doctrine
  Finding 3 extends at the cadence axis only.
- [[conductor/design/dispatch-manifest]] — the pointer-seam pattern Finding
  2 mirrors.
- [[conductor/design/hygiene-audit-2026-08]] — the menu this review
  interleaves with.
