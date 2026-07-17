#!/usr/bin/env python3
"""Per-skill ``Stop`` hook for ``conductor:implement`` and ``conductor:parallel``.

Deterministic replacement for the inline ``type: prompt`` Stop hooks that used
to live in ``skills/implement/SKILL.md`` and ``skills/parallel/SKILL.md`` (a
haiku model read the transcript and judged four invariants). Both skills now
wire this script as a ``type: command`` Stop hook; the prompt text is gone from
the skill bodies.

It blocks the orchestrator's stop when conductor state is left dirty, mirroring
the four invariants the old prompts audited:

1. **stale serial in_progress lock** — a task abandoned between ``dispatch-prepare``
   and ``dispatch-finalize``. The skill's own yield rule says "the Stop hook
   will flag it." Wave-authorized in_progress is exempt (it is ledger-gated, not
   a serial lock).
2. **unintegrated wave** — an active ``.conductor/parallel.json`` ledger with
   ``in_flight`` members (the parallel skill stopped mid-wave, violating its
   "yield only after drained" rule).
3. **uncommitted conductor state** — ``track-state.json`` / ``plan.md`` changes
   the orchestrator must commit before stopping (scoped to those files so an
   unrelated working-tree edit never blocks it).
4. **state<->plan drift** — ``plan.md`` and ``track-state.json`` disagree on
   structure. Delegates to ``validate._validate_plan_consistency`` (the canonical
   checker) rather than reimplementing it.

**Loop safety.** If the hook input's ``stop_hook_active`` is true (the agent is
on its second stop attempt — it could not, or chose not to, clean up after the
first block), allow the stop unconditionally. This is the same escape hatch the
old prompt hook used, and it is how a deliberate HALT escapes the gate.

**Fail-open.** If the hook itself cannot determine state (no tracks registry,
import failure, git unavailable), it allows the stop rather than wedging the
orchestrator. The global warn-only checker (``state-consistency-check.py``) still
runs independently and surfaces what it can.

Pure helpers below take explicit args so they test cleanly; only ``main()`` does
hook I/O.
"""

import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Set, Tuple

# ``scripts/`` is sys.path[0] when invoked as a script, so ``track_state`` and
# ``lib`` both resolve. Add lib explicitly (mirrors on-subagent-stop.py).
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output  # noqa: E402
from lib.json_utils import load_json_safe  # noqa: E402
from lib.path_utils import extract_track_dirs, find_tracks_registry  # noqa: E402

# Statuses that mean a track is done — not being orchestrated, so not audited.
_TERMINAL_TRACK = {"completed", "archived", "cancelled"}


def stale_serial_locks(state: dict, wave_locs: Set[Tuple[int, int]]) -> List[str]:
    """In_progress task locs NOT authorized by an active wave ledger.

    Returns ``"P{pi}.T{ti}"`` strings for each stale serial lock. A wave holds
    multiple in_progress tasks via its ledger, not F1 — those ``wave_locs`` are
    exempt here (they are audited separately as an unintegrated wave, not a lock).
    Subtask in_progress counts too (a subtask lock is just as stale).
    """
    stale: List[str] = []
    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            if task.get("status") == "in_progress" and (pi, ti) not in wave_locs:
                stale.append(f"P{pi}.T{ti}")
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if sub.get("status") == "in_progress":
                    # Subtasks are never wave members (v1: flat tasks only), so
                    # any in_progress subtask is a serial lock.
                    stale.append(f"P{pi}.T{ti}.{si}")
    return stale


def plan_drift_errors(track_dir: Path, state: dict) -> List[str]:
    """Structural disagreements between plan.md and track-state.json.

    Delegates to ``validate._validate_plan_consistency`` (the canonical checker)
    and returns only hard errors — warnings (e.g. missing checkpoint) are not
    stop-blocking. Best-effort: any checker failure returns ``[]`` so a drift
    checker fault never blocks the orchestrator.
    """
    errors: List[str] = []
    warnings: List[str] = []
    try:
        from track_state.validate import _validate_plan_consistency

        _validate_plan_consistency(str(track_dir), state, errors, warnings)
    except Exception:
        return []
    return errors


def conductor_state_changes(cwd: Path, track_dirs: Iterable[str]) -> List[str]:
    """Uncommitted tracked changes to conductor state files under track dirs.

    Scoped to ``track-state.json`` / ``plan.md`` (the orchestrator's mutation
    surface) so an unrelated working-tree edit on another file never blocks the
    orchestrator. Gitignored state (``.conductor/result.json``, the wave ledger)
    never appears in ``git status`` and so is naturally excluded. Returns []
    on any git failure (fail-open).
    """
    targets = set()
    for td in track_dirs:
        td = td.strip("/")
        targets.add(f"{td}/track-state.json")
        targets.add(f"{td}/plan.md")
    if not targets:
        return []
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "status", "--porcelain=v1", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except Exception:
        return []
    dirty: List[str] = []
    for line in out.splitlines():
        if len(line) < 3:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:  # rename: keep the destination
            path = path.split(" -> ", 1)[1].strip().strip('"')
        if path in targets:
            dirty.append(path)
    return dirty


def audit_track(track_dir: Path) -> List[str]:
    """Blocking issues for one track. Empty if the track is terminal/absent."""
    state = load_json_safe(track_dir / "track-state.json")
    if not state:
        return []
    if state.get("status") in _TERMINAL_TRACK:
        return []  # not being orchestrated

    name = track_dir.name

    # Wave-inflight locs (ledger-gated). Fail-open to empty on any error.
    try:
        from track_state.validate import _wave_inflight_locs

        wave_locs = _wave_inflight_locs(str(track_dir))
    except Exception:
        wave_locs = set()

    issues: List[str] = []

    # (2) unintegrated wave — checked first so the wave locs are explained,
    # then (1) any *other* in_progress is a serial lock.
    if wave_locs:
        issues.append(
            f"{name}: unintegrated wave ({len(wave_locs)} in_flight member(s)) "
            "— integrate via wave-finalize before stopping"
        )

    stale = stale_serial_locks(state, wave_locs)
    if stale:
        issues.append(
            f"{name}: stale in_progress lock {'; '.join(stale)} "
            "— finalize or release before stopping"
        )

    drift = plan_drift_errors(track_dir, state)
    if drift:
        issues.append(f"{name}: state/plan drift — {'; '.join(drift)}")

    return issues


# Regex slice of a dispatch-lifecycle line. The log format (see
# lib/dispatch_lifecycle.py:emit) is space-delimited ``key=value``:
#   dispatch_lifecycle event=start session=<tok> agent=<a> phase=<p> task=<t> subtask=<s> ...
# We extract the four fields we join on (session + phase/task/subtask) and the
# event verb. Keep this tolerant: a malformed line just won't match.
import re  # noqa: E402

_LIFECYCLE_FIELD = re.compile(
    r"(?:^|\s)event=(\S+)"
    r"(?:.*?\ssession=(\S+))?"
    r"(?:.*?\sphase=(\S+))?"
    r"(?:.*?\stask=(\S+))?"
    r"(?:.*?\ssubtask=(\S+))?"
)
# Events that mean a dispatch reached resolution: either the guard saw it
# proceed (probe) or the agent reported back (stop). A `start` with no such
# follow event for the same (session, phase, task, subtask) is a stall.
_RESOLVE_EVENTS = {"probe", "stop", "re-dispatch"}


def stalled_dispatch_hint() -> str | None:
    """Detect a teleoperator stall from the dispatch-lifecycle log.

    The implement-step skill's yield rule is "NEVER stop between a dispatch and
    the next ``track-state step`` call." Small-window models drop that prose
    instruction and stall on "Agent dispatched, waiting…" — a ``start`` event
    in ``dispatch-lifecycle.log`` with no following ``probe``/``stop`` is the
    exact, already-captured signal (Phase 1–2 telemetry). This reads the log,
    finds the most recent unresolved ``start`` per ``(session, phase, task,
    subtask)`` join key, and returns a one-line reminder to run ``step``.

    Pure + best-effort: never raises; returns ``None`` on any ambiguity (no
    log, unreadable, no stall). Advisory only — the caller surfaces it via
    ``additionalContext``, never as a block (the agent may legitimately be
    mid-work when Stop fires).
    """
    try:
        from lib.env import get_logs_dir

        log_path = get_logs_dir() / "dispatch-lifecycle.log"
        if not log_path.is_file():
            return None
        lines = log_path.read_text(errors="replace").splitlines()
    except Exception:
        return None

    # Walk newest-first; the FIRST unresolved start we hit (most recent) is the
    # stall candidate. Once we've seen a resolve event for a key, any earlier
    # start on that key is closed and we skip it.
    resolved: set = set()
    for line in reversed(lines):
        m = _LIFECYCLE_FIELD.search(line)
        if not m:
            continue
        event, session, phase, task, subtask = m.groups()
        key = (session or "-", phase or "-", task or "-", subtask or "-")
        if event in _RESOLVE_EVENTS:
            resolved.add(key)
            continue
        if event == "start" and key not in resolved:
            # Found the most-recent unresolved dispatch. Only surface if the
            # indices are real (not "-") — a start with no resolved phase/task
            # is a pre-lock emit, not a teleoperator stall.
            if phase != "-" and task != "-":
                sub = f"/{subtask}" if subtask not in (None, "-", "") else ""
                return (
                    f"[Conductor] dispatch phase={phase} task={task}{sub} has no "
                    "completion event in dispatch-lifecycle.log — the teleoperator "
                    f"may have stalled between dispatch and `track-state step`. "
                    "If the agent finished, run `track-state step <track_dir>` to advance."
                )
    return None


def main() -> None:
    input_data = read_hook_input()

    # Loop-break: second stop attempt after a prior block — allow unconditionally.
    # This is also how a deliberate HALT (which can't clean up) escapes the gate.
    if input_data.get("stop_hook_active"):
        write_hook_output()
        return

    cwd_str = input_data.get("cwd", "")
    cwd = Path(cwd_str) if cwd_str else Path.cwd()

    tracks_file = find_tracks_registry(cwd)
    if not tracks_file:
        write_hook_output()  # no conductor project here — nothing to audit
        return
    track_dirs = extract_track_dirs(tracks_file)

    issues: List[str] = []
    for td in track_dirs:
        issues.extend(audit_track(cwd / td))

    dirty = conductor_state_changes(cwd, track_dirs)
    if dirty:
        issues.append(
            "uncommitted conductor state: " + ", ".join(dirty) + " — commit before stopping"
        )

    if issues:
        reason = "Conductor stop audit: " + " | ".join(issues)
        write_hook_output(decision="block", reason=reason)
    else:
        # Advisory-only: a detected teleoperator stall surfaces as context the
        # model sees on stop, without blocking it (the agent may be mid-work).
        hint = stalled_dispatch_hint()
        write_hook_output(additional_context=hint)


if __name__ == "__main__":
    main()
