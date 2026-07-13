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
| `dispatch_batch` | **Fire `conductor:<member.agent>` for each entry in `wave`, in ONE message (parallel Agent calls)**, prompting each member's ``prompt`` field **verbatim** (pre-assembled — ac-tracer + test-runner). Then read the two RESULT blocks and **transcribe** them to the spine: from ac-tracer's `---AC TRACE RESULT---` take `VERDICT` (+ `GATE` if `FAILED`, `N_UNGROUNDED` if `warn`); from test-runner's `---L1 VERIFY RESULT---` take `STATUS` + `COMMAND`. Run **`track-state phase-verdict "<td>" --ac-verdict <V> [--ac-gate <G>] [--ac-n-ungrounded <N>] --l1-status <S> --l1-command "<CMD>"`** (this owns the §3.2 parse+assemble in code). Then → §2.0. |
| `dispatch_phase_checker` | **Dispatch `conductor:phase-checker`**, prompt the fenced ``prompt`` field **verbatim** (pre-assembled from the fanned verdicts). After it returns, read its `---CHECKPOINT RESULT---`: `STATUS` (`PASSED`/`FAILED`), `CHECKPOINT_SHA` (if `PASSED`), `FAILURE_REASON` (if `FAILED`). Run **`track-state phase-checkpoint-review "<td>" --status <STATUS> [--sha <SHA>] [--reason "<R>"]`** — this owns the §3.7 stamp/halt in code. `PASSED` → §2.0. `FAILED` → announce the reason and **STOP** (an AC-trace authoring defect needs spec/plan edits, not a retry; re-invoke after the fix re-runs the phase). |
| `dispatch_skip_analyst` | **Dispatch `conductor:skip-analyst`**, prompt the fenced ``prompt`` field **verbatim** (pre-assembled). After it returns, read its `---SKIP ANALYSIS---` JSON: `recommendation` (`skip`/`pause_and_escalate`/`retry_with_modification`), `reasoning`, `impact`, `can_skip`. Run **`track-state skip-analyst-verdict "<td>" --recommendation <R> [--reasoning "<text>"] [--impact "<text>"] [--can-skip <bool>]`** (the spine derives phase/task; this owns the §3.6 parse in code). Then → §2.0. |
| `dispatch_refuter` | **Dispatch `conductor:refuter`**, prompt the fenced ``prompt`` field **verbatim** (pre-assembled — the CLAIM embeds skip-analyst's reasoning). After it returns, read its `---REFUTATION RESULT---` `STATUS` (`SUSTAINED`/`REFUTED`/`FAILURE`). Run **`track-state skip-refute-review "<td>" --status <STATUS> [--reasoning "<text>"]`** (this owns the §3.6 refute→route in code). Then → §2.0. |
| `halt` | Announce the `reason` + `reasoning` (+ `impact` / `evidence` when present) to the user → **STOP**. A deliberate stop-for-human (skip-analyze `pause_and_escalate` / `retry_with_modification`, or a refute `SUSTAINED` overriding a skip). Edit the spec/plan/task per the reasoning, then re-invoke to resume. |
| `ask` | `AskUserQuestion(decision.question, decision.header, decision.options)`. Run `decision.commands[<chosen label>]` **verbatim** (one shell-safe line each). If `decision.next[<chosen label>] == "HALT"` → STOP. Else → §2.0. |
| `done` | Track finalized → hand off to the post-loop spine: `/conductor:post-loop-step "<td>"` (one-line skill invocation; no prose template read). |
| `error` | Announce the error → STOP. |

### Non-spine branch (B-min boundary — hand off to another spine)

`wave_active` needs a different spine, so `step` surfaces it as a named action
rather than collapsing it: hand the track to `/conductor:parallel` (the wave spine
owns it). That is the measured B-min boundary, not a gap.

## 3.0 STATE-LOCK INVARIANTS (resume safety)

The loop is long-running and runs uninterrupted.
Even so, a harness compaction can pause you mid-transaction — keep the state machine clean so resume is automatic:

**NEVER stop between a `dispatch` and the next `step` call** — that leaves a
stale `[~]` lock. `step` is state-driven, so re-entry is automatic on the next
invocation: a task still `in_progress` with no result and a Start HEAD re-dispatches
without burning a retry. Likewise never stop between a `dispatch_batch` returning
and its `phase-verdict` call, between a `dispatch_phase_checker` returning and its
`phase-checkpoint-review` call, between a `dispatch_skip_analyst` returning and its
`skip-analyst-verdict` call, or between a `dispatch_refuter` returning and its
`skip-refute-review` call — dropping the verdict the spine needs forces an expensive
re-fan / re-synth / re-analyze on resume. (If you do, `step` still recovers: with no
verdict marker it re-fans / re-analyzes; a `synth_pending` checkpoint marker
re-dispatches the synthesizer; an `analyzed` skip marker re-routes from there.)

---

**Spike status:** the spine (`dispatch` / `dispatch_batch` / `dispatch_phase_checker` /
`dispatch_skip_analyst` / `dispatch_refuter` / `ask` / `halt` / `done` / `error`) is
fully code-driven and tested — the phase-checkpoint handshake (§3.2/§3.7) AND the
skip_analyze handshake (§3.6: skip-analyst → refute → route) both run in code via
their transcribe commands, not teleoperator prose. The verdict-on-disk gate is closed.
The only remaining non-spine *routing* branch is `wave_active` (`/conductor:parallel`
— a different spine).

**Refactor mechanism in B-min:** the mechanical **Step 5** tier is active on
`step`-dispatched tasks — it is inline in `task-executor` §4.0, so the executor runs
it regardless of which spine dispatched it. The opt-in post-SUCCESS seams (`[Review]`
§3.6b self-review, `[Refactor]` §3.6c tactical refactorer) are NOT in the spine:
`step` routes SUCCESS straight to the next leaf, so they stay Rail A prose — deferred
B-full graduations (model-judgment passes that benefit less from determinism). See
`${CLAUDE_PLUGIN_ROOT}/conductor/design/rail-b-step.md` for the action contract and
the B-full options.
