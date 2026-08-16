#!/usr/bin/env python3
"""PreToolUse guards for /conductor:brief — the grill tripwire.

The problem this solves
-----------------------
The brief skill's §3 grill is a **one-question-at-a-time** interview via
``AskUserQuestion`` — a model-compliance discipline (ask one decision, wait,
then the next) that sonnet under pressure routinely violates by batching
questions or free-texting them, then jumping straight to writing ``brief.md``
from guesses. The brief is the *input* to all downstream planning, so a brief
written from guesses (not a completed grill) pollutes everything downstream.

Prose in the SKILL ("MUST — one question at a time") raises salience but can't
*guarantee* it — the same prose-invariant-a-model-ignores gap that
``on-write-result-clean-tree.py``, ``on-dispatch-dedupe.py``, and
``on-category-write-guard.py`` close. This hook makes the invariant
deterministic: while a brief's resume marker is ``committed:false`` (the grill
is in progress), a ``Write``/``Edit`` to that track's ``brief.md`` is **denied**
until the orchestrator has EITHER signaled ``grill_complete`` on the marker
(the real invariant — shared understanding reached) OR recorded at least
``MIN_GRILL_QUESTIONS`` ``AskUserQuestion`` turns (the legacy lower bound).

Why two signals, not just the count
-----------------------------------
The count alone is a **proxy** for "shared understanding reached," and a proxy
that's wrong exactly when the skill is used *well*: §3 explicitly says many
decisions are pre-resolved by reading docs / carried by ``$ARGUMENTS``, which
legitimately produces *fewer* than ``MIN_GRILL_QUESTIONS`` turns. So the
explicit ``grill_complete`` flag (set via ``track-state brief-grill-done``) is
the primary gate, and the count floor is the backstop for a model that writes
without signaling. Either satisfies the gate.

How it fires (two matchers, one file)
-------------------------------------
Registered for both ``Write|Edit`` and ``AskUserQuestion`` in hooks.json. It
branches on ``tool_name``:

- ``AskUserQuestion`` → increment a per-track counter (sidecar under
  ``get_data_dir()``), keyed by track_id derived from cwd. Always allow.
- ``Write``/``Edit`` → if the target resolves to ``<track_dir>/brief.md`` AND
  that track's brief marker is ``committed:false`` AND the counter is below the
  floor → **deny** with a reason prescribing "finish the §3 grill first." Else
  allow (a finalized brief, a human re-editing, or a non-brief write is fine).

Scope / fail-open
-----------------
Only ``brief.md`` targets are gated, and only while ``committed:false``. A
missing/corrupt marker, a finalized brief (``committed:true`` or no marker), an
unresolvable track_id, or any path/IO error → allow + stderr warning (mirrors
``on-orchestrator-read-guard.py``'s fail-open contract: a misbehaving guard is
worse than none). The counter is best-effort; if it can't be read, the hook
allows — it never blocks a legitimate write on a counter glitch.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
sys.path.insert(0, str(Path(__file__).parent))

from hook_io import read_hook_input, write_hook_output  # noqa: E402
from logging import init_logging, log_entry  # noqa: E402
from lib.brief_counters import bump_counter, read_counters, read_count  # noqa: E402

# Lead for the deny reason — a brief-specific marker (NOT [Conductor Recovery],
# which marks a hook-injected stop-recovery turn; this is a permission denial).
_BRIEF_GUARD = "[Conductor Brief Guard]"

# The floor: the brief grill's §3 decision tree has seven core nodes (Problem,
# Goals, Out-of-Scope, Constraints, Stakeholders, References, Open Qs / ACs).
# A completed grill asks at least this many AskUserQuestion turns. Set just
# below the node count so a grill that confirms two nodes in one turn (legit)
# isn't falsely blocked, but a 2-question shortcut is.
MIN_GRILL_QUESTIONS = 6
_BRIEF_MARKER = "brief-progress.json"
# Counter sidecar vocabulary (file name, schema, TTL reap, finalize clear) is
# single-homed in lib/brief_counters.py — shared with track_state.brief's
# finalize so a stale high count can never pre-satisfy a later brief's floor.


def _derive_track_id_from_cwd(cwd):
    """Best-effort: derive the track_id from a cwd that is (or sits under) a
    track dir ``conductor/tracks/<track_id>/``. Returns None if not in one."""
    if not cwd:
        return None
    p = Path(cwd).resolve()
    # The track dir itself: its parent is ``tracks``, its name is the id.
    if p.name and p.parent.name == "tracks":
        return p.name
    # Walk up a few levels looking for a ``tracks/<id>`` ancestor.
    for parent in p.parents:
        if parent.name and parent.parent.name == "tracks":
            return parent.name
    return None


def _marker_committed_false(track_dir):
    """True iff this track has a brief-progress.json marker that is literally
    ``committed:false`` — the grill is in progress. Missing/corrupt/committed →
    False (not a grill-in-progress state)."""
    marker = Path(track_dir) / ".conductor" / _BRIEF_MARKER
    if not marker.exists():
        return False
    try:
        data = json.loads(marker.read_text())
        return isinstance(data, dict) and data.get("committed") is False
    except (ValueError, OSError):
        return False


def _read_marker(track_dir):
    """Tolerant marker reader: returns the marker dict or None on missing/corrupt."""
    marker = Path(track_dir) / ".conductor" / _BRIEF_MARKER
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text())
        return data if isinstance(data, dict) else None
    except (ValueError, OSError):
        return None


def _marker_grill_complete(track_dir):
    """True iff this track's brief marker carries ``grill_complete: true`` — the
    orchestrator's explicit signal that shared understanding was reached (which
    may be in FEWER than ``MIN_GRILL_QUESTIONS`` AskUserQuestion turns, because
    decisions were pre-resolved by reading docs / carried by ``$ARGUMENTS``)."""
    data = _read_marker(track_dir)
    return bool(data and data.get("grill_complete") is True)


def _resolve_registry(start):
    """Locate ``conductor/tracks.md`` by walking up from ``start``. Mirrors
    ``track_state.helpers._find_registry`` (both ``<cand>/conductor/tracks.md``
    and ``<cand>/tracks.md`` at each ancestor) so the hook resolves the active
    brief marker from a project-root cwd, not just a track-dir cwd. Returns the
    registry ``Path`` or ``None``."""
    try:
        p = Path(start or Path.cwd()).resolve(strict=False)
    except OSError:
        return None
    for cand in (p, *p.parents):
        for cand_root in (cand / "conductor", cand):
            f = cand_root / "tracks.md"
            if f.is_file():
                return f
    return None


def _resolve_active_brief_track(start):
    """Resolve the single in-progress brief's track_dir from cwd, even when the
    orchestrator's cwd is the PROJECT ROOT (not the track dir).

    The original counter-key bug: ``_derive_track_id_from_cwd`` returns None when
    cwd isn't under ``tracks/<id>/``, so ``_bump_counter`` skipped the write
    (line 103–104) and the counter never landed — the Write gate then read 0 and
    the deny reason said "0 of 6" even though a real grill happened. This scans
    the registry's ``tracks/*/``  for a ``committed:false`` marker: if exactly
    one exists, that's unambiguously the active brief. Returns the track_dir
    (str) or None (zero or multiple in-progress briefs → ambiguous, can't pick)."""
    registry = _resolve_registry(start)
    if registry is None:
        return None
    tracks_dir = registry.parent / "tracks"
    if not tracks_dir.is_dir():
        return None
    active = []
    for marker in tracks_dir.glob("*/.conductor/" + _BRIEF_MARKER):
        track_dir = marker.parent.parent
        data = _read_marker(track_dir)
        if data and data.get("committed") is False:
            active.append(str(track_dir.resolve()))
    if len(active) == 1:
        return active[0]
    return None


def _resolve_brief_target(file_path, cwd):
    """If ``file_path`` resolves to a ``<track_dir>/brief.md``, return the
    track_dir; else None. Tolerates absolute and project-relative paths."""
    if not file_path:
        return None
    fp = Path(str(file_path).replace("\\", "/"))
    if fp.name != "brief.md":
        return None
    # Absolute path: its parent is the track dir.
    if fp.is_absolute():
        return str(fp.parent)
    # Relative: resolve against cwd.
    base = Path(cwd) if cwd else Path.cwd()
    return str((base / fp).resolve().parent)


def main():
    input_data = read_hook_input()
    tool = input_data.get("tool_name")
    cwd = input_data.get("cwd") or str(Path.cwd())
    log_file = init_logging("on-brief-grill-tripwire")

    # --- AskUserQuestion: count the grill turn, always allow. ---
    if tool == "AskUserQuestion":
        # Resolve the counter key robustly: cwd-derived id FIRST (cheap, exact
        # when the orchestrator is cd'd into the track dir), then fall back to
        # the active in-progress brief marker (handles the project-root-cwd case
        # where ``_derive_track_id_from_cwd`` returned None and the bump was
        # silently skipped — the "counter missing / 0 of 6" bug). Both resolve
        # to the track_id the Write gate reads back, so the keys match.
        track_id = _derive_track_id_from_cwd(cwd)
        if not track_id:
            active_dir = _resolve_active_brief_track(cwd)
            if active_dir:
                track_id = Path(active_dir).name
        if track_id:
            count = bump_counter(track_id)
            log_entry(log_file, f"event=grill_question track={track_id} count={count}")
        write_hook_output()
        return

    # --- Write|Edit|MultiEdit: gate a brief.md write during the grill. ---
    if tool not in ("Write", "Edit", "MultiEdit"):
        write_hook_output()
        return

    file_path = (input_data.get("tool_input") or {}).get("file_path", "")
    track_dir = _resolve_brief_target(file_path, cwd)
    if track_dir is None:
        write_hook_output()  # not a brief.md write
        return

    # No committed:false marker → not a grill-in-progress; allow (finalized,
    # human re-editing, or pre-init). This is the common path for re-runs.
    if not _marker_committed_false(track_dir):
        write_hook_output()
        return

    # Grill in progress — check the gate. The gate is the union of two signals:
    #   (a) ``grill_complete`` on the marker — the orchestrator's explicit
    #       "shared understanding reached" signal, set via ``track-state
    #       brief-grill-done``. This is the REAL invariant: a grill can be DONE
    #       WELL in fewer than MIN questions (decisions pre-resolved by reading
    #       docs / carried by $ARGUMENTS), so the question count is only a proxy.
    #   (b) the AskUserQuestion floor — the legacy lower bound, the backstop for
    #       a model that writes without signaling grill-done.
    # Either satisfies the gate. See the module docstring for why count alone is
    # a proxy that's wrong exactly when the skill is used well.
    track_id = _derive_track_id_from_cwd(cwd) or Path(track_dir).name
    if _marker_grill_complete(track_dir):
        log_entry(log_file,
                  f"event=brief_write_allowed track={track_id} reason=grill_complete")
        write_hook_output()
        return
    data = read_counters()
    count = read_count(data, track_id)
    if count >= MIN_GRILL_QUESTIONS:
        log_entry(log_file, f"event=brief_write_allowed track={track_id} count={count}")
        write_hook_output()
        return

    # Below the floor and no grill-complete signal — the grill is incomplete.
    # Deny and prescribe finishing it (or signaling grill-done if it genuinely is).
    reason = (
        f"{_BRIEF_GUARD} brief.md write blocked — the §3 grill is incomplete "
        f"(only {count} of {MIN_GRILL_QUESTIONS} required AskUserQuestion turns "
        f"recorded for track '{track_id}', whose brief-progress marker is "
        f"committed:false, and no grill-complete signal is set). A brief written "
        f"from guesses pollutes all downstream planning. Either:\n"
        f"  (a) finish the §3 decision tree one question at a time via "
        f"AskUserQuestion (Problem → Goals → Out-of-Scope → Constraints → "
        f"Stakeholders → References → Open Qs / ACs), reach shared understanding, "
        f"THEN write brief.md in §4; OR\n"
        f"  (b) if the grill genuinely is complete (you reached shared "
        f"understanding in FEWER than {MIN_GRILL_QUESTIONS} turns because "
        f"decisions were pre-resolved by reading docs / carried by $ARGUMENTS), "
        f"emit the grill-done signal before writing:\n"
        f"      track-state brief-grill-done \"{track_dir}\"\n"
        f"    then write brief.md in §4.\n"
        f"If the counter is stale (you ran from a project-root cwd), signal (b) "
        f"is the correct path — it sets grill_complete directly."
    )
    log_entry(log_file,
              f"event=brief_write_denied track={track_id} count={count} "
              f"grill_complete=false")
    write_hook_output(
        permission_decision="deny",
        permission_decision_reason=reason,
        additional_context=reason,
    )


if __name__ == "__main__":
    main()
