---
type: concept
sources:
  - runtime/core-contract
last_verified: 2026-07-02
---

# Decision: Multi-Agent Pattern Realization (prose skill vs Workflow tool)

Status: **Accepted** — the plugin's orchestration runs on two rails, assigned by
whether a pattern touches the construction state machine. Construction
(implement / parallel / revert) stays on **prompt-driven prose skills** welded to
`track-state.json`, F1–F6, the deps substrate, and the wave ledger. Analysis
(adversarial review, tournament planning, loop-until-dry audits) moves to the
**`Workflow` JS tool**, whose deterministic code control flow is strictly better
for stateless fan-out/synthesize and relieves the prose-hardening burden. This
record governs the forthcoming pattern additions (P0–P3 below).

## Context

`conductor:parallel` (`skills/parallel/SKILL.md`) is a rigorous instance of the
harness-engineering **fan-out-and-synthesize** pattern: §3.2 dispatches all wave
members in one message (a `parallel()` barrier), §4.0 synthesizes by serial
squash-merge; each member runs worktree-isolated (`scripts/track_state/wave.py`)
behind the deps-declared ready-set. Fan-out is solved. The same literature names
seven more patterns — adversarial verification, tournament, loop-until-done,
classify-and-act, generate-and-filter, completeness critic, quarantine — and a
repository grep for `adversarial|refute|tournament|pairwise|loop-until|skeptic`
finds **zero** structural uses: `review` dispatches a single `code-reviewer`
(one pass, not refute-by-default); `spec-planner` emits one plan (no
tournament); no construct loops until a dry condition.

The realization question is not *whether* to add these patterns but *on which
rail*. The dynamic-workflows model realizes patterns through the `Workflow` tool
— a deterministic JS script with `agent()` / `parallel()` / `pipeline()`,
built-in resumability, and per-agent `model` / `effort` routing. This plugin
realizes its orchestration through **prompt-driven skills** that drive the
`Agent` tool, backed by `track-state.json`, the F1–F6 firewalls
([[runtime/core-contract]]), the deps substrate, the parallel ledger
(`.conductor/parallel.json`), and `result.json`. Both rails express every
pattern; their engineering cost differs by an order of magnitude.

## Decision

**Assign each pattern to a rail by whether it touches the construction state
machine.**

- **Rail A — prose skill (current architecture).** Required for anything that
  flows through `dispatch-finalize`, writes `result.json`, honors F1, or
  squash-merges into the track branch. The wave skill is the canonical shape; it
  is **not** ported to the Workflow tool.
- **Rail B — `Workflow` JS tool.** Reserved for stateless fan-out/synthesize
  where deterministic control flow in code beats prose: tournaments, adversarial
  panels, loop-until-dry audits. A Workflow's `agent()` calls do **not** touch
  `track-state.json` — that is the property that makes it safe to adopt for
  analysis and unsafe for construction.

The principle: **construction stays welded to the state machine; analysis is
freed from it.** Adding the missing patterns on Rail B relieves the prose surface
area whose drift is the dominant maintenance cost (phantom-reference fixes,
comprehension digests, integrity gates), rather than re-inflating it.

## Rationale

1. **The two rails have opposite strengths, and the split is the natural seam.**
   Prose skills integrate with everything load-bearing in construction but
   express control flow poorly (the model re-derives it each turn — the source of
   the hardening churn). The Workflow tool expresses control flow in 20 lines of
   code but cannot see `track-state.json`. Assigning by touch gives each pattern
   the rail it needs without forcing the other to compensate.
2. **Verification asymmetry favors analysis-side fan-out.** The literature's core
   empirical claim — *verifying is cheaper than producing, and comparative
   judgment beats absolute scoring* — means analysis patterns (refuters,
   pairwise judges, dry-loops) repay their token cost in quality, while
   construction is spec-bounded by a fixed plan and gains nothing from a
   tournament. Routing analysis to Rail B is where the leverage actually is.
3. **Opt-in discipline, consistent with prior decisions.** Every new pattern
   carries a gate, the way waves require `<!-- deps: -->`
   ([[runtime/contracts/plan-format-contract]]) and the loop heartbeat is opt-in
   ([[conductor/design/decision-loop-heartbeat]]). Tournaments fire only on
   flagged-ambiguous specs; adversarial panels only at review; dry-loops only on
   explicit audit. Patterns that become the default path silently multiply token
   cost — the discipline already in the plugin extends to them.
4. **Resumability and model routing come free on Rail B.** The Workflow tool's
   cached-prefix resume and per-agent `model` / `effort` routing are exactly the
   levers the plugin currently approximates with yield discipline and per-skill
   model pins (`parallel → sonnet`, `doc-linter → sonnet`). Analysis patterns get
   them natively; construction keeps its own yield/ledger resume because it must
   interleave with the state machine.

## Pattern catalog and roadmap

| Pattern | Rail | Status |
|---|---|---|
| Fan-out-and-synthesize | A | **Shipped** — `conductor:parallel` wave |
| Classify-and-act | A | **Present** — task-tag → agent routing; `dispatch-next` action routing; serial-vs-wave via deps. Keep declarative — do **not** add a heuristic independence classifier (file-overlap-at-squash-merge is the failure the deps gate prevents) |
| Quarantine | A | **Present** — `runtime/subagent-firewall.md` is the untrusted-content ↔ privileged-action split |
| Completeness critic | A | **Proposed P2** — phase/final gate agent: enumerate unverified ACs / untested edges / undocumented modules; drives the AC-coverage rates to 100% (the integrity gate's TC/plan/verification measures); strengthens F5 |
| Adversarial verification | B | **Proposed P0** — review panel Workflow: per changed hunk → N diverse-lens verifiers (correctness / security / plan-compliance / test-coverage), refute-by-default, dedup-vs-`seen` (not `confirmed`), loop-until-dry; confirmed findings fed back to the thin `review` skill |
| Tournament | B | **Proposed P1** — gated on spec ambiguity: N competing plans → pairwise-judge against acceptance criteria → synthesize winner + graft runners-up; emits `plan.md` + a `decision-*.md` record |
| Loop-until-dry | B | **Proposed P3** — `wiki-doctor` / drift audit loops until K consecutive empty rounds |
| Generate-and-filter | B | Subsumed by tournament for the planning case |

The construction spine (implement / parallel / revert) is unchanged. P0 is the
highest-value start: stateless (no state-machine entanglement), the pattern the
literature most emphasizes, and it slots behind the existing `review` skill.

## When to revisit

- **Porting construction to Rail B** would require a Workflow `agent()` call to
  participate in `dispatch-finalize`, write `result.json`, and honor F1 — i.e. a
  bridge from the Workflow tool back into the state machine. Defer unless
  construction control flow becomes unmaintainable in prose; until then the wave
  skill is the right shape.
- **A heuristic classify-and-act layer for wave eligibility** is intentionally
  rejected (see catalog). Revisit only if the deps annotation burden proves
  prohibitive for authors, and even then prefer an *advisory* explorer pass that
  *suggests* deps annotations rather than inferring parallelism.

## See Also

- [[runtime/core-contract]] — F1–F6; the construction invariants Rail A preserves and Rail B is structurally exempt from.
- [[conductor/design/decision-serial-execution]] — the serial-default / wave-opt-in model this record extends with analysis-side patterns.
- [[conductor/design/decision-loop-heartbeat]] — the opt-in discipline every new pattern inherits.
- [[runtime/contracts/plan-format-contract]] — the `<!-- deps: -->` annotation the wave ready-set consumes.
- [[runtime/contracts/doc-conventions]] — corpus-authoring conventions.
