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

This module is the on-disk marker that closes that window. The SubagentStart
hook stamps it the moment a single-writer subagent actually SPAWNS
(:func:`stamp`, called from ``on-subagent-start.py``); ``finalize_dispatch``
(and the stale-result reap sites) clear it. The PreToolUse:Agent dedupe hook
(``on-dispatch-dedupe.py``) reads it and ``permissionDecision: "deny"`` a
second spawn for the same in-flight task.

Marker semantics — "spawned", not "prepared"
--------------------------------------------
The marker is deliberately NOT written by ``prepare_dispatch``. Between
``step`` emitting ``action: dispatch`` and the orchestrator's Agent call the
dispatch is *prepared but not spawned* — a state a stateless PreToolUse hook
cannot tell apart from "agent running". Stamping at prepare therefore made
the guard deny the FIRST spawn itself (the 2026-09-01 dispatch-deadlock
incident: every dispatch denied → dispatch-finalize → re-prepare → denied
again, looping until the retry budget died). Stamping at spawn keeps every
guarded state one where an agent has demonstrably started:

- spawn denied / pulled back before SubagentStart → no marker → retry clean;
- agent running (incl. background mode) → marker + predicate below → a second
  spawn denied;
- session killed mid-run → marker survives → next spawn denied → the deny
  reason's ``dispatch-finalize`` recovery is the terminating exit.

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

The marker lives at ``<track_dir>/.conductor/.dispatch-inflight-*.json`` — it is
transient lock state (stamped at spawn by the SubagentStart hook, cleared on
finalize/reap), covered by the per-track ``.conductor/.gitignore`` rule
``.dispatch-inflight-*.json`` (written by ``track_state.quality._ensure_conductor_gitignore``),
and **never** handed to the model or staged. Treat it as ignore-only plumbing.

Note: the repo-root ``/.conductor/`` gitignore rule is root-anchored and
deliberately does NOT cover per-track ``conductor/tracks/<id>/.conductor/``
(that directory carries committed conductor bookkeeping — ``result.json``,
``parallel.json``, etc. are listed explicitly in the per-track gitignore while
transient markers like this one are glob-ignored). See
``templates/conductor-gitignore.md`` and ``tests/test_conductor_gitignore.py``.
"""
import json
import re
import subprocess
from pathlib import Path

from .constants import DISPATCH_INFLIGHT_TMPL


def marker_path(track_dir, phase, task, subtask=None):
    """Pure path to the inflight marker — deliberately no mkdir.

    Mirrors ``_phase_cp_marker_path`` / ``_skip_analysis_marker_path`` in the
    spine: path only, so a read of a never-dispatched task is a cheap
    ``.exists() == False`` with no side effects.
    """
    sub = f"-{subtask}" if subtask is not None else ""
    return Path(track_dir) / ".conductor" / DISPATCH_INFLIGHT_TMPL.format(
        phase=phase, task=task, sub=sub)


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
    — stamp 1" (:func:`stamp` reads this then bumps). Never
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

    Low-level tolerant writer. The production path is :func:`stamp` (the
    SubagentStart spawn stamp, which owns the gen bump under
    ``dispatch_lock``); this writer exists for it and for tests fabricating
    markers.

    ``start_sha`` is the commit HEAD sits on when the agent spawns — the value
    the hook compares the live HEAD against. ``written_at_iso`` is passed in
    (not read here) so the caller owns the timestamp source.

    ``gen`` is the dispatch generation — a monotonic counter bumped per spawn
    for ``(phase, task, subtask)``. It does NOT gate the hook's deny decision
    (that is the HEAD+result predicate below); it disambiguates the lifecycle
    telemetry: two probes/start events sharing a gen = one dispatch spawned
    twice, a higher gen = a fresh spawn.
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


def stamp(track_dir, phase, task, subtask=None):
    """Stamp the inflight marker for a task that has just SPAWNED.

    Single home of the spawn-time stamp (called by ``on-subagent-start.py``).
    ``start_sha`` is the live HEAD (7-char) read here — on the serial spine the
    Start commit ``prepare_dispatch`` just made is HEAD, and on a resume path
    the prior Start commit is, so the value the dedupe hook later compares the
    live HEAD against is "where the working tree sat when this agent started".
    The ``gen`` is the bumped prior generation (``read_gen + 1``) so a
    re-dispatch's spawn stamps a fresh generation.

    The read-modify-write gen bump sits inside ``dispatch_lock.acquire`` (an
    exclusive ``fcntl.flock`` on ``<track_dir>/.conductor/.dispatch.lock``) —
    the same critical section ``prepare_dispatch``'s stamp used to own, moved
    here unchanged so two racing stamps cannot both read ``gen=N`` and write
    ``gen=N+1``.

    Import-light by design (``subprocess`` git + ``datetime`` only — no
    ``track_state``), mirroring the module's hook-consumable layout. Returns
    the written marker dict, or ``None`` when even a stamp must fail-open
    (unreadable HEAD still stamps — with ``start_sha: null``, which the hook's
    ``bool(start_sha)`` predicate reads as not-in-flight, the safe direction).
    Never raises.
    """
    from datetime import datetime, timezone

    from . import dispatch_lock

    try:
        head = None
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short=7", "HEAD"],
                capture_output=True, text=True, cwd=str(track_dir), timeout=5,
            )
            sha = result.stdout.strip()
            head = sha if re.match(r"^[0-9a-f]{7}$", sha) else None
        except Exception:
            head = None
        written_at = datetime.now(timezone.utc).isoformat()
        with dispatch_lock.acquire(track_dir):
            prev_gen = read_gen(track_dir, phase, task, subtask)
            data = {
                "phase": phase,
                "task": task,
                "subtask": subtask,
                "start_sha": head,
                "written_at": written_at,
                "gen": prev_gen + 1,
            }
            path = marker_path(track_dir, phase, task, subtask)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, ensure_ascii=False))
            except OSError:
                return None
        return data
    except Exception:
        return None


def clear(track_dir, phase, task, subtask=None):
    """Remove the inflight marker if present. Swallows ``OSError`` (fail-open)."""
    path = marker_path(track_dir, phase, task, subtask)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def find_active(track_dir):
    """The ``(phase, task, subtask)`` of the single active inflight marker, or ``None``.

    Conductor locks one task at a time, so at most one inflight marker is
    legitimate under a track's ``.conductor/`` at any moment. This globs for
    any ``.dispatch-inflight-*.json`` and returns the parsed
    ``(phase, task, subtask)`` from the first match — the identity of the task
    THIS dispatch was for, recoverable even after the singleton cursor lock has
    been released (the marker persists until finalize/reap).

    Used by ``on-subagent-stop``'s telemetry: the lock is frequently already
    gone by SubagentStop time, so without this the stop event renders
    ``phase=- task=-`` and the dispatch can't be joined to its task in
    ``dispatch-lifecycle.log``. The marker IS "a dispatch was born for this
    task and not yet finalized" — exactly the task this stop belongs to.

    Tolerant: missing/corrupt marker, no marker, or I/O error → ``None``.
    Never raises.
    """
    cdir = Path(track_dir) / ".conductor"
    try:
        for path in cdir.glob(".dispatch-inflight-*.json"):
            # Parse the marker directly — we're globbing precisely to DISCOVER
            # the indices, so we can't pass them to ``read``. A marker is a
            # small JSON dict written by ``write``.
            try:
                m = json.loads(path.read_text())
                if isinstance(m, dict) and "phase" in m and "task" in m:
                    return (m["phase"], m["task"], m.get("subtask"))
            except (ValueError, OSError):
                continue
    except OSError:
        pass
    return None


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
