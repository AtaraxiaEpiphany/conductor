"""Brief resume-marker CLI — the /conductor:brief counterpart to new_track.py.

The marker ``<track_dir>/.conductor/brief-progress.json`` is a transient
(gitignored, deleted at §5 hand-off) pre-state record: it exists BEFORE any
``track-state.json`` and lets an interrupted ``brief`` run be detected so the
orchestrator can offer resume/discard instead of silently overwriting.

A Brief is a single interview→write, so the marker is minimal — just init and
finalize (no multi-step state machine like new-track's ``steps_done``). The skill
never hand-edits the JSON; it calls these commands. Mirrors new_track.py's
invariants: idempotent, tolerant reader, parents=True mkdir, always-CLI-invoked.
"""
import json
from pathlib import Path

from .helpers import out, _find_registry

_BRIEF_MARKER = "brief-progress.json"


def _brief_marker_path(track_dir):
    """Pure path to the marker — deliberately does NOT mkdir, so a read never
    creates directories as a side effect (mirrors new_track._nt_marker_path)."""
    return Path(track_dir) / ".conductor" / _BRIEF_MARKER


def _brief_read_marker(track_dir):
    """Tolerant reader: survives a missing or corrupt file by returning None."""
    path = _brief_marker_path(track_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (ValueError, OSError):
        pass
    return None


def _brief_write_marker(track_dir, data):
    """Write the whole marker dict, creating the track dir + .conductor/ if
    needed (the track dir may not yet exist at init time)."""
    cdir = Path(track_dir) / ".conductor"
    cdir.mkdir(parents=True, exist_ok=True)
    _brief_marker_path(track_dir).write_text(json.dumps(data, ensure_ascii=False))


def cmd_brief_init(track_dir, track_id):
    """Write the initial marker. Idempotent: a no-op (``action: exists``) if a
    marker already exists, so a resumed run never clobbers its own progress."""
    existing = _brief_read_marker(track_dir)
    if existing is not None:
        out(dict(ok=True, action="exists", track_dir=str(track_dir),
                 track_id=existing.get("track_id")))
        return
    data = {
        "track_id": track_id, "track_dir": str(track_dir),
        "committed": False,
    }
    _brief_write_marker(track_dir, data)
    out(dict(ok=True, action="created", track_dir=str(track_dir), track_id=track_id))


def cmd_brief_grill_done(track_dir):
    """Mark the brief's grill as complete — the orchestrator's explicit signal
    that shared understanding was reached (which may be in FEWER than
    ``MIN_GRILL_QUESTIONS`` AskUserQuestion turns, because decisions were
    pre-resolved by reading docs / carried by ``$ARGUMENTS``).

    Writes ``grill_complete: true`` onto the marker (re-reading + merging so a
    prior ``committed:false`` marker is preserved). Tolerant of a missing
    marker: the grill-done signal is still recorded by creating the marker with
    ``grill_complete: true`` + ``committed: false``, so the tripwire honors it
    even if ``brief-init`` was skipped. Always CLI-invoked — the skill never
    hand-edits the JSON.

    This decouples the tripwire's write-gate from the raw AskUserQuestion count
    (a proxy that's wrong exactly when the grill is done well): the gate becomes
    ``grill_complete OR count >= MIN_GRILL_QUESTIONS``."""
    data = _brief_read_marker(track_dir) or {}
    data["grill_complete"] = True
    # Preserve committed:false if it was set (a grill-done marker with no
    # committed flag would read as finalized; default to False to stay in the
    # grill-in-progress state until brief-finalize runs).
    data.setdefault("committed", False)
    data.setdefault("track_dir", str(track_dir))
    _brief_write_marker(track_dir, data)
    out(dict(ok=True, action="grill_done", grill_complete=True,
             track_dir=str(track_dir)))


def cmd_brief_finalize(track_dir):
    """Delete the marker once ``brief.md`` is written and the run is durable.
    Idempotent — a missing marker is a no-op success. Verifies brief.md exists:
    finalizing without a brief would leave a track dir with no artifact and no
    marker, masking the failure. ``brief_present`` reports the check so the skill
    can warn without the command hard-failing (finalize is the cleanup step)."""
    path = _brief_marker_path(track_dir)
    removed = path.exists()
    if removed:
        path.unlink()
    brief_present = (Path(track_dir) / "brief.md").exists()
    out(dict(ok=True, finalized=True, removed=removed,
             brief_present=brief_present, track_dir=str(track_dir)))


def cmd_brief_resume():
    """Detect any interrupted brief (committed:false marker) and emit its resume
    directive. Always exits 0 — switch on ``action``: ``none`` → fresh brief
    (§1.0); ``resume`` → ``candidates`` is the (1+) partial briefs; the skill
    AskUserQuestions over them (resume re-interviews gaps vs the existing
    brief.md, or discard)."""
    registry = _find_registry()
    if registry is None:
        out(dict(action="none", reason="no_registry",
                 hint="No conductor/tracks.md found — nothing to resume."))
        return
    tracks_dir = registry.parent / "tracks"
    candidates = []
    if tracks_dir.is_dir():
        for marker in sorted(tracks_dir.glob("*/.conductor/" + _BRIEF_MARKER)):
            track_dir = marker.parent.parent  # .conductor's parent = the track dir
            data = _brief_read_marker(track_dir)
            # Only a literal committed:false is resumable; missing/corrupt is
            # ambiguous — never resume from one.
            if not data or data.get("committed") is not False:
                continue
            candidates.append(dict(
                track_id=data.get("track_id"),
                track_dir=str(track_dir.resolve()),
                brief_present=(Path(track_dir) / "brief.md").exists(),
            ))
    if not candidates:
        out(dict(action="none", candidates=[]))
        return
    out(dict(action="resume", candidates=candidates))
