---
type: concept
sources:
  - runtime/contracts/grill-discipline
  - scripts/track_state/dispatch.py
  - scripts/track_state/task_profiles.py
  - templates/workflow/task-type-profiles.json
  - templates/task-workflow.md
  - agents/task-executor.md
  - conductor/design/decision-pattern-realization
  - conductor/design/single-source-authority
  - "~/Documents/wiki (external; agent-engineering + skills notes read at 2026-08-20, wiki commit 03e6faa)"
last_verified: 2026-08-20
---

# Dispatch-Manifest Seam (workflow-as-data campaign)

Status: **Agreed** (2026-08-20, resolved via grill per
`runtime/contracts/grill-discipline.md` — premise challenge + four frontier
rounds; every decision user-confirmed) — not yet implemented.

## Context

The ask: make the plan → implement-step workflow more extendable and generic.
The floated shape — "one general subagent doing all the work, plus a
dynamically generated prompt file" — bundled two separate pains, and the
premise challenge (§4) unbundled them:

1. **Workflow content is welded into two hardcoded homes.** The default TDD
   path's step prose lives in `templates/task-workflow.md` Steps 3–8 (a
   template shared by every task type), and the `[Migrate]`-style bespoke
   paths live as JSON-string prose inside
   `templates/workflow/task-type-profiles.json` `workflow` fields.
2. **The executor re-derives the path decision every dispatch.**
   `agents/task-executor.md` §1.5/§4.0 branch on tag fast-path →
   registry-doc fetch → full TDD, reading injected registry state and
   deciding where its instructions live. Extending the system means editing
   plugin templates AND executor branching prose.

What already works and stays: the two registries (task-type profiles own
node behavior; workflow shapes own topology — `nodes` advisory,
`verifiers`/`gates` load-bearing), `build_dispatch_prompt` as the one pure
`(action, state) → (agent, prompt)` composer, the dispatch lifecycle
(prepare → Start commit → executor Step-8 commit → finalize with notes/SHA/
plan.md), F1–F6.

Constraint history honored: 1B declined a `step_sequence` axis (a second
representation of step semantics = a drift surface); D2 dynamic spine and
D1 native Workflow stay deferred ([[conductor/design/single-source-authority]]
Non-goals); the phase-verify apparatus was unwound — one verification path
survives; the single-source ladder's Non-goal "no new injection channels"
still binds.

Wiki principles that shaped the design (external notes, cited above): the
node test (a node differs by model, toolset, or role — "steps I could
inline" are not nodes); harness shell ≠ workflow content (frontmatter
model/tools/maxTurns/permissionMode cannot be expressed in a prompt); the
agent-brief precedent and its lesson ("a document restating the environment
is a cache" — and caches drift); "the pointer's wording, not its target,
decides when the agent reaches it."

## Decisions

Seven decisions, each with its rejected alternative.

### D1 — Workflow-as-data, keep role shells (the premise resolution)

Agents keep harness-shell specialization (model tier, tool fences, maxTurns,
permissionMode); per-task *workflow* becomes a code-composed, registry-derived
artifact. **Rejected: one-general-executor collapse** — the roster's
harness-level differences ARE the specialization, and prompts cannot express
them (node test). **Rejected: a full agent-brief cache** per task —
restating the environment per agent is the cache anti-pattern; pointing
keeps one representation.

### D2 — Registry-indexed docfiles (`templates/workflow/steps/<name>.md`)

Registry rows gain a `workflow_doc` field naming their step-prose docfile.
`default-tdd.md` is seeded **verbatim** from `task-workflow.md` Steps 3–8 —
a relocation (the Delete→Point rung of the single-source ladder), not a
copy; the prose MOVES home. `migrate.md` absorbs the `[Migrate]` `workflow`
JSON-string prose. Project overlays may point at
`conductor/workflow/steps/*.md` (project wins, mirroring the registry
overlay rule). Docfiles carry **step prose only** — gates, exemptions, and
verifiers stay registry-owned shape fields (every freedom declares a
substitute). **A project adds a full custom workflow = one docfile + one
registry row, zero plugin edits.** `registry-doc --tag` renders the resolved
docfile. **Rejected: a `step_sequence` axis** — re-trips declined 1B; the
seam derives-and-points, never restates.

### D3 — `task-workflow.md` shrinks to its orchestrator-owned truth

The template keeps Steps 1–2/9–11 (orchestrator-owned), the ownership split,
and a pointer into the steps library. It stops being two documents in one
file. The bare `./phase-checkpoint.md` reference at its step-9 line remains
a documented non-offender for the dangling-doctrine lint.

### D4 — Dispatch manifest (per-dispatch pointer artifact)

A pure `compose_manifest(track_dir, state, pre) -> str` writes
`<track>/.conductor/dispatch-manifest.md` at dispatch-prepare — the one
site that serves both rails — containing: task identity, resolved
gates/exemptions (shape ⊕ tag), the **path decision** (tag fast-path |
bespoke docfile | default-tdd), and pointers. The dispatch envelope gains
`WORKFLOW_FILE=<path>`. The manifest is transient (gitignored via the
transient-marker tuple, reaped at finalize/recover/wave teardown) and
deterministic — no timestamps, no absolute plugin paths; byte-identical on
retry and across plugin upgrades. It derives from the same registries as
the injected `[Conductor Registry]` block; a golden test asserts their
agreement (the floor invariant).

### D5 — Executor §1.5/§4.0 collapse to manifest-read

The branching prose becomes "read your manifest, follow your docfile." The
injected registry block stays the deterministic floor (no new injection
channel — the manifest is fetch-side, Tier B in
[[runtime/contracts/context-model]]); the manifest is the derived per-task
view. `on-subagent-start.py`'s `workflow: present` pointer retargets to the
manifest/docfile (`registry-doc` stays the human view).

### D6 — Roster: command cluster merge only

{test-digester, log-checker} → `command-digester` (haiku; Bash/Read/Grep/
Glob; maxTurns 12; nested-only; `PURPOSE=red|coverage|log-verify`). 24 → 23
agents. **Rejected: merging the wiki pair** (wiki-differ is Write-capable,
wiki-researcher read-only — a real toolset difference; the node test
again). **Rejected: build-runner/test-runner and general verifier merges**
(registry vocabulary — different verifiers-for-shape contracts).

### D7 — Single cutover, test-gated

No feature flag; the suite's drift gates carry the transition.
Operationalized unknown (§5 discipline): manifest-driven executor
compliance ≈ inline-branch compliance, measured on a scratch track —
identical gate outcomes, no extra tripwire rounds. A miss is pointer
wording, not architecture; fix the pointer, not the seam.

## Phases

- **Phase A — docfile library + `workflow_doc` field.** Create the two
  docfiles; `task_profiles.py` gains `workflow_doc_for(tag)` + the
  plugin⊕project resolver (fail-open → `default-tdd.md`);
  `registry_validate.py` knows the field + cross-checks declared docfiles
  exist; `cmd_registry_doc --tag` renders the docfile verbatim; shrink
  `templates/task-workflow.md`; repoint consumers (task-executor,
  spec-planner, setup skill); retarget wiring tests. Docfiles + accessor
  land BEFORE the render swap; template shrink lands LAST (no consumer
  pointer ever dangles).
- **Phase B — manifest generator + envelope + executor collapse.** New pure
  `scripts/track_state/dispatch_manifest.py`; write at `prepare_dispatch`,
  reap at `finalize_dispatch`/`cmd_recover`; wave members get per-worktree
  manifests; envelope `WORKFLOW_FILE=` (executor arm only; rides inside
  `prompt`, already compact-allowlisted); transient-marker + gitignore
  entries; executor §1.5/§4.0 and the subagent-start pointer retarget.
- **Phase C — roster merge (command cluster only).** Create
  `command-digester.md`; delete `test-digester.md`/`log-checker.md`;
  retarget `AGENT_REMINDERS`, hooks matchers, call sites, wiring tests;
  README re-sync (24 → 23).
- **Phase D — drift gates + docs.** Golden manifest test (byte-stable;
  manifest/registry-block agreement); extend the dangling-doctrine lint to
  the steps library (plugin refs must carry the `${CLAUDE_PLUGIN_ROOT}/`
  prefix; project-relative refs are legitimate); README registry table +
  `plan-format-contract` + `context-model` tier table. Schema: no change
  (manifest is derived + transient).

Per-phase conventional commits; full suite green each phase; end-to-end
scratch-track smoke on Rail B + wave smoke at the end.

## Non-goals

- **No one-general-executor collapse; no agent-brief cache.** D1's rejected
  alternatives, recorded so they are not re-litigated.
- **No `step_sequence` axis** (1B stands; re-proposal bar: ≥2 task-types
  with bespoke step *semantics*).
- **D2 dynamic spine / D1 native Workflow stay deferred**
  ([[conductor/design/single-source-authority]]).
- **No dispatch-lifecycle change** — prepare/Start-commit/Step-8-commit/
  finalize (notes/SHA/plan.md) untouched.
- **No new injection channel** — the manifest is fetched (Tier B), never
  hook-injected.
- **No wiki-pair or build/test-runner merges** (D6's rejected set).

## See Also

- [[conductor/design/decision-workflow-as-data]] — the ADR recording the
  direction decision and its gate check
- [[conductor/design/single-source-authority]] — the ladder D2's relocation
  follows; the Non-goals this campaign inherits
- [[conductor/design/decision-pattern-realization]] — the two-rail
  assignment; why construction stays on prose rails welded to the state
  machine
- [[conductor/design/interaction-layer-import]] — the predecessor grill
  session (and this doc's format exemplar)
- [[runtime/contracts/grill-discipline]] — §4 premise challenge, §7
  crystallization writes (this doc IS one)
- [[runtime/contracts/context-model]] — Tier B (manifest fetch) / Tier C
  (docfile self-load) placement
- [[conductor/resource/glossary]] — **dispatch manifest**, **workflow
  docfile** entries (settled vocabulary from this grill)
