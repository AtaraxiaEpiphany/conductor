"""Tests for lib.locked_task.resolve — the active-task scoping aid shared by the
SubagentStop recovery flow and the result freshness probe."""
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.locked_task import resolve, _locked_indices


def _state(pi, ti, si=None, status="in_progress"):
    """A minimal track-state with one phase/one task, current index pointing at it."""
    sub = None
    if si is not None:
        sub = [{"name": "child", "status": status if si == 1 else "pending"}]
    return {
        "current_phase_index": pi,
        "current_task_index": ti,
        "current_subtask_index": si,
        "phases": [{"tasks": [{"name": "t", "status": "in_progress",
                               **({"subtasks": sub} if sub else {})}]}],
    }


class LockedIndicesTests(TestCase):
    def test_resolves_flat_task(self):
        self.assertEqual(_locked_indices(_state(1, 1)), (1, 1, None))

    def test_resolves_subtask(self):
        self.assertEqual(_locked_indices(_state(1, 1, 1)), (1, 1, 1))

    def test_none_when_no_indices(self):
        self.assertIsNone(_locked_indices({"current_phase_index": 0, "current_task_index": 0, "phases": []}))

    def test_none_when_target_not_in_progress(self):
        st = _state(1, 1)
        st["phases"][0]["tasks"][0]["status"] = "completed"
        self.assertIsNone(_locked_indices(st))


class ResolveTests(TestCase):
    def _track(self, root, track_id, state):
        tdir = Path(root) / "conductor" / "tracks" / track_id
        tdir.mkdir(parents=True)
        (tdir / "track-state.json").write_text(json.dumps(state))
        return tdir

    def test_finds_locked_track(self):
        with tempfile.TemporaryDirectory() as d:
            self._track(d, "alpha_20260628", _state(1, 1))
            track_dir, p, t, s = resolve(d)
            self.assertTrue(track_dir.endswith("alpha_20260628"))
            self.assertEqual((p, t, s), (1, 1, None))

    def test_skips_idle_track_finds_active_one(self):
        with tempfile.TemporaryDirectory() as d:
            idle = {"current_phase_index": 0, "current_task_index": 0, "phases": []}
            self._track(d, "alpha_20260628", idle)
            self._track(d, "beta_20260628", _state(1, 1, 1))
            track_dir, p, t, s = resolve(d)
            self.assertTrue(track_dir.endswith("beta_20260628"))
            self.assertEqual((p, t, s), (1, 1, 1))

    def test_none_when_all_tracks_idle(self):
        with tempfile.TemporaryDirectory() as d:
            idle = {"current_phase_index": 0, "current_task_index": 0, "phases": []}
            self._track(d, "alpha_20260628", idle)
            self.assertIsNone(resolve(d))

    def test_none_when_no_tracks(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "conductor", "tracks").mkdir(parents=True)
            self.assertIsNone(resolve(d))

    def test_skips_malformed_state_file(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = Path(d) / "conductor" / "tracks" / "broken_20260628"
            tdir.mkdir(parents=True)
            (tdir / "track-state.json").write_text("{ not json")
            self.assertIsNone(resolve(d))  # does not raise


if __name__ == "__main__":
    main()
