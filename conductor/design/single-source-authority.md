---
type: concept
sources:
  - runtime/contracts/prose-style
  - scripts/check-contract-registry-sync.py
  - scripts/track_state/cli.py
last_verified: 2026-08-16
---

# Single-Source Authority Campaign

Status: **Agreed** (2026-08-16, resolved via grill per
`runtime/contracts/grill-discipline.md`) — not yet implemented.

## Context

An evidence sweep of every doc surface (skills, agents, runtime, templates,
README) against the code that owns its facts found that the plugin's
anti-drift doctrine works exactly where it is applied and fails exactly where
it is not. The dynamic channels already built — `[Conductor Registry]`
injection, `build_dispatch_prompt`, `COMPACT_FIELDS`, the drift-lint family —
show **zero divergence**. Every confirmed divergence lives in static prose
that restates a code-owned fact with nothing pinning the two together.

Four live contradictions at sweep time (all hand-verified):

1. `skills/parallel/SKILL.md` §0.0 says waves are "Capped at 4 members";
   `scripts/track_state/wave.py` defaults to 2 (`CONDUCTOR_WAVE_SIZE`
   tunable). The same skill's §3.2 and `parallel-step` both say 2.
2. `README.md` claims "23 specialized AI agents" and its agent table omits
   `build-runner`; `agents/` holds 24 and `hooks.json` matches 24.
3. `agents/{phase-checker,build-runner,test-runner}.md` document
   `PHASE_INDEX` as 0-based; the dispatch layer emits 1-based (the CLI
   auto-converts 0-based inputs up, tests assert `PHASE_INDEX=1`).
4. `templates/task-workflow.md` Step 5 enumerates `[Docs]`/`[Config]`/
   `[Chore]` as TDD-exempt — a registry-owned set (which also includes
   `[Manual]`/`[Explore]`) restated in the one surface family the drift
   lint does not watch.

Behind them stand the systematic classes: six independently maintained agent
rosters, five renderings of the marker↔status map, a hand-maintained compact
restatement of the core contract inside `scripts/session-start.py`, Rail A
skills hand-writing dispatch prompts that Rail B pre-assembles in code, and
fast-rotating counts/groupings in the README.

A separate code-side sweep adds: ~530 lines of verified-dead library code, a
latent grill-gate bypass (the brief-grill counter's clear function is dead,
so stale high counts can pre-satisfy the grill floor for a reused
`track_id`), a duplicated registry-format parser with no parity test, and a
~90-entry hand-copied sanctioned-subcommand set that could simply import its
source.

## Decisions

Four decisions were grilled to closure. They govern this whole campaign.

### D1 — The ladder, with injection last

For any prose that restates a code-owned fact, climb this ladder and stop at
the first rung that works:

| Rung | Mechanism | Cost profile |
|---|---|---|
| 1. Delete | Remove the restatement | free |
| 2. Point | Reference the single home | ~0 tokens until needed |
| 3. Lint | Equality/drift test pinning mirror↔source | one-time + CI |
| 4. Derive | Generate the artifact from code (by import or codegen) | build-time |
| 5. Inject | Runtime hook/CLI emission | **every session, forever** |

The governing trade-off: injection pays tokens on every session; a pointer
pays nothing until needed; a lint pays once. Existing injection channels are
adequate — this campaign adds **no new injection channels**. The work is
overwhelmingly rungs 1–4. (This ladder generalizes the Bucket taxonomy of
`runtime/contracts/prose-style.md`; that contract gains a short pointer here.)

### D2 — Full sequenced campaign

Phase 1 (pure fixes) → Phase 2 (enforcement rungs) → Phase 3 (structural,
**only if** pain persists after 1+2). Tests green at every phase boundary;
each phase is independently shippable.

### D3 — Rail A goes paste-verbatim everywhere

`build_dispatch_prompt` coverage extends until every dispatch the `implement`
/ `parallel` / `post-loop` surfaces make is "paste the envelope's `prompt`
field verbatim" — the idiom those skills already use for
executor/explorer/verifier fan-out. The hand-written KEY=value blocks are
deleted, not linted.

### D4 — README becomes generated + sync-checked

A `check-readme-sync.py` joins the lint family: volatile fragments (agent
table, command table, CLI group table, counts) regenerate from code between
HTML markers; a test wrapper fails on divergence. Marketing prose stays
hand-written.

## Phase 1 — Correctness fixes (pure, independently shippable)

1. **Four contradictions** — restate to match code (wave default; README
   agent roster incl. `build-runner`; `PHASE_INDEX` 1-based; Step-5 exemption
   prose rewritten to the registry-pointer form core-contract F2/F3 already
   use).
2. **Brief-grill gate bypass** — wire the counter clear into the
   `brief-finalize` path (which already deletes the marker) and reap stale
   counters on write, mirroring `scripts/lib/recovery.py`'s session counters.
3. **Dead code (~530 lines)** — re-verify by whole-repo grep, then delete:
   the dead function sets in `scripts/lib/{git_utils,json_utils,path_utils,
   env,validation,hook_io,logging}.py`, `scripts/test-all.py`, and
   `dispatch.py`'s dead `_modified_guidance_clear`. Watch the
   duplicate-definition traps (`env.get_session_id`/`get_cwd` shadowed by
   `hook_io`'s live versions).
4. **Hygiene** — delete `scripts/.backup/bash-originals/` (git history
   preserves them); bump `plugin.json` to 1.1.0.

Verification: full suite green (`PYTHONPATH=. python3 -m pytest`).

## Phase 2 — Enforcement rungs

### Rung 4 — derive

- `_SANCTIONED_TS_SUBCOMMANDS` **imports** `_COMMAND_GROUPS` (a mirror with
  a "keep in sync" comment is the anti-pattern this campaign exists to kill).
- Hoist `_INDEX_COMMANDS` beside its sibling allowlists; add a meta-test
  that every grouped command reaches a `main()` branch (today a grouped
  command with no elif branch falls to "Unknown command" silently).
- `session-start.py` COMPACT_CONTENT marker/commit lines generated from
  `MARKER_MAP` / `VALID_COMMIT_TYPES` at import.
- The V10 deny message renders from `VALID_COMMIT_TYPES`;
  `quality.py:_TRANSIENT_MARKERS` imports marker constants instead of
  re-typing 19 literals (rename a marker today and it silently exits the
  transient/gitignore set).
- `check-readme-sync.py` + test wrapper (D4).
- Marker read/write/clear triplet single-homed in `helpers.py` (≥6
  hand-rolled clones today; this is the crash-safety backbone).
- **Rail A paste-verbatim (D3)** — extend `build_dispatch_prompt` to
  phase-checker, skip-analyst, skip-refute refuter, failure-analyst,
  self-review code-reviewer, refactorer; `parallel` switches its wave-member
  prompt to the pre-assembled form; delete the hand-written blocks from
  `implement`/`parallel`/`templates/post-loop.md`.

### Rung 3 — lint

- Marker-map equality across `MARKER_MAP`, core-contract's Task State Model,
  the `status` skill render map, and `code-reviewer` — reconciling the
  `archived` 9th status first (add to the map or drop from the skill).
- Registry-parser parity test over a shared corpus
  (`misc._iter_registry_entries` vs `path_utils.extract_track_dirs`, whose
  own comment admits "update BOTH").
- WATCHED-list expansion: `templates/task-workflow.md`, and evaluate
  `new-track`/`setup`/`review`/`status` for admission.
- `AGENT_REMINDERS` keys ↔ `agents/` directory guard (extends the existing
  matcher-coverage test); `brief-progress.json` single-homed (the tripwire
  imports the marker module instead of re-parsing the file).

### Rungs 1–2 — delete/point

- `agents/build-runner.md` language table → point at
  `templates/dev-commands/` (it already resolves from there).
- `skills/setup` styleguide table → point at `templates/code-styleguides/`.
- Key-paths blocks duplicated across four skills → point at the project
  index / TOC, the declared single map.
- Tripwire numbers in `task-executor` and `refactor` → reference constants
  by name only (the `recovery-policy.md` precedent: names, never values).

## Phase 3 — Structural (conditional)

Enter only if pain persists after Phases 1–2: `cli.py` dict dispatch table
(replacing the 388-line elif chain), `misc.py` split at its resolve/status
seam, `dispatch.py` marker-families/`_step_route_*` slice if the file keeps
growing.

## Non-goals

- No new injection channels (SessionStart/SubagentStart additions, skill
  `!`cmd`` preprocessing) — D1 reserves injection for session-volatile
  facts, and the existing channels cover them.
- Previously declined work stays declined (harness-optimization items #1/#3,
  the task-workflow step-sequence split). D1/D2 of the plugin-generality
  design stay deferred.
- `extract_tags` keeps its intentional per-call rebuild (no `@lru_cache`).

## See Also

- `runtime/contracts/prose-style.md` — Bucket taxonomy this ladder extends.
- `runtime/contracts/grill-discipline.md` — the procedure that closed the
  four decisions.
- `scripts/check-contract-registry-sync.py` — the existing lint whose WATCHED
  list Phase 2 extends.
