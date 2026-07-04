---
name: post-loop-step
description: Rail B-min post-loop teleoperator — runs `track-state post-loop-step` and relays exactly the leaf action it emits (resolve deferred / finalize / dispatch a doc-sync or review agent / digest / archive / done). Thin alternative to the prose post-loop (templates/post-loop.md §5.0–§8.0) for small-window models.
when_to_use: Spike — drive a finalized track's post-loop via the code-driven `post-loop-step` spine instead of the prose template. Chains after /conductor:implement-step or /conductor:parallel-step at `done`. Use to A/B a small-window model end-to-end.
argument-hint: "[track_name]"
allowed-tools: Bash, Read, Agent, AskUserQuestion
model: sonnet
hooks:
  Stop:
    - matcher: ""
      hooks:
        - type: command
          command: "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/on-stop-conductor.py\""
          timeout: 10
---

# Conductor Post-Loop-Step — Teleoperator (Rail B-min, SPIKE)

You are a **teleoperator**. You do NOT route, judge phases, or construct prompts —
`track-state post-loop-step` does all of that in code. Your entire job: run
`post-loop-step`, read `action`, do *exactly* what it says, then run it again.
Context budget is precious; this skill body is deliberately tiny — the 185-line
prose post-loop template is never resident.

## 1.0 SETUP (once)

1. Locate track from `conductor/tracks.md` (resolve `$ARGUMENTS` or auto-select).
2. `track-state post-loop-status "<td>"` is NOT required — `post-loop-step` reads
   the gates itself. Skip straight to §2.0.

## 2.0 THE LOOP

```bash
track-state post-loop-step "<td>"
```

Read `action` and do **only** that:

| action | you do |
|---|---|
| `deferred_ask` | `AskUserQuestion(decision.question, decision.header, decision.options)`. Run `decision.commands[<chosen label>]` **verbatim** (one shell-safe line each). Then → §2.0. |
| `finalize` | The finalize already ran in code. Run the envelope's `post` lines **verbatim** (sync-plan + registry-update + commit). Then → §2.0. |
| `dispatch` | **Dispatch `conductor:<agent>`**, prompt the fenced ``prompt`` field **verbatim** (pre-assembled — do not edit). Then run `post` per the **`post` rule** below. Then → §2.0. |
| `dispatch_advisory` | Same as `dispatch` (wiki-differ is advisory — surface its `STALE`/`MOVED`/`UNCOVERED` counts and recommend `/conductor:wiki-doctor diff` if non-zero). Then run `post` per the rule. Then → §2.0. |
| `digest` | Announce the `digest` block to the user (what shipped / outcome / read-this-first), then run `post` per the rule. Then → §2.0. |
| `archive_ask` | `AskUserQuestion(decision.question, decision.header, decision.options)`. Run `decision.commands[<chosen label>]` **verbatim**. If `decision.next[<chosen label>] == "HALT"` → STOP. Else → §2.0. |
| `halt` | Announce the `incomplete` list (finalize refused false completion) → STOP. |
| `done` | Post-loop complete → STOP. |
| `error` | Announce the error → STOP. |

### The `post` rule (gate-advance bookkeeping)

Most leaves carry a `post` field — deterministic bash lines that MERGE the gate's
sidecar marker so the next `post-loop-step` call advances past it. Run `post`
**verbatim** (one shell-safe line each) UNLESS:

- the leaf was a `dispatch`/`dispatch_advisory` whose agent returned a RESULT-block
  with `STATUS: FAILURE`, **AND**
- the envelope's `post_on` is **not** `"always"`.

So: the §7.0 code-reviewer leaf (`post_on` omitted → `"non_failure"`) skips `post`
on a failed review (no real review ran → don't stamp the range). The §6.0 advisory,
§6.5 lint, and §7.5 digest leaves set `post_on: "always"` — they advance on any
return (advisory/lint are non-blocking; digest has no agent). When in doubt, run
`post`.

## 3.0 CONTEXT-BUDGET YIELD

The post-loop is long-running (doc-sync alone is 2 sequenced agents + review).
If context runs low: finish the in-flight `dispatch` to a terminal state first
(the agent returned AND, when present, `post` has run), then stop with exactly:

`"⏸️ Conductor post-loop checkpoint — state committed. Re-invoke /conductor:post-loop-step to resume (the spine gates skip completed phases automatically)."`

**NEVER stop between a `dispatch` agent returning and running its `post`** — for
the code-reviewer leaf that loses the reviewed-range stamp and forces an
expensive re-review. Re-entry is automatic: the sidecar's `reviewed_range`
equality and the `deferred_resolved` / doc-sync-commit / advisory / lint / digest
gates make `post-loop-step` pick up exactly where it stopped.

---

**Spike status:** the spine (`deferred_ask` / `finalize` / `dispatch` /
`dispatch_advisory` / `digest` / `archive_ask` / `done` / `halt` / `error`) is
fully code-driven and tested, covering finalize → doc-sync (Phase 1+2) → advisory
→ lint → review+stamp → digest → archive. See `conductor/design/rail-b-step.md`
for the action contract.
