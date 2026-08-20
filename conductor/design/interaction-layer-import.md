---
type: concept
sources:
  - runtime/contracts/grill-discipline
  - skills/brief/SKILL.md
  - skills/discover/SKILL.md
  - scripts/on-brief-grill-tripwire.py
  - scripts/lib/brief_counters.py
  - "~/Documents/wiki/agent-engineering/mattpocock-skills.md (external; read at 2026-08-19, wiki commit 03e6faa)"
last_verified: 2026-08-20
---

# Interaction-Layer Import Campaign (grill optimization + skill-design patterns)

Status: **Implemented** (2026-08-19 agreed via grill per
`runtime/contracts/grill-discipline.md`; implemented 2026-08-20,
`84c7e61` → `e7149bb`: Phase 1 grill mechanics D1/D3/D4, Phase 2 docs
grounding D2, Phase 3 router + description audit + two-axis review split +
writing-for-agents sweep D5; 2457 tests green). Two user-approved
adjustments vs this doc's original shape:

- **D5.4 shipped as a reporting split, not a new subagent.** The existing
  4-lens fan-out + critic + per-lens refuters already isolate the axes; the
  gap was the tail — §2.4 now renders per-lens verdicts side by side
  (Standards vs Spec), never merged or re-ranked, persisted as
  `lens_verdicts` on the finalized review-result.json.
- **D2's routing landed as a read-row, not a writer-ownership matrix.**
  `doc-routing.md` gained a decision-lookup row; the write discipline
  single-homes in grill-discipline §7 (crystallization writes), not in a
  new doc-routing writer column.

## Context

A consultation against `mattpocock/skills` (distilled in the personal wiki
note cited above) compared its interview discipline and skill-design theory
with conductor's grill apparatus. The stance was grilled first and settled:
**complementary import.** MP's set is explicitly positioned *against*
process-owning frameworks — conductor is one, deliberately, and the
deterministic core (hooks, track-state CLI, firewall, drift lints) is the
differentiator, not a defect. What imports cleanly is his **interaction
layer**. His advisory-only posture and tracker-as-state model are consciously
rejected (conductor's state machine is stronger than issue-tracker state).

What conductor already has that MP's set lacks — kept, not traded:

- deterministic enforcement (`on-brief-grill-tripwire`: write denied while
  `committed:false` without done-signal; count floor as backstop only)
- the posture spectrum (§1 — the guard against over-grilling)
- single-home discipline with a drift lint (`lint-grill-contract-drift.py`)
- premise-challenge bounded to one (§4) and operationalized unknowns (§5)

Gap findings (all verified in-tree at grill time):

1. **Batching divergence.** Contract §3 mandates one decision per
   `AskUserQuestion` call; MP's `grilling` asks the whole frontier per round.
   Both agree on dependency order, recommended-answer-first, wait-before-next,
   facts-are-not-questions. The frontier is by definition the parallel-safe
   set — decisions whose answers do not feed each other — so batching it loses
   nothing epistemically and cuts a 7–8 round-trip brief grill to 2–3. The
   "bewildering" failure the rule was written against was prose-list batching;
   the structured tool form (≤4 questions with option chips) is a different
   medium.
2. **Docs-grounding gap (the "with-docs" half).** The artifacts already exist
   — `conductor/resource/glossary.md` (create-if-missing, global) and
   `conductor/design/decision-*.md` (ADR-style, append-never-delete) — but
   they are populated only *post-track* by the doc-sync pipeline (corpus-writer
   Pass 2). No grill surface reads either (brief §2.0 scans `conductor/index.md`
   for paths only; discover §1.0 reads 4 signal sources), and none writes them:
   terms and decisions crystallized mid-grill evaporate into that track's
   brief.md prose. MP's glossary rule (term → tight definition + *Avoid*-list
   of rejected synonyms, written the moment it crystallizes) is why "the docs
   variant is strictly the better one."
3. **No institutional memory in discover.** Prior `discoveries/*.md` Dropped
   sections are never read back — a candidate dropped with a recorded reason
   gets re-litigated from scratch. MP's `triage` keeps an `.out-of-scope/`
   knowledge base against exactly this.
4. **Fact-finding blocks and bloats.** Grill surfaces' `allowed-tools` omit
   the Agent tool; the orchestrator Reads docs inline during §3 — serializing
   facts with decisions and pulling full doc content into the grill session's
   context (the thing `doc-probe` exists to prevent). MP: a frontier question
   needing a fact dispatches a sub-agent without blocking the rest of the
   frontier.
5. **A second questioning home.** new-track's brief-absent fallback (2–5
   bespoke sequential questions) drops recommended-answer-first and
   look-it-up-first — rules that are baseline competence, not full-grill
   extras — and cites no contract.
6. **Pointer staleness.** brief's `description` frontmatter restates the
   one-question rule ("Grill the user one question at a time…") — stale the
   moment D1 lands. Symptom of a class: descriptions are level-1 firing
   surfaces nothing audits.

## Decisions

Five decisions were grilled to closure. They govern this whole campaign.

### D1 — Frontier rounds, capped at 4 (batching)

Contract §3 replaces "one decision per call, never batch" with: **batch only
mutually-independent decisions** (the frontier: every decision whose
prerequisites are settled), ≤4 per `AskUserQuestion` call — the tool's native
cap — each question still recommended-answer-first. The premise-challenge (§4)
stays strictly solo. A decision whose input depends on another question in the
same round is not on the frontier and must wait. The "one question at a time"
wording survives only as the rule for *dependent* decisions and the challenge.

Enforcement ripple: `on-brief-grill-tripwire` counts **calls** today; it must
count **questions** (the hook input JSON carries `questions[]` — sum lengths
across calls in `scripts/lib/brief_counters.py`, shared with finalize). The
6-floor stays meaningful against batched calls (2 calls × 4 questions = 8 ≥ 6,
legitimate; 2 calls × 1 question = 2, blocked as today). Done-signal remains
the real gate; the count stays a backstop (contract §6 unchanged).

### D2 — Docs-grounded grill on existing homes

No new artifacts. Grills READ and WRITE the two existing homes:

- **Read-back (Q1 fuel):** brief §2.0 and discover §1.0 read
  `conductor/resource/glossary.md` and `conductor/design/decision-*.md`
  (when present) before questioning — settled vocabulary and prior decisions
  are shared-known, never re-asked. discover additionally reads prior
  `discoveries/*.md` **Dropped sections only** (Accepted entries already
  became tracks; the tracks duplicate guard covers those) so a prior drop with
  a reason is surfaced, not re-litigated.
- **Inline writes (crystallization):** when a term crystallizes mid-grill
  (user coins one, or two synonyms collide), the grill writes a glossary
  entry (definition + Avoid-list). When the premise-challenge resolves, or an
  Out-of-Scope decision has a rejected alternative worth remembering, the
  grill appends `conductor/design/decision-*.md`.
- **Sparsity gate (MP's rule, all three):** a decision record only when
  hard-to-reverse ∧ surprising-without-context ∧ a-real-trade-off. Glossary
  entries are cheap; decision records are not. The gate is write discipline at
  grill time; append-never-delete stays.

Ripples: `doc-routing.md` gains the orchestrator-as-grill-stage writer row
(read+append, existing homes); brief §4.1's scoped commit widens to include
touched glossary/decision files; the corpus-writer post-track pass now *co
-checks* rather than *solely owns* these two docs (it reconciles, the grill
no longer defers to it). `on-category-write-guard` is unaffected (guards
category index.md, not these paths).

### D3 — Non-blocking fact subagents

brief/discover `allowed-tools` gain the Agent tool. When a recommendation
needs a fact (doc content, codebase state, a count from a log), dispatch a
read-only subagent (the `explorer`/`doc-probe` pattern) while the frontier
round waits on the human; fold the result into the next round's
recommendations. Facts never block decisions; full doc content never enters
the grill session's context. Contract §3 rule 2 (look-it-up-first) gains the
dispatch form as its preferred mechanism for non-trivial lookups; inline
Read/Grep stays fine for one-liners.

### D4 — The legacy Q&A path folds under the contract

new-track's brief-absent fallback keeps existing (the posture spectrum
legitimizes a light path) but cites `grill-discipline.md` with an explicit
**batch-confirm** posture pick. Fewer questions allowed; recommended-answer-
first and look-it-up-first remain mandatory — they are baseline competence,
not full-grill extras. The bespoke 2–5-question script is deleted; wiring
tests pin the citation. The drift lint needs no change — its trigger regex
(`four-quadrant|one question at a time`) does not match the folded path's
prose, and the citation requirement is enforced by the wiring test instead.

### D5 — Plugin-wide actionable set (all four) + two observations

1. **Router skill** (the `ask-matt` pattern): one new user-invoked
   `/conductor:route` intent→command map ("nothing exists, find work" →
   discover → brief → new-track; "spec exists" → implement; "mid-track
   disaster" → re-spec/revert; "health" → status/dashboard; "wiki drifted" →
   wiki-doctor). It cites the README's generated tables as its roster source
   (single-source; `check-readme-sync` already pins them) and is kept honest
   by a wiring test — a router that lies is worse than none. Any future skill
   add/rename re-syncs it (rule lands in the repo docs).
2. **Pointer/description audit**: systematic pass over all 17
   `description`/`when_to_use` frontmatter fields as firing pointers.
   Front-load the leading word; one trigger per branch (synonyms renaming one
   trigger = one trigger written twice); cut identity the body carries.
   brief's description fix rides in Phase 1 (D1 forces it anyway).
3. **Writing-for-agents sweep** (its own follow-on pass, contract-by-contract):
   leading words (prefer pretrained compact tokens — "frontier", "tracer
   bullet" — over coinages paying definition tokens; Rail A/B-min are pinned
   brand vocabulary, defined once at first use); negation pairing (audit
   core-contract V1–V11 — every prohibition paired with its positive target);
   no-op sweep (per sentence: does this change behavior vs the model's
   default? delete whole when not — the complementary lens to the
   single-source campaign, which killed restatements, not never-load-bearing
   lines). Wiring tests pin prose; they move in lockstep.
4. **Two-axis review split**: split plan/spec-faithfulness out of
   code-reviewer into a parallel isolated subagent beside it (the `refuter`
   precedent), reported side-by-side, never merged or re-ranked — that is the
   reranking the separation exists to prevent. Lens-matrix contract updates
   to the two-axis form.

Observations (recorded, not actionable now): (a) conductor has zero
model-invoked skills — disciplines are contracts reachable only via explicit
Read pointers; defensible given hook-enforced dispatch, revisit if a surface
ever needs autonomous firing; (b) no periodic deep-module survey exists (MP's
`improve-codebase-architecture`: cadence, commit-hot-spot scoping, deletion
test, deepening candidates) — the two-tier refactor covers tactical depth
only.

## Phases

### Phase 1 — Grill mechanics (contract + enforcement + wiring)

1. Edit `runtime/contracts/grill-discipline.md` §3 (frontier rounds ≤4,
   dependent-decisions-serial, challenge solo; §3 rule 2 gains the dispatch
   form). Update `last_verified`.
2. Tripwire + `brief_counters.py`: count questions, not calls. Tests.
3. brief: description frontmatter, §2.5/§3 wiring, Agent tool; discover:
   §2.0/§3 wiring, Agent tool. Wiring tests in lockstep
   (test-asserted-string inventory BEFORE editing — the body-trim campaign
   pattern).
4. new-track: fold legacy path under contract citation (D4).

### Phase 2 — Docs grounding

1. Read-back wiring (brief §2.0, discover §1.0 + prior-drops).
2. Inline writes at crystallization + sparsity gate prose (single-homed in
   the contract or a small `domain-language` contract — decide at
   implementation; do NOT restate per-surface).
3. `doc-routing.md` row; brief §4.1 commit scope; corpus-writer co-check
   language.

### Phase 3 — Plugin-wide (D5 items 1–4; item 3 may phase separately)

Success signals, measured not asserted (benchmarking-crimes discipline):
frontier batching — questions-per-call and calls-per-grill from the (already
existing) grill counter sidecar, pre/post; docs grounding — glossary/decision
hit rate on later grills (entries read back vs re-asked); pointer audit —
wiring-test coverage of description claims.

## Non-goals

- **No rearchitect toward advisory-only.** Rejected at the premise challenge;
  the deterministic core is the differentiator.
- **No new knowledge artifacts.** No `CONTEXT.md` import; glossary and
  decision records already have homes. The wiki corpus stays the project-doc
  layer; the glossary stays the vocabulary layer.
- **No uncapped whole-frontier prose rounds.** Rounds past the 4-question tool
  cap degrade to prose lists — the failure mode the old rule existed for.
- **Grill scope stays spec-input surfaces.** The posture spectrum is
  unchanged; executors stay ask-nothing, spec-reviewer stays lens-only.
- **No `to-questionnaire` inverse skill.** Single-user tool; knowledge is not
  in a third party's head.

## See Also

- `runtime/contracts/grill-discipline.md` — the contract Phase 1 edits
- `conductor/design/single-source-authority.md` — the doctrine D2's read-back
  rule and the router's roster citation inherit
- `scripts/on-brief-grill-tripwire.py`, `scripts/lib/brief_counters.py` — the
  D1 enforcement pair
- The personal wiki note (external): mattpocock/skills distillation —
  grilling §5.1, domain-modeling §5.2, writing-for-agents §5.5, invocation
  §3
- <https://github.com/mattpocock/skills> — upstream (read at 9c9f36c)
