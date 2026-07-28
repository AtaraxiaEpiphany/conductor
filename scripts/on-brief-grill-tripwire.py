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
until the orchestrator has recorded at least ``MIN_GRILL_QUESTIONS``
``AskUserQuestion`` turns. The grill's seven core decision-tree nodes set the
floor; a thorough grill asks more, so the floor is a lower bound, not a target.

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
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from hook_io import read_hook_input, write_hook_output  # noqa: E402
from logging import init_logging, log_entry  # noqa: E402
from env import get_data_dir  # noqa: E402

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
_COUNTER_FILE = "brief-grill-counters.json"


def _counter_path():
    return get_data_dir() / _COUNTER_FILE


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


def _read_counters():
    path = _counter_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        return raw if isinstance(raw, dict) else {}
    except (ValueError, OSError):
        return {}


def _bump_counter(track_id):
    """Increment the AskUserQuestion counter for a track. Best-effort, always
    returns the new count (≥1) so the caller never blocks on a write glitch."""
    if not track_id:
        return 1
    try:
        data = _read_counters()
        count = (data.get(track_id) or 0) + 1
        data[track_id] = count
        path = _counter_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False))
        return count
    except Exception:
        return 1


def _clear_counter(track_id):
    """Drop the counter once the brief is written (marker → committed/finalized)
    so a later brief for the same track_id starts at a fresh grill budget."""
    if not track_id:
        return
    try:
        data = _read_counters()
        if track_id in data:
            del data[track_id]
            _counter_path().write_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


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
        track_id = _derive_track_id_from_cwd(cwd)
        if track_id:
            count = _bump_counter(track_id)
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

    # Grill in progress — check the AskUserQuestion floor.
    track_id = _derive_track_id_from_cwd(cwd) or Path(track_dir).name
    data = _read_counters()
    count = (data.get(track_id) or 0)
    if count >= MIN_GRILL_QUESTIONS:
        log_entry(log_file, f"event=brief_write_allowed track={track_id} count={count}")
        write_hook_output()
        return

    # Below the floor — the grill is incomplete. Deny and prescribe finishing it.
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "${CLAUDE_PLUGIN_ROOT}")
    reason = (
        f"{_BRIEF_GUARD} brief.md write blocked — the §3 grill is incomplete "
        f"(only {count} of {MIN_GRILL_QUESTIONS} required AskUserQuestion turns "
        f"recorded for track '{track_id}', whose brief-progress marker is "
        f"committed:false). A brief written from guesses pollutes all downstream "
        f"planning. Finish the §3 decision tree one question at a time via "
        f"AskUserQuestion (Problem → Goals → Out-of-Scope → Constraints → "
        f"Stakeholders → References → Open Qs / ACs), reach shared understanding, "
        f"THEN write brief.md in §4. If the grill genuinely is complete and the "
        f"counter is stale, run `track-state brief-finalize \"<track_dir>\"` to "
        f"clear the marker and retry."
    )
    log_entry(log_file, f"event=brief_write_denied track={track_id} count={count}")
    write_hook_output(
        permission_decision="deny",
        permission_decision_reason=reason,
        additional_context=reason,
    )


if __name__ == "__main__":
    main()
