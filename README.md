# Conductor

**Spec-Driven Development Orchestration Plugin for [Claude Code](https://claude.ai/code)**

Conductor coordinates software construction by managing the full lifecycle of development *tracks* — from specification and planning through TDD implementation, code review, documentation sync, and archival. It enforces quality gates, state consistency, and workflow discipline through an extensive system of hooks, subagents, and a state machine CLI.

## Features

- **Track-based project management** — Work is organized into tracks (feature / bugfix / chore / docs), each with spec, plan, state, and handoff files
- **TDD enforcement** — Mandatory test-driven development with an 80 % coverage gate (server-side verification, not agent self-report)
- **Registry-driven workflow** — The tag/shape vocabulary that drives routing, gating, and topology is *data*, resolved as **plugin baseline ⊕ project overlay** (one JSON row to add a task type or a workflow shape — zero plugin edits). A full bespoke workflow is one docfile in the steps library + one registry row; each dispatch pins the resolved gates + workflow path in a code-composed **dispatch manifest** (`WORKFLOW_FILE` in the executor's envelope)
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

<!-- conductor:begin:commands-table -->
| Command | Description |
|---------|-------------|
| `/conductor:adopt-skill` | Adopt an outside skill into the conductor — two roads. Road A adopts it as a project TASK TYPE (tag row + workflow docfile; [<Tag>] tasks run the skill's procedure inside task-executor). Road B adopts it as a WRAPPER AGENT (.claude/agents/<name>.md + agent-roster row). Each road is one validated command. |
| `/conductor:brief` | Grill the user (frontier rounds of up to 4 questions per call) to reach shared understanding of a track, then write a brief.md that /conductor:new-track consumes as authoritative planning input |
| `/conductor:dashboard` | Live resolved-workflow dashboard — renders the track's resolved shape (nodes, checkpoint verifier fan-out, gates) with the current position, the task tree, and quality gauges. Read-only in-chat snapshot. |
| `/conductor:discover` | Find recurring dev frictions worth making tracks for (read git log + dispatch-lifecycle.log + .conductor/ signals first), grill-triage them with the user, then write a proposals.md the user feeds to /conductor:brief one proposal at a time |
| `/conductor:implement` | Execute a planned track task-by-task — dispatches each task to a subagent, tracks results and retries through track-state.json, and runs the loop to archive |
| `/conductor:implement-step` | Rail B-min dispatch loop — a teleoperator that runs `track-state step` and relays exactly the leaf action it emits (dispatch one subagent / ask / done). Thin alternative to /conductor:implement for small-window models. |
| `/conductor:new-track` | Create a new track — writes spec.md, plan.md, and track-state.json for orchestrator-driven execution; consumes a brief.md when present (skipping its own Q&A) |
| `/conductor:parallel` | Parallelize opt-in within-track worktree waves — fans out file-disjoint, deps-declared tasks concurrently, then serially integrates each member's commit back |
| `/conductor:parallel-step` | Rail B-min wave loop — a teleoperator that runs `track-state wave-step` and relays exactly the leaf action it emits (fan out a batch / integrate one member / ask / done). Thin alternative to /conductor:parallel for small-window models. |
| `/conductor:post-loop-step` | Rail B-min post-loop teleoperator — runs `track-state post-loop-step` and relays exactly the leaf action it emits (resolve deferred / finalize / dispatch a doc-sync or review agent / digest / archive / done). Thin alternative to the prose post-loop template (§5.0–§8.0) for small-window models. |
| `/conductor:re-spec` | Edit a spec mid-track (AC/constraint/workflow in spec.md) — surface which completed SHAs a changed AC puts at risk, re-validate, commit, then hand off to /conductor:reconcile |
| `/conductor:reconcile` | Re-sync track-state.json after a hand-edit of plan.md (git reset + tag/split/reorder), preserving commit SHAs |
| `/conductor:revert` | Reverts work with track-state.json state synchronization |
| `/conductor:review` | Reviews completed track work using track-state.json for context and commit tracking |
| `/conductor:route` | Route a described goal to the one /conductor: command that starts it — a thin intent-to-command lookup table ("find work", "plan a track", "implement", "review", "undo", "check progress") that prints the command to run; does not execute it |
| `/conductor:setup` | Scaffolds the project with Conductor environment, creates initial track with track-state.json |
| `/conductor:status` | Project status overview — renders the code-owned track-state status backend (authoritative statuses, summary, issues, deferred). Read-only. |
| `/conductor:wiki` | Reads and builds the Conductor documentation wiki — health/status, topic search with citations, directional intent, single-source ingest, and bulk organize-and-file (build) |
| `/conductor:wiki-doctor` | Diagnoses wiki health — lint audits and diff against codebase reality |
<!-- conductor:end:commands-table -->

### `track-state` CLI

<!-- conductor:begin:cli-groups -->
The `bin/track-state` command provides direct state management. Run `bin/track-state help` for the full, current list (95 subcommands across 18 groups) — it is grouped and self-describing. The complete surface, straight from `track_state/commands.py` (the same single source the pre-command guard derives its sanctioned set from):

| Group | Subcommands |
|-------|-------------|
| **Lifecycle** | `init-from-plan`, `start`, `set-mode`, `set-recovery-policy`, `set-workflow-shape`, `finalize`, `archive` |
| **Navigation** | `next`, `dispatch-next`, `recover`, `indices` |
| **State Mutations** | `lock`, `complete`, `fail`, `skip`, `defer`, `block`, `reset`, `set-max-retries`, `split` |
| **Sync & Registry** | `sync-plan`, `reconcile-plan`, `sync-handoff`, `registry-update`, `registry-add`, `registry-doc` |
| **Handoff** | `get-handoff`, `append-handoff`, `harvest-candidates`, `compile-track-findings` |
| **Result Processing** | `write-result`, `process-result` |
| **Dispatch Composites** | `dispatch-prepare`, `dispatch-finalize`, `record-summary` |
| **Rail B-min Spines** | `step`, `post-loop-step`, `post-loop-review`, `phase-verdict`, `phase-checkpoint-review`, `skip-analyst-verdict`, `skip-refute-review`, `plan-refute-prompt`, `grounding-prompt`, `failure-analyst-verdict`, `phase-failure-analyst-verdict`, `amend-apply`, `amend-clear`, `review-attest` |
| **Wave Parallelism** | `dispatch-wave`, `wave-status`, `wave-finalize`, `wave-abort`, `wave-step` |
| **Naming** | `derive-name`, `propose-shape`, `propose-grounding`, `resolve-track`, `check` |
| **New-Track Resume** | `new-track-resume`, `new-track-init`, `new-track-step`, `new-track-set-mode`, `new-track-set-shape`, `new-track-finalize` |
| **Brief** | `brief-resume`, `pending-briefs`, `brief-init`, `brief-finalize`, `brief-grill-done` |
| **Diagnostics** | `validate`, `gc`, `shas`, `post-loop-status`, `checklist-verify`, `deferred-report`, `phase-done`, `add-checkpoint`, `replan`, `preflight`, `quality-snapshot`, `spec-integrity`, `spec-anchors`, `spec-delta`, `task-context`, `view`, `status` |
| **Workflow Studio** | `shape-studio`, `registry-json`, `registry-save` |
| **Roster** | `roster` |
| **Task Types** | `tag` |
| **Probes** | `probe` |
| **Logs** | `log-path`, `subagent-log` |
<!-- conductor:end:cli-groups -->

> `setup` survives as a hidden alias of `check` for back-compat. `reconcile-plan` is dry-run by default (writes only with `--apply`); `validate` is diagnostic by default (`--fix` mutates).

## Registry-driven workflow

The conductor's routing vocabulary is **data, not code.** Four registries, each an ordered JSON document, drive what was previously hardcoded Python sets and agent-prose `if/elif` ladders. Each resolves as **plugin baseline ⊕ project overlay** — a project drops any subset of the JSON files at `conductor/workflow/` to add or override entries with **zero plugin edits** (opt-in by file presence; the project wins conflicts).

| Axis | What it declares | Source of the *name* | Mutable? |
|------|------------------|----------------------|----------|
| **Task-type** (`task-type-profiles.json`) | What a tag *means* — route, `gates` + `grounding` (the class's positive declaration of what "done, verified" means), optional executor `agent` (persona binding), when-to-use hint, optional executor workflow (`workflow_doc` docfile — preferred — or legacy inline `workflow`), optional `refactor: true` (tactical-refactor opt-in) | Re-derived from the task name's leading tag | No (re-parsed at every read; `task_type` is a typed cache) |
| **Workflow shape** (`workflow-shapes.json`) | The **topology** — which dispatch agents run, in what order, its stop condition | The `workflow_shape` field on `track-state.json` | **Yes** — the one declaration/knob axis (`set-workflow-shape`). Advisory today: declares intended topology and surfaces `shape_violation` drift, but does not reorder dispatch (both built-in shapes plan-first) |
| **Agent roster** (`agent-roster.json`) | The **dispatch scaffold** per agent — class (executor / verifier / reviewer / advisory), result fence, `[Conductor Registry]` injection, retry context, single-writer guard, stop-hook recovery | The agent name a skill dispatches | No (re-read per dispatch; changed only by editing the file) |
| **Probes** (`probes.json`) | The **dynamic-context vocabulary** — named, read-only, side-effect-free snapshot commands an agent fetches on demand (`track-state probe <name>`): builtins (`test-state`, `label-accuracy`, `skill-fires`, `gate-outcomes`) or bounded project commands | The probe name an agent or skill asks for | No (re-read per fetch) |

**Adding a task type, workflow shape, agent, or probe is one row in a registry.** Tag extraction, gate derivation, dispatch routing, the `[Conductor Registry]` block injected into agents, the SubagentStart/Stop scaffold, and the `registry-doc` render all derive from it automatically.

- **Integrating an agent is one row** — `"<name>": {class, fence, recovery, recovery_instruction}` in the overlay. The dispatch hooks read the merged roster: an executor-class row derives the single-writer guard; a `stdout-block` recovery row gets a recovery turn instruction; unrostered names stay fail-open (no scaffold, no deny — `track-state check` flags them instead).
- **Integrating a skill is a wrapper agent + one row** — the conductor dispatches agents, not skills. Write a thin `.claude/agents/<name>.md` wrapper whose `skills:` frontmatter preloads the skill (procedure up front, reference on fetch), add its roster row, and the skill rides with conductor's full scaffold: safety floor, result fence, and recovery — zero plugin edits. `track-state roster add <name> --skill <skill>` (front-doored by `/conductor:adopt-skill`) generates both files validated — defaults mirror the task-executor scaffold, so most adoptions need just the two names.
- **Inspect the resolved registry** — `track-state registry-doc` prints the full resolved tables (baseline + your overlay); `registry-doc --tag <Name>` / `--shape <s>` / `--roster <agent>` prints one row plus its prompt-shaping prose verbatim.
- **Project overlay** — drop `conductor/workflow/task-type-profiles.json` (or the workflow-shape equivalent) next to the files `setup` scaffolds there. Absent = plugin defaults, no behavior change. A project's own workflow docfiles live at `conductor/workflow/steps/<name>.md` and win over the plugin's `templates/workflow/steps/` — a full custom workflow is one docfile + one registry row.
- **Unknown values** — an unknown task tag is a **hard error** at `init-from-plan` (a wrong tag means wrong executor behavior). `workflow_shape` reads **fail-open** to `default` but a deliberate declaration **hard-rejects** an unknown shape — `set-workflow-shape`, and `init-from-plan --shape <name>` (which writes the §2.1-confirmed shape at state creation) — so a deliberate set never silently no-ops.
- **Guardrails** — every cap is disclosed rather than silently enforced, the "definition of done" is read-only to the executing agent, and a drift-killer lint (`scripts/check-contract-registry-sync.py`) forbids a second hand-maintained vocabulary home in the contract.

The authoritative grammar and invariants live in `runtime/contracts/plan-format-contract.md` (the registries own the *vocabulary*; the contract owns the *rules*).

### Which seam do I extend?

"I want conductor to do X — which seam?" Every extension is data (one registry row, one docfile, or one wrapper agent); zero plugin edits.

| You want… | The seam |
|---|---|
| A new **kind of work** — a deliverable class with its own definition of done | One overlay row in `task-type-profiles.json` (`gates` + `grounding`) + one steps docfile declaring what its witness attests |
| A class to run **through a skill** | A `.claude/agents/<name>.md` wrapper + an `agent-roster.json` row + `agent: <name>` on the class row (`/conductor:adopt-skill` generates all three) |
| A different **topology** for a track | One row in `workflow-shapes.json` (plus a planning docfile if it plans differently) |
| Different **step prose** for one class | One docfile at `conductor/workflow/steps/<name>.md` (project wins over the plugin's library) |
| Different **planning doctrine** for one shape | One docfile at `conductor/planning/<name>.md` |
| A **measurement** (a context snapshot or a cross-track aggregate) | One row in `probes.json` — builtin or a bounded read-only command |
| The plan to **adapt mid-track** | Nothing to extend — phase-gate replanning is built in (`track-state replan` after each PASSED checkpoint; see `runtime/contracts/phase-gate-replanning.md`) |
| A **guardrail** on tool use or session behavior | A hook (`hooks/hooks.json`) — the frozen anchor points |

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
├── hooks/hooks.json        9 hook event types, 19 hook entries
├── runtime/                System prompt material injected into sessions
│   ├── core-contract.md      Main-session contract (F1–F6, V1–V11)
│   ├── subagent-firewall.md  Subagent safety floor (dispatch injection)
│   └── contracts/            Per-role contracts (plan-format, review schema, refactor, doc conventions …)
├── schemas/                JSON Schema for track-state.json
├── scripts/
│   ├── lib/                Shared library (env, dispatch_inflight, hook_io, logging, recovery, validation …)
│   ├── track_state/        State machine CLI package (cli + cmd_* modules + *_profiles.py registries)
│   └── *.py                Hook scripts (session start/end, subagent-start, dispatch-dedupe, tripwire …)
├── skills/                 19 slash-command skills (implement, new-track, reconcile, re-spec, parallel, wiki …)
├── templates/
│   ├── workflow/             The two registries: task-type / workflow-shape profiles (.json) + steps/ docfile library
│   ├── code-styleguides/     10 language style guides
│   ├── dev-commands/         8 language dev-command templates
│   └── testing/              Testing strategy template
└── tests/                  pytest suite (run from repo root: PYTHONPATH=. python3 -m pytest tests/)
```

### Subagents

<!-- conductor:begin:agents-table -->
| Agent | Model | Purpose |
|-------|-------|---------|
| `ac-tracer` | sonnet | The AC-evidence-trace tier of phase verification (read-only). Runs track-state spec-integrity, parses the result, and returns the per-AC grounding verdict. Fanned out in parallel with conductor:build-runner and conductor:test-runner before conductor:phase-checker (the synthesizer) consumes the fleet. |
| `apply-fixes` | sonnet | Bounded remediation patcher — applies ONE chunk of post-review findings (Critical/High severity, one file) committed-by-commit, runs the suite, returns a compact block. Dispatched ONLY by the post-loop-step spine (§7.0 step 4). NOT a plan task — no PHASE/TASK, no result.json, no state mutation. Replaces the prior open-ended free-form patch agent (the "unguarded chimney"). |
| `build-runner` | haiku | The L0 compile/build tier of phase verification (read-only). Resolves the project's build/compile command and runs it ONCE — no fix, no edit. Fanned out in parallel with conductor:ac-tracer and conductor:test-runner before conductor:phase-checker (the synthesizer) consumes the fleet. A compile failure here fails the checkpoint before the more expensive test tier is spent — the cheapest-first graduated gate. |
| `code-reviewer` | sonnet | Performs deep code analysis on a track's implementation. Dispatched by conductor:review to analyze diffs, verify plan compliance, check style, run tests, and produce structured findings. |
| `command-digester` | haiku | Read-only command digester — runs ONE bounded command class (test/coverage run OR git-history log verification), parses the result, returns a compact block. Dispatched ONLY nested (task-executor PURPOSE=red|coverage; doc-linter PURPOSE=log-verify) so verbose output stays out of the parent's context. |
| `corpus-writer` | sonnet | Phase 1 of the doc-sync pipeline. Analyzes the source (track spec + handoffs, or an ad-hoc source) against the project's documentation corpus, proposes targeted updates for each affected document, applies user-confirmed edits, and commits them. The interactive, divergence-curing half of doc sync; wiki-synthesizer runs Phase 2 after. |
| `doc-linter` | sonnet | Health-checks the Conductor documentation wiki for broken cross-references, stale claims, coverage gaps, and consistency issues. Read-only analysis agent. |
| `doc-probe` | haiku | Read-only corpus-doc digester — reads ONE scoped design doc against a task scope, returns a compact relevance + anchors digest. Dispatched ONLY by task-executor (nested, opt-in) for Layer 0(b) so N full docs never enter the parent's context; the parent assembles N digests instead. |
| `explorer` | sonnet | Read-only code exploration agent. Records findings to the task handoff (Exploration Notes) as the Layer-0 map for the downstream task-executor. Dispatched by conductor:implement for [Explore] tagged tasks. |
| `failure-analyst` | haiku | Diagnoses why a repeatedly failed track task (TASK mode) OR a failed phase checkpoint (PHASE mode) keeps failing and recommends the next action (retry differently / replan / decompose / escalate). TASK mode: dispatched before the final retry attempt and on skip-analyst retry_with_modification. PHASE mode: dispatched when a phase checkpoint FAILED on an auto-routing track, before halting. |
| `phase-checker` | sonnet | The synthesizer for the phase checkpoint. conductor:build-runner (L0 compile/build), conductor:test-runner (L1 verify-only), and conductor:ac-tracer (AC-evidence) are fanned out first; this agent consumes their verdicts (cheapest-first graduated gate — a build failure fails the checkpoint before the test tier is spent), owns the L1 fix-and-retry pass when tests fail, runs L2 browser-E2E (when a browser-automation MCP is available) and the L4 manual plan, then makes the checkpoint commit. |
| `project-analyzer` | sonnet | Analyzes a brownfield project to detect tech stack, architecture, and structure. Dispatched by conductor:setup during brownfield project discovery. |
| `refactorer` | sonnet | Bounded tactical-refactor patcher — ONE behavior-preserving refactor pass on a task's commit, runs the suite, returns a compact block. Dispatched ONLY by conductor:implement at the opt-in [Refactor] seam (§3.6c). NOT a plan task — no PHASE/TASK, no result.json, no state mutation. The tactical tier; task-executor's inline Step 5 is the mechanical tier. |
| `refuter` | sonnet | Adversarial read-only verifier — re-examines a single claim/verdict/finding-set against ground truth, defaulting to SUSTAINED when uncertain. Dispatched by skills to challenge consequential one-shot decisions (plan, skip, cross-member seam). |
| `skip-analyst` | haiku | Analyzes whether a repeatedly failed track task can be safely skipped. Dispatched by the conductor:implement orchestrator when retry count is exhausted. |
| `spec-planner` | sonnet | Generates spec.md and plan.md from user requirements and project context. Writes files directly, returns compact summary to minimize parent context pressure. Dispatched by conductor:setup and conductor:newTrack. |
| `spec-reviewer` | sonnet | Read-only auditor for spec.md and plan.md. Runs EARS-conformance + plan-tag audits, returns a compact verdict + findings list. Non-interactive — the orchestrator owns the human review loop. Keeps full file contents out of the orchestrator context. |
| `strategy-writer` | sonnet | Inspects a brownfield project's real test layout and writes a project-specific conductor/workflow/testing/strategy.md, asking the user questions interactively. Dispatched by conductor:setup when the user opts out of the default filtered template. |
| `task-executor` | sonnet | Executes a single track task via TDD workflow (Steps 3-8). Self-loads all context from files. Dispatched by conductor:implement. |
| `test-runner` | haiku | The L1 verify-only tier of phase verification (read-only). Resolves the project's test command and runs it ONCE — no fix, no edit. Fanned out in parallel with conductor:ac-tracer before conductor:phase-checker (the synthesizer) consumes the fleet. phase-checker owns the fix-and-retry pass if this agent reports failure. |
| `wiki-differ` | haiku | Compares the Conductor documentation wiki against the actual codebase to surface drift — stale references (files/modules/functions the wiki names that no longer exist), moved references (renamed or relocated), and coverage gaps (code areas with no wiki mention). Analysis subagent that extracts verifiable claims from wiki docs and checks each against the code via Glob/Grep (writes only its own diff report). |
| `wiki-researcher` | haiku | Searches the Conductor documentation wiki for a topic and synthesizes a cited answer. Read-only retrieval subagent — orients via overview/index, routes to scoped docs, greps + graph-expands [[wikilinks]], ranks by signal density, returns a synthesized answer with [[wikilink]] citations and a source list. |
| `wiki-synthesizer` | sonnet | Phase 2 of the doc-sync pipeline. Regenerates conductor/overview.md from the loaded corpus, co-edits conductor/purpose.md (LLM-maintained sections only), appends the change log, runs the inline drift gate with auto-repair of auto-owned files, and commits. Runs after corpus-writer (Phase 1). Automatic — no user confirmation. |
<!-- conductor:end:agents-table -->

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

The `scripts/` tree also hosts a family of **drift gates** (`lint-*.py` / `check-*.py`) — each is wired through a `tests/test_<gate>.py` wrapper that runs it over the live tree, so `pytest tests/` sweeps them too. Three examples: `lint-prose-impl-leak.py` flags rotting `file.ext:NN` line-number citations in markdown prose (a `:NN` goes stale the moment a line is inserted above it); `check-contract-registry-sync.py` forbids a second hand-maintained vocabulary home in the contract; and `lint-grill-contract-drift.py` flags prompt prose that restates the grill discipline (the `four-quadrant` stance / `one question at a time` loop) without citing its single home (`runtime/contracts/grill-discipline.md`) — a restated discipline drifts the moment a second surface adopts it. The full prompt-prose keep/cut rules live in [`runtime/contracts/prose-style.md`](runtime/contracts/prose-style.md).

### Verification ladder

Conductor verifies work across a ladder — **L0** static, **L1** unit/integration, **L2** browser-E2E (opportunistic, when a browser-automation MCP is connected), **L4** human manual plan. It does **not** cover **L3** (production logs/metrics/traces): that rung is project-specific infrastructure a generic plugin cannot provision and is left to the host project.

## Project conventions

**Scripts and dev commands belong in the target project, not under `conductor/`.** Put run/build helpers (`start.sh`, `run.sh`) at the project root in `./scripts/` or a `Makefile`/`package.json` script; tests go in the test root `setup` resolves (detected from `conductor/.conductor/analysis.json`, else `tests/`); one-off CLIs go in `./bin/`. The `conductor/` tree is the spec/wiki/planning map that Conductor agents route on — it is not a home for executable code, so it intentionally has no `scripts/` slot and no row in `conductor/index.md`. (The `conductor/workflow/` overlay JSON is the one exception — it is *declarative data* the registries read, not executable code.) See `templates/testing/strategy.md` → *Scripts & Dev Commands* for the full placement table.

## License

[MIT](LICENSE)
