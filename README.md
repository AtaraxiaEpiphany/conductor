# Conductor

**Spec-Driven Development Orchestration Plugin for [Claude Code](https://claude.ai/code)**

Conductor coordinates software construction by managing the full lifecycle of development *tracks* — from specification and planning through TDD implementation, code review, documentation sync, and archival. It enforces quality gates, state consistency, and workflow discipline through an extensive system of hooks, subagents, and a state machine CLI.

## Features

- **Track-based project management** — Work is organized into tracks (feature / bugfix / chore / docs), each with spec, plan, state, and handoff files
- **TDD enforcement** — Mandatory test-driven development with an 80 % coverage gate (server-side verification, not agent self-report), paired with a frozen-anchor counter-metric that an executing agent cannot game
- **Registry-driven workflow** — The tag/mode/shape vocabulary that drives routing, gating, and topology is *data*, resolved as **plugin baseline ⊕ project overlay** (one JSON row to add a task type, a verify mode, or a workflow shape — zero plugin edits)
- **Subagent orchestration** — A main orchestrator dispatches 23 specialized AI agents for isolated, focused work
- **State machine CLI** — `track-state` manages all state mutations atomically; `plan.md` stays in sync as the human-readable mirror
- **Execution firewall** — Six mandatory pre-action checks (F1–F6) and eleven anti-patterns (V1–V11) prevent workflow violations
- **Resumable recovery** — Handoff files, state recovery on resume, name-keyed plan reconciliation, and SubagentStop result-block recovery — an agent that crashes before emitting its result block earns a recovery turn instead of being silently lost
- **Git integration** — Conventional commits, git notes for audit trail, checkpoint commits per phase, SHA tracking on every task

## Requirements

- **Python 3** (standard library only — no pip dependencies)
- **Claude Code** CLI
- **Git**

## Installation

Place this project inside your Claude Code plugins directory. The plugin root is identified by the `CLAUDE_PLUGIN_ROOT` environment variable.

## Usage

All interaction happens through Claude Code slash commands:

| Command | Description |
|---------|-------------|
| `/conductor:setup` | Scaffold a new project with the Conductor environment |
| `/conductor:new-track [description]` | Create a new track (generates spec, plan, state) |
| `/conductor:brief [topic]` | Grill shared understanding one question at a time, then write a `brief.md` that `new-track` consumes as authoritative input |
| `/conductor:implement [track]` | Execute the dispatch loop to implement a track |
| `/conductor:implement-step [track]` | Thin Rail B-min teleoperator — relays exactly the leaf action `track-state step` emits (small-window friendly) |
| `/conductor:parallel [track]` | Fan out file-disjoint, deps-declared tasks into worktree-isolated waves, then serially integrate each commit |
| `/conductor:parallel-step [track]` | Thin teleoperator over `track-state wave-step` (small-window friendly) |
| `/conductor:post-loop-step [track]` | Thin teleoperator over `track-state post-loop-step` — deferred/finalize/doc-sync/review/archive spine |
| `/conductor:reconcile [track]` | Re-sync `track-state.json` after a hand-edit of `plan.md`, **preserving commit SHAs** |
| `/conductor:re-spec [track]` | Mid-track re-spec after a `git reset` — edit `spec.md`, surface completed SHAs a changed AC puts at risk, commit |
| `/conductor:status [track]` | View project / track progress |
| `/conductor:review [track]` | Code review of completed work |
| `/conductor:revert [scope]` | Revert work with state consistency |
| `/conductor:wiki [topic]` | Query the Conductor docs wiki — status snapshots & topic search |
| `/conductor:wiki-doctor` | Diagnose wiki health — lint audits & diff vs codebase reality |

### `track-state` CLI

The `bin/track-state` command provides direct state management. Run `bin/track-state help` for the full, current list (≈ 70 subcommands) — it is grouped and self-describing. The surface, grouped by purpose:

| Group | Representative subcommands |
|-------|----------------------------|
| **Lifecycle / setup** | `init-from-plan`, `start`, `set-mode`, `set-workflow-shape`, `finalize`, `archive`, `derive-name`, `resolve-track`, `check` (resolve + preflight), `preflight` |
| **Task mutations** | `lock`, `complete`, `fail`, `skip`, `block`, `defer`, `reset`, `split`, `set-max-retries`, `set-contract` |
| **Dispatch (Rail A)** | `next`, `dispatch-next`, `dispatch-prepare`, `dispatch-finalize`, `process-result`, `write-result` |
| **Rail B-min spines** | `step`, `wave-step`, `post-loop-step`, `recover` |
| **Wave parallelism** | `dispatch-wave`, `wave-status`, `wave-finalize`, `wave-abort` |
| **Phase verification** | `phase-done`, `phase-verdict`, `phase-checkpoint-review`, `failure-analyst-verdict`, `skip-analyst-verdict`, `skip-refute-review` |
| **Plan / spec sync** | `sync-plan`, `reconcile-plan`, `sync-handoff`, `add-checkpoint`, `compile-track-findings`, `harvest-candidates` |
| **Frozen anchor** | `freeze`, `thaw`, `anchor-status` (run `--verify` for the counter-metric) |
| **Registries (read-only)** | `registry-doc` (resolved task-type / verify-mode / workflow-shape tables) |
| **Observability (read-only)** | `indices`, `shas`, `log-path`, `subagent-log`, `quality-snapshot`, `spec-integrity`, `spec-anchors`, `spec-delta`, `deferred-report`, `post-loop-status` |
| **Maintenance** | `validate` (`--fix` to persist repairs), `gc` |
| **Resume markers** | `new-track-*`, `brief-*` (idempotent resume state for the skills) |

> `setup` survives as a hidden alias of `check` for back-compat. `reconcile-plan` is dry-run by default (writes only with `--apply`); `validate` is diagnostic by default (`--fix` mutates).

## Registry-driven workflow

The conductor's routing vocabulary is **data, not code.** Three registries, each an ordered JSON document, drive what was previously hardcoded Python sets and agent-prose `if/elif` ladders. Each resolves as **plugin baseline ⊕ project overlay** — a project drops any subset of the three JSON files at `conductor/workflow/` to add or override entries with **zero plugin edits** (opt-in by file presence; the project wins conflicts).

| Axis | What it declares | Source of the *name* | Mutable? |
|------|------------------|----------------------|----------|
| **Task-type** (`task-type-profiles.json`) | What a tag *means* — route, TDD/coverage exemption, when-to-use hint, optional executor `workflow`, optional `refactor: true` (tactical-refactor opt-in) | Re-derived from the task name's leading tag | No (re-parsed at every read; `task_type` is a typed cache) |
| **Phase-verify mode** (`verify-mode-profiles.json`) | What a gate *means* — which steps run, fix policy, the `protocol` prose `phase-checker` emits | Re-parsed from the phase heading's `<!-- verify: … -->` | No (advisory metadata, never persisted to state) |
| **Workflow shape** (`workflow-shapes.json`) | The **topology** — which dispatch agents run, in what order, its stop condition | The `workflow_shape` field on `track-state.json` | **Yes** — the one declaration/knob axis (`set-workflow-shape`). Advisory today: declares intended topology and surfaces `shape_violation` drift, but does not reorder dispatch (both built-in shapes plan-first) |

**Adding a task type / verify mode / workflow shape is one row in the registry.** Tag extraction, TDD-gating, dispatch routing, the `[Conductor Registry]` block injected into agents, and the `registry-doc` render all derive from it automatically.

- **Inspect the resolved registry** — `track-state registry-doc` prints the full resolved tables (baseline + your overlay); `registry-doc --tag <Name>` / `--mode <m>` / `--shape <s>` prints one row plus its prompt-shaping prose verbatim.
- **Project overlay** — drop `conductor/workflow/task-type-profiles.json` (or the verify-mode / workflow-shape equivalent) next to the files `setup` scaffolds there. Absent = plugin defaults, no behavior change.
- **Unknown values** — an unknown task tag is a **hard error** at `init-from-plan` (a wrong tag means wrong executor behavior); an unknown verify mode only **warns** ("no directive" is a valid state). `workflow_shape` reads **fail-open** to `default` but `set-workflow-shape` **hard-rejects** an unknown shape so a deliberate set never silently no-ops.
- **Guardrails** — every optimization-shaped registry field pairs with an independent counter-metric (the frozen anchor's measured pass/drift rate vs. self-reported coverage), every cap is disclosed rather than silently enforced, and the "definition of done" is read-only to the executing agent. A drift-killer lint (`scripts/check-contract-registry-sync.py`) forbids a second hand-maintained vocabulary home in the contract.

The authoritative grammar and invariants live in `runtime/contracts/plan-format-contract.md` (the registries own the *vocabulary*; the contract owns the *rules*).

## Recovery after a `git reset`

When a divergent run is undone with `git reset`, two name-keyed paths bring state back in sync **without losing the `commit_sha` records on work that survived**:

- **`plan.md` side** — `track-state reconcile-plan` (driven by `/conductor:reconcile`) matches **by phase number + task name**, not position, so a SHA stays on its named task across reorders and inserts. It is dry-run by default, bucketing the diff (`unchanged` / `tag_or_status` / `split` / `unmatched` / `dangling_sha`) and refusing ambiguous nodes until you pass `--rename` / `--drop` / `--clear-dangling`. Contrast `sync-plan` (positional — reorders silently rebind SHAs) and `init-from-plan --force` (wipes every SHA — a fresh start, never mid-track).
- **`spec.md` side** — `/conductor:re-spec` (driven by `track-state spec-delta`) edits an Acceptance Criterion, commits it as a scoped `docs(spec):`, then **surfaces** the completed tasks whose `commit_sha` the changed AC may have invalidated (`at_risk_tasks`) and halts for your keep-vs-reset decision — it never auto-resets. The two commands compose; they do not overlap.

## Architecture

```
conductor-plugin/
├── agents/                 23 specialised agent definitions (.md)
├── bin/track-state         Shell wrapper for the state CLI
├── conductor/design/       Decision records (serial-execution, loop-heartbeat, rail-b step/wave …)
├── hooks/hooks.json        9 hook event types, 21 matcher entries
├── runtime/                System prompt material injected into sessions
│   ├── core-contract.md      Main-session contract (F1–F6, V1–V11)
│   ├── subagent-firewall.md  Subagent safety floor (dispatch injection)
│   └── contracts/            Per-role contracts (plan-format, review schema, refactor, doc conventions …)
├── schemas/                JSON Schema for track-state.json
├── scripts/
│   ├── lib/                Shared library (env, dispatch_inflight, hook_io, logging, recovery, validation …)
│   ├── track_state/        State machine CLI package (cli + cmd_* modules + *_profiles.py registries)
│   └── *.py                Hook scripts (session start/end, subagent-start, dispatch-dedupe, tripwire …)
├── skills/                 15 slash-command skills (implement, new-track, reconcile, re-spec, parallel, wiki …)
├── templates/
│   ├── workflow/             The three registries: task-type / verify-mode / workflow-shape profiles (.json)
│   ├── code-styleguides/     10 language style guides
│   ├── dev-commands/         8 language dev-command templates
│   └── testing/              Testing strategy template
└── tests/                  pytest suite (run from repo root: PYTHONPATH=. python3 -m pytest tests/)
```

### Subagents

| Agent | Model | Purpose |
|-------|-------|---------|
| `task-executor` | sonnet | TDD implementation (steps 3–8) |
| `explorer` | sonnet | Read-only code investigation, Layer-0 map |
| `spec-planner` | sonnet | Generate spec.md + plan.md |
| `spec-reviewer` | haiku | Interactive spec/plan review |
| `strategy-writer` | sonnet | Project-specific testing/strategy.md from real test layout |
| `project-analyzer` | sonnet | Brownfield project detection |
| `phase-checker` | sonnet | Phase checkpoint synthesizer (mode-agnostic verify loop) |
| `ac-tracer` | sonnet | AC-evidence-trace phase verification (read-only) |
| `test-runner` | haiku | L1 verify-only phase tier — runs the suite once (read-only) |
| `code-reviewer` | sonnet | Deep code review against spec/plan |
| `refuter` | sonnet | Adversarial read-only verdict/finding verifier |
| `refactorer` | sonnet | Bounded tactical refactor (behavior-preserving) |
| `apply-fixes` | sonnet | Bounded remediation patcher (one finding chunk) |
| `skip-analyst` | haiku | Failed-task skip analysis |
| `failure-analyst` | haiku | Diagnoses why a repeatedly failed task keeps failing; recommends retry-differently / replan / decompose / escalate |
| `test-digester` | haiku | Read-only test/coverage digest (delegated by task-executor) |
| `doc-probe` | haiku | Read-only scoped design-doc digester |
| `log-checker` | haiku | Read-only git-history verifier for doc-update entries |
| `corpus-writer` | sonnet | Doc-sync Phase 1 — corpus edits + graduation |
| `wiki-synthesizer` | sonnet | Doc-sync Phase 2 — overview/purpose/log synthesis |
| `doc-linter` | sonnet | Docs wiki health-check (broken refs, stale claims, gaps) |
| `wiki-researcher` | haiku | Wiki topic query — cited answer synthesis (read-only) |
| `wiki-differ` | haiku | Wiki-vs-codebase drift detection (read-only) |

### Execution Firewall

Before every code-modifying action, Conductor enforces six checks (authoritative source: `runtime/core-contract.md`):

1. **F1** — Global State Lock: at most one active `[~]` task at a time (wave-lock relaxation under `/conductor:parallel`)
2. **F2** — TDD Gate: no implementation before a failing test
3. **F3** — Coverage Gate: test coverage ≥ 80 % before commit
4. **F4** — SHA Must Exist: every non-transient marker carries a `[sha]`
5. **F5** — Checkpoint Integrity: phase checkpoint is mandatory
6. **F6** — Context Guard: never skip workflow steps

## Testing

```bash
PYTHONPATH=. python3 -m pytest tests/
```

### Drift gates

The `scripts/` tree also hosts a family of **drift gates** (`lint-*.py` / `check-*.py`) — each is wired through a `tests/test_<gate>.py` wrapper that runs it over the live tree, so `pytest tests/` sweeps them too. Two examples: `lint-prose-impl-leak.py` flags rotting `file.ext:NN` line-number citations in markdown prose (a `:NN` goes stale the moment a line is inserted above it), and `check-contract-registry-sync.py` forbids a second hand-maintained vocabulary home in the contract.

### Verification ladder

Conductor verifies work across a ladder — **L0** static, **L1** unit/integration, **L2** browser-E2E (opportunistic, when a browser-automation MCP is connected), **L4** human manual plan. It does **not** cover **L3** (production logs/metrics/traces): that rung is project-specific infrastructure a generic plugin cannot provision and is left to the host project.

## Project conventions

**Scripts and dev commands belong in the target project, not under `conductor/`.** Put run/build helpers (`start.sh`, `run.sh`) at the project root in `./scripts/` or a `Makefile`/`package.json` script; tests go in the test root `setup` resolves (detected from `conductor/.conductor/analysis.json`, else `tests/`); one-off CLIs go in `./bin/`. The `conductor/` tree is the spec/wiki/planning map that Conductor agents route on — it is not a home for executable code, so it intentionally has no `scripts/` slot and no row in `conductor/index.md`. (The `conductor/workflow/` overlay JSON is the one exception — it is *declarative data* the registries read, not executable code.) See `templates/testing/strategy.md` → *Scripts & Dev Commands* for the full placement table.

## License

[MIT](LICENSE)
