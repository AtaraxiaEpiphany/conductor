# Probes — tier B's dynamic arm

**Status:** shipped (2026-08-27) — registry + loader + `probe` CLI + the
`test-state` exemplar + `check` lint.
**Contract home:** [[runtime/contracts/context-model]] (tier B, the probe
paragraph). **Code:** `scripts/track_state/probes.py`;
registry `templates/workflow/probes.json` (baseline) ⊕
`conductor/workflow/probes.json` (project overlay, row-level replace).

## Why

The grilling locked this framing: "dynamic context via scripts" is a real
need (agents want live state no static file carries — did the suite pass?
what did the last run change?), but the obvious implementations are all
drift machines:

- **Hardcoded hooks per question** — every new question is a plugin edit;
  projects cannot extend.
- **Ad-hoc scraping from agent prose** (`tail` this log, `curl` that
  dashboard) — unauditable, uncacheable, untestable, and it re-imports the
  tmux-capture heuristic the user explicitly demoted ("just a heuristic, not
  the design").
- **Injecting live state into every dispatch** — violates tier A's "small,
  per-task, resolved" floor; most dispatches never need it.

The design answer is the same one the other three registries gave: make the
**vocabulary** data. A probe is a *registered, named* context snapshot; the
registry says which exist, the CLI is the single execution path, the lint
says when a row is dead.

## The contract (the five adjectives are the whole design)

A probe is **named** (registered — a lint-visible name, not a string typed
into a shell), **read-only** (side-effect-free; documented + reviewed, NOT
enforced — a mutating `command` row is a design violation caught in review,
while the builtins are read-only by construction), **cheap** (builtin
parsers read one log; `command` rows run under a hard 10s timeout with
bounded stdout), **registered** (baseline ⊕ overlay, exactly like the other
three registries), and **fetch-on-demand** (tier B — never injected into
every dispatch).

Anything an agent wants to "just check quickly" must be a file (tier C), a
registered probe (tier B), or it does not happen.

## Registry shape

```jsonc
{ "probes": {
    "test-state": { "description": "Latest test-run verdicts (on-test-run.log)",
                    "kind": "builtin" },
    "ci-status":  { "description": "…", "kind": "command",
                    "command": "ci-status --json" } } }
```

- `kind: builtin` — implemented in `probes.py` (`_BUILTINS` is the closed
  set); a builtin row naming an unimplemented builtin is a lint error (dead
  name).
- `kind: command` — `shlex`-split argv, no shell, `subprocess.run` with
  `timeout=10`, stdout bounded to the last 8 KiB. The command MUST be
  read-only.

`track-state check` lints the resolved registry (`probe_errors` in the
preflight envelope — non-empty makes `ok` false). Runtime stays fail-open: an
invalid row degrades to the unknown-probe response, never a crash.

## The exemplar: `test-state`

`track-state probe test-state` parses `<logs>/on-test-run.log` — the ledger
the on-test-run PostToolUse hook already writes (`{iso} [INFO] {ts}
test_command="…" result=passed|failed|interrupted`) — into
`{last, recent[≤20], summary:{passed,failed,interrupted}}`. Absent/empty
ledger → `{ok: false, reason: "no test runs recorded"}`.

Why this one first: the data already exists, the hook already owns the write
side, and "did the suite pass lately?" is the question executors and
verifiers actually ask mid-track. The probe adds the read side without a
second writer.

## Deferred families (deliberately out of v1)

- **Session/agent activity** (dispatch-lifecycle digest, "what has this
  agent already tried") — needs a retention story first; the lifecycle log
  is unbounded today.
- **External tool probes** (CI, dashboards, ticket state via `command` rows)
  — the mechanism ships with this design note; the rows are the project's to
  add. A project-local row is the intended path, NOT a plugin baseline row.
- **Probe fan-out** (`probe --all`) — no consumer yet; YAGNI.

## Rejected alternatives

- **tmux capture/send-key as a context source** — interaction, not context:
  write-side effects, session-coupled, unauditable. The user's framing
  ("heuristic, not the design") is recorded here so it is not re-proposed.
- **A generic "run any command" probe** — the registry IS the allowlist; a
  wildcard row would be the ad-hoc scraping the design removes.
- **Injecting probe results at dispatch (tier A)** — per the rule of thumb:
  fetch the join/snapshot on demand, inject only the small + per-task +
  resolved.
