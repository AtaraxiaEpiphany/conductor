"""Shared result.json freshness probe.

Single source of truth for the "did this subagent leave a fresh result.json?"
check used by both:

- the SubagentStop guard (``on-subagent-stop.py``) — the single completion
  signal for result-file agents (task-executor, explorer); absence triggers a
  recovery turn.
- the PostToolUse output filter (``filter-subagent-output.py``) — lets it treat
  a missing ``---RESULT---`` block as OK when a fresh result.json was written.

Centralizing this here means the path list and freshness window have one
definition; the SubagentStop guard no longer regex-scans prose (the source of
the old ``[:2000]`` truncation bug and the ``SAFE_CONTEXT`` false-positive
suppression).
"""
import time
from pathlib import Path

# Generous enough for long-running agents, narrow enough to reject stale files
# left by crashed sessions in other tracks.
RESULT_FRESHNESS_SECONDS = 180


def is_fresh(path: Path, threshold: float) -> bool:
    """True if ``path`` exists and was modified at/after ``threshold`` (epoch s)."""
    try:
        return path.stat().st_mtime >= threshold
    except OSError:
        return False


def fresh_result_exists(cwd: str, seconds: int = RESULT_FRESHNESS_SECONDS) -> bool:
    """True if a result.json was freshly written (within ``seconds``) under ``cwd``.

    Checks ``.conductor/result.json`` directly first (most common path), then
    ``conductor/tracks/*/.conductor/result.json``. Short-circuits on the first
    fresh hit; stale files never match.
    """
    threshold = time.time() - seconds
    try:
        base = Path(cwd)
        if is_fresh(base / ".conductor" / "result.json", threshold):
            return True
        for p in base.glob("conductor/tracks/*/.conductor/result.json"):
            if is_fresh(p, threshold):
                return True
    except (TypeError, ValueError, OSError):
        pass
    return False
