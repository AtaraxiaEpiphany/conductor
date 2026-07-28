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
from pathlib import PurePath
from typing import Optional
import re

from lib.env import get_data_dir
from lib.logging import init_logging, log_entry

# Single shared log file across all three hooks. init_logging derives a path
# from script_name; passing this constant makes every caller append to one
# file so the lifecycle can be joined by grep. This is the join key.
_LIFECYCLE_LOG_NAME = "dispatch-lifecycle"

# Parser for the ``key=value`` suffix ``emit`` writes. The lifecycle schema is
# space-delimited (values never contain spaces), so each value runs to the next
# whitespace. Single source for the regex so a format change to ``emit`` needs
# no mirror edit in the readers (logs_read.py, detect-concurrent-relapse.py).
KV_RE = re.compile(r"(\w+)=(\S*)")


def parse_kv(suffix: str) -> dict:
    """Parse a ``key=value key=value`` run into a dict (last write wins)."""
    return {k: v for k, v in KV_RE.findall(suffix)}



def session_token(input_data: Optional[dict], fallback: str = "") -> str:
    """Resolve a stable session token from a hook input payload.

    The join key for ``dispatch-lifecycle.log``. Three hooks write to that log
    — ``on-dispatch-dedupe`` (PreToolUse: ``probe``), ``on-subagent-start``
    (``start``), ``on-subagent-stop`` (``stop``) — and a grep must be able to
    group a single dispatch's events together. They can only be grouped if all
    three agree on the session value.

    The problem this solves
    -----------------------
    All three hooks read ``input_data["session_id"]`` — but PreToolUse, in some
    Claude Code versions, delivers that field empty while SubagentStart/Stop
    populate it. The result (seen in real captures) is ``probe session=`` next
    to ``start session=<uuid>`` for the *same* dispatch — a dead join key, which
    makes a captured relapse impossible to disambiguate
    (``start…start`` vs ``start…stop…start`` vs no-probe).

    **A second, harder capture.** Real-session captures (``.data/logs/dispatch-
    lifecycle.log``) show BOTH ``session_id`` AND ``transcript_path`` empty on
    every event — rendering ``session=`` / ``session=-`` / garbage for the whole
    trail, so NO relapse in those sessions was ever classifiable. The two
    documented fields are simply absent on these payloads.

    Fallback chain (first non-empty wins; never raises):
    1. ``session_id`` from the payload, if present and non-empty.
    2. The session UUID parsed from ``transcript_path``
       (``.../projects/<proj>/<sessionId>.jsonl``) — a documented common input
       field present on every hook event, encoding the session UUID as the
       trailing filename stem.
    3. ``fallback`` — the caller's resolved ``track_dir`` / ``cwd``. Conductor
       runs one track per session and locks at most one ``in_progress`` task,
       so the track dir is present on every real dispatch and is stable across
       ``probe``/``start``/``stop`` for the same task. It is NOT a session id,
       but it is a *join key* — which is all the grep needs. Each caller already
       resolves it, so passing it through is one argument.
    4. ``"-"`` if none resolve (matches the ``-``-on-None convention every other
       index field uses, so a missing resolution is visible, not silent).
    """
    try:
        if isinstance(input_data, dict):
            sid = (input_data.get("session_id") or "").strip()
            if sid:
                return sid
            tp = (input_data.get("transcript_path") or "").strip()
            if tp:
                stem = PurePath(tp).stem
                if stem:
                    return stem
        if fallback:
            return fallback
    except Exception:
        pass
    return "-"


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
    gen: str = "-",
) -> None:
    """Append one structured dispatch-lifecycle event to the shared log.

    All fields are rendered space-delimited ``key=value`` so the whole trail is
    one ``grep dispatch-lifecycle`` + an optional ``grep 'phase=1 task=1'``
    filter. ``phase``/``task``/``subtask`` render as ``-`` when unknown (no
    locked task resolved) so a missing resolution is visible, not silent.

    ``gen`` is the dispatch generation from the inflight marker. It
    disambiguates the relapse shapes once the join key is reliable: a genuine
    concurrent double-spawn shows the SAME gen on two probes (one dispatch, two
    spawns); a spine re-dispatch shows a HIGHER gen on the second probe (a fresh
    prepare bumped it). Without gen those two look identical.

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
            f"decision={decision} head={head} had_result={had_result} gen={gen}"
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
