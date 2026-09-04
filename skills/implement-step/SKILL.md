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

You are a **teleoperator**. You do NOT route, judge, or construct prompts — `track-state step` does all of that in code. Your entire job: run `step`, read `action`, do *exactly* what it says, then run `step` again.

## 1.0 SETUP (once)

1. `track-state check "$ARGUMENTS"` — always exits 0; outcome is in `action`:
   - `proceed` → `<td>` = `td`; **print `announce`**; continue to step 2.
   - `ask` → `AskUserQuestion` over `candidates` (label = `track_id`), then re-run `track-state check "<chosen track_id>"`.
   - `halt` → print `message`; HALT.
2. `track-state recover "<td>"`. Then `track-state start "<td>"`.

## 2.0 THE LOOP

```bash
track-state step "<td>"
```

Read `action` and do **only** that:

| action | you do |
|---|---|
| `dispatch` | **Dispatch `conductor:<agent>`**, prompt the fenced ``prompt`` field **verbatim** (it is pre-assembled — do not edit or re-fill any field). Then → §2.0. |
| `dispatch_batch` | **Fire `conductor:<member.agent>` for each entry in `wave`, in ONE message (parallel Agent calls)**, prompting each member's ``prompt`` field **verbatim** (pre-assembled — the wave is the `ac-tracer` + `build-runner` + `test-runner` checkpoint fan-out, cheapest-first). Then read the RESULT blocks and **transcribe** them to the spine: from ac-tracer's `---AC TRACE RESULT---` take `VERDICT` (+ `GATE` if `FAILED`, `N_UNGROUNDED` if `warn`); from build-runner's `---BUILD VERIFY RESULT---` take `STATUS` + `COMMAND`; from test-runner's `---L1 VERIFY RESULT---` take `STATUS` + `COMMAND`. Run **`track-state phase-verdict "<td>" --ac-verdict <V> [--ac-gate <G>] [--ac-n-ungrounded <N>] --build-status <S> --build-command "<CMD>" --l1-status <S> --l1-command "<CMD>"`** (this owns the §3.2 parse+assemble in code). Omit the `--build-*`/`--l1-*` pairs on a code-free phase (build-runner + test-runner narrowed out of the wave). If the envelope carries `missing_verdicts`, a member's RESULT block was not transcribed — parse it and re-run; if it carries `fixes_applied`, run the emitted `bookkeeping` commit line before dispatching (phase-boundary auto-fixes leave `track-state.json` dirty). Then → §2.0. |
| `dispatch_phase_checker` | **Dispatch `conductor:phase-checker`**, prompt the fenced ``prompt`` field **verbatim** (pre-assembled from the fanned verdicts). After it returns, read its `---CHECKPOINT RESULT---`: `STATUS` (`PASSED`/`FAILED`), `CHECKPOINT_SHA` (if `PASSED`), `FAILURE_REASON` (if `FAILED`). Run **`track-state phase-checkpoint-review "<td>" --status <STATUS> [--sha <SHA>] [--reason "<R>"]`** — this owns the §3.7 stamp/route in code. If the envelope carries `fixes_applied`, run the emitted `bookkeeping` commit line before proceeding. `PASSED` → run **`track-state replan "<td>"`**: if `replan_due: true`, execute the phase-gate replanning pass per `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/phase-gate-replanning.md` (it always ends with `track-state replan "<td>" --ack`); then → §2.0. `replan_due: false` → §2.0 straight. `FAILED` → the spine owns the branch in code: if the output carries `routed_recovery: true` (an auto-routing track — `recovery_policy=auto` or continuous) → **§2.0** (the next `step` dispatches the phase failure-analyst, `dispatch_phase_failure_analyst`); otherwise (an ask-surface track) → announce the reason and **STOP** (byte-identical to before — re-invoke after editing spec/plan re-runs the phase). |
| `dispatch_phase_failure_analyst` | **Dispatch `conductor:failure-analyst`**, prompt the fenced ``prompt`` field **verbatim** (pre-assembled — `PHASE_INDEX` without `TASK_INDEX` puts the agent in PHASE mode). Fires when a phase checkpoint FAILED on an auto-routing track, before halting. After it returns, read its `---FAILURE ANALYSIS---` JSON: `category`, `recommendation` (`retry_modified`/`replan`/`escalate`), `root_cause`, `modification`, `what_was_done`, and (on `replan`) `ac_superseded`/`ac_prime_text`/`affected_tasks`. Run **`track-state phase-failure-analyst-verdict "<td>" --category <C> --recommendation <R> [--root-cause "<text>"] [--modification "<text>"] [--what-was-done "<text>"] [--ac-superseded <AC-N>] [--ac-prime-text "<text>"] [--affected-tasks "<a,b>"]`** (the spine derives the phase from the recovery marker; this owns the route in code — `retry_modified` reactivates the phase's tasks with the fix injected and loops to re-dispatch; `replan` with AC details → `ask` (Apply amendment runs `track-state amend-apply` verbatim / Edit manually runs `track-state amend-clear` / Halt); `replan` without AC details / `escalate` halt). Then → §2.0. |
| `dispatch_skip_analyst` | **Dispatch `conductor:skip-analyst`**, prompt the fenced ``prompt`` field **verbatim** (pre-assembled). After it returns, read its `---SKIP ANALYSIS---` JSON: `recommendation` (`skip`/`pause_and_escalate`/`retry_with_modification`), `reasoning`, `impact`, `can_skip`. Run **`track-state skip-analyst-verdict "<td>" --recommendation <R> [--reasoning "<text>"] [--impact "<text>"] [--can-skip <bool>]`** (the spine derives phase/task; this owns the §3.6 parse in code). Then → §2.0. |
| `dispatch_failure_analyst` | **Dispatch `conductor:failure-analyst`**, prompt the fenced ``prompt`` field **verbatim** (pre-assembled). Fires when a failure auto-routes (`recovery_policy=auto` or continuous): once before the final retry (so the last attempt is modified, not identical), and on a skip-analyst `retry_with_modification` hand-off. After it returns, read its `---FAILURE ANALYSIS---` JSON: `category`, `recommendation` (`retry_modified`/`replan`/`decompose`/`escalate`), `root_cause`, `modification`, `what_was_done`, and (on `replan`) `ac_superseded`/`ac_prime_text`/`affected_tasks`. Run **`track-state failure-analyst-verdict "<td>" --category <C> --recommendation <R> [--root-cause "<text>"] [--modification "<text>"] [--what-was-done "<text>"] [--ac-superseded <AC-N>] [--ac-prime-text "<text>"] [--affected-tasks "<a,b>"]`** (the spine derives phase/task; this owns the route in code — `retry_modified` re-dispatches task-executor with the modification injected; `decompose` → `ask` (Apply split runs `track-state split` verbatim / Skip original / Escalate); `replan` with the AC details → `ask` (Apply amendment runs `track-state amend-apply` verbatim / Edit manually runs `track-state amend-clear` / Halt); `replan` without / `escalate` halt). Then → §2.0. |
| `dispatch_refuter` | **Dispatch `conductor:refuter`**, prompt the fenced ``prompt`` field **verbatim** (pre-assembled — the CLAIM embeds skip-analyst's reasoning). After it returns, read its `---REFUTATION RESULT---` `STATUS` (`SUSTAINED`/`REFUTED`/`FAILURE`). Run **`track-state skip-refute-review "<td>" --status <STATUS> [--reasoning "<text>"]`** (this owns the §3.6 refute→route in code). Then → §2.0. |
| `halt` | Announce the `reason` + `reasoning` (+ `impact` / `evidence` / `recovery` when present) to the user → **STOP**. A deliberate stop-for-human (skip-analyze `pause_and_escalate` / `retry_with_modification`, a refute `SUSTAINED` overriding a skip, or failure-analyst `replan` without AC details/`escalate`). `decompose` and `replan`-with-AC-details do NOT halt — they arrive as an `ask` (see `dispatch_failure_analyst` above). When `recovery` is present it is the safe manual recipe — act on it (on `decompose`, preserve the original task's commit; do NOT revert), then re-invoke to resume. Otherwise edit the spec/plan/task per the reasoning, then re-invoke. |
| `ask` | `AskUserQuestion(decision.question, decision.header, decision.options)`. Run `decision.commands[<chosen label>]` **verbatim** (one shell-safe line each). If `decision.next[<chosen label>] == "HALT"` → STOP. Else → §2.0. |
| `done` | Track finalized → hand off to the post-loop spine: `/conductor:post-loop-step "<td>"` (one-line skill invocation; no prose template read). |
| `error` | Announce the error → STOP. |

### Absent result block (don't improvise)

If `conductor:task-executor` returns **no** `---TASK RESULT---` block, do **nothing but** run `track-state step "<td>"` again — the spine's re-dispatch/finalize branch owns recovery, and `dispatch-finalize` synthesizes a result from git state (committed code → SUCCESS; nothing → FAILURE + retry handoff). **Never read `spec.md`/`plan.md`/source, and never implement the task yourself.** A vanished result block is a model-judgment gap the `on-orchestrator-read-guard` hook now closes deterministically — business-file reads are denied while a task is in flight, so the only forward path is the spine.

### Non-spine branch (B-min boundary — hand off to another spine)

`wave_active` needs a different spine, so `step` surfaces it as a named action rather than collapsing it: hand the track to `/conductor:parallel` (the wave spine owns it). That is the measured B-min boundary, not a gap.
## 3.0 STATE-LOCK INVARIANTS (resume safety)

**NEVER stop between a `dispatch` and the next `step` call** (or between any
verdict-returning dispatch — `dispatch_batch`→`phase-verdict`,
`dispatch_phase_checker`→`phase-checkpoint-review`,
`dispatch_skip_analyst`→`skip-analyst-verdict`,
`dispatch_refuter`→`skip-refute-review`,
`dispatch_failure_analyst`→`failure-analyst-verdict`,
`dispatch_phase_failure_analyst`→`phase-failure-analyst-verdict`) — that leaves a stale lock or drops the
verdict the spine needs. Re-entry is still automatic: `step` re-fans / re-analyzes
with no verdict marker, and re-dispatches the synthesizer on a `synth_pending` marker.

---

**Scope:** the spine (`dispatch` / `dispatch_batch` / `dispatch_phase_checker` /
`dispatch_skip_analyst` / `dispatch_refuter` / `dispatch_failure_analyst` /
`dispatch_phase_failure_analyst` / `ask` / `halt` / `done` / `error`)
is fully code-driven. The only non-spine *routing* branch is `wave_active` (→
`/conductor:parallel`). The opt-in post-SUCCESS seams (`[Review]` self-review,
`[Refactor]` tactical refactorer) are NOT in the spine — `step` routes SUCCESS
straight to the next leaf, so they stay Rail A prose. Design contract + B-full
options: `${CLAUDE_PLUGIN_ROOT}/conductor/design/rail-b-step.md`.