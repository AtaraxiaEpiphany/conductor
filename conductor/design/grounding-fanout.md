---
type: concept
sources:
  - conductor/design/extensibility-review-2026-08
  - conductor/design/planning-as-data
  - conductor/design/decision-grounding-fanout
  - runtime/contracts/grill-discipline
  - templates/planning/research-first
  - skills/new-track/SKILL
  - agents/explorer
  - agents/spec-planner
  - scripts/track_state/misc
  - scripts/track_state/handoff
  - "~/Documents/wiki (external; dynamic-workflows read at 2026-09-02)"
last_verified: 2026-09-02
---

# Grounding Fan-out — fog-gated pre-plan enumeration

Status: **Shipped** (implemented 2026-09-02, commits `3170d05` + `09bc5b7` +
`16152c1` — fog gate, §2.2.5 step + slice prompts, Track E fold-in; suite
2874 green). This doc was the grill-resolved design; the seam claims below
were verified on disk before implementation and the shipped code follows
them. Answers the ask — *can we define a personal search space and use
subagent enumeration to avoid result homogenization at new-track?* — by
redirecting it from plan-diversity to plan-grounding, then extending the two
seams that already exist.

**Grill-resolved direction decisions** (this session, per
[[runtime/contracts/grill-discipline]] — premise challenge first, then three
frontier rounds):

| Decision | Resolution |
| :--- | :--- |
| Real target | **Grounding fan-out** — enumerate project facts for spec-planner; NOT best-of-N plan diversity |
| Trigger | Fog-gated — pure signal matcher on description ⊕ brief + one confirm ask |
| Substrate | Existing surfaces: conductor/index.md, wiki, git history, the codebase itself |
| Size | 3 explorers fixed, concern-sliced (below); no adaptive sizing |
| Delivery | `RESEARCH_NOTES` envelope seam (the research-first Prelude precedent) |
| Sequencing | Implementation after Track D; menu Track E folds into this track |
| Session scope | Design only — this document + the decision record |

## Why grounding, not diversity

The ask bundled two different buys. **(a) Grounding** — more facts about the
project (concrete files, modules, constraints), which fixes the recorded
pains: the planner cannot name concrete files, `[Explore]` is silenced by the
unsure→untagged doctrine, explore findings never reach the planner. **(b)
Diversity** — more competing plan hypotheses, which fights one-context-one-
bias. The premise challenge resolved (a) as the real target:

- The recorded accuracy failures are all grounding failures. A plan wrong
  because the planner did not know what modules exist is not fixed by
  sampling three plans that all do not know.
- Best-of-N without grounding produces N confident wrong plans sharing the
  same blind spot — and homogenization re-enters at **selection**: plans from
  the same model over the same context converge on the median plan, so
  picking among them is a popularity contest. The only non-homogenizing
  selectors the repo has are deterministic gates and the refuter's
  evidence-grounded verdict; neither needs N plans to be useful.
- Compute-for-intelligence is still the right trade — spent on **enumerating
  the ground** (cheap parallel read-only explorers), not on resampling the
  same planning context.

In dynamic-workflows terms (the external wiki's failure-mode table):
homogenization is **self-preferential bias**, and the structural fix is
isolated contexts each holding a focused goal. Here the focused goal is one
disjoint *slice* of the same ground — three witnesses that must agree on
facts, not opinions.

## Mechanism

Three pieces, each an extension of a verified seam.

### 1. The fog gate — `track-state propose-grounding`

A pure subcommand, the `propose-shape` precedent exactly (pure
signal-matching over description ⊕ brief; deterministic, no model call, no
filesystem writes):

- **Signals.** Complexity/cross-module wording ("unfamiliar", "integrate",
  "cross-cutting", "investigate", "multi-system", "migrate", "refactor the
  …") matched with the word-boundary regex imported from
  `task_profiles._signal_in` — one matcher, one home, never copied.
- **Brief structure signals.** `## Open Questions` item count (≥2 non-empty
  items → a fog point), `## References` present-but-empty (planner will have
  no doc anchors → a fog point). Both are plain section parses.
- **Signals data home.** A top-level `grounding_signals` array in
  `templates/workflow/workflow-shapes.json` — one registry, the project
  overlay already merges it, no new file. Content-owned, code-rendered.
- **JSON output.** `ok, foggy, score, hits, confirm_required, brief_used,
  rationale` where `confirm_required = foggy` (the gate fires only on signal;
  the ask is the anchor). `rationale` is deterministic, composed from the
  hits — the `propose-shape` rationale pattern.

### 2. The new-track step (§2.2.5)

After brief detection, before the §2.3 dispatch:

1. Run `track-state propose-grounding "<track_dir>"` (with `--brief` when a
   brief exists).
2. `foggy: true` → ONE `AskUserQuestion`: *"Ground looks foggy (<hits>) —
   run a 3-explorer grounding fan-out before planning?"* Options: **Yes,
   fan out (Recommended)** / **No, plan directly**. The ask is the anchor —
   no silent spend.
3. `foggy: false` → proceed to §2.3, no ask, no fan-out (quiet tracks pay
   nothing).
4. **Skip when `$WORKFLOW_SHAPE` is `research-first`** — its Prelude already
   dispatches an explorer; a fan-out on top would explore twice.

### 3. The fan-out — three sliced explorers, code-assembled prompts

Dispatch 3 × `conductor:explorer` in parallel. Prompts are **assembled in
code** (`track-state grounding-prompt "<track_dir>" --slice <n>`), the
plan-refute-prompt pattern — the deterministic delivery channel is the
incident lesson (registry facts and charters ride the prompt, never an
injection the agent must trust-and-locate).

Each prompt carries:

```
TRACK_DIR={track_dir}
PHASE=0 TASK={1|2|3}
NAME=Grounding — {slice} — {track_description}
PRE_PLAN=1: plan.md and spec.md do NOT exist yet (skip that part of §3.0)
SLICE={slice-name}
CHARTER: {slice-specific enumeration charter, from the table below}
```

| Task slot | Slice | Enumeration charter |
| :--- | :--- | :--- |
| P0T1 | architecture / data-flow | Name the concrete modules the work would touch, the dataflow between them, entry points, and the two riskiest seams. Output = Exploration Notes. |
| P0T2 | api / contracts | Name the public interfaces, their call sites, invariants the work must not break, and prior decisions that constrain the surface. |
| P0T3 | tests / constraints / history | Name the test tiers covering the area, the gates that will judge it (tags, shapes), and `git log` evidence of prior approaches that failed and why. |

Each explorer records via `append-handoff` into its own slot file
(`.conductor/handoff/P0T1.md`, `P0T2.md`, `P0T3.md`) and returns its
`---TASK RESULT---` block. The distinct task slots are deliberate: the
append path is a plain read-modify-write with no file lock, so three
concurrent writers to ONE slot would risk lost updates — three slots make
the race structurally impossible.

**Verified-safe dispatch conditions** (why three parallel explorers do not
trip the guards): at §2.2.5 there is no `track-state.json` yet (it is minted
in §2.6), so the dispatch-dedupe hook resolves no locked task and allows,
and the SubagentStart inflight stamp is skipped (it stamps only when a
locked task resolves). Both facts verified on disk this session.

**Known inert hazards, documented not fixed:**

- `result.json` is one file per track and last-write-wins across the three
  explorers. Inert at pre-plan: the parsed channel is each explorer's
  stdout result block; nothing consumes `result.json` until the dispatch
  spine exists.
- The phase-0 task-name lookup in `append-handoff` falls back to a generic
  name when no `track-state.json` exists. Harmless — the slot files are
  keyed by index, not name.

**Failure posture** — the research-first Prelude precedent verbatim: a
FAILED slice is announced and planning proceeds with the survivors. A failed
exploration never blocks planning.

### 4. Delivery — `RESEARCH_NOTES`, multi-path

`RESEARCH_NOTES={track_dir}/.conductor/handoff/P0T1.md;P0T2.md;P0T3.md` on
the §2.3 spec-planner envelope (semicolon-joined). One spec-planner.md
amendment: the input table's "path" becomes "path (or semicolon-joined
paths)" and §3.0 reads each before the codebase scan. Carried forward on
the §2.3 re-dispatch envelope exactly as today's single path is.

No new envelope field — the Prelude seam already owns this delivery, and a
second field would duplicate it.

### 5. Resume marker — nothing stamped

No new `steps_done` key. If the run is interrupted mid-fan-out, a re-run
re-dispatches the slices and `append-handoff` stacks fresh timestamped
sections — additive, not corrupting. Revisit only if live runs show the
re-spend mattering.

## Menu Track E folds in

The fan-out track and menu Track E (labeling optimization,
[[conductor/design/extensibility-review-2026-08]] Finding 1) share the
matcher substrate and ship together:

1. **The fog-test complexity rule** in spec-planner §4.2: for each area the
   plan touches where the planner cannot name the concrete files/modules,
   insert an `[Explore]` task ahead of the building tasks. The fan-out's
   notes make "can I name the files" answerable at planning time — the rule
   and the notes are two ends of one mechanism.
2. **Research-first signal widening** — the same complexity wording list,
   as `signals` data on the research-first shape row. One registry edit.
3. **`examples` field on tag rows** + **telemetry persistence** (Finding 1
   methods 4–5), unchanged from the review.

## Track seed

Sequenced **after Track D** (the findings edge — it stabilizes the handoff
substrate this track reads and extends). Estimated S–M. ACs:

- `propose-grounding` is pure (no writes, no model), tested for both hit
  and miss paths, brief-structural signals included.
- new-track §2.2.5: ask only on `foggy`, skip when research-first, FAILURE
  of a slice never blocks planning.
- three parallel dispatches, code-assembled prompts, distinct task slots.
- `RESEARCH_NOTES` carries all surviving paths; spec-planner reads each
  before the scan.
- Track E items (fog rule, signals, examples, telemetry) land in the same
  track.

## Rejected alternatives

- **Best-of-N plan sampling** — the premise challenge; three confident
  wrong plans sharing one blind spot, selection homogenizes, ~3× planner
  cost. Revisit only if grounded plans still disagree in ways telemetry
  shows a second sample would catch.
- **Always-on fan-out** — trivial tracks pay for facts they do not need;
  the fog gate exists to price the spend to the fog.
- **Authored search-space map file** (new per-project artifact) — drift
  liability; the existing surfaces (index.md, wiki, git, code) already are
  the search space. A stale map is worse than none.
- **New envelope field** (e.g. `GROUNDING_FILE`) — duplicates the
  RESEARCH_NOTES seam the Prelude already owns.

## Companion findings this session (recorded, no action beyond noted)

- **Failure-analysis depth (the max-retries question).** Verified adequate:
  the failure-analyst reads every attempt handoff record, re-runs the
  failing command, and reports `root_cause` (must name file/function/API)
  and `what_was_done` (path tried + partial work); the verdict marker is
  transient — consumed by the router, cleared on route. The one gap (verdict
  synthesis not durable post-route) folds into Track D's always-compile:
  compile appends the verdict's root_cause/what_was_done to the handoff
  narrative when routing clears the marker.
- **Refuter vs plan-reviewer duplication.** No plan-reviewer agent exists;
  plan review is refuter (§2.3b, semantic one-claim adversarial pass) plus
  spec-reviewer (§2.4, systematic audit). Lanes pinned by the niche guard;
  the single overlap (over-tag audit in both) is intentional
  belt-and-suspenders at different pipeline stages. No change.

## See Also

- [[conductor/design/decision-grounding-fanout]] — the direction decision
  and its gate check.
- [[conductor/design/extensibility-review-2026-08]] — the menu this track
  sequences into; Finding 1 is the folded-in Track E.
- [[conductor/design/planning-as-data]] — the Prelude seam being extended.
