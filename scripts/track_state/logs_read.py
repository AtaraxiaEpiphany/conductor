"""Read-only views over Conductor's dispatch telemetry logs.

The problem this solves
-----------------------
Conductor already records a full dispatch lifecycle — ``probe`` (the dedupe
hook) / ``start`` (SubagentStart) / ``stop`` (SubagentStop) in
``dispatch-lifecycle.log``, plus per-stop outcomes in ``result-recovery.log``
and every recovery fire in ``subagent-failures.log``. But there was no way to
*read* that trail except raw ``grep`` — and worse, the data dir can resolve to
one of two locations (project ``.conductor/`` vs the shared plugin ``.data``),
so users routinely "couldn't see the file."

Two read-only commands close that gap, both pure-stdlib and import-light
(mirroring ``scripts/git-notes-query.py``):

- ``cmd_log_path`` — prints which data/logs dir resolved, by which tier, and
  lists the log files present + their size/mtime. Ends "where did the file
  go?".
- ``cmd_subagent_log`` — joins the lifecycle + recovery logs into one human
  timeline (probe → start → stop → recovery), optionally filtered to one
  ``(phase, task)``.

Neither command writes anything. They are the *view layer* over existing
instrumentation — no new logger, no new channel (a parallel channel would
split the join key the lifecycle trail depends on).
"""

import os
import re
from pathlib import Path
from typing import Optional

from lib.env import resolve_data_dir, get_logs_dir
from lib.dispatch_lifecycle import parse_kv


# ─────────────────────────────────────────────────────────────────────────────
# log-path: where do my logs live?
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_tier() -> tuple[Path, str]:
    """The data dir + which tier fired. Delegates to ``lib.env.resolve_data_dir``.

    Returns ``(data_dir, tier_label)`` so ``log-path`` reports the tier that
    ACTUALLY fired, not a guess. The tier ladder lives in one place
    (``resolve_data_dir``); this is a thin alias for the call sites below.
    """
    return resolve_data_dir()


# The logs worth surfacing. The lifecycle/recovery trio are the load-bearing
# ones for diagnosing "subagent didn't return a result"; the per-hook debug
# logs are listed for completeness.
_LOG_FILES = [
    "dispatch-lifecycle.log",
    "result-recovery.log",
    "subagent-failures.log",
    "on-subagent-stop.log",
    "on-subagent-start.log",
    "on-dispatch-dedupe.log",
    "override-audit.log",
]


def cmd_log_path(track_dir=None) -> int:
    """Print the resolved data/logs dir, the tier that fired, and file inventory.

    Read-only; returns an exit code (0 always — advisory).
    """
    data_dir, tier = _resolve_tier()
    logs_dir = get_logs_dir()

    print(f"data_dir:   {data_dir}")
    print(f"            resolved via: {tier}")
    env_notes = []
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        env_notes.append(f"CLAUDE_PLUGIN_DATA={os.environ['CLAUDE_PLUGIN_DATA']}")
    else:
        env_notes.append("CLAUDE_PLUGIN_DATA unset")
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        env_notes.append(f"CLAUDE_PROJECT_DIR={os.environ['CLAUDE_PROJECT_DIR']}")
    else:
        env_notes.append("CLAUDE_PROJECT_DIR unset")
    print(f"            ({'; '.join(env_notes)})")
    print(f"logs_dir:   {logs_dir}")
    print()
    print("log files:")
    any_present = False
    for name in _LOG_FILES:
        path = logs_dir / name
        if path.exists():
            any_present = True
            st = path.stat()
            size = _human_size(st.st_size)
            mtime = _mtime_str(st.st_mtime)
            print(f"  {name:<28s} {size:>8s}   {mtime}")
    if not any_present:
        print("  (none yet — no lifecycle events have fired for this project)")
    print()
    print("Tip: 'track-state subagent-log' prints the dispatch timeline from these files.")
    return 0


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _mtime_str(ts: float) -> str:
    try:
        import time
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return "?"


# ─────────────────────────────────────────────────────────────────────────────
# subagent-log: a dispatch timeline
# ─────────────────────────────────────────────────────────────────────────────

# dispatch-lifecycle.log lines look like:
#   2026-07-22T04:05:02.066435+00:00 [INFO] dispatch_lifecycle event=stop session=... agent=task-executor phase=1 task=1 ...
# result-recovery.log lines look like:
#   2026-07-22T04:05:02.357876+00:00 [INFO] session=... agent=task-executor outcome=recovered reason=no_fresh_result
_TS_RE = re.compile(r"^(\S+) \[\w+\]\s+(.*)$")


def _parse_kv_line(line: str) -> Optional[dict]:
    """Parse a ``key=value`` telemetry line into a dict, or ``None``.

    Drops the timestamp + level prefix, then extracts every ``key=value``
    token. ``value`` is taken verbatim up to the next whitespace (the lifecycle
    schema is space-delimited; values never contain spaces).
    """
    m = _TS_RE.match(line)
    body = m.group(2) if m else line
    if not body.startswith("dispatch_lifecycle") and "outcome=" not in body and "agent=" not in body:
        return None
    kv = parse_kv(body)
    if not kv:
        return None
    if m:
        kv["_ts"] = m.group(1)
    return kv


def _ts_time(ts: Optional[str]) -> str:
    """Render a log timestamp as ``HH:MM:SS`` for the timeline, or ``--:--:--``."""
    if not ts:
        return "--:--:--"
    # iso8601 with offset: 2026-07-22T04:05:02.066435+00:00
    try:
        return ts[11:19]  # HH:MM:SS slice
    except Exception:
        return "--:--:--"


def cmd_subagent_log(track_dir=None, phase=None, task=None) -> int:
    """Print the dispatch timeline from the lifecycle + recovery logs.

    Groups events by ``(phase, task)`` (falling back to the agent when indices
    are ``-``), chronologically. Optional ``phase``/``task`` filter to one task.
    Read-only; returns 0.
    """
    logs_dir = get_logs_dir()
    lifecycle_path = logs_dir / "dispatch-lifecycle.log"
    recovery_path = logs_dir / "result-recovery.log"

    if not lifecycle_path.exists():
        data_dir, tier = _resolve_tier()
        print(f"No dispatch-lifecycle.log at {lifecycle_path}")
        print(f"(data_dir resolved via: {tier})")
        print("Run 'track-state log-path' to see where logs are being written.")
        return 0

    # (1) parse lifecycle events.
    events: list[dict] = []
    for raw in lifecycle_path.read_text(encoding="utf-8", errors="replace").splitlines():
        kv = _parse_kv_line(raw)
        if not kv:
            continue
        event = kv.get("event", "")
        if event not in ("probe", "start", "stop", "re-dispatch"):
            continue
        events.append(kv)

    if not events:
        print(f"(no probe/start/stop events in {lifecycle_path})")
        return 0

    # (2) build an agent-keyed queue of recovery outcomes to attach to stops.
    # Both logs are append-ordered and written by the same hook on the same
    # stop, so the nearest same-agent recovery line is the matching outcome.
    recoveries: dict[str, list[dict]] = {}
    if recovery_path.exists():
        for raw in recovery_path.read_text(encoding="utf-8", errors="replace").splitlines():
            kv = _parse_kv_line(raw)
            if not kv or "outcome" not in kv:
                continue
            agent = kv.get("agent", "")
            if agent:
                recoveries.setdefault(agent, []).append(kv)

    def _pop_recovery(agent: str) -> Optional[str]:
        """Nearest unconsumed outcome for ``agent``, or ``None``."""
        q = recoveries.get(agent)
        if not q:
            return None
        rec = q.pop(0)
        out = rec.get("outcome", "?")
        reason = rec.get("reason", "")
        return f"{out}" + (f" ({reason})" if reason else "")

    # (3) optional filter.
    filt = None
    if phase is not None or task is not None:
        filt = (
            str(phase) if phase is not None else None,
            str(task) if task is not None else None,
        )

    # (4) group + print.
    # Group key: (phase, task) when both known, else a synthetic "—.<agent>" so
    # un-indexed events (phase=-) still surface together by agent.
    def _group_key(kv: dict) -> tuple:
        p, t = kv.get("phase", "-"), kv.get("task", "-")
        if p != "-" and t != "-":
            return (f"P{p}.T{t}", p, t)
        return (f"—.{kv.get('agent', '?')}", "-", "-")

    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for kv in events:
        if filt is not None:
            p, t = kv.get("phase", "-"), kv.get("task", "-")
            if filt[0] is not None and p != filt[0]:
                continue
            if filt[1] is not None and t != filt[1]:
                continue
        gk = _group_key(kv)
        if gk not in groups:
            groups[gk] = []
            order.append(gk)
        groups[gk].append(kv)

    if not order:
        print(f"(no events match phase={phase} task={task})")
        return 0

    for gk in order:
        label, _, _ = gk
        # Agent label from the first event in the group.
        agent = groups[gk][0].get("agent", "?")
        print(f"{label}  {agent}")
        for kv in groups[gk]:
            ts = _ts_time(kv.get("_ts"))
            event = kv.get("event", "?")
            gen = kv.get("gen", "-")
            tail = ""
            if event == "probe":
                tail = f"gen={gen}" if gen != "-" else ""
            elif event == "start":
                tail = f"gen={gen}" if gen != "-" else ""
            elif event == "stop":
                had = kv.get("had_result", "-")
                outcome = _pop_recovery(kv.get("agent", ""))
                if had == "1":
                    tail = "had_result=1  → ok  ✓"
                elif had == "0":
                    tail = f"had_result=0  → {outcome or 'recovered'}"
                else:
                    tail = f"had_result={had}  → {outcome or '-'}"
            elif event == "re-dispatch":
                tail = f"gen={gen}" if gen != "-" else ""
            tail = f"  {tail}" if tail else ""
            print(f"  {ts}  {event:<11s}{tail}")
        print()

    return 0
