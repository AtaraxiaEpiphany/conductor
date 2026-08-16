"""Shared grill-counter vocabulary for the brief tripwire pair.

The counter sidecar ``brief-grill-counters.json`` (under ``get_data_dir()``,
project-scoped, gitignored) counts per-track ``AskUserQuestion`` turns while a
brief's grill is in progress. Two consumers share this module so the file
format and its lifecycle are single-homed:

- ``scripts/on-brief-grill-tripwire.py`` — bumps on ``AskUserQuestion``,
  reads on the ``Write``/``Edit`` gate. Best-effort like the rest of the
  guard: a counter glitch never blocks a legitimate write.
- ``track_state.brief.cmd_brief_finalize`` — clears the counter when a brief
  run finalizes, so a later brief for the same track_id starts at a fresh
  grill budget.

Entries are ``track_id -> {"count": int, "ts": epoch}``. Stale entries (older
than ``BRIEF_COUNTER_TTL``) are reaped on every write, and legacy plain-int
entries (pre-schema) are treated as absent and reaped — mirroring
``lib.recovery``'s session counters. Without the clear + reap, a stale high
count could pre-satisfy the grill floor for a reused track_id — a silent
grill bypass.
"""
import json
import time

from .env import get_data_dir

BRIEF_COUNTER_FILE = "brief-grill-counters.json"
# A grill is a single sitting; cross-day counts are stale by definition. The
# finalize-clear is the primary hygiene — the TTL is defense-in-depth for a
# run that never finalized (crashed mid-grill, track_id later reused).
BRIEF_COUNTER_TTL = 86400  # seconds


def counter_path():
    return get_data_dir() / BRIEF_COUNTER_FILE


def read_counters():
    """Tolerant read: dict-of-dict-entries only. Missing/corrupt file or
    legacy plain-int values are dropped (a legacy high count is exactly the
    stale pre-satisfaction this module exists to prevent)."""
    path = counter_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (ValueError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def read_count(data, track_id):
    """The track's recorded count from a ``read_counters`` dict — TTL-aware:
    an entry older than ``BRIEF_COUNTER_TTL`` reads as 0 (a stale count must
    not satisfy a later brief's grill floor, even before any bump reaps it)."""
    entry = data.get(track_id)
    if not isinstance(entry, dict):
        return 0
    try:
        if time.time() - float(entry["ts"]) > BRIEF_COUNTER_TTL:
            return 0
    except (KeyError, TypeError, ValueError):
        return 0
    try:
        return int(entry.get("count") or 0)
    except (TypeError, ValueError):
        return 0


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def bump_counter(track_id):
    """Increment the AskUserQuestion counter for a track, reaping stale
    entries on the write. Best-effort, always returns the new count (>= 1) so
    the caller never blocks on a write glitch."""
    if not track_id:
        return 1
    try:
        path = counter_path()
        data = read_counters()
        now = time.time()
        for tid in list(data):
            ts = data[tid].get("ts")
            if not isinstance(ts, (int, float)) or now - ts > BRIEF_COUNTER_TTL:
                del data[tid]
        count = read_count(data, track_id) + 1
        data[track_id] = {"count": count, "ts": now}
        _write(path, data)
        return count
    except Exception:
        return 1


def clear_counter(track_id):
    """Drop the counter once the brief is finalized so a later brief for the
    same track_id starts at a fresh grill budget. Best-effort."""
    if not track_id:
        return
    try:
        path = counter_path()
        data = read_counters()
        if track_id in data:
            del data[track_id]
            _write(path, data)
    except Exception:
        pass
