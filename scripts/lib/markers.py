"""Tolerant JSON-marker I/O triplet — read/write/clear for the per-track
verdict-on-disk marker families (phase-checkpoint, skip-analysis, failure
analysis, brief/new-track resume, post-loop sidecar, …).

Single home for bodies that were previously cloned per family (dispatch,
new_track, brief, on-brief-grill-tripwire, misc, post-loop): every family keeps
its own named path builder; only the tolerant bodies live here. Lives in lib
(rather than track_state.helpers) so HOOKS import it cheaply — importing
track_state pulls the whole state-machine chain (~80ms) that a hot hook must
not pay per call.
"""
import json


def json_marker_read(path):
    """Tolerant reader: the marker dict, or ``None`` on missing/corrupt.

    ``None`` always means "treat as absent" so routing branches on the marker
    without existence checks and a half-written file never crashes the spine.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (ValueError, OSError):
        pass
    return None


def json_marker_write(path, data):
    """Write the whole marker dict; ``parents=True`` ensures ``.conductor/``
    (and any still-missing track dir) exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def json_marker_clear(path):
    """Delete the marker; idempotent (a missing file is a no-op success)."""
    if path.exists():
        path.unlink()
