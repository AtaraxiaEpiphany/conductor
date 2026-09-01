#!/usr/bin/env python3
"""PreToolUse:Agent dedupe hook — deny a second dispatch for an in-flight task.

The problem this solves
-----------------------
In the ``implement-step`` (Rail B-min) teleoperator spine, the orchestrator
sometimes dispatches **multiple ``task-executor`` subagents for the same locked
task**. The state machine is single-writer and correct (``prepare_dispatch``
locks one ``in_progress`` task), but the hole is the gap between ``step``
emitting ``action: dispatch`` and the agent returning: if the teleoperator
calls ``step`` again while the first task-executor is still running, ``cmd_step``
takes its re-dispatch branch and fires a *second* spawn for the same task. Two
agents then race on the same working tree and the same ``.conductor/result.json``.

The SKILL.md §3.0 "never stop between a dispatch and the next step" rule that
should prevent this is **prose a small-window model can ignore** — the same
class of gap the round tripwire (``on-pre-tool-tripwire.py``) was built to
close. This hook makes the single-writer invariant **deterministic**: it sees
the orchestrator's ``Agent`` tool call *before* the subagent spawns, reads the
inflight marker the **SubagentStart hook stamped at spawn**
(``on-subagent-start.py:_stamp_inflight`` → ``lib.dispatch_inflight.stamp``),
and ``permissionDecision: "deny"`` a second spawn for a task already in flight.

Marker semantics — "spawned", not "prepared"
--------------------------------------------
The marker is deliberately NOT stamped by ``prepare_dispatch``. Between
``step`` emitting ``action: dispatch`` and the Agent call the dispatch is
*prepared but not spawned* — a state a stateless PreToolUse hook cannot tell
apart from "agent running". A prepare-time stamp therefore made this guard deny
the FIRST spawn itself (the 2026-09-01 dispatch-deadlock incident: every
dispatch denied → dispatch-finalize → re-prepare → denied again, looping until
the retry budget died). With the spawn-time stamp every guarded state is one
where an agent demonstrably started; a spawn denied or pulled back before
SubagentStart leaves no marker, so a retry spawns clean. See
``lib/dispatch_inflight`` for the full record.

How it fires
------------
PreToolUse fires in the orchestrator's own tool loop. For an ``Agent`` dispatch,
``tool_name == "Agent"`` and ``tool_input.subagent_type`` names the target. This
hook filters to the single-writer-critical agents — the agent-roster registry's
``single_writers()`` (executor-class rows: the agents that *write* the working
tree for a locked task); read-only verifiers and lifecycle-owning agents are
left alone.

Resolution + in-flight test
---------------------------
- Resolve the locked ``in_progress`` task via ``lib.locked_task.resolve`` →
  ``(track_dir, phase, task, subtask)``. No locked task → allow.
- Read the inflight marker for that task. Missing → allow (no prior dispatch
  recorded; fresh or pre-this-change state).
- Marker present: a task is **in flight** iff ``git HEAD == marker.start_sha``
  AND no ``.conductor/result.json`` exists — the *same predicate* ``cmd_step``
  uses to decide finalize-vs-redispatch. In flight → ``deny``. Otherwise the
  marker is stale (HEAD advanced / a result landed) → allow + clear it.

Fail-open
---------
This hook must **never block productive work**. Any resolution, I/O, SHA, or
parsing error → allow + stderr warning. The marker read/write errors are also
swallowed in ``lib.dispatch_inflight``. A misbehaving guard is worse than none.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.locked_task import resolve as resolve_locked_task
from lib import dispatch_inflight as inflight
from lib import dispatch_lifecycle as lifecycle


# Single-writer membership is roster-driven: the agent-roster registry's
# ``single_writers()`` (rows with class executor, or an explicit single_writer
# override). Only these agents mutate the working tree for a locked task, so
# only they are single-writer-critical; verifiers, phase-checker, skip-analyst,
# failure-analyst and refuter are read-only or own their lifecycle → excluded.


def _single_writers():
    """The roster's single-writer set, or ``()`` when the roster is unimportable.

    Function-level import (hooks run with ``scripts/`` on ``sys.path``);
    ``()`` fail-opens to allow — this hook must never block productive work.
    """
    try:
        from track_state import agent_roster
        return agent_roster.single_writers()
    except Exception:
        return ()


def _emit_probe(input_data, subagent_type, phase, task, subtask,
                marker, in_flight, decision, head="-", track_dir=None, gen="-"):
    """Append a dispatch-lifecycle ``probe`` event.

    The probe proves the hook *fired* (the load-bearing signal: if no probe line
    appears during a known double-dispatch, the matcher/plumbing regressed and
    no guard logic matters). Best-effort — telemetry must never raise into a
    permission-decision path.

    ``track_dir`` is the join-key fallback: on real payloads both ``session_id``
    and ``transcript_path`` arrive empty, so ``session_token`` falls back to the
    track dir (present on every real dispatch; stable across probe/start/stop
    for the same task). Early probes fire before a locked task is resolved and
    pass ``None`` — they get ``session=-``, which is correct (no task to join).

    ``gen`` is the inflight marker's dispatch generation. Two probes with the
    SAME gen = a single dispatch spawned twice (concurrent relapse); a higher
    gen on the second = the spine re-dispatched (fresh prepare). That
    disambiguation is the whole point of the gen field.
    """
    try:
        lifecycle.emit(
            event="probe",
            session=lifecycle.session_token(input_data, fallback=track_dir or ""),
            agent=subagent_type or "",
            phase=phase, task=task, subtask=subtask,
            marker=marker, in_flight=in_flight,
            decision=decision, head=head, gen=gen,
        )
    except Exception:
        pass


def _head_sha(track_dir):
    """Short (7-char) HEAD SHA, matching ``git_ops._git_head_sha``'s format so
    the comparison against ``marker.start_sha`` is apples-to-apples. None on
    failure (fail-open)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, cwd=track_dir, timeout=5,
        )
        sha = result.stdout.strip()
        return sha if re.match(r"^[0-9a-f]{7}$", sha) else None
    except Exception:
        return None


def _result_exists(track_dir):
    try:
        return (Path(track_dir) / ".conductor" / "result.json").exists()
    except Exception:
        return False


def _canonical_roster_key(subagent_type):
    """The bare roster key for a (possibly namespaced) dispatch name, or ``None``.

    Single-writer membership is roster-keyed on bare names while Agent-tool
    dispatches name the agent plugin-namespaced (``conductor:task-executor``)
    when the plugin is installed — canonicalizing here keeps the dedupe guard
    live in installed-plugin projects (mirrors agent_roster.canonical_name,
    fail-open to ``None`` so an unimportable roster never crashes the guard).
    """
    try:
        from track_state import agent_roster
        return agent_roster.canonical_name(subagent_type)
    except Exception:
        return None


def main():
    input_data = read_hook_input()
    subagent_type = (input_data.get("tool_input") or {}).get("subagent_type", "")

    # Only the orchestrator's Agent dispatches are in scope. PreToolUse fires
    # inside subagents too (with agent_type set) — those inner tool calls are
    # not Agent-dispatches and must be left alone.
    if input_data.get("tool_name") != "Agent":
        write_hook_output(permission_decision="allow")
        return

    # Probe at the very top: proves the hook fired for an Agent dispatch. If no
    # probe line ever appears during a known double-dispatch, the matcher is
    # silently not matching — a plumbing regression no guard logic can fix.
    _emit_probe(input_data, subagent_type, None, None, None,
                marker="-", in_flight="-", decision="allow-not-write-or-early")

    if _canonical_roster_key(subagent_type) not in set(_single_writers()):
        write_hook_output(permission_decision="allow")
        return

    cwd = input_data.get("cwd") or str(Path.cwd())
    try:
        locked = resolve_locked_task(cwd)
    except Exception:
        locked = None
    if locked is None:
        _emit_probe(input_data, subagent_type, None, None, None,
                    marker="-", in_flight="0", decision="allow-no-locked-task")
        write_hook_output(permission_decision="allow")
        return

    track_dir, phase, task, subtask = locked

    try:
        marker = inflight.read(track_dir, phase, task, subtask)
    except Exception:
        # Corrupt/missing marker → treat as not-in-flight (fail-open).
        marker = None
    marker_present = "1" if marker is not None else "0"
    if marker is None:
        _emit_probe(input_data, subagent_type, phase, task, subtask,
                    marker="0", in_flight="0", decision="allow-no-marker",
                    track_dir=track_dir)
        write_hook_output(permission_decision="allow")
        return

    start_sha = marker.get("start_sha")
    head = _head_sha(track_dir)
    result_present = _result_exists(track_dir)
    try:
        gen = str(int(marker.get("gen", 1)))
    except (TypeError, ValueError):
        gen = "-"

    # In flight iff the Start commit is still HEAD (no work advanced past it)
    # AND no result.json was written. Same predicate as cmd_step's
    # finalize-vs-redispatch branch — the hook and spine agree on "still working".
    in_flight = bool(start_sha) and head == start_sha and not result_present

    if not in_flight:
        # HEAD advanced or a result landed → the prior dispatch finalized /
        # returned. The marker is stale; clear it so it can't misfire later.
        _emit_probe(input_data, subagent_type, phase, task, subtask,
                    marker=marker_present, in_flight="0", decision="allow-stale",
                    head=head or "-", track_dir=track_dir, gen=gen)
        try:
            inflight.clear(track_dir, phase, task, subtask)
        except Exception:
            pass
        write_hook_output(permission_decision="allow")
        return

    _emit_probe(input_data, subagent_type, phase, task, subtask,
                marker=marker_present, in_flight="1", decision="deny",
                head=head or "-", track_dir=track_dir, gen=gen)

    # A dispatch is already in flight for this task → deny the second spawn.
    sha_hint = (start_sha or "?")[:8]
    loc = f"P{phase}T{task}" + (f".S{subtask}" if subtask is not None else "")
    # IMPORTANT: do NOT point the model back at `track-state step` here. In this
    # exact state (in_progress + HEAD == start_sha + no result.json) `step`
    # takes its no-retry-burn branch and re-emits `action: dispatch`, so the
    # model would spawn again → this hook denies again → `step` again: an
    # infinite flailing loop that strands the task. The *terminating* recovery
    # is `dispatch-finalize`, which synthesizes a FAILURE verdict from the
    # locked-task state, advances the cursor, and clears this marker — breaking
    # the loop. (See _resolve_finalize_target in track_state/dispatch.py.)
    #
    # First branch of the reason: the agent may STILL BE RUNNING in this very
    # session (foreground subagent whose result simply hasn't landed yet, or a
    # background-mode spawn — CLAUDE_AUTO_BACKGROUND_TASKS auto-backgrounds
    # after ~2 min, CLAUDE_CODE_FORK_SUBAGENT forces it). Finalizing a live
    # agent burns a retry for work that is about to land; WAITING is then the
    # correct move. finalize is the exit only once the agent is gone.
    reason = (
        f"A {subagent_type} is already in flight for {loc} (Start {sha_hint} "
        f"still HEAD, no result.json). Do NOT dispatch again. Do NOT re-run "
        f"`track-state step` — in this state it hands you another `dispatch` "
        f"and you will loop here. If the {subagent_type} is still running in "
        f"this session (including auto-backgrounded — check running tasks "
        f"before assuming), WAIT for it to finish and process its result. "
        f"Only if the agent is gone (interrupted/lost session) run "
        f"`track-state dispatch-finalize \"{track_dir}\"` to synthesize a "
        f"FAILURE verdict from the locked task and advance the track; that "
        f"clears this guard."
    )
    print(f"⚠️  CONDUCTOR DEDUPE: denied duplicate {subagent_type} dispatch for "
          f"{loc} (Start {sha_hint} still HEAD, no result.json).",
          file=sys.stderr)
    write_hook_output(permission_decision="deny", permission_decision_reason=reason)


if __name__ == "__main__":
    main()
