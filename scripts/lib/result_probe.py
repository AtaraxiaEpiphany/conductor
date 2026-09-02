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

Invariant: result.json is a single-slot mailbox — exactly one content consumer
(dispatch-finalize). Parallel writers are legal only in consumer-free windows
(e.g. the pre-plan grounding fan-out); this probe reads presence/freshness
only, never per-agent attribution.
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


def fresh_result_exists(cwd: str, seconds: int = RESULT_FRESHNESS_SECONDS,
                        track_dir: str = None) -> bool:
    """True if a result.json was freshly written (within ``seconds``).

    With ``track_dir`` given, checks ONLY that track's
    ``.conductor/result.json`` — the track-scoped path. This avoids the
    cross-track false positive where a fresh result.json in track B satisfies a
    probe running for track A (the caller knows which track is locked; scope to
    it). ``on-subagent-stop`` resolves the locked track and passes it here.

    Without ``track_dir`` (default), falls back to the cwd-relative checks:
    ``.conductor/result.json`` directly first (most common path), then
    ``conductor/tracks/*/.conductor/result.json``. Short-circuits on the first
    fresh hit; stale files never match.
    """
    threshold = time.time() - seconds
    try:
        if track_dir is not None:
            return is_fresh(Path(track_dir) / ".conductor" / "result.json", threshold)
        base = Path(cwd)
        if is_fresh(base / ".conductor" / "result.json", threshold):
            return True
        for p in base.glob("conductor/tracks/*/.conductor/result.json"):
            if is_fresh(p, threshold):
                return True
    except (TypeError, ValueError, OSError):
        pass
    return False
