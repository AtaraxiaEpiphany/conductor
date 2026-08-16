"""New-track resume-marker CLI — promotes the §0.5 / §2.x prose JSON bookkeeping
into code.

The marker ``<track_dir>/.conductor/new-track-progress.json`` is a transient
(gitignored, deleted at the §2.6 commit) pre-state record: it exists BEFORE
``track-state.json`` and lets an interrupted new-track run resume instead of
restarting. Until now the model hand-edited it — init, four ``steps_done``
appends, set-mode, finalize-and-delete, and the §0.5 glob+jump detection — with
no code reading or writing it and no test covering it. Hand-editing invites the
same clobber / literal-token-survives / lost-resume-point failures the post-loop
sidecar avoids by MERGE-stamping (``dispatch.py:_post_loop_stamp_line``).

These commands do the I/O directly in Python: they are invoked BY THE SKILL as
``track-state new-track-*`` (not emitted as bash for a teleoperator to relay),
so they read-modify-write with idempotent, order-preserving appends and a
tolerant reader — the same invariants as the post-loop sidecar, without the
``python3 -c`` one-liner envelope. The skill never hand-edits the marker JSON.

Lifecycle: ``init`` → ``step spec_planned`` → ``step reviewed`` → ``set-mode``
→ ``step state_created`` → ``step registry_updated`` → ``finalize`` (deletes).
"""
from pathlib import Path

from .constants import EXECUTION_MODES
from .helpers import out, _find_registry

from lib.constants import NT_PROGRESS_MARKER as _NT_MARKER
from lib.markers import json_marker_read, json_marker_write  # single home (quality gitignore derives here)

# Ordered resume keys (skills/new-track/SKILL.md §0.5). The first key NOT in
# steps_done is where an interrupted run resumes. state_created / registry_updated
# both land in §2.6 (init-from-plan vs registry-add + commit).
_STEP_ORDER = ("spec_planned", "reviewed", "state_created", "registry_updated")
_RESUME_TARGET = {
    "spec_planned": "§2.3",
    "reviewed": "§2.4",
    "state_created": "§2.6",
    "registry_updated": "§2.6",
}


def _nt_marker_path(track_dir):
    """Pure path to the marker — deliberately does NOT mkdir (unlike
    ``conductor_dir``), so a read never creates directories as a side effect."""
    return Path(track_dir) / ".conductor" / _NT_MARKER


def _nt_read_marker(track_dir):
    """Tolerant reader (lib.markers): survives a missing or corrupt file by
    returning ``None``, so callers branch without existence checks and a
    half-written file never crashes the resume glob."""
    return json_marker_read(_nt_marker_path(track_dir))


def _nt_write_marker(track_dir, data):
    """Write the whole marker dict (lib.markers): ``parents=True`` ensures the
    track dir AND its ``.conductor/`` exist (may not yet at §2.1 init time)."""
    json_marker_write(_nt_marker_path(track_dir), data)


def cmd_new_track_init(track_dir, track_id, description, type_):
    """Write the initial marker. Idempotent: a no-op (``action: exists``) if a
    marker already exists, so a resumed run never clobbers its own progress."""
    existing = _nt_read_marker(track_dir)
    if existing is not None:
        out(dict(ok=True, action="exists", track_dir=str(track_dir),
                 steps_done=existing.get("steps_done", [])))
        return
    data = {
        "track_id": track_id, "track_dir": str(track_dir),
        "description": description, "type": type_,
        "execution_mode": None, "steps_done": [], "committed": False,
    }
    _nt_write_marker(track_dir, data)
    out(dict(ok=True, action="created", track_dir=str(track_dir), steps_done=[]))


def cmd_new_track_step(track_dir, key):
    """Append a resume key to ``steps_done``; idempotent and order-preserving.
    Rejects unknown keys (a code guard: the model can't stamp garbage)."""
    if key not in _STEP_ORDER:
        out(dict(error=f"unknown resume step key: {key!r}",
                 hint=f"legal keys: {', '.join(_STEP_ORDER)}"))
        return
    data = _nt_read_marker(track_dir)
    if data is None:
        out(dict(error="no new-track-progress marker — run new-track-init first",
                 track_dir=str(track_dir)))
        return
    done = data.setdefault("steps_done", [])
    if key not in done:
        done.append(key)
    _nt_write_marker(track_dir, data)
    out(dict(ok=True, track_dir=str(track_dir), steps_done=done))


def cmd_new_track_set_mode(track_dir, mode):
    """Write ``execution_mode`` (validated against EXECUTION_MODES)."""
    if mode not in EXECUTION_MODES:
        out(dict(error=f"invalid execution mode: {mode!r}",
                 hint=f"one of: {', '.join(EXECUTION_MODES)}"))
        return
    data = _nt_read_marker(track_dir)
    if data is None:
        out(dict(error="no new-track-progress marker — run new-track-init first",
                 track_dir=str(track_dir)))
        return
    data["execution_mode"] = mode
    _nt_write_marker(track_dir, data)
    out(dict(ok=True, track_dir=str(track_dir), execution_mode=mode))


def cmd_new_track_finalize(track_dir):
    """Delete the marker (the track is durable now). Idempotent — a missing
    file is a no-op success, so a re-finalize after a partial commit is safe."""
    path = _nt_marker_path(track_dir)
    removed = path.exists()
    if removed:
        path.unlink()
    out(dict(ok=True, finalized=True, removed=removed, track_dir=str(track_dir)))


def cmd_new_track_resume():
    """§0.5 detect+jump promoted to code: glob every track's marker for
    ``committed: false`` and emit one resume directive per candidate.

    Always exits 0 — switch on ``action``: ``none`` → fresh track (§1.0);
    ``resume`` → ``candidates`` is the (1+) partial tracks; the skill
    ``AskUserQuestion``s over them, then jumps each to its ``resume_target``.
    """
    registry = _find_registry()
    if registry is None:
        out(dict(action="none", reason="no_registry",
                 hint="No conductor/tracks.md found — nothing to resume."))
        return
    # registry = <conductor_root>/conductor/tracks.md → tracks live one level down
    tracks_dir = registry.parent / "tracks"
    candidates = []
    if tracks_dir.is_dir():
        for marker in sorted(tracks_dir.glob("*/.conductor/" + _NT_MARKER)):
            track_dir = marker.parent.parent  # .conductor's parent = the track dir
            data = _nt_read_marker(track_dir)
            # Only a literal committed:false is resumable: committed:true is
            # stale (finalize should have deleted it), and a missing/corrupt
            # marker is ambiguous — never resume from one.
            if not data or data.get("committed") is not False:
                continue
            done = list(data.get("steps_done", []))
            first_missing = next((k for k in _STEP_ORDER if k not in done), None)
            candidates.append(dict(
                track_id=data.get("track_id"),
                track_dir=str(track_dir.resolve()),
                description=data.get("description"),
                type=data.get("type"),
                execution_mode=data.get("execution_mode"),
                steps_done=done,
                last_step=done[-1] if done else None,
                first_missing_step=first_missing,
                resume_target=_RESUME_TARGET.get(first_missing) if first_missing else None,
            ))
    if not candidates:
        out(dict(action="none", candidates=[]))
        return
    out(dict(action="resume", candidates=candidates))
