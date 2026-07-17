"""Shared dispatch-lifecycle telemetry line.

The problem this solves
-----------------------
The single-writer invariant (one agent owns a locked task at a time) is enforced
by the ``PreToolUse:Agent`` dedupe hook (``on-dispatch-dedupe.py``). When it
relapses — a subagent re-runs while a previous one is still in flight — three
distinct failure shapes look identical in the UI ("the same agent ran twice")
but have fixes in different modules:

- ``start`` … ``start`` (no ``stop`` between) → two agents truly concurrent →
  the dispatch guard.
- ``start`` … ``stop`` … ``start`` (short gap, stop had no result) → the first
  agent ended and the orchestrator re-derived → the finalize/reap contract.
- no ``probe`` line at all during a known double-dispatch → the dedupe hook
  isn't firing → the matcher/plumbing.

To tell them apart we need a replayable trail: one structured event on each
side of the lifecycle (``SubagentStart`` / ``SubagentStop``) plus a ``probe``
from the guard itself, all keyed identically by ``(phase, task, subtask)`` so
they can be joined by a single grep.

This module is that shared line. Every hook writes it to the SAME file
(``<data_dir>/logs/dispatch-lifecycle.log``) with the SAME schema, so the grep
works regardless of which hook emitted the event. It is deliberately
best-effort: telemetry must never raise, never block, and never perturb the
control flow of a hook that is in a permission-decision hot path (a hook that
crashed mid-decision could strand a task — far worse than a missing log line).
"""
from typing import Optional

from lib.env import get_data_dir
from lib.logging import init_logging, log_entry

# Single shared log file across all three hooks. init_logging derives a path
# from script_name; passing this constant makes every caller append to one
# file so the lifecycle can be joined by grep. This is the join key.
_LIFECYCLE_LOG_NAME = "dispatch-lifecycle"


def emit(
    *,
    event: str,
    session: str,
    agent: str,
    phase: Optional[int],
    task: Optional[int],
    subtask: Optional[int],
    marker: str = "-",
    in_flight: str = "-",
    decision: str = "-",
    head: str = "-",
    had_result: str = "-",
) -> None:
    """Append one structured dispatch-lifecycle event to the shared log.

    All fields are rendered space-delimited ``key=value`` so the whole trail is
    one ``grep dispatch-lifecycle`` + an optional ``grep 'phase=1 task=1'``
    filter. ``phase``/``task``/``subtask`` render as ``-`` when unknown (no
    locked task resolved) so a missing resolution is visible, not silent.

    Best-effort: swallows every error. Telemetry is advisory and must never
    raise into a hook's permission-decision path.
    """
    try:
        log_file = init_logging(_LIFECYCLE_LOG_NAME)
        p = phase if phase is not None else "-"
        t = task if task is not None else "-"
        s = subtask if subtask is not None else "-"
        line = (
            f"dispatch_lifecycle event={event} session={session} agent={agent} "
            f"phase={p} task={t} subtask={s} marker={marker} in_flight={in_flight} "
            f"decision={decision} head={head} had_result={had_result}"
        )
        log_entry(log_file, line)
    except Exception:
        # Never raise from telemetry. A broken log channel must not strand a
        # task or perturb a permission decision.
        pass


def fmt_idx(value) -> str:
    """Render a ``phase``/``task``/``subtask`` index as a compact ``-``-on-None.

    Convenience for callers that build the line fields inline.
    """
    return "-" if value is None else str(value)
