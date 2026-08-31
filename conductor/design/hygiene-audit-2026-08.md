---
type: concept
sources:
  - scripts/track_state/dispatch.py
  - scripts/track_state/cli.py
  - scripts/pre-command-check.py
  - README.md
last_verified: 2026-08-31
---

# Post-Campaign Hygiene Audit — 2026-08

Advisory read of the whole plugin after the brief→new-track seam shipped
(`91eead8`, v1.1.0). Scope agreed via grill 2026-08-31: whole repo, all four
dimensions (architecture seams, code health, efficiency, test health),
maintainability-first ranking, static + measured evidence. **Advisory only —
nothing here is implemented.** Each Tier-1 finding is pre-shaped so it can seed
a future brief without re-derivation.

## Measured baseline

| Metric | Value |
| :--- | :--- |
| Test suite | 2,727 passed (+72 subtests), two consecutive full runs, 0 failures |
| Suite wall time | 94.6 s (collection 1.56 s) |
| Slowest tests | 10.01 s `test_probe.py` command-timeout (deliberate), 1.44 s roster safety-floor, 0.96 s transaction F1 |
| CLI cold start | 0.11–0.16 s (`scripts/track-state --help`, 3×) |
| Hottest hook (`pre-command-check.py`, every Bash PreToolUse) | 0.18–0.29 s per invocation |
| Python core | 33,942 lines in `scripts/`, 945 functions, 33 over 100 lines |
| Marker debt | zero TODO/FIXME/XXX/HACK in code |
| Tree | git clean at `d17344f` |

## Tier 1 — ranked findings

### 1. `dispatch.py` is a god module — watch · effort M · risk low

4,370 lines — 12.9% of the Python core — hosting dispatch composites, Rail B
spine routing, failure-analysis routing, and the post-loop step in one import
graph. 133 functions keep average granularity healthy, but module cohesion is
gone: every recent campaign touched this file and its gotchas dominate the
campaign retros.

**Fix:** split along the CLI's own group seams — dispatch composites
(prepare/next/finalize), post-loop and phase routing, failure-analysis
routing — as pure function moves with the green suite bracketing each hop.
This is the two-tier refactor doctrine: mechanical, default-on, no behavior
change. Fold the `misc.py` dissolution (Tier 2) into the same track while the
import graph is open.

```text
wc -l   scripts/track_state/    dispatch.py 4370 · misc.py 2290 · shape_studio.py 1230 · cli.py 1035 · wave.py 959
longest (AST)  cli.py:575 main 457 · misc.py:1914 cmd_registry_doc 351 ·
               plan_parse.py:252 parse_plan 201 · dispatch.py:221 cmd_dispatch_next 185
```

**Seeds:** dispatch-split track · mechanical refactor · AC = byte-identical
behavior, suite green after each move.

### 2. Hand-copied command lists still drift — fix · effort S · risk low

The proven cure already exists in the hottest hook:
[pre-command-check.py](../scripts/pre-command-check.py) derives its sanctioned
set from `COMMAND_GROUPS` via `_load_sanctioned_subcommands()` — a file-load
of the stdlib-only leaf (~0.2 ms, avoiding an ~80 ms import chain) — and its
docstring records that the old ~90-entry hand copy "historically missed
subcommands." Two hand lists survive anyway: the `_NO_TRACK_DIR_COMMANDS`
frozenset and the inline `_QUERY_FNS` map, both maintained by hand in
[cli.py](../scripts/track_state/cli.py). Campaign retros confirm the burn:
"roster/probe in BOTH no-track-dir lists."

**Fix:** same derivation pattern — encode the flags the lists represent
(query-only, no-track-dir) on the command rows and derive both frozensets
from the one table. New subcommands then touch one place; the gotcha class
dies instead of recurring per campaign.

**Seeds:** cli-table-derivation track · single-source authority applied to
its last hand lists.

### 3. README registry narrative lags the code — fix · effort S · risk low

The generated blocks are accurate (91 subcommands / 18 groups verified by
`check-readme-sync`); the hand-written prose around them drifted.
[README.md:87](../README.md) says "Three registries" while `probes.json`
self-describes as the fourth; `README.md:132` says "The two registries"; the
`templates/planning/` family and `conductor/resource/glossary.md` are absent
from the architecture tree. Prose restating a registry is a drift liability —
the single-source-authority campaign's own finding.

**Fix:** count the registries in code and feed the sentence to
`check-readme-sync` as a generated fragment (the mechanism already used for
the tables); add the planning family and glossary to the tree. Run
`check-readme-sync --fix` after the registry-count change — the known
fragment-sync gotcha.

**Seeds:** readme-narrative-sync track · pairs naturally with finding 2 in
one session.

### 4. Advisory-only shape axis needs an honest label — decision · S or M

The workflow-shape registry declares topology (nodes, verifiers, gates,
checkpoint policy), surfaces `shape_violation` drift, but does not reorder
dispatch. The README discloses this, yet the registry rows themselves read as
if they drive. A declared-but-inert knob is the one architectural honesty
gap: the next reader will assume the rows are load-bearing and plan against a
mirage.

**Recommended:** re-label now — a registry field or README row stating
"advisory, drift-gated" (S). Making gates actually reorder dispatch is a real
campaign (M–L); per the declined-proposals history, do that only when a
concrete track needs it. Write a `decision-*.md` record either way so the
choice stops being re-derived.

**Seeds:** honesty-pass track · or one decision record + two-line registry
edit.

### 5. `spec-planner` still advertises a dead route — fix · effort S · risk low

The agent's description and body say it is "dispatched by conductor:setup and
conductor:newTrack" — camelCase for a skill that has been
`conductor:new-track` for a long time, and stale routing prose now that
new-track adopts briefs through the pre-derive scan instead of exact-id
round-trips. The description is the matching surface: wrong names degrade
routing and teach readers a seam that no longer exists.
[spec-planner.md:3](../agents/spec-planner.md) and `:14`.

**Fix:** rename to the current skill id and restate the actual route (setup;
new-track via brief adoption). Grep for other camelCase survivors.

**Seeds:** honesty-pass track · two-line edit + one grep.

### 6. Legacy inline `workflow` field is dead weight — watch · effort S

The task-type registry keeps inline `workflow` support marked "legacy" beside
the preferred `workflow_doc`, and no baseline row uses it today. Pure
back-compat surface: every reader pays the two-field decode, and the
two-homes guard already rejects rows carrying both.

**Fix:** scan project overlays for inline usage, then delete the field and
its parser arm with a clear error naming the migration (`workflow` →
`workflow_doc` filename). Land it inside the honesty pass so the deprecation
is one story, not a stray commit.

**Seeds:** honesty-pass track · overlay-usage check is the gate.

## Tier 2 — one-liners by dimension

**Architecture**

| Finding | Note |
| :--- | :--- |
| Contract re-verify lag | `last_verified` runs 2026-06→08; grill-discipline (08-20) predates the brief seam (08-29). Light re-verify pass over the 13 contracts — not a rewrite. |
| Empty placeholder dirs | `commands/`, `output-styles/`, `themes/`, `monitors/` are `.gitkeep`-only. Populate on the next feature that needs them, or drop until then. |
| Double wrapper shim | `bin/track-state` → `scripts/track-state` → package. Deliberate and documented; note only. |

**Code health**

| Finding | Note |
| :--- | :--- |
| `misc.py` catch-all | 2,290 lines, 55 defs; the name concedes no organizing principle. Dissolve during the dispatch split — same import-graph surgery, one recovery. |
| 33 functions over 100 lines | Watch, don't mass-refactor. Next natural touch of `parse_plan` (201 lines) table-izes the grammar walk; the rest age fine. |

**Efficiency**

| Finding | Note |
| :--- | :--- |
| Hook floor ~0.2 s per Bash call | Interpreter spawn cost, already minimized by the leaf-file-load trick (avoids ~80 ms). A daemon or compiled hook is not worth the complexity. No action. |
| No new context bloat found | COMPACT_FIELDS discipline, output filtering, three-tier context model all hold. Suggested tier-B probe: dispatch-prompt byte count per task type, registered in `probes.json`. |

**Test health**

| Finding | Note |
| :--- | :--- |
| `test_track_state.py` holds 94 tests | Split mirroring the module split from finding 1 — same track, after the moves settle. No flakiness observed; stdlib-only intact. |
| One 10 s outlier | Deliberate probe-timeout test. Slow-marker only if the suite grows past ~3 min; at 94.6 s there is no pressure. |

**Housekeeping**

| Finding | Note |
| :--- | :--- |
| Dogfood scratch in the plugin tree | `.conductor/` and `.data/` are env-ladder fallback residue, gitignored, uncommitted. Occasional `track-state gc` or leave. |

## What is already excellent

- **Zero marker debt** at 34k lines — the only TODO/FIXME hits are styleguide
  prose *about* TODO format.
- **The suite is a real safety net** — 2,727 green twice in a row, 94.6 s,
  stdlib-only, 1.56 s collection, no flakes. This is what makes finding 1
  low-risk.
- **Derivation-over-hand-copy already proven** — the sanctioned-set loader in
  the hottest hook is exactly the pattern finding 2 generalizes. The doctrine
  works; two lists are left to eat.
- **Generated README tables held** through five campaigns; only un-gated
  prose drifted, which is finding 3, not a gate failure.
- **Git clean at a shipped-campaign commit** — hygiene between campaigns is
  already real; this audit found polish targets, not rot.

## Track menu (sequenced)

1. **Track A — CLI & README sync** (findings 2 + 3 · S · one session).
   Derive `_NO_TRACK_DIR_COMMANDS` and the query-map from `COMMAND_GROUPS`
   row flags; regenerate the README registry narrative as a checked fragment;
   add planning family + glossary to the tree. Kills two recurring
   per-campaign gotchas. First — smallest, and Track C lands on the improved
   table.
2. **Track B — Honesty pass** (findings 4 + 5 + 6 · S–M). Shape-axis
   advisory re-label + decision record; spec-planner route prose fix; legacy
   inline `workflow` removal gated on an overlay scan. One story: the
   surfaces now say what the code does.
3. **Track C — dispatch.py split** (finding 1 + misc dissolution + test
   split · M). Mechanical module split along CLI group seams; `misc.py`
   dissolved; `test_track_state.py` split to mirror. Largest payoff, lowest
   urgency — the suite brackets every hop. Last.

## Method & constraints

Static: full-repo structure sweep (read-only subagent), AST walk for function
lengths, hand-verification of every quoted line. Measured: two consecutive
full pytest runs, 3× CLI cold start, 3× hot-hook invocation, collect-only
timing — all at HEAD `d17344f`. Ranking axis: maintainability first,
efficiency second, per the grill. Declined proposals stay declined: no
harness-opt #1/#3, no step-sequence split, no manifest+archetype.

**Open items for the author:** overlay usage of inline `workflow` (gates
finding 6); whether any live project overlay references the registry count.

## See Also

- [[conductor/design/single-source-authority]] — the doctrine findings 2 and 3 apply to its last hand lists.
- [[conductor/design/decision-planning-as-data]] — the planning family finding 3 adds to the README tree.
- [[conductor/design/agent-roster]] — roster surface finding 5 corrects routing prose on.
