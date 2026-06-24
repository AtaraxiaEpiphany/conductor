r"""Tests for state-consistency-check.py — the Stop hook that scans for stale
in_progress locks, builds the session-handoff summary, and collects GC metrics.

The pure helpers are exercised directly (the hook also has I/O side effects in
main(); the helpers take explicit paths so they test cleanly).
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "state_consistency_check", _scripts / "state-consistency-check.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
find_stale_in_progress_tasks = _mod.find_stale_in_progress_tasks
get_track_handoff_info = _mod.get_track_handoff_info
collect_gc_metrics = _mod.collect_gc_metrics


def _write_state(d: Path, body):
    p = d / "track-state.json"
    p.write_text(body if isinstance(body, str) else json.dumps(body))
    return p


class FindStaleInProgressTests(TestCase):
    def test_finds_in_progress_task_and_subtask(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_state(Path(d), {
                "phases": [{"tasks": [
                    {"name": "A", "status": "completed",
                     "subtasks": [{"name": "A1", "status": "in_progress"}]},
                    {"name": "B", "status": "in_progress"},
                ]}],
            })
            stale = find_stale_in_progress_tasks(p)
        self.assertEqual(len(stale), 2)
        self.assertTrue(any("Task 1.1" in s and "A1" in s for s in stale))
        self.assertTrue(any("Task 2" in s and "B" in s for s in stale))

    def test_clean_state_has_no_stale(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_state(Path(d), {"phases": [{"tasks": [
                {"name": "A", "status": "completed"}]}]})
            self.assertEqual(find_stale_in_progress_tasks(p), [])

    def test_missing_file_returns_empty(self):
        self.assertEqual(find_stale_in_progress_tasks(Path("/no/such/state.json")), [])


class TrackHandoffInfoTests(TestCase):
    def test_terminal_track_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_state(Path(d), {"status": "completed"})
            self.assertIsNone(get_track_handoff_info(p))

    def test_active_track_with_position(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_state(Path(d), {
                "status": "in_progress", "track_id": "feat",
                "current_phase_index": 2, "current_task_index": 3,
                "execution_mode": "continuous",
            })
            info = get_track_handoff_info(p)
        self.assertIn("feat", info)
        self.assertIn("P2.T3", info)
        self.assertIn("continuous", info)

    def test_active_track_without_position_shows_na(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_state(Path(d), {
                "status": "in_progress", "track_id": "x",
                "current_phase_index": 0, "current_task_index": 0,
            })
            self.assertIn("N/A", get_track_handoff_info(p))


class CollectGcMetricsTests(TestCase):
    def test_counts_archived_stale_and_orphaned(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            a = cwd / "tracks" / "a"; a.mkdir(parents=True)
            _write_state(a, {"status": "archived", "phases": []})

            b = cwd / "tracks" / "b"; b.mkdir(parents=True)
            _write_state(b, {"status": "in_progress",
                             "phases": [{"tasks": [{"name": "x", "status": "in_progress"}]}]})

            c = cwd / "tracks" / "c"; c.mkdir(parents=True)
            _write_state(c, {"status": "completed",
                             "phases": [{"tasks": [{"name": "y", "status": "completed"}]}]})
            (c / ".conductor").mkdir()
            (c / ".conductor" / "result.json").write_text('{"status":"SUCCESS"}')

            m = collect_gc_metrics(cwd, ["tracks/a", "tracks/b", "tracks/c"])

        self.assertEqual(m["total_tracks"], 3)
        self.assertEqual(m["archived_tracks"], 1)
        self.assertEqual(m["stale_tasks"], 1)
        self.assertEqual(m["orphaned_results"], 1)
        self.assertEqual(len(m["gc_warnings"]), 2)  # one for orphaned, one for stale


if __name__ == "__main__":
    main()
