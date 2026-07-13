"""Resolve the active (locked, ``in_progress``) task from on-disk state.

Read-only helper shared by the SubagentStop recovery flow
(``on-subagent-stop.py``) and the result freshness probe
(``lib.result_probe``) — both need to know WHICH task a project currently has
locked, so they can scope a recovery-turn counter / a ``result.json`` freshness
check to that one task rather than globbing across every track.

A task is "locked" when its ``track-state.json``'s ``current_*_index`` point at
an ``in_progress`` unit (the indices ``dispatch-prepare``'s ``_do_lock`` sets).
The index resolution mirrors ``dispatch._synthesize_result_from_state`` so the
two agree on what counts as the active task.

Returns ``(track_dir, phase, task, subtask_or_None)`` or ``None`` when no track
has a locked ``in_progress`` task (e.g. fresh run, or all tracks idle). Callers
fall back to their pre-resolution behavior on ``None`` — this helper is strictly
a scoping aid and never raises on malformed/unreadable state.

The cursor→target primitive (``_cursor_target``) and track-scan iterator
(``_iter_track_states``) are the shared building blocks; the refactor-scope hook
(``pre-command-check.py``) reuses them gated on ``completed`` (the refactorer
runs after dispatch-finalize, so its task is terminal) rather than
``in_progress``.
"""
import json
from pathlib import Path


def _cursor_target(state, *, status="in_progress"):
    """``(target, pi, ti, si)`` when the cursor resolves to a ``status`` unit, else ``None``.

    The shared cursor→target resolution for every caller that needs to know
    WHICH task/subtask a state's ``current_*_index`` point at. Reads the 1-based
    ``current_phase_index`` / ``current_task_index`` (and optional
    ``current_subtask_index``), resolves the target task or subtask, and
    confirms its ``status``. A stale cursor pointing at a non-matching (e.g.
    terminal) unit, or out-of-range indices, → ``None``. Never raises.

    ``status`` selects the gate: ``"in_progress"`` for the active-task lock
    (``_locked_indices`` / ``resolve``), ``"completed"`` for the refactor-scope
    bound in ``pre-command-check`` (the refactorer runs after dispatch-finalize,
    so its task is terminal). ``si`` mirrors the input subtask index, or
    ``None`` when no subtask is pointed at / the index is out of range.
    """
    pi = state.get("current_phase_index", 0)
    ti = state.get("current_task_index", 0)
    if pi < 1 or ti < 1:
        return None
    try:
        task = state["phases"][pi - 1]["tasks"][ti - 1]
    except (IndexError, KeyError):
        return None
    si = state.get("current_subtask_index")
    if si is not None:
        try:
            tgt = task["subtasks"][si - 1]
        except (IndexError, KeyError):
            tgt = task
            si = None
    else:
        tgt = task
    if tgt.get("status") != status:
        return None
    return tgt, pi, ti, si


def _iter_track_states(cwd, *, tracks_glob="conductor/tracks/*/track-state.json"):
    """Yield ``(state_path, state)`` for each readable track-state.json under ``cwd``.

    Shared scan loop for ``resolve`` and the refactor-scope bound so the
    glob/load/except scaffolding lives in one place. Malformed/unreadable files
    are skipped, never raised.
    """
    for state_path in Path(cwd).glob(tracks_glob):
        try:
            with open(state_path) as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        yield state_path, state


def _locked_indices(state):
    """``(phase, task, subtask|None)`` for the locked in_progress task, else ``None``.

    Lock-free read of a single already-loaded state dict. Thin wrapper over
    ``_cursor_target`` (the shared index→target resolution); the in_progress-gated
    entry point ``resolve`` and its callers build on.
    """
    hit = _cursor_target(state, status="in_progress")
    if hit is None:
        return None
    _tgt, pi, ti, si = hit
    return pi, ti, si


def resolve(cwd, *, tracks_glob="conductor/tracks/*/track-state.json"):
    """Find the track with a locked ``in_progress`` task.

    Scans ``<cwd>/conductor/tracks/*/track-state.json`` and returns
    ``(track_dir, phase, task, subtask_or_None)`` for the first track whose
    ``current_*_index`` resolves to an ``in_progress`` unit, else ``None``.

    The conductor runs one track at a time per session, so "first locked track"
    is the active one. If two were ever locked simultaneously the result is
    best-effort (the first match) — callers fall back to their unscoped
    behavior on ambiguity rather than acting on a guess. Malformed/unreadable
    state files are skipped, never raised.
    """
    for state_path, state in _iter_track_states(cwd, tracks_glob=tracks_glob):
        hit = _cursor_target(state, status="in_progress")
        if hit is None:
            continue
        _tgt, pi, ti, si = hit
        return (str(state_path.parent), pi, ti, si)
    return None
