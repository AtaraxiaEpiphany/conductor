---
type: concept
sources:
  - agents/spec-planner
  - agents/task-executor
  - agents/failure-analyst
  - scripts/track_state/quality
  - scripts/track_state/dispatch
last_verified: 2026-08-31
---

# Task-Type Ownership (planner-authored labels + misroute recovery)

Status: **Agreed** (2026-08-31, grill-resolved; second grill same day confirmed
scope and widened R2) — task-tag assignment becomes planner-authored content,
the keyword matcher demotes to an advisory lint, and a misroute recovery
verdict closes the loop for plans already in flight.

## Context

User pain: task types in plan.md mostly wrong or empty. The canonical failure:
an exploration task arrives untagged, stores as `default` (full TDD), and
dispatches to task-executor — which does exploration-shaped work as a code
task, producing garbage and burning retries. task-executor's explore refusal
never fires because that rule keys on the `[Explore]` tag being present.

Root cause is an ownership inversion, not a taxonomy defect. spec-planner —
the one agent holding the full spec + codebase context — was *forbidden* from
labeling: it must run `propose-tags` on each description and adopt the output
verbatim, never resolving ambiguity itself. The label was owned by a keyword
matcher that sees a description string and nothing else. Untagged was
overloaded: it meant both "matcher found no signal" and "planner chose
implementation" — two meanings, one token.

The registry-driven mechanism itself is sound (see
[[conductor/design/decision-workflow-as-data]] for the executor-side seam): one
leading tag resolves route, workflow, gate exemptions, and docfile — a deep
module. What was broken is who writes the token and what happens when the
token is wrong.

## Decisions

1. **R1 — planner-authored labels.** spec-planner labels each top-level task
   directly from the resolved registry vocab (the `registry-doc` render fetched
   in its §3.1): `when_to_use` + `signals` are *guidance for the planner's
   judgment*, not a matcher to mirror. No `propose-tags` round-trip. Leaving a
   task untagged remains a deliberate implementation choice (default full
   TDD). The `propose-tags` subcommand is **deleted** (dead route); the
   `rank_tags` core survives as the lint engine.
2. **R1b — advisory lint as telemetry.** `init-from-plan --check` (and plain
   init) prints declared-vs-signals disagreements as non-blocking warnings:
   for each top-level task, the matcher's conservative proposal
   (`derive_task_tag` over the tag-stripped name) is compared to the declared
   leading tag. Every run on a real plan is a telemetry sample; accuracy is
   revisited after a few tracks. Synthetic corpora are regression canaries
   only, never an accuracy claim.
3. **R2 — misroute recovery, broadened.** task-executor gains a misroute
   self-report: when the task is exploration-shaped (deliverable is findings,
   no code change expected) it reports FAILURE with a deterministic
   `MISROUTE` signature — **even when untagged**, which is the observed pain;
   the tagged `[Explore]`-refusal stays. failure-analyst's taxonomy gains
   category `misrouted_explore` → recommendation `reroute_explorer`. The
   verdict router's new arm **amends the plan**: append `[Explore]` to the
   task line in plan.md, mirror the tag into state (name + `task_type`),
   reactivate preserving retry budget, bookkeeping commit, re-dispatch —
   routing then derives `explorer` through the normal classification path.
   No confirm relay: the verdict was already gated by failure-analyst's
   judgment; the commit message records the tag-add for audit.
4. **R3 — manifest mismatch advisory.** `compose_manifest` (pure, fail-open)
   appends one advisory line when a task is untagged but explore signals hit:
   surfaces the possible misroute at dispatch time, the earliest honest
   checkpoint for in-flight plans.
5. **Default stays full TDD.** Untagged now means planner-chose-implementation
   — one meaning. The F2 fail-safe rationale (a wrongly-untagged exemption
   task costs one extra Red cycle; a wrongly-tagged feature task silently
   skips TDD) holds for code tracks.

## Rationale

1. **AI owns content, code owns contract** (the OpenSpec split this repo
   already follows at every prior seam). A task's label is authored content —
   like the task name itself — written by the agent that wrote the name. The
   vocab, the unknown-tag hard error, the TAG_CONFIRM gate relay, and the
   routing table stay contract: code-owned, validated.
2. **Classification belongs where the context is.** classify-and-act is a
   sound pattern when the classifier has context; the matcher had none. The
   planner classifies at authoring time; the spine routes deterministically at
   dispatch; failure-analyst corrects post-hoc. Three stages, each with the
   right knowledge — this *is* the dynamic-execution shape for this system,
   without re-opening D2 (per-dispatch model judgment was declined for
   determinism and byte-identical replay, and stays declined: that stance
   governs control flow, not authored content).
3. **Recovery must be durable, not ephemeral.** `task_type` is a re-derived
   field (reconcile re-derives it from the plan task line), so a
   dispatch-time route override would be silently reverted by the next
   sync. Amending the plan tag fixes the *label* — the actual defect — and
   survives retries, reboots, and reconcile.
4. **Telemetry over synthetic benchmarks.** Real plans are unreachable from
   the work environment; a synthetic-plan accuracy eval would be benchmarking
   the wrong distribution. The lint's disagreement output on real plans is the
   honest instrument.

## Seams and drift sites

- `agents/spec-planner.md` §4.2 — tag decision rule rewritten (planner
  judgment; no command round-trip; TAG_CONFIRM + unknown-tag rules unchanged).
- `skills/new-track/SKILL.md` TAG-confirm relay prose — retargeted from
  "matcher proposed" to "planner authored".
- `agents/task-executor.md` — misroute self-report rule beside the existing
  explore refusal.
- `agents/failure-analyst.md` — taxonomy row + recommendation.
- `scripts/track_state/misc.py` + `cli.py` + `commands.py` + README fragment —
  `propose-tags` deleted end-to-end (check-readme-sync --fix).
- `scripts/track_state/quality.py` — declared-vs-signals advisory lint.
- `scripts/track_state/dispatch.py` — verdict enums, router arm, plan-tag
  amendment helper.
- `scripts/track_state/dispatch_manifest.py` — R3 advisory line.
- `skills/adopt-skill/SKILL.md` — matcher mention retargeted.

## Test inventory

- Lint: agree → silent; declared≠signals → warning; present in `--check`
  output; canary corpus for `derive_task_tag` regressions (kept from the
  propose-tags suite).
- Verdict: new enums validate; `reroute_explorer` arm amends plan + state,
  preserves retry budget, re-dispatches explorer-routed; idempotent when tag
  already present.
- Deletion: `test_propose_tags.py` CLI-facing cases removed, engine cases
  retargeted to the lint; README/cli-groups sync check green.

## When to revisit

- Lint disagreements dominated by one tag family after a few real tracks →
  vocab/signals need editing, not the ownership model.
- Misroute self-report false positives (executor refuses genuine code work) →
  tighten the signature, never route the refusal around the analyst.
- A second misroute class (e.g. manual work auto-executed) → same pattern:
  self-report signature + verdict + tag amendment; do not generalize into a
  runtime classifier.

See [[conductor/design/decision-task-type-ownership]] for the gate check.
Related: [[conductor/design/planning-as-data]] (the vocab render R1 reads),
[[conductor/design/agent-roster]] (route targets).
