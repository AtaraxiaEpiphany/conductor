#!/usr/bin/env python3
"""PreToolUse tripwire: force task-executor to respect its shutdown budget.

The problem this solves
-----------------------
``task-executor`` is instructed (``agents/task-executor.md`` §7.0) to stop
implementation work at ~38 tool-call rounds and spend its remaining turns
writing the two shutdown artifacts (handoff deviation log + ``result.json``
FAILURE block). That rule is **prose a small-window model can ignore** — and
when it ignores it, the agent overruns toward either the hard ``maxTurns`` cap
or a context-window overflow, both of which skip the shutdown artifacts and
leave the orchestrator with no result (the "task didn't return a structure
result or commit" symptom).

This hook makes the invariant **deterministic**: it counts task-executor's
PreToolUse rounds against the locked task and, at the threshold, injects an
``additionalContext`` directive ordering shutdown. The model cannot avoid the
message the way it can avoid prose buried in its system prompt.

How it fires
------------
PreToolUse (per the Claude Code hooks reference) fires **inside a subagent's
own tool loop**, with ``agent_type``/``agent_id`` added to the input when
running under ``--agent``. So this hook sees every tool call task-executor
makes — not just the orchestrator's dispatch call. It filters in-code on
``agent_type == "task-executor"``; all other agents / main-session calls are
a no-op (allow, no context).

Resolution + counting
---------------------
- Resolve the locked ``in_progress`` task via ``lib.locked_task.resolve`` →
  ``(track_dir, phase, task, subtask)``. No locked task → no-op.
- The round counter is a scratch file under the track's ``.conductor/``:
  ``.tripwire-<phase>-<task>-<subtask>.count``. It is **reset on dispatch**
  by ``on-subagent-start.py`` (SubagentStart fires once per dispatch), so a
  retry starts the count fresh at 0.
- On each PreToolUse inside task-executor, increment the counter. At
  ``TRIPWIRE_HARD`` rounds with no commit, inject the shutdown directive.

Fail-open
---------
This hook must **never block productive work**. Any resolution, I/O, or
counting error → emit allow + no context. The ``on-subagent-stop`` recovery
net (Layer 2) remains the backstop if this heuristic misses.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.locked_task import resolve as resolve_locked_task


# The round at which to force the shutdown directive. task-executor's prose
# tripwire is "~38 rounds"; we match it so code and prose agree. With maxTurns
# at 64 (after the 3.2 bump), 38 leaves ~26 shutdown rounds — deliberately early;
# the point is to trip before a context overflow, not proportionally to the cap.
TRIPWIRE_HARD = 38

_TARGET_AGENT = "task-executor"

# The shutdown directive. Echoes agents/task-executor.md §6/§7 so the model
# recognizes the channel it has been told to use for its shutdown artifacts.
_DIRECTIVE = (
    "⚠️ CONDUCTOR TRIPWIRE: you have crossed ~38 tool-call rounds without "
    "committing. STOP implementation work NOW. Spend your remaining turns on "
    "the two shutdown artifacts, in order: (1) handoff deviation log via "
    "`track-state append-handoff` (type=deviation), then (2) the result.json "
    "FAILURE block via `track-state write-result --status failure` with "
    "--summary/--failure-done/--failure-reason. This hands a rich ### Attempt "
    "record to a fresh retry BEFORE the context overflows. Tripping early is "
    "correct. Do not attempt more implementation."
)


def _count_file(track_dir: Path, phase, task, subtask) -> Path:
    """Scratch counter path under the track's .conductor/ (gitignored runtime)."""
    sub = f"-{subtask}" if subtask is not None else ""
    name = f".tripwire-{phase}-{task}{sub}.count"
    return Path(track_dir) / ".conductor" / name


def _read_count(path: Path) -> int:
    try:
        return int(path.read_text().strip() or "0")
    except (OSError, ValueError):
        return 0


def _bump_count(path: Path) -> int:
    """Increment and return the new count. Never raises (fail-open).

    ``.conductor/`` is created at track setup (``new_track.py``) and the counter
    is reset on every dispatch (``on-subagent-start``), so the dir exists by the
    time we bump — we only ``mkdir`` on the first touch (missing file), avoiding
    a redundant syscall on every PreToolUse round.
    """
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        n = _read_count(path) + 1
        path.write_text(str(n))
        return n
    except OSError:
        return -1


def main():
    input_data = read_hook_input()

    # Only task-executor is in scope. PreToolUse adds agent_type when running
    # inside a subagent; main-session calls have no agent_type → no-op.
    if input_data.get("agent_type") != _TARGET_AGENT:
        write_hook_output(permission_decision="allow")
        return

    cwd = input_data.get("cwd") or str(Path.cwd())
    try:
        locked = resolve_locked_task(cwd)
    except Exception:
        locked = None
    if locked is None:
        write_hook_output(permission_decision="allow")
        return

    track_dir, phase, task, subtask = locked
    count = _bump_count(_count_file(track_dir, phase, task, subtask))

    # -1 = I/O failure → fail-open. Below threshold → allow silently.
    if count < 0 or count < TRIPWIRE_HARD:
        write_hook_output(permission_decision="allow")
        return

    # At/over threshold: inject the shutdown directive but still allow the tool
    # call — never block. The directive repeats on every subsequent round until
    # the agent stops, which is the intended escalation.
    write_hook_output(
        permission_decision="allow",
        additional_context=_DIRECTIVE,
    )


if __name__ == "__main__":
    main()
