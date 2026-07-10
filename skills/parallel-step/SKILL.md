---
name: parallel-step
description: Rail B-min wave loop — a teleoperator that runs `track-state wave-step` and relays exactly the leaf action it emits (fan out a batch / integrate one member / ask / done). Thin alternative to /conductor:parallel for small-window models.
when_to_use: Spike — drive a parallel track via the code-driven `wave-step` spine instead of the prose wave loop. Use to A/B a small-window model against /conductor:parallel.
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

# Conductor Parallel-Step — Wave Teleoperator (Rail B-min, SPIKE)

You are a **teleoperator**. You do NOT route, judge, or construct prompts —
`track-state wave-step` does all of that in code. Your entire job: run `wave-step`,
read `action`, do *exactly* what it says, then run `wave-step` again. Context budget
is precious; this skill body is deliberately tiny.

## 1.0 SETUP (once)

1. `track-state check "$ARGUMENTS"` — always exits 0; outcome is in `action`:
   - `proceed` → `<td>` = `td`; **print `announce`**; continue to step 2.
   - `ask` → `AskUserQuestion` over `candidates` (label = `track_id`), then re-run `track-state check "<chosen track_id>"`.
   - `halt` → print `message`; HALT.
2. `track-state recover "<td>"`. If `status == "new"` → `track-state start "<td>"` + commit.
3. If recover surfaces an interrupted wave (`wave_active`), just enter §2.0 — `wave-step` integrates in-flight members before anything else.

## 2.0 THE LOOP

```bash
track-state wave-step "<td>"
```

Read `action` and do **only** that:

| action | you do |
|---|---|
| `dispatch_batch` | **Dispatch `conductor:task-executor` for EVERY member of `wave` in ONE message** (concurrent Agent calls — that parallelism is the whole point). Each agent's prompt is that member's `prompt` field **verbatim** (pre-assembled — do not edit or re-fill any field). If `deferred` is non-empty, first announce `⚠️ Wave cap reached; deferring <P{p}.T{t} …>` for each (the cap is env-tunable via `CONDUCTOR_WAVE_SIZE`, default 2). Then → §2.0. |
| `wave_integrate` | Run exactly: `track-state wave-finalize "<td>" --phase <phase> --task <task>`. Then → §2.0. |
| `done` | Track finalized → hand off to the post-loop spine: `/conductor:post-loop-step "<td>"` (one-line skill invocation; no prose template read). |
| `error` | Announce the error → STOP. |

A single-member `dispatch_batch` with `is_resume: true` is a **re-dispatch of one interrupted member** — fire that one agent (the prompt is already pinned to the existing worktree). Do not finalize it; the next `wave-step` does.

### Non-spine branches (B-min boundary — hand off, then resume this loop)

| action | hand-off |
|---|---|
| `seam_review` | `/conductor:parallel` §4.15 — dispatch `conductor:code-reviewer` (read-only) over `revision_range` with `SCOPE=cross-member interaction defects at deps boundaries only`; write Critical/High to `{td}/.conductor/seam-findings.json`; dispatch `conductor:refuter` to re-examine each; surface survivors via `AskUserQuestion` (fix-now / accept-with-debt / block). Then resume §2.0. |
| `serial` | Run `track-state step "<td>"` ONCE and relay exactly what it emits (`dispatch` / `dispatch_batch` / `ask` / `done` — handle each per `implement-step` §2.0; a `dispatch_batch` fires the ac-tracer + test-runner fan-out, then `/conductor:implement` §3.2 synthesizes). Then → §2.0 — `wave-step` re-checks for waves (the serial task may have unlocked one). Announce `ineligible` reasons if non-empty. |
| `phase_checkpoint` | `/conductor:implement` §3.2 — fan out `ac-tracer` + `test-runner` in parallel, then `phase-checker` synthesizes. Then → §2.0. |
| `ask` | `AskUserQuestion(decision.question, decision.header, decision.options)`. Run `decision.commands[<chosen label>]` **verbatim** (one shell-safe line each). If `decision.next[<chosen label>] == "HALT"` → STOP. Else → §2.0. |
| `skip_analyze` | `/conductor:implement` §3.6 — `skip-analyst` → `refuter` refute → route. Then resume §2.0. |

## 3.0 CONTEXT-BUDGET YIELD

The yield unit is **one wave fully drained** — every in-flight member integrated to
a terminal `member_status`. If context runs low: finish integrating every in-flight
member (`wave_integrate` until the wave drains), then stop with exactly:

`"⏸️ Conductor wave checkpoint — wave drained, state committed. Re-invoke /conductor:parallel-step to resume (wave-step is state-driven; it picks up here)."`

**NEVER stop mid-wave** — between `dispatch_batch` and the last `wave_integrate`
every member's worktree is live and its branch unmerged. The no-retry-burn
discriminator protects an interrupted member (it re-dispatches without burning a
retry), but yielding at a drained boundary is always cleaner.

---

**Spike status:** the spine (`dispatch_batch` / `wave_integrate` / `ask` / `done` /
`error`) is fully code-driven and tested. `seam_review`, `serial`, and
`phase_checkpoint` deliberately defer to `/conductor:parallel` / `/conductor:implement`
prose — that is the measured B-min boundary, not a gap. See
`${CLAUDE_PLUGIN_ROOT}/conductor/design/rail-b-wave-step.md` for the action contract and the B-full options.
