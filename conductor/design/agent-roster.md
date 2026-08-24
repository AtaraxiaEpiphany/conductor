---
type: concept
sources:
  - runtime/contracts/grill-discipline
  - scripts/on-subagent-start
  - scripts/on-dispatch-dedupe
  - scripts/filter-subagent-output
  - hooks/hooks.json
  - runtime/contracts/context-model
  - conductor/design/decision-planning-as-data
  - conductor/design/dispatch-manifest
  - "~/Documents/wiki (external; claude-code-extensions/subagents.md §8.4 + skills.md §6.2 read at 2026-08-21)"
last_verified: 2026-08-21
---

# Agent Roster (roster-as-data seam)

Status: **Implemented** (designed 2026-08-21, shipped 2026-08-24) — decision
record: [[conductor/design/decision-agent-roster]]. Phase 0 `84821ea`
(grill artifacts + D5 matcher-reality fix), A `1d1832a` (registry + loader +
validator + `--roster` render), B `1fb584e` (hooks derive; matchers widen),
C `a17b026` (check lint + contract-sync derivation gates), D (this commit:
README recipe, row-level goldens, lazy package init, both experiments
resolved — see Results). The advisory digest stays deferred (bar: a real
consumer).

## Context

Subagent-driven development is this plugin's model — every task runs in a
subagent, and Claude Code resolves agent names across three homes (plugin
`agents/`, project `.claude/agents/`, user `~/.claude/agents/`). A project
agent is therefore already *dispatchable* from any docfile, registry field,
or envelope: nothing intercepts the name. What it is not is *scaffolded* —
the contract bundle around a dispatch is welded to hardcoded name lists in
six places:

- `AGENT_REMINDERS` — 23 bespoke result-fence reminders in
  `scripts/on-subagent-start.py`;
- `_REGISTRY_AGENTS` — the registry-vocab injection set, same file;
- `_RETRY_AGENTS` — prior-failure context, same file;
- `_WRITE_AGENTS` — the single-writer dedupe guard in
  `scripts/on-dispatch-dedupe.py`;
- `_RESULT_FILE_INSTRUCTIONS` + `STDOUT_BLOCK_AGENTS` — the stop-hook
  recovery instructions in `scripts/on-subagent-stop.py`;
- the static name alternations in `hooks.json` (SubagentStart;
  SubagentStop ×2) — the wall that keeps any non-plugin name from ever
  reaching the scripts behind them. The dispatch-dedupe and
  filter-subagent-output hooks are already tool-level `Agent` matchers
  that branch on agent name *inside* the script — their migration is an
  internal literal swap, not a matcher change.

`filter-subagent-output.py` matches a generic `RESULT_BLOCK_PATTERN` — no
fence restatement — so the fences have exactly one literal home today; the
roster replaces it, it does not merge two.

The ask behind this seam: integrate outside skills/agents into the task
workflow. The wiki lookup settled how skills ride (subagents.md §8.4): a
subagent's `skills:` frontmatter **preloads the full skill content at
startup** — deterministic, harness-guaranteed, no Skill tool. The inverse
(a skill with `context: fork` + `agent:` running *as* a subagent) is
main-session-invoked, not agent-typed, so it serves orchestrator-side use
(a Prelude may invoke one) but cannot ride the Agent-tool dispatch path —
which is where the roster governs.

## Decisions

### D1 — A third registry: `agent-roster.json`

The scaffold contract becomes data in
`templates/workflow/agent-roster.json` (baseline) ⊕
`conductor/workflow/agent-roster.json` (project overlay, project rows
added/wins — the shared merge ladder the two existing registries use).
**The registry owns policy (what scaffold each agent gets); agent bodies
own behavior** — the docfile invariant, applied to the dispatch contract.
A project gives its agent the scaffold = one overlay row, zero plugin
edits.

**Rejected:** contract-in-the-agent-file (frontmatter is harness-owned
territory — unknown keys are version-brittle; four hooks would parse
markdown instead of one JSON load; none of the merge/validate machinery
applies) and envelope-carried contracts (the dispatch prose carries the
fence — fragments the contract across every dispatch site, the multi-home
drift class every prior campaign hunted).

### D2 — Row shape: class + fence + overrides

The 23 fences are irregular (18 share `---<LABEL> RESULT--- / ---END
RESULT---`, five have bespoke tails, one is PURPOSE-conditional), so the
fence is genuinely per-agent data:

```jsonc
{ "test-runner": { "class": "verifier", "fence": "---L1 VERIFY RESULT--- ... ---END RESULT---" },
  "explorer":    { "class": "executor", "fence": "---TASK RESULT--- ... ---END RESULT---",
                   "registry_injection": false } }
```

`class` ∈ executor / verifier / reviewer / advisory derives the
single-writer default and documents role; `fence` carries the exact
reminder string (the single home — `AGENT_REMINDERS` dies into it);
overrides (`single_writer`, `registry_injection`, `retry`) are explicit
only where they differ from the class default.

### D3 — Skills ride subagents by preload; context rule

A skill integrates as a project agent: `.claude/agents/<name>.md` with
`skills: [<skill>]` in frontmatter (the harness preloads the content at
subagent start) + a body that is the conductor-facing procedure + one
roster row for the scaffold. The context-engineering rule, mirroring the
context-model tiers: **preload procedure, fetch reference** —
procedure-shaped skills (small, every-run utilization) preload;
reference-shaped skills (large, sometimes-used) stay out of frontmatter
and are fetched on demand by a thin body. Guidance-only — no size lint.
`disable-model-invocation: true` skills cannot preload (harness rule);
missing skills are skipped with a debug warning — the roster's
declared-names lint catches that class at `check`, not at runtime.

**Rejected:** the Skill tool on task-executor (unverified subagent
invocation, and unnecessary once preload exists); fork-skill dispatch
(not on the Agent path — no roster, no fences, re-opens the contract
problem); project skill slots on plugin agents (couples plugin frontmatter
to project content).

### D4 — Unrostered agents: fail-open runtime, lint-loud

An agent not in the merged roster (built-in Explore, ad-hoc
general-purpose, an unrostered project agent) runs untouched — no
injection, no filtering, exactly today's behavior. A deny would outlaw
every legitimate built-in dispatch. Typos and dead names are lint
matters: `track-state check` cross-checks every registry-declared name
(shape `verifiers`, `nodes`) against the live three-directory roster and
hard-fails on unknown class values; runtime row-skips them with loud
stderr (a dispatch hook must never hard-fail a session — the registries'
fail-open precedent).

### D5 — Matchers widen; scripts consult the roster

The SubagentStart and SubagentStop matchers drop their name alternations
and fire for every subagent; each script consults the merged roster and
fast-no-ops unrostered names. (The dispatch-dedupe and
filter-subagent-output hooks are already tool-level `Agent` matchers that
branch on agent name inside the script — their change is the literal swap,
not the matcher.) Matchers are static JSON — no registry-aware pattern
exists, so widening is the only way a project name ever reaches the
scripts. Cost (one no-op spawn per subagent incl. built-ins) is pinned by
the Phase D measurement below.

### D6 — Drift gates mirror the seam family

`check-contract-registry-sync.py`'s `_REGISTRY_AGENTS` parity assertion
flips to a derivation assertion (the literal sets must be **gone** — the
hooks read the roster); shipped docfiles' `Dispatch \`<agent>\`` targets
are cross-checked against the baseline roster; golden tests pin the 23
baseline rows; README regen picks up the recipe. Per-phase commits, full
suite green each boundary — the campaign discipline.

## Phases (the minimal ship)

- **Phase A — registry + loader.** `agent-roster.json` baseline (23 rows);
  the loader with the shared merge ladder; `registry-doc --roster` renders
  the merged view; validator cross-check lands before any consumer reads
  it.
- **Phase B — hooks derive.** The six literal sets deleted into roster
  reads; the SubagentStart/SubagentStop matchers widen (dedupe/filter swap
  internals only); unrostered = fast no-op.
- **Phase C — lint surfaces.** `check` += declared-names-exist (live
  three-directory roster) + unknown-class hard-fail; contract-sync
  derivation gates + shipped-docfile Dispatch-target cross-check.
- **Phase D — docs + gates + smoke.** README recipe (integrate a skill:
  wrapper agent + roster row; integrate an agent: one row), golden
  baseline tests, both experiments below, Status → Implemented.

## Experiments (resolved — Phase D, 2026-08-24)

1. **Preload coexistence — CONFIRMED.** Hypothesis: a `skills:`-preloaded
   subagent and conductor's SubagentStart injection both land in one
   context and the fence is honored. Method: scratch probe agent
   (`.claude/agents/`) + scratch skill (`.claude/skills/`) + one overlay
   roster row, dispatched in a fresh headless session
   (`claude --plugin-dir . -p`) — mid-session registration does not
   refresh the Agent tool's registry, so the probe rode a sub-session
   rather than a dispatch through `implement` (same SubagentStart event,
   same seam; the `implement` wrapper adds nothing the variable
   isolates). The probe reports what its context contains; the single
   variable is the agent's `skills:` frontmatter line. Result: with
   preload — `PRELOAD: 39` (the skill's secret number) AND
   `SCAFFOLD: present` in one context; without — `PRELOAD: absent`,
   `SCAFFOLD: present` unchanged. RESULT block survives
   `filter-subagent-output` in both arms. Bonus finding, now pinned: a
   bare reply with no fence gets its output replaced by the filter's
   recovery path — ad-hoc probes must emit the roster fence.
2. **Widened-matcher cost — CONFIRMED after one remediation.** Hypothesis:
   a no-op hook spawn is imperceptible at dispatch cadence (<100 ms).
   First measurement (20 fires, loaded machine): median 278 ms — over the
   ~250 ms abort bar. Cause was not the widening but the eager
   `track_state/__init__` chain (`cli` → `shape_studio` → `http.server`,
   …) that every function-level hook import paid; made lazy (PEP 562
   `__getattr__` re-exports). Interleaved re-measurement (20 rounds,
   floor vs hook): interpreter floor 48 ms; Start-hook marginal
   **31 ms**, Stop-hook marginal **22 ms** — well inside the hypothesis.
   The abort arm (start-only widening) never fired.

## Non-goals

- **No advisory digest** (available-agents listing at new-track) —
  deferred until a real consumer exists; discovery proposes nothing in
  this seam, declarations dispose.
- **No auto-selection** — the roster contracts named agents; it never
  chooses them (D2 of planning-as-data already rejected model-judgment
  selection; the deterministic seam holds).
- **No runtime deny or telemetry line** for unrostered dispatches —
  fail-open is the whole runtime story; the lint is the guard.
- **No agent-frontmatter contracts, no fork-skill dispatch, no executor
  Skill tool** — the rejected homes/mechanisms of D1/D3, recorded so they
  are not re-litigated.
- **No mid-session roster mutation** — the overlay is read per dispatch,
  changed only by editing the file.

## See Also

- [[conductor/design/decision-agent-roster]] — the ADR recording the premise resolution + gate check
- [[conductor/design/planning-as-data]] / [[conductor/design/dispatch-manifest]] — the seam family this completes
- [[runtime/contracts/context-model]] — the Tier A/B discipline the preload rule mirrors
- [[runtime/contracts/grill-discipline]] — §4 premise challenge, §7 crystallization writes (this doc IS one)
- [[conductor/resource/glossary]] — **agent roster**, **scaffold contract** entries
