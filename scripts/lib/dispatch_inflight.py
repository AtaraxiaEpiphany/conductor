"""In-flight dispatch marker — the single-writer invariant, encoded on disk.

The problem this solves
-----------------------
The Rail B-min spine (``implement-step``) is a teleoperator that relays
``track-state step``'s leaf actions. The state machine locks exactly one
``in_progress`` task per track, so at most one writer should ever be working
it. The hole is the gap between ``step`` emitting ``action: dispatch`` and the
agent returning: if the teleoperator calls ``step`` a second time while the
first task-executor is still running (a Stop, a compaction, or an agent that
returned early without committing / writing ``result.json``), ``cmd_step``
re-enters the re-dispatch branch and **fires a second task-executor for the
same locked task** — two agents then race on the same working tree and the
same ``.conductor/result.json``.

This module is the on-disk marker that closes that window. ``prepare_dispatch``
writes it when a fresh dispatch is born; ``finalize_dispatch`` (and the stale-
result reap sites) clear it. The PreToolUse:Agent dedupe hook
(``on-dispatch-dedupe.py``) reads it and ``permissionDecision: "deny"`` a
second spawn for the same in-flight task.

Marker semantics — "in flight"
------------------------------
The marker alone does NOT mean in-flight. The hook treats a task as in-flight
when **all three** hold:

1. the marker file exists for ``(phase, task, subtask)``,
2. ``git HEAD == marker["start_sha"]`` (the Start commit hasn't been advanced
   past by real work), AND
3. no ``.conductor/result.json`` exists (no verdict was written).

This is deliberately the *same predicate* ``cmd_step`` already uses to decide
finalize-vs-redispatch, so the hook and the spine agree on what counts as "an
agent is still working." Anything else → the marker is stale and cleared.

Layout
------
Pure, import-light helpers (no ``emit``, no ``track_state`` import) so the hook
can import this without pulling the heavy dispatch graph. Mirrors the shape of
``lib/locked_task`` (shared between spine and hooks). Failures are tolerated:
readers return ``None`` on missing/corrupt; writers/clearers swallow ``OSError``
so a marker I/O error can never block a dispatch (fail-open at the hook).
"""
import json
from pathlib import Path


def marker_path(track_dir, phase, task, subtask=None):
    """Pure path to the inflight marker — deliberately no mkdir.

    Mirrors ``_phase_cp_marker_path`` / ``_skip_analysis_marker_path`` in the
    spine: path only, so a read of a never-dispatched task is a cheap
    ``.exists() == False`` with no side effects.
    """
    sub = f"-{subtask}" if subtask is not None else ""
    name = f".dispatch-inflight-{phase}-{task}{sub}.json"
    return Path(track_dir) / ".conductor" / name


def read(track_dir, phase, task, subtask=None):
    """Tolerant reader: the marker dict, or ``None`` on missing/corrupt.

    ``None`` always means "treat as not-in-flight" — the hook fail-opens to
    allow. Never raises.
    """
    path = marker_path(track_dir, phase, task, subtask)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (ValueError, OSError):
        pass
    return None


def read_gen(track_dir, phase, task, subtask=None):
    """Tolerant reader for the dispatch ``gen`` of an existing marker.

    Returns the stored ``gen`` (an int, defaulting to ``1`` for markers written
    before the gen field existed / when absent), or ``0`` when there is no
    marker. ``0`` is the sentinel the writer uses to mean "no prior generation
    — stamp 1" (``_dispatch_inflight_write`` reads this then bumps). Never
    raises.
    """
    data = read(track_dir, phase, task, subtask)
    if data is None:
        return 0
    try:
        gen = int(data.get("gen", 1))
    except (TypeError, ValueError):
        gen = 1
    return gen if gen >= 1 else 1


def write(track_dir, phase, task, subtask, start_sha, written_at_iso, gen=1):
    """Write the inflight marker. Swallows ``OSError`` (fail-open).

    ``start_sha`` is the commit HEAD is sitting on right after the Start commit
    (or the existing Start commit on a resume path) — the value the hook
    compares the live HEAD against. ``written_at_iso`` is passed in (not read
    here) so the spine owns the timestamp source.

    ``gen`` is the dispatch generation — a monotonic counter bumped by the spine
    on every write for ``(phase, task, subtask)`` so the dedupe hook's in-flight
    test can be gen-based (decoupled from git HEAD). Default ``1``; the spine
    computes ``prev_gen + 1`` before calling.
    """
    path = marker_path(track_dir, phase, task, subtask)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "phase": phase,
            "task": task,
            "subtask": subtask,
            "start_sha": start_sha,
            "written_at": written_at_iso,
            "gen": gen,
        }
        path.write_text(json.dumps(data, ensure_ascii=False))
    except OSError:
        pass


def clear(track_dir, phase, task, subtask=None):
    """Remove the inflight marker if present. Swallows ``OSError`` (fail-open)."""
    path = marker_path(track_dir, phase, task, subtask)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def clear_all(track_dir):
    """Remove EVERY inflight marker under this track's ``.conductor/``.

    Used at crash-recovery sites where the cursor is invalid (no active task /
    bad index) so we can't know which ``(phase, task, subtask)`` a stale marker
    belongs to. Conductor runs one track per session and locks at most one
    ``in_progress`` task, so within a single track dir at most one inflight
    marker is legitimate at a time — glob-clearing here is safe and prevents a
    crashed run from leaving the dedupe hook guarding a phantom dispatch.
    Swallows all errors (fail-open).
    """
    cdir = Path(track_dir) / ".conductor"
    try:
        for path in cdir.glob(".dispatch-inflight-*.json"):
            try:
                path.unlink()
            except OSError:
                pass
    except OSError:
        pass
