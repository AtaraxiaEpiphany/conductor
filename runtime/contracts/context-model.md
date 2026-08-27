---
type: concept
sources:
  - scripts/on-subagent-start.py
  - scripts/track_state/task_context.py
  - scripts/track_state/probes.py
  - agents/spec-planner
  - agents/task-executor
last_verified: 2026-08-27
---

# Context Model (three tiers)

The single home for **how a subagent receives its context**. Every subagent in
the conductor follows the same three-tier discipline — the inconsistencies this
contract retires (a brief inlined into one prompt, a full tag catalog injected
into another, a cross-file join hand-extracted in a third) were all violations
of it. Author a new agent or change what one loads by consulting this page, not
by restating the tiers in the agent's own body.

Sibling of [[runtime/contracts/prose-style]] (what the prompt *says*) and
[[runtime/contracts/plan-format-contract]] (the plan grammar); this page governs
*how context reaches the agent*, independent of content.

## The three tiers

**Tier A — hook-injected (SubagentStart).** The universal safety floor + the
result-format reminder, PLUS the **small, per-task, resolved** data a dispatch
already computed and that the agent cannot re-derive cheaply. Today: the locked
task's leading-tag profile and resolved exemption sets (`task-executor`), the
review flags (`spec-reviewer` / `refuter`), and the retry/modified-retry nudge.
**Never a full catalog** — the floor stays small so it earns its place in every
dispatch. See `scripts/on-subagent-start.py::_REGISTRY_AGENTS` for the allowlist.

**Tier B — on-demand CLI.** **Full catalogs and cross-file joins** — too large
or too conditional to inject into every dispatch, so the agent fetches them with
one `track-state` call when it needs them. Today: the resolved tag + shape
registry (`registry-doc`, fetched by `spec-planner` at planning start), a single
tag's `workflow` prose (`registry-doc --tag <Tag>`, fetched by `task-executor`
only when the leading tag carries one), the per-task plan↔spec AC/TC join
(`task-context`), the retry handoff (`get-handoff`), and **live project state
via registered probes** (`probe test-state`, fetched by executors/verifiers
when they need the live run ledger — see below). Same tier, file-delivered:
the **per-dispatch workflow manifest** (`WORKFLOW_FILE` in `task-executor`'s
envelope — a code-composed join of shape gates ⊕ tag exemptions ⊕ the workflow
path decision; small, per-task, resolved, and byte-stable across retries).

**Probes (tier B's dynamic arm).** A *probe* is a **named, registered,
read-only, side-effect-free, cheap** snapshot command (`track-state probe
<name>`) over state no static file carries — the exemplar `test-state`
returns the latest test-run verdicts from the on-test-run ledger. Probes live
in the fourth registry (`templates/workflow/probes.json` baseline ⊕ project
overlay, row-level replace; `track-state check` lints it); `kind: builtin`
rows are parsers in `scripts/track_state/probes.py`, `kind: command` rows run
a registered argv under a hard timeout. The rules are the point: dynamic
context is fine, *undisciplined* context is not — anything an agent wants to
"just check quickly" must either be a file (tier C), a registered probe
(tier B), or it does not happen. Ad-hoc context scraping (shelling out to
inspect tmux panes, tailing arbitrary logs, `curl`-ing dashboards from agent
prose) stays out of the tiers entirely. See `conductor/design/probes.md`.

**Tier C — self-load Read.** **Durable file content** — the project's own
artifacts (`brief.md`, `spec.md`, `plan.md`, the workflow/styleguide docs) and
the **workflow docfile** the manifest's path decision names (plugin steps
library or its project override). The
prompt carries a **path**; the agent `Read`s the file itself, just in time, so
the content never enters the orchestrator's context and never goes stale inside
a prompt. A prompt that inlines a durable file's content (instead of naming its
path) is the canonical violation.

## The rule of thumb

> Inject the **small / resolved / per-task**; fetch the **full catalog / join**
> on demand; read **durable files** by path.

The discriminator is *size + scope*, not format:

- **Small + per-task-resolved** (one tag's profile, one retry record) → inject.
  The dispatch already paid the resolution; re-fetching it per agent is waste,
  and the agent can't re-derive a resolved profile without the locked-task
  context.
- **Full catalog or cross-file join** (the whole tag set, an AC→task→TC join) →
  on-demand CLI. Large, and usually only one agent in the run needs it. A CLI
  owns the extraction deterministically (it can't drift from the parsers' grammar
  the way hand-extraction in prose can).
- **Durable file** (a doc the project owns and edits) → path + self-load. The
  source of truth is the file; a prompt copy is a stale copy.

## Worked examples

- `spec-planner` needs the **full tag catalog** to author plan.md → Tier B:
  `track-state registry-doc` (§3.1). It is NOT in `_REGISTRY_AGENTS`.
- `task-executor` needs **its own task's** leading-tag profile → Tier A
  (injected), AND the per-dispatch workflow manifest (resolved gates + workflow
  path) → Tier B (`WORKFLOW_FILE`), AND the leading tag's `workflow` prose when
  the path decision is `inline` → Tier B pointer (`registry-doc --tag`), AND the
  docfile the manifest names → Tier C (self-load), AND the task's AC text →
  Tier B (`task-context`), AND `spec.md`'s Out-of-Scope → Tier C (self-load),
  AND "did the suite pass since my last change?" → Tier B probe
  (`track-state probe test-state`).
- Every agent reads `brief.md` / `spec.md` / `plan.md` by path (Tier C) — never
  inlined. A `USER_CONTEXT: brief` signal names the file; it does not carry the
  file's content.

## Anti-patterns

- Inlining a durable file's content into a dispatch prompt (a second home that
  drifts from the file; doubles the tokens).
- Injecting a full catalog (every dispatch pays for data one agent needs once).
- Hand-extracting a cross-file join in agent prose when a CLI could own it (the
  extraction drifts from the parsers' grammar).
- Ad-hoc dynamic context from agent prose (tailing an arbitrary log, scraping
  a pane, curl-ing a dashboard) — if the question recurs, it earns a
  registered probe; if not, the agent asks the orchestrator.
