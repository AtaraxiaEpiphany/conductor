---
type: concept
sources:
  - conductor/design/agent-roster
  - conductor/design/decision-planning-as-data
  - conductor/design/single-source-authority
last_verified: 2026-08-21
---

# Decision: Agent Roster (roster-as-data seam)

Status: **Accepted** (2026-08-21, grill-resolved) — the scaffold contract
around subagent dispatch (result fences, registry injection, output
filtering, write-guards) moves from hardcoded name lists into a merged
data file; skills ride subagents by harness preload. Full decision set and
phases: [[conductor/design/agent-roster]].

## Context

Subagent-driven development means every task runs in a subagent, and the
harness already resolves agent names across plugin, project, and user
homes — so outside agents are dispatchable today but receive no scaffold:
the contract bundle is welded to `AGENT_REMINDERS` (23 fences),
`_REGISTRY_AGENTS`, `_RETRY_AGENTS`, `_WRITE_AGENTS`, the stop-hook
recovery sets (`_RESULT_FILE_INSTRUCTIONS`, `STDOUT_BLOCK_AGENTS`), and the
static matcher alternations that keep non-plugin names from ever reaching
the scripts. The grill's premise challenge targeted the home for that
contract: a third registry re-states what each agent's body already
implies — the exact second-home drift class the single-source campaign
deleted — so the alternatives were live options, not strawmen.

The same grill settled the skills axis by lookup, not preference: the
harness natively preloads a subagent's `skills:` frontmatter at startup
(wiki, subagents.md §8.4), which makes "a skill runs in a subagent" a
wrapper-agent pattern rather than a plugin capability.

## Decision

1. **Third registry.** `agent-roster.json`, baseline ⊕ project overlay on
   the shared merge ladder — the same shape as the task-type and shape
   registries. Registry owns policy (what scaffold each agent gets);
   bodies own behavior.
2. **Row = class + fence + overrides.** The bespoke fence string is
   per-agent data (irregular labels, one PURPOSE-conditional); class
   (executor/verifier/reviewer/advisory) derives single-writer default;
   three override flags only where a row differs.
3. **Skills ride by preload.** A skill integrates as a project agent
   (`skills:` frontmatter + procedure body + one roster row). Context
   rule: preload procedure, fetch reference — guidance, not lint.
4. **Fail-open runtime, lint-loud.** Unrostered agents run untouched
   (today's behavior); `check` cross-checks declared names against the
   live three-directory roster and rejects unknown classes; runtime
   row-skips bad overlay rows with stderr, never hard-fails a session.

## Rationale

1. **The machinery exists and is proven twice.** Merge ladder, validator,
   drift gates, README sync — the two registries already pay for all of
   it; a third rider costs one loader and one watcher entry.
2. **The rejected homes fail on harness grounds, not taste.** Agent
   frontmatter is harness-owned territory (unknown keys are
   version-brittle, four hooks would parse markdown); envelope-carried
   contracts fragment the fence across every dispatch site — the
   multi-home drift class this repo hunts for a living.
3. **Preload beats tool-invocation for contract-carrying content.**
   Harness-guaranteed injection is the hooks principle ("this always
   happens, regardless of the model's judgment") applied to context — no
   gamble on the model remembering to invoke mid-flight.
4. **The deterministic seam holds.** The roster contracts *named* agents;
   it never chooses them. Model-judgment selection over an open roster
   was already rejected one layer up (planning-as-data D2) and is not
   re-opened here.

## Gate check (all three hold)

- **Hard to reverse:** deletes six literal sets into data, widens the
  SubagentStart/SubagentStop hook matchers (every future subagent pays
  them), adds a validated registry + lint surfaces.
- **Surprising without context:** it looks like a fourth registry of the
  same kind as task-types/shapes while contracting behavior, not task
  semantics; and it hands project agents conductor's scaffold without any
  plugin review — the lint is the only gatekeeper.
- **A real trade-off was rejected:** contract-in-the-agent-file and
  envelope-carried contracts (D1); executor Skill tool and fork-skill
  dispatch (D3); runtime deny and telemetry (D4) — all live options with
  named costs.

## When to revisit

- **A real consumer wants new-track visibility into available agents** —
  the deferred advisory digest's bar (mirrors the archetype-axis bar).
- **A second scaffold facet emerges that class cannot express** (e.g.
  per-agent telemetry) — revisit the row shape before adding a fifth
  registry.
- **The harness grows native per-agent contract hooks** (subagent-scoped
  `hooks:` in frontmatter) — the agent-file home becomes viable and D1
  should be re-litigated.

## See Also

- [[conductor/design/agent-roster]] — the full decision set + phases this record governs
- [[conductor/design/decision-planning-as-data]] — the seam-family ADR whose discipline this inherits
- [[conductor/design/single-source-authority]] — the Delete→Point ladder the literal migration follows
- [[conductor/resource/glossary]] — **agent roster**, **scaffold contract** entries
