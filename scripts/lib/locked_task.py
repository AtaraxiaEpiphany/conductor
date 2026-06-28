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
"""
import json
from pathlib import Path


def _locked_indices(state):
    """``(phase, task, subtask|None)`` for the locked in_progress task, else ``None``.

    Lock-free read of a single already-loaded state dict. Mirrors the
    index→target resolution in ``dispatch._synthesize_result_from_state``: read
    ``current_*_index`` → resolve the target task/subtask → confirm it is
    actually ``in_progress`` (a stale index pointing at a terminal task is not a
    live lock).
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
    if tgt.get("status") != "in_progress":
        return None
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
    base = Path(cwd)
    for state_path in base.glob(tracks_glob):
        try:
            with open(state_path) as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        idx = _locked_indices(state)
        if idx is None:
            continue
        pi, ti, si = idx
        return (str(state_path.parent), pi, ti, si)
    return None
