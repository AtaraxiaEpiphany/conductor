---
type: concept
sources:
  - agents/spec-planner
last_verified: 2026-07-01
---

# Plan Format Contract

The mandatory structure, status-marker, dispatch-tag, and subtask rules for `plan.md`. `spec-planner` (§4.2) reads this when generating `plan.md` and must follow it exactly. The orchestrator's plan parser and dispatch router depend on this structure: a task line without a `[ ]` checkbox is **silently dropped** by the parser, and dispatch tags (the closed vocabulary from the resolved task-type registry — `track-state registry-doc`) drive task routing and TDD gating. Violating any rule below breaks the orchestrator.

> **Meta-rule:** the tag/shape **vocabulary + semantics** live in the resolved registries (`task-type-profiles.json`, `workflow-shapes.json` — plugin baseline ⊕ project overlay), rendered by `track-state registry-doc`. This file holds only the **grammar and invariants** — the structural rules that are tag/shape-agnostic. It deliberately carries no hand-maintained tag or shape enumeration table (that would be a third home for the vocab and the first to drift); the drift-killer lint `scripts/check-contract-registry-sync.py` enforces this.

## Status-Marker & Structure Rules

These rules are **non-negotiable**. Violating any rule will break the orchestrator.

1. **Status Markers**: Every task and subtask gets a `[ ]` status marker. Indented subtasks use two-space indentation under their parent. A line without `[ ]` is silently dropped by the parser — the task/subtask would vanish. The `Task:`/`Subtask:` prefix is **optional convention only**: the parser keys on the `[ ]` checkbox + indent, not the keyword, so it may be omitted (`- [ ] build API`); if kept it must *follow* the checkbox, never replace it. ❌ `- build API` (no checkbox) → ✅ `- [ ] build API`. A `[Tag]` is **not** a substitute for the checkbox: ❌ `- [Explore] build API` → ✅ `- [ ] [Explore] build API`.
2. **Manual Verification**: Append a manual verification task at the end of each phase. Tag it with a **manual-route** tag (the shipped registry's `[Manual]` — `track-state registry-doc`) so the orchestrator can auto-defer it in continuous mode; the parser validates the route (`route_for == manual`), not the tag name.
3. **Phase Order**: Phases should follow logical dependency order.
4. **Atomic Tasks**: Tasks should be atomic and independently testable.
5. **Workflow Conventions**: Read the workflow file to respect any task-level conventions.
6. **AC Traceability**: Each implementation task MUST have an HTML comment `<!-- AC-n, TC-n.n, ... -->` linking to the acceptance criteria and test scenarios it covers. This enables the orchestrator to pass precise AC context to conductor:task-executor subagents. **Only the parent task carries the AC annotation** — subtasks inherit AC context from their parent. This rule is **enforced by the `check-plan-annotations` PreToolUse hook** (sibling to `check-plan-checkboxes`): a missing or malformed annotation on an untagged implementation task line hard-denies the Write/Edit before it lands — otherwise the parser silently records empty refs and the task loses all traceability. Dispatch-tagged tasks (any tag in the resolved registry — `track-state registry-doc`) and indented subtasks are exempt.
7. **Test ↔ TC Naming Link**: Each `TC-{n}.{m}` row is verified by a test function named `test_TC_{n}_{m}_*(…)` — one function per TC row (`async def` is fine; the `{n}.{m}` become underscores). This **closes the traceability loop**: the orchestrator's `tc_consistency_gate` resolves the TCs a task *claims* (`tc_coverage`) to these real functions (the third link of the self-extraction chain: declared → claimed → grounded), and `ac_verification_measured_rate` measures how many ACs are backed by such tests. Without the link the gate can only trust self-report. The check measures *naming* coverage, not test isolation — a kitchen-sink `test_TC_2_1_and_2_2` grounds both TC-2.1 and TC-2.2, which is accepted.

## Editing plan.md mid-track: use `reconcile-plan`, not `sync-plan` / `init-from-plan`

After a track is running you will sometimes hand-edit `plan.md` — typically after a `git reset` that undid a divergent task-executor run (large refactor / tech-stack-upgrade that violated a constraint): you change a task's tag, split a task, or reorder/reconstruct the remaining tasks. **`track-state.json` must then be brought back in sync without losing the `commit_sha` records on tasks whose work survived.**

The three plan→state paths are NOT interchangeable:

| Path | Matching | SHA handling | Use when |
|---|---|---|---|
| `sync-plan` | **positional** (phase/task index) | re-renders state→plan; rebinding on reorder is silent | only a plain marker re-render, no structural edit |
| `init-from-plan --force` | full rebuild | **wipes every SHA** to `pending` (V7 invariant) | a fresh start; never mid-track |
| **`reconcile-plan`** | **by phase number + task name** (tag-insensitive) | **preserves `commit_sha`** on tasks whose work survives; refuses unmatched nodes until resolved | any hand-edit after a reset |

**`sync-plan`'s positional matching is the trap:** it indexes `state["phases"][n]["tasks"][m]` by the task's *position* in the plan (`sync.py` — `_do_sync_plan`). Reorder or insert a task above a completed one and the completed task's SHA silently rebinds to whichever task now occupies that slot — the history is lost with no error. `reconcile-plan` keys by name instead, so a SHA stays on its named task across reorders (the headline safety test: `test_reorder_does_not_rebind_shas`).

`reconcile-plan` is **dry-run by default**: it prints a bucketed diff (`unchanged` / `tag_or_status` / `split` / `unmatched` / `dangling_sha`) and writes nothing until `--apply`. `unmatched` nodes (a rename-vs-delete ambiguity) are **refused** until resolved by an explicit `--rename` / `--drop` flag — the command never guesses. `dangling_sha` flags a terminal node whose `commit_sha` is unreachable in git (you reset past its Complete commit); the terminal marker is respected and the SHA reported as a warning, with `--clear-dangling` to requeue if the work should be redone. See `/conductor:reconcile`.

**Name-keying invariant:** identity is `(phase number, task name)` with dispatch tags (the §Task Type Tags vocabulary) and trailing `[sha]` markers stripped, case-folded. A pure tag-prefix edit therefore matches the same node and is bucketed as `tag_or_status` (the new name, tag and all, is persisted) rather than as an unmatched new task. Exact match only — **no fuzzy matching**, so a genuine rename must be supplied via `--rename "<phase>:<old>=<new>"`.

## Editing spec.md mid-track: use `/conductor:re-spec` (the spec half), not a hand-edit + silence

`reconcile-plan` covers the **plan.md** side of a post-`git reset` recovery. The **spec.md** side — rewording an Acceptance Criterion, adding a constraint, injecting workflow guidance for the remaining tasks — has a distinct hazard that `reconcile-plan` does **not** cover: **a changed AC can silently invalidate a completed task's SHA.** A task whose `<!-- AC-3 -->` annotation claimed the old AC text carries a `commit_sha` whose work may no longer satisfy the new, stricter AC. `reconcile-plan` reconciles *structure* (names/markers/tags); it cannot reason about *meaning*. `track-state spec-delta` is the engine that closes that gap, and `/conductor:re-spec` is the teleoperator that drives it.

**Two invariants the re-spec path enforces:**

1. **A spec edit gets its own scoped `docs(spec):` commit — never conductor staging.** Conductor's bookkeeping commit stages only `track-state.json`/`plan.md`/`.conductor/` (`git_ops.py` — `_git_commit`'s staging set); `spec.md` is deliberately not in that set. So `/conductor:re-spec` runs `git add spec.md && git commit -m "docs(spec): …"` scoped to `spec.md` only. Extending the global staging set to sweep `spec.md` would drag every track's spec into every bookkeeping commit — a blast-radius trap. **Do not** add `spec.md` to `_git_commit`'s staging paths to "help."
2. **A changed AC invalidates completed SHAs — surface, never auto-reset.** `spec-delta` joins changed ACs → plan tasks claiming them (via `<!-- AC-n -->`) → terminal tasks with a `commit_sha`, and reports that set as `at_risk_tasks`. `/conductor:re-spec` prints it and **halts for the user's keep-vs-reset decision**; it never runs `track-state reset` or `reconcile-plan --clear-dangling` itself. The SHA is the user's to keep or destroy. This mirrors reconcile's refuse-on-ambiguity philosophy.

**Constraints ride a separate channel, not the parser.** `spec_parse`'s `_SECTION_HEADINGS` is a closed four-key set (FR/NFR/AC/TC); the `## Constraints` section is **not** parsed (`spec_parse.py` — `_SECTION_HEADINGS`). So a new `## Constraints` bullet is dead text to every machine check. `/conductor:re-spec --add-constraint` therefore mirrors the constraint into `.conductor/track-directives.md` (under `.conductor/`, so conductor's existing staging sweeps it — no staging-set change). The parser is **not** extended to capture Constraints (that would be cross-consumer blast radius); the directive channel carries them instead.

**The recovery pipeline:** `git reset --hard` (you) → `/conductor:re-spec` (edit spec, commit, surface at-risk SHAs, re-validate) → `/conductor:reconcile` (if plan.md also needs structural edits). `re-spec` never touches `plan.md` or `track-state.json`; `reconcile` never touches `spec.md`. The two compose, they don't overlap.

## Task Type Tags

Prepend the tag BEFORE the task description. Tag determines whether TDD is required.

**Vocabulary + semantics live in the resolved registry, not in this file.** The tag vocabulary and what each tag MEANS (routing + TDD/coverage exemption + when-to-use hint + optional executor `workflow`) are data-driven from a registry that resolves as **plugin baseline ⊕ project overlay**. Render the resolved set on demand — humans and tooling ask `track-state registry-doc` (full tables) or `track-state registry-doc --tag <Name>` (one tag's row plus its `workflow` prose verbatim, the payload `task-executor` fetches). Do **not** hand-maintain a tag enumeration table here: it would be a third home for the vocabulary (alongside the registry and the `[Conductor Registry]` block injected into agents) and the first place to drift. The baseline ships at `templates/workflow/task-type-profiles.json`; a project may drop `conductor/workflow/task-type-profiles.json` (alongside the other workflow files setup scaffolds there) to add project-specific tags or override a built-in tag's semantics — **opt-in by file presence** (absent = plugin defaults). The overlay merges over the baseline: project tags are added, the project wins a conflicting tag, and a project `default` profile wins per-key. **Adding a task type is one row in the registry** — `extract_tags`, `_classify_task`, the F2/F3 exemption sets, the SubagentStart injection, and the `registry-doc` render all derive from it automatically (a project adds its row to the *project* overlay; the plugin ships its row in the *baseline*).

**Unknown tags are a hard error.** A task name carrying a tag-shaped bracket that isn't in the *resolved* registry (baseline ⊕ project overlay) — a typo like `[Docss]`, or a new tag nobody registered like `[Springboot3]` — **blocks `init-from-plan`** (it is reported in `errors`, not `warnings`). There is no safe default: a wrong tag means wrong executor behavior (the silent-drift defect this prevents). Fix the typo, add a row to the plugin baseline, or — for a project-specific tag — register it in the project's `conductor/workflow/task-type-profiles.json` overlay. This is why the error message names the registry path: the validator checks the resolved vocab, so a tag the project registered is recognized.

**Grammar (tag-agnostic invariants):**
- **Prepend the tag BEFORE the task description**, inside its own brackets and before the `[ ]` checkbox: `- [ ] [Config] bump retry limit`. (A `[Tag]` is **not** a substitute for the `[ ]` checkbox — see Rule 1.)
- **Subtasks inherit the parent's tag.** Do NOT tag subtasks individually.
- **Tag determines whether TDD is required** — resolved from the registry profile at dispatch (the `[Conductor Registry]` block `task-executor` receives carries the resolved `tdd_exempt`/`coverage_exempt` for the locked task's leading tag). A tag whose profile carries a `workflow` (a project may register a bespoke-workflow tag) diverges from default TDD; the executor fetches that prose with `track-state registry-doc --tag <Tag>` and follows it verbatim. A tag whose profile carries `refactor: true` (today: `[Refactor]`) opts the task into the tactical refactorer at the §3.6c seam — the orchestrator dispatches `conductor:refactorer` once after the task succeeds, with no `[Refactor]` name marker or env required (those remain as per-task / per-session escape hatches for a task whose leading tag is something else). A project overlay may add more bespoke-workflow or refactor-opt-in tags the same way — **the registry, not this file, is canonical for tag-specific executor behavior.**

## Workflow Shapes

A track's `track-state.json` carries an optional `workflow_shape` field declaring **the node sequence the workflow intends to run** — the second axis. Task-type tags (above) say what each node *says*; the workflow shape says **which dispatch agents are intended to run, in what order, its verify policy, and its stop condition**. The conductor's fixed state-machine topology (planner → executor → checker) is *declared* here, not hardcoded — so a project ships a custom shape (e.g. a `research-first` shape that *declares* `explorer` should precede `spec-planner`) with zero plugin edits. The shape's `nodes` topology is advisory today — recording intent and surfacing drift (`shape_violation`) without reordering dispatch (both built-in shapes plan-first).

**Vocabulary + semantics live in the resolved registry, not in this file.** The shape vocabulary and what each shape MEANS (its `nodes`, `verify_policy`, `stop_condition`, optional `instruction`) are data-driven from a registry that resolves as **plugin baseline ⊕ project overlay** — the mirror of the task-type registry above. Render the resolved set on demand via `track-state registry-doc` (full tables) or `track-state registry-doc --shape <name>` (one shape's row plus its `instruction` prose verbatim). Do **not** hand-maintain a shape table here — same drift liability as tag tables. The baseline ships at `templates/workflow/workflow-shapes.json`; a project may drop `conductor/workflow/workflow-shapes.json` (alongside the other workflow files setup scaffolds there) to add project-specific shapes or override a built-in shape — **opt-in by file presence** (absent = plugin defaults). The overlay merges over the baseline: project shapes are added, the project wins a conflicting shape, and a project `default` profile wins per-key. **Adding a shape is one row in the registry** — `SHAPES_VOCAB`, the advisory `shape_violation` disclosure, and the `registry-doc` render all derive from it automatically.

**Grammar (shape-agnostic invariants):**
- **Form:** the `workflow_shape` field on `track-state.json` is a bare shape name (e.g. `default`, `research-first`). `init-from-plan` writes `default` (v1 always does; a future init may infer a shape from track-type).
- **Resolution:** `dispatch` reads the field via `workflow_shapes.resolve_shape`. An unknown or absent shape resolves to `default` (fail-open — a typo or a track predating the field runs the standard loop rather than blocking), surfacing a warning.
- **Load-bearing (advisory) — `nodes` (spine topology):** when a dispatch action's agent is outside the resolved shape's `nodes`, the spine surfaces a `shape_violation` disclosure (no-silent-caps) rather than silently dispatching off-topology. The dispatch still proceeds so a shape misconfiguration never deadlocks a track; the violation is visible for the operator to act on.

**The fixed spine stays action-driven.** The shape's `nodes` adds a *diagnostic*, not a rewrite: the planner→executor→checker sequence is implicit in the dispatch actions, and `nodes` only *discloses* (via `shape_violation`) when an action drifts off the declared topology — it does not refuse or reorder it. This is the OpenSpec pattern (the walker owns topology; the agents own intelligence) applied at the altitude the codebase is already built for — explicitly NOT a model-authored harness, which would break the deterministic resumability the `step`/`wave` spines bought. Making `nodes` genuinely drive ordering (rather than diagnose drift) is a future code change at the dispatch emit site.

## Registry Guardrails (for any dynamic content)

The two registries (task-type, workflow-shape) make the conductor *content*-data-driven. Two disciplines generalize to **any** registry row — including a project overlay's custom rows — so data-driven never becomes a foot-gun:

**1. Immutability of the "definition of done."** Any registry field that declares a pass bar — a shape's `stop_condition` — is **read-only to the executing agent**. The agent cannot rewrite its own pass bar or stop condition: those are read from the resolved registry at dispatch, not authored by the agent mid-flight. A project overlay that adds a `stop_condition` inherits the same protection: the field is a contract the agent obeys, never one it edits.

**2. No-silent-caps.** Every cap the machinery applies is **disclosed**, never silently enforced — mirrors the wave scheduler's `ineligible` list (each pending task rejected on a `no_ready_tasks` carries its per-task reason). Today's caps and their disclosures: the per-task retry budget `max_retries` (surfaced to `task-executor` as `MAX_RETRIES=N`); the phase-checker's two-attempt L1 fix-and-retry limit (stated in its assignment, not silently applied); the wave `deferred`/`ineligible` lists; the `shape_violation` disclosure for an off-topology dispatch. A new registry field that bounds behavior (a cap, a budget, a stop-on) MUST pair with a disclosure surface — a silent cap reads as "the agent gave up" when the cap actually fired.

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

**When to declare deps:** emit a `<!-- deps: ... -->` comment **only** when there is a real predecessor (a coupled task) or when you deliberately want a flat independent task parallelized. Do **not** reflexively annotate every task — most tasks are sequential and need no comment.
- **Coupled task** (builds on an artifact a sibling produced — a model, a utility, a config key): `<!-- deps: P1.T1 -->`. The task stays serial until `P1.T1` lands, then becomes wave-eligible. This is the common reason to declare deps.
- **Independent task you want the wave scheduler to parallelize** (genuinely disjoint files/modules, flat, in a phase the user wants fanned out): `<!-- deps: -->` — an *empty* deps comment is the explicit "I have no dependencies" declaration. This is what makes a task a wave candidate; the scheduler treats it as deps-satisfied immediately. Emit it **deliberately** — an empty deps comment on an ordinary sequential task is clutter that buys nothing (the task would have run on time anyway on the serial spine).
- **No comment at all** (the default for sequential work): the task is assumed serial-order-dependent and never wave-parallelized. It runs normally in declaration order; it just forgoes the parallel speedup — which is the correct outcome for the majority of tasks.
