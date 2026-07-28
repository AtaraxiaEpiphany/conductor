"""Tests for ``scripts/on-brief-grill-tripwire.py`` — the brief grill guard.

Feeds the guard a synthetic hook payload on stdin and asserts the
permissionDecision. Covers: deny a brief.md write during an incomplete grill
(marker committed:false, counter below floor); allow once the counter reaches
the floor; allow when the marker is finalized (committed:true) or absent;
allow non-brief writes; the AskUserQuestion matcher increments the counter.
Isolates CLAUDE_PLUGIN_DATA so sibling tests' counters don't interfere.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_REPO = Path(__file__).resolve().parent.parent
_GUARD = _REPO / "scripts" / "on-brief-grill-tripwire.py"

# Must match the hook's floor — keep in sync with scripts/on-brief-grill-tripwire.py.
_MIN_FLOOR = 6


def _probe(project_dir, tool, file_path=None, cwd=None):
    payload = {"tool_name": tool, "cwd": cwd or str(project_dir)}
    if file_path is not None:
        payload["tool_input"] = {"file_path": file_path}
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    env["CLAUDE_PLUGIN_DATA"] = str(project_dir / ".data")
    env["CLAUDE_PLUGIN_ROOT"] = str(project_dir)
    r = subprocess.run(
        [sys.executable, str(_GUARD)], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


def _set_marker(track_dir, committed):
    cdir = track_dir / ".conductor"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "brief-progress.json").write_text(json.dumps({
        "track_id": track_dir.name, "committed": committed,
    }))


def _track(project_dir, track_id="foo_20260728"):
    return project_dir / "conductor" / "tracks" / track_id


class BriefGrillTripwireTests(TestCase):
    def test_deny_brief_write_during_incomplete_grill(self):
        """A brief.md write while the marker is committed:false and the counter
        is below the floor must be DENIED — the §3 grill is incomplete and a
        brief written from guesses pollutes downstream planning."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            dec = _probe(Path(d), "Write", str(td / "brief.md"))
            self.assertEqual(dec, "deny")

    def test_allow_brief_write_after_grill_floor_reached(self):
        """Once MIN_GRILL_QUESTIONS AskUserQuestion turns are recorded, the
        brief.md write is allowed — the grill is sufficiently complete."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            for _ in range(_MIN_FLOOR):
                _probe(Path(d), "AskUserQuestion", cwd=str(td))
            dec = _probe(Path(d), "Write", str(td / "brief.md"))
            self.assertIsNone(dec)  # allow

    def test_askuserquestion_increments_counter(self):
        """The AskUserQuestion matcher must increment the per-track counter —
        without it the floor can never be reached and every brief write is
        blocked forever."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            # One short of the floor → still denied.
            for _ in range(_MIN_FLOOR - 1):
                _probe(Path(d), "AskUserQuestion", cwd=str(td))
            self.assertEqual(_probe(Path(d), "Write", str(td / "brief.md")), "deny")
            # One more AskUserQuestion reaches the floor → allowed.
            _probe(Path(d), "AskUserQuestion", cwd=str(td))
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))

    def test_allow_brief_write_when_marker_finalized(self):
        """A committed:true marker means the brief is durable (post-§5 finalize)
        — a re-edit must NOT be blocked even if the counter is low."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=True)
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))

    def test_allow_brief_write_when_no_marker(self):
        """No marker (pre-init, or a human editing an old brief outside the
        skill) → allow. The guard only constrains an active grill."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            td.mkdir(parents=True, exist_ok=True)
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))

    def test_allow_non_brief_write_during_grill(self):
        """Only brief.md is gated — other writes pass through even mid-grill."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            self.assertIsNone(_probe(Path(d), "Write", str(td / "notes.md")))

    def test_corrupt_marker_fail_opens_to_allow(self):
        """A corrupt marker must NOT crash or block — fail-open (allow), mirroring
        the other conductor guards: a misbehaving guard is worse than none."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            cdir = td / ".conductor"
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "brief-progress.json").write_text("{ not json")
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))


if __name__ == "__main__":
    main()
