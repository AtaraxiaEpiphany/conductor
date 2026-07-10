---
name: implement-step
description: Rail B-min dispatch loop — a teleoperator that runs `track-state step` and relays exactly the leaf action it emits (dispatch one subagent / ask / done). Thin alternative to /conductor:implement for small-window models.
when_to_use: Spike — drive a track via the code-driven `step` spine instead of the prose dispatch loop. Use to A/B a small-window model against /conductor:implement.
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

# Conductor Implement-Step — Teleoperator (Rail B-min, SPIKE)

You are a **teleoperator**. You do NOT route, judge, or construct prompts —
`track-state step` does all of that in code. Your entire job: run `step`, read
`action`, do *exactly* what it says, then run `step` again. Context budget is
precious; this skill body is deliberately tiny.

## 1.0 SETUP (once)

1. `track-state check "$ARGUMENTS"` — always exits 0; outcome is in `action`:
   - `proceed` → `<td>` = `td`; **print `announce`**; continue to step 2.
   - `ask` → `AskUserQuestion` over `candidates` (label = `track_id`), then re-run `track-state check "<chosen track_id>"`.
   - `halt` → print `message`; HALT.
2. `track-state recover "<td>"`. If `status == "new"` → `track-state start "<td>"` + commit.

## 2.0 THE LOOP

```bash
track-state step "<td>"
```

Read `action` and do **only** that:

| action | you do |
|---|---|
| `dispatch` | **Dispatch `conductor:<agent>`**, prompt the fenced ``prompt`` field **verbatim** (it is pre-assembled — do not edit or re-fill any field). Then → §2.0. |
| `dispatch_batch` | **Fire `conductor:<member.agent>` for each entry in `wave`, in ONE message (parallel Agent calls)**, prompting each member's ``prompt`` field **verbatim** (pre-assembled — ac-tracer + test-runner). Collect both RESULT blocks, then switch to `/conductor:implement` §3.2 Step 2 (parse verdicts → dispatch `phase-checker`). Then resume this loop. |
| `ask` | `AskUserQuestion(decision.question, decision.header, decision.options)`. Run `decision.commands[<chosen label>]` **verbatim** (one shell-safe line each). If `decision.next[<chosen label>] == "HALT"` → STOP. Else → §2.0. |
| `done` | Track finalized → hand off to the post-loop spine: `/conductor:post-loop-step "<td>"` (one-line skill invocation; no prose template read). |
| `error` | Announce the error → STOP. |

### Non-spine branches (B-min boundary — hand off to `/conductor:implement`)

These need model judgment or another spine, so `step` surfaces them as named
actions rather than collapsing them. When you see one, switch to the matching
section of `/conductor:implement`:

| action | hand-off |
|---|---|
| `skip_analyze` | `/conductor:implement` §3.6 — `skip-analyst` → `refuter` refute → route. Then resume this loop. |
| `wave_active` | `/conductor:parallel` — the wave spine owns this track. |

## 3.0 CONTEXT-BUDGET YIELD

The loop is long-running. If context runs low (~6+ dispatches, or compaction
approaching): finish the in-flight `dispatch` to a terminal state first, then
stop with exactly:

`"⏸️ Conductor checkpoint — state committed. Re-invoke /conductor:implement-step to resume (step is state-driven; it picks up here)."`

**NEVER stop between a `dispatch` and the next `step` call** — that leaves a
stale `[~]` lock. `step` is state-driven, so re-entry is automatic on the next
invocation: a task still `in_progress` with no result and a Start HEAD re-dispatches
without burning a retry.

---

**Spike status:** the spine (`dispatch` / `dispatch_batch` / `ask` / `done` /
`error`) is fully code-driven and tested. The two non-spine branches
deliberately defer to `/conductor:implement` — that is the measured B-min
boundary, not a gap. See `${CLAUDE_PLUGIN_ROOT}/conductor/design/rail-b-step.md` for the action
contract and the B-full options.
