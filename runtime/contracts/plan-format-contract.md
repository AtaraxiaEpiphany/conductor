---
type: concept
sources:
  - agents/spec-planner
last_verified: 2026-07-01
---

# Plan Format Contract

The mandatory structure, status-marker, dispatch-tag, and subtask rules for `plan.md`. `spec-planner` (§4.2) reads this when generating `plan.md` and must follow it exactly. The orchestrator's plan parser and dispatch router depend on this structure: a task line without a `[ ]` checkbox is **silently dropped** by the parser, and dispatch tags (`[Explore]`, `[Docs]`, `[Config]`, `[Chore]`, `[Manual]`) drive task routing and TDD gating. Violating any rule below breaks the orchestrator.

## Status-Marker & Structure Rules

These rules are **non-negotiable**. Violating any rule will break the orchestrator.

1. **Status Markers**: Every task and subtask gets a `[ ]` status marker. Indented subtasks use two-space indentation under their parent. A line without `[ ]` is silently dropped by the parser — the task/subtask would vanish. The `Task:`/`Subtask:` prefix is **optional convention only**: the parser keys on the `[ ]` checkbox + indent, not the keyword, so it may be omitted (`- [ ] build API`); if kept it must *follow* the checkbox, never replace it. ❌ `- build API` (no checkbox) → ✅ `- [ ] build API`. A `[Tag]` is **not** a substitute for the checkbox: ❌ `- [Explore] build API` → ✅ `- [ ] [Explore] build API`.
2. **Manual Verification**: Append a manual verification task at the end of each phase. Tag it with `[Manual]` so the orchestrator can auto-defer it in continuous mode.
3. **Phase Order**: Phases should follow logical dependency order.
4. **Atomic Tasks**: Tasks should be atomic and independently testable.
5. **Workflow Conventions**: Read the workflow file to respect any task-level conventions.
6. **AC Traceability**: Each implementation task MUST have an HTML comment `<!-- AC-n, TC-n.n, ... -->` linking to the acceptance criteria and test scenarios it covers. This enables the orchestrator to pass precise AC context to conductor:task-executor subagents. **Only the parent task carries the AC annotation** — subtasks inherit AC context from their parent. This rule is **enforced by the `check-plan-annotations` PreToolUse hook** (sibling to `check-plan-checkboxes`): a missing or malformed annotation on an untagged implementation task line hard-denies the Write/Edit before it lands — otherwise the parser silently records empty refs and the task loses all traceability. Dispatch-tagged tasks (`[Explore]`/`[Docs]`/`[Config]`/`[Chore]`/`[Manual]`) and indented subtasks are exempt.
7. **Test ↔ TC Naming Link**: Each `TC-{n}.{m}` row is verified by a test function named `test_TC_{n}_{m}_*(…)` — one function per TC row (`async def` is fine; the `{n}.{m}` become underscores). This **closes the traceability loop**: the orchestrator's `tc_consistency_gate` resolves the TCs a task *claims* (`tc_coverage`) to these real functions (the third link of the self-extraction chain: declared → claimed → grounded), and `ac_verification_measured_rate` measures how many ACs are backed by such tests. Without the link the gate can only trust self-report. The check measures *naming* coverage, not test isolation — a kitchen-sink `test_TC_2_1_and_2_2` grounds both TC-2.1 and TC-2.2, which is accepted.

## Editing plan.md mid-track: use `reconcile-plan`, not `sync-plan` / `init-from-plan`

After a track is running you will sometimes hand-edit `plan.md` — typically after a `git reset` that undid a divergent task-executor run (large refactor / tech-stack-upgrade that violated a constraint): you change a task's tag, split a task, or reorder/reconstruct the remaining tasks. **`track-state.json` must then be brought back in sync without losing the `commit_sha` records on tasks whose work survived.**

The three plan→state paths are NOT interchangeable:

| Path | Matching | SHA handling | Use when |
|---|---|---|---|
| `sync-plan` | **positional** (phase/task index) | re-renders state→plan; rebinding on reorder is silent | only a plain marker re-render, no structural edit |
| `init-from-plan --force` | full rebuild | **wipes every SHA** to `pending` (V7 invariant) | a fresh start; never mid-track |
| **`reconcile-plan`** | **by phase number + task name** (tag-insensitive) | **preserves `commit_sha`** on tasks whose work survives; refuses unmatched nodes until resolved | any hand-edit after a reset |

**`sync-plan`'s positional matching is the trap:** it indexes `state["phases"][n]["tasks"][m]` by the task's *position* in the plan (`sync.py:42-67`). Reorder or insert a task above a completed one and the completed task's SHA silently rebinds to whichever task now occupies that slot — the history is lost with no error. `reconcile-plan` keys by name instead, so a SHA stays on its named task across reorders (the headline safety test: `test_reorder_does_not_rebind_shas`).

`reconcile-plan` is **dry-run by default**: it prints a bucketed diff (`unchanged` / `tag_or_status` / `split` / `unmatched` / `dangling_sha`) and writes nothing until `--apply`. `unmatched` nodes (a rename-vs-delete ambiguity) are **refused** until resolved by an explicit `--rename` / `--drop` flag — the command never guesses. `dangling_sha` flags a terminal node whose `commit_sha` is unreachable in git (you reset past its Complete commit); the terminal marker is respected and the SHA reported as a warning, with `--clear-dangling` to requeue if the work should be redone. See `/conductor:reconcile`.

**Name-keying invariant:** identity is `(phase number, task name)` with dispatch tags (the §Task Type Tags vocabulary) and trailing `[sha]` markers stripped, case-folded. A pure tag-prefix edit therefore matches the same node and is bucketed as `tag_or_status` (the new name, tag and all, is persisted) rather than as an unmatched new task. Exact match only — **no fuzzy matching**, so a genuine rename must be supplied via `--rename "<phase>:<old>=<new>"`.

## Editing spec.md mid-track: use `/conductor:re-spec` (the spec half), not a hand-edit + silence

`reconcile-plan` covers the **plan.md** side of a post-`git reset` recovery. The **spec.md** side — rewording an Acceptance Criterion, adding a constraint, injecting workflow guidance for the remaining tasks — has a distinct hazard that `reconcile-plan` does **not** cover: **a changed AC can silently invalidate a completed task's SHA.** A task whose `<!-- AC-3 -->` annotation claimed the old AC text carries a `commit_sha` whose work may no longer satisfy the new, stricter AC. `reconcile-plan` reconciles *structure* (names/markers/tags); it cannot reason about *meaning*. `track-state spec-delta` is the engine that closes that gap, and `/conductor:re-spec` is the teleoperator that drives it.

**Two invariants the re-spec path enforces:**

1. **A spec edit gets its own scoped `docs(spec):` commit — never conductor staging.** Conductor's bookkeeping commit stages only `track-state.json`/`plan.md`/`.conductor/` (`git_ops.py:183`); `spec.md` is deliberately not in that set. So `/conductor:re-spec` runs `git add spec.md && git commit -m "docs(spec): …"` scoped to `spec.md` only. Extending the global staging set to sweep `spec.md` would drag every track's spec into every bookkeeping commit — a blast-radius trap. **Do not** add `spec.md` to `_git_commit`'s staging paths to "help."
2. **A changed AC invalidates completed SHAs — surface, never auto-reset.** `spec-delta` joins changed ACs → plan tasks claiming them (via `<!-- AC-n -->`) → terminal tasks with a `commit_sha`, and reports that set as `at_risk_tasks`. `/conductor:re-spec` prints it and **halts for the user's keep-vs-reset decision**; it never runs `track-state reset` or `reconcile-plan --clear-dangling` itself. The SHA is the user's to keep or destroy. This mirrors reconcile's refuse-on-ambiguity philosophy.

**Constraints ride a separate channel, not the parser.** `spec_parse`'s `_SECTION_HEADINGS` is a closed four-key set (FR/NFR/AC/TC); the `## Constraints` section is **not** parsed (`spec_parse.py:16-19`). So a new `## Constraints` bullet is dead text to every machine check. `/conductor:re-spec --add-constraint` therefore mirrors the constraint into `.conductor/track-directives.md` (under `.conductor/`, so conductor's existing staging sweeps it — no staging-set change). The parser is **not** extended to capture Constraints (that would be cross-consumer blast radius); the directive channel carries them instead.

**The recovery pipeline:** `git reset --hard` (you) → `/conductor:re-spec` (edit spec, commit, surface at-risk SHAs, re-validate) → `/conductor:reconcile` (if plan.md also needs structural edits). `re-spec` never touches `plan.md` or `track-state.json`; `reconcile` never touches `spec.md`. The two compose, they don't overlap.

## Task Type Tags

Prepend the tag BEFORE the task description. Tag determines whether TDD is required.

**Source of truth:** the tag vocabulary and its semantics (routing + TDD/coverage exemption) are data-driven from a registry that resolves as **plugin baseline ⊕ project overlay**. The baseline ships at `templates/workflow/task-type-profiles.json` (always present). A project may drop `conductor/workflow/task-type-profiles.json` (alongside the other workflow files setup scaffolds there) to add project-specific tags or override a built-in tag's semantics — **opt-in by file presence** (absent = plugin defaults). The overlay merges over the baseline: project tags are added, the project wins a conflicting tag, and a project `default` profile wins per-key. The table below is the human-readable view of the *baseline* registry. **Adding a task type is one row in the registry** — `extract_tags`, `_classify_task`, and the F2/F3 exemption sets all derive from it automatically (a project adds its row to the *project* overlay; the plugin ships its row in the *baseline*).

**Unknown tags are a hard error.** A task name carrying a tag-shaped bracket that isn't in the *resolved* registry (baseline ⊕ project overlay) — a typo like `[Migration]`, or a new tag nobody registered like `[Springboot3]` — **blocks `init-from-plan`** (it is reported in `errors`, not `warnings`). There is no safe default: a wrong tag means wrong executor behavior (the silent-drift defect this prevents). Fix the typo, add a row to the plugin baseline, or — for a project-specific tag — register it in the project's `conductor/workflow/task-type-profiles.json` overlay. (This is deliberately stricter than `verify:` mode directives, which *warn* on an unknown mode because "no directive" is a valid intended state — a wrong task tag never is.) This is why the error message names the registry path: the validator checks the resolved vocab, so a tag the project registered is recognized.

| Tag         | Meaning                                                       | TDD Required | When to Use                                                                                                                                                                                                     |
| ----------- | ------------------------------------------------------------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `[Explore]` | Code investigation, architecture analysis, dependency mapping | **NO**       | Phase 1 exploration — understanding codebase before implementation. Examples: "Explore the authentication module architecture", "Map API endpoints and their handlers"                                          |
| `[Docs]`    | Documentation-only changes                                    | **NO**       | Writing or updating docs. No code changes.                                                                                                                                                                      |
| `[Config]`  | Configuration file changes                                    | **NO**       | .env, .yaml, .json config files. No business logic.                                                                                                                                                             |
| `[Chore]`   | Maintenance tasks                                             | **NO**       | Dependencies, tooling, CI/CD. No feature code.                                                                                                                                                                  |
| `[Manual]`  | Requires human verification, cannot be automated              | **NO**       | Tasks that need human eyes/hands: manual UI testing, cross-browser checks, staging deployment verification, email delivery confirmation, accessibility audit. These tasks are auto-deferred in continuous mode. |
| `[Migrate]` | Framework/version migration, large mechanical refactor       | **NO**       | Code-changing work where an existing test suite is the safety net and TDD red-green is the wrong model — e.g. a springboot2→3 upgrade, the `javax→jakarta` package rename, bumping a major dependency. **The suite starts red (the bump broke it); success is making it green again, not writing a new failing test first.** task-executor still runs (routes to `executor`, wave-eligible when flat + deps-declared), loads ACs normally, and commits each fix as `fix(migrate): …`/`refactor(migrate): …`. Coverage gate is suspended (behavior is preserved, not added). At a phase checkpoint, an all-migration phase that ends red is reported FAILED with the failing-test list rather than auto-writing tests. |
| *(no tag)*  | Standard implementation task                                  | **YES**      | Default. Full TDD workflow: Red → Green → Refactor.                                                                                                                                                             |

**Important**: Subtasks inherit the parent's task type tag. Do NOT tag subtasks individually.

### `[Migrate]` workflow (canonical)

`[Migrate]` is the one tag whose executor behavior diverges enough to warrant a named workflow. **The canonical `workflow` prose lives in the registry** — `templates/workflow/task-type-profiles.json` under the `Migrate.workflow` field — and is **surfaced to `task-executor` at dispatch** via the SubagentStart hook's `[Conductor Registry]` injection (it follows that prose instead of default TDD). `task-executor` §4.M and the `phase-checker` migration-phase branch point here and carry only their agent-specific bindings. This subsection is the human-readable view of the same prose.

A `[Migrate]` task is code-changing work where an **existing** test suite is the safety net and the TDD red-green model is inverted: **Red is the starting state** (the version bump / package rename / API removal already broke the suite), **Green is the goal.** You are not writing a new failing test first; you are making the existing suite pass again. Routes to `executor` (wave-eligible when flat + deps-declared); loads ACs normally.

The executor follows the injected `Migrate.workflow` verbatim — no Step 3 (Red), Step 4 (Green) is the whole task (`fix(migrate): …` / `refactor(migrate): …`, both F2-exempt), Step 5 (Refactor) stays, no Step 6 coverage gate, Steps 7/8 apply normally. **Stop conditions:** suite green → success (the existing suite is the evidence); suite still red after exhausting the failure list → failure with the remaining failing tests (the phase-checker treats an all-migration phase's red exit as a FAILED report, not a fix-and-retry trigger — see the migration-phase branch). To change this behavior, edit the registry row, not the agents.

## Phase Verify Directives

A `## Phase N:` heading MAY carry a `<!-- verify: <modes> -->` HTML comment declaring what **"done" looks like for the phase** — the gate `phase-checker` runs at the checkpoint. This is a **phase-level** concern, distinct from the task-level tags above (a tag gates *per-task TDD behavior*; the directive gates *per-phase verification*). It exists so a phase whose goal is "compiles" is not forced through the full test-suite gate.

**Source of truth:** the mode vocabulary, its semantics (which gate steps a mode runs, its fix policy), AND the per-mode `protocol` prose `phase-checker` emits are data-driven from a registry that resolves as **plugin baseline ⊕ project overlay** — the mirror of the task-type registry above. The baseline ships at `templates/workflow/verify-mode-profiles.json` (always present). A project may drop `conductor/workflow/verify-mode-profiles.json` (alongside the other workflow files setup scaffolds there) to add project-specific modes or override a built-in mode's semantics — **opt-in by file presence** (absent = plugin defaults). The overlay merges over the baseline: project modes are added, the project wins a conflicting mode, and a project `default` profile wins per-key. `phase-checker`'s Step-3 directive is a **mode-agnostic loop**: for each declared mode it reads that mode's `runs`/`fix_policy`/`protocol` from the registry, rather than carrying a per-mode `if/elif` branch. The table below is the human-readable view of the *baseline* registry. **Adding a verify-mode is one row in the registry** — `MODE_VOCAB`/`plan_parse`, and the phase-checker loop all derive from it automatically (a project adds its row to the *project* overlay; the plugin ships its row in the *baseline*).

```markdown
## Phase 1: Migrate dependencies <!-- verify: compile -->
## Phase 2: Migrate source        <!-- verify: compile -->
## Phase N: Wire up and boot      <!-- verify: test,start -->   ← final integration phase
## Phase R: Refactor internals    <!-- verify: anchor -->       ← frozen subset must hold
## Phase X: Standard feature                                    ← no directive = full gate (default)
```

**Closed mode vocabulary** (comma-separated, order-independent):

| Mode | Gate | When to use |
|---|---|---|
| `compile` | run the **build** command from `dev-commands/<lang>.md` (NOT the test suite); skip fix-and-retry; checkpoint on a green build | A mid-migration phase whose goal is "it compiles" — the test suite is expected red, the build is the gate. Canonical use: the intermediate phases of a staged framework/version migration. |
| `test` | run the test suite (the default Step-3 gate) | Explicitly opt back into the suite gate, e.g. as part of `test,start`. Alone, equivalent to omitting the directive. |
| `start` | additionally run the app's run command **once** as a boot smoke check (start, confirm "ready"/no startup stack trace, stop) | A phase whose deliverable includes "the app boots." Typically combined with `test` on the final integration phase, or with `compile` on the phase that first achieves a bootable build. Advisory pre-flight when the phase already has a trailing `[Manual]` boot task. |
| `anchor` | run the **frozen test subset** (`track-state anchor-status <track> --verify`) and checkpoint on `frozen_anchor_pass_rate == 100%` AND `drift_rate == 0%` — the Goodhart counter-anchor to `coverage_pct` | A phase whose safety net is the frozen subset, not the full suite. `anchor` alone: a refactoring phase where the broader suite is in flux but the pinned anchor must hold (the mirror image of `compile`). `test,anchor`: both the suite AND the frozen subset must pass. No-op on an unfrozen track (degrades gracefully until `track-state freeze` is run). See the frozen-anchor commands (`track-state freeze`/`thaw`/`anchor-status`) and `scripts/track_state/anchor.py`. |
| *(no directive)* | full gate: suite (L1) → L2 → L4 manual | The default. Backward-compatible — every existing phase behaves exactly as before. |

**Resolution:** `phase-checker` reads the directive directly from the phase heading in `plan.md` (it does not flow through `track-state.json` or the dispatch marker). On `compile`, it **ignores** the `test-runner` verdict — the suite still runs (the fan-out is hardcoded) but its red result does not gate the checkpoint; the build does. On `anchor`, it runs the frozen subset via `anchor-status --verify` and gates on its measured pass/drift rate (no-op if the track has no frozen anchor yet). The directive takes precedence over the all-`[Migrate]` migration-phase branch: a phase that declares `verify: compile` or `verify: anchor` is gated on that signal regardless of its task tags, and the migration branch remains the safety net for an all-migration phase that forgot its directive — and, when it fires on a directive-less all-`[Migrate]` phase whose suite is red, it **prescribes the directive in its `FAILURE_REASON`** (add `verify: compile` if the phase's goal is "it compiles," or `verify: test` if its goal is "tests pass," then re-run), so the author learns the escape hatch from the failure itself rather than having to discover the directive independently.

**Validation:** `init-from-plan --check` parses the directive and **warns** (does not block) on an unknown mode or an empty `<!-- verify: -->` comment — the same advisory posture as `<!-- deps: -->` typos. (This is deliberately looser than task-type tags, which *error* on an unknown tag because a wrong tag never has a valid intended state — a missing directive does: the default full gate.) The validator checks the *resolved* vocab (baseline ⊕ project overlay), so a mode the project registered in its overlay is recognized. The directive is advisory metadata parsed for validation surface; it is **not persisted** into `track-state.json` (the parser drops it, like `ac_refs`/`deps_refs`).

**Authoring guidance (spec-planner):** for a staged migration, emit `verify: compile` on every intermediate phase whose goal is "compiles," and `verify: test,start` on the final phase whose goal is "starts + tests green." For non-migration tracks, emit nothing — the default full gate is correct for feature work.

## Subtask Rules

Not every task needs subtasks. Follow these guidelines:

**When to use subtasks:**
- The task involves **3+ distinct logical steps** that each need independent verification.
- The task spans **multiple files or modules** with clear boundaries.
- The task has **complex acceptance criteria** that map to distinct deliverables.

**When NOT to use subtasks (keep flat):**
- The task is a single, focused change (e.g., "Add validation to form field").
- The task touches one file or one module.
- The task has simple, single-aspect acceptance criteria.

**Subtask format rules:**
- Indent subtasks with 2 spaces under the parent task.
- Subtask descriptions should be specific and actionable.
- A parent with subtasks does NOT carry its own implementation — the subtasks ARE the implementation.
- A parent without subtasks IS the implementation task.
- Subtask count: minimum 2, recommended maximum 5. If more than 5, split into separate parent tasks.

## Inter-Task Dependencies (optional, advisory)

A top-level task MAY declare which earlier tasks it depends on with a second HTML comment, separate from the AC/TC comment:

```markdown
- [ ] build user API <!-- AC-3, TC-3.1 --> <!-- deps: P1.T1 -->
```

`P{n}.T{n}` is the runtime's own positional coordinate — Phase `n`, Task `n` (1-based, top-level only), the same `P{pi}.T{ti}` notation `lint-track-state` and the handoff use. Multiple deps are comma-separated: `<!-- deps: P1.T1, P1.T3 -->`.

**Rules:**
1. **Optional.** Deps are not required. A task with no `<!-- deps: -->` comment simply has no declared predecessor — the conductor's default serial order still applies.
2. **The AC/TC comment (§6) is still mandatory and separate.** Deps is an *additional* comment, never a replacement. A line with only `<!-- deps: -->` and no AC/TC still fails the `check-plan-annotations` hook.
3. **Top-level tasks only.** Subtasks inherit context and are sequentially decomposed (they ARE one deliverable), so they are never parallel candidates and their `deps` are ignored by the parser.
4. **Positional refs shift on reorder.** `P1.T2` means "the second top-level task in phase 1." If you insert a task above it, the coordinate moves — a known v1 tradeoff. (A future revision may add stable `<!-- id: name -->` anchors if reordering becomes common.)
5. **Opt-in to within-track parallelism — validated AND consumed.** The parser (`plan_parse.validate_deps`) checks every `deps` annotation for dangling refs, self-deps, and cycles and surfaces them as **warnings** at `track-state init-from-plan --check`; they do not block init. A `<!-- deps: -->` comment is now the **opt-in gate** for the wave scheduler (`track_state.wave._ready_set`): a flat, executor-routed, pending task WITH a deps comment whose every declared target is satisfied (completed/skipped/deferred) is eligible to run in a worktree-isolated wave under `conductor:parallel`. A task with **no** deps comment is assumed serial-order-dependent and stays on the serial spine — so the presence of the comment (not its content) is the opt-in: it signals "the author has reasoned about this task's file-surface." See [[conductor/design/decision-serial-execution]] (wave escape hatch).
6. **Waves are flat-only (v1) — the practical seam.** Rule 3 says subtasks are never parallel candidates; this restates the consequence for authors who want parallelism. A **subtasked task can be a dep *target*** (it reaches `completed` once its subtasks finish, releasing flat dependents), but it can **never be a wave *member*** — `wave._eligible_members` rejects any task with subtasks before the deps check runs. So the deps opt-in only ever fires for **flat** tasks. Because the planner's default is to decompose non-trivial work into subtasks (§subtask rules: min 2), the common plan shape is wave-ineligible *by construction*. **To parallelize a unit of work, author it flat** (no subtasks — inline the steps as the task body) **and add `<!-- deps: -->`**. A `no_ready_tasks` envelope from `dispatch-wave` carries an `ineligible` list naming the gate that rejected each candidate (`subtasked` | `non_executor` | `no_deps_comment` | `deps_unsatisfied`) so this conflict is surfaced, not silent. Lifting the flat-only gate so a subtasked task runs its subtasks serially-internally but concurrently with sibling members is tracked as v2.

**When to declare deps:** always emit a `<!-- deps: ... -->` comment on any top-level task you want the wave scheduler to consider.
- **Coupled task** (builds on an artifact a sibling produced — a model, a utility, a config key): `<!-- deps: P1.T1 -->`. The task stays serial until `P1.T1` lands, then becomes wave-eligible.
- **Independent task** (touches genuinely disjoint files/modules): `<!-- deps: -->` — an *empty* deps comment is the explicit "I have no dependencies" declaration. This is what makes a task a wave candidate; the scheduler treats it as deps-satisfied immediately.
- **No comment at all**: the task is assumed serial-order-dependent and never wave-parallelized. It still runs normally on the serial spine; it just forgoes the parallel speedup. Declare deps (even empty) on independent tasks to opt them in.

