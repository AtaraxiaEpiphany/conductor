"""Tests for scripts/state-consistency-check.py — Stop-hook audit + GC metrics.

The hook scans every track in conductor/tracks.md for stale in_progress tasks
(left-behind locks), writes a session-handoff file, and collects lightweight GC
metrics (archived tracks, stale tasks, orphaned result.json files). Previously
0% covered. Hyphenated module loaded by path; main() exercised via subprocess.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "scripts" / "state-consistency-check.py"

_SCRIPTS_DIR = str(REPO / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _load_hook():
    spec = importlib.util.spec_from_file_location("scc_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_hook(cwd, data_dir):
    payload = json.dumps({"hook_event_name": "Stop", "cwd": str(cwd)})
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    proc = subprocess.run(
        ["python3", str(HOOK)], input=payload, capture_output=True,
        text=True, env=env, cwd=str(REPO))
    assert proc.returncode == 0, f"hook failed: {proc.stderr}"
    return json.loads(proc.stdout)


def _state(**phases_overrides):
    """Minimal valid state; default has one in_progress task."""
    return {
        "track_id": "t", "status": "in_progress",
        "current_phase_index": 1, "current_task_index": 1,
        "execution_mode": "interactive",
        "phases": [{"name": "Phase 1", "status": "in_progress",
                    "tasks": [{"name": "Task A", "status": "in_progress"}]}],
        **phases_overrides,
    }


def _write_state(d, state):
    (Path(d) / "track-state.json").write_text(json.dumps(state))


# --------------------------------------------------------------------------- #
# Stale in_progress detection
# --------------------------------------------------------------------------- #
class TestStaleInProgress(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.f = Path(self.d, "track-state.json")

    def test_finds_stale_parent_and_subtask(self):
        _write_state(self.d, _state())
        # add an in_progress subtask
        st = json.loads(self.f.read_text())
        st["phases"][0]["tasks"][0]["subtasks"] = [
            {"name": "Sub 1", "status": "in_progress"}]
        self.f.write_text(json.dumps(st))
        stale = self.mod.find_stale_in_progress_tasks(self.f)
        self.assertTrue(any("Task A" in s for s in stale))
        self.assertTrue(any(".1:" in s for s in stale))  # subtask index

    def test_no_stale_when_all_terminal(self):
        st = _state()
        st["phases"][0]["tasks"][0]["status"] = "completed"
        _write_state(self.d, st)
        self.assertEqual(self.mod.find_stale_in_progress_tasks(self.f), [])

    def test_missing_file_returns_empty(self):
        self.assertEqual(self.mod.find_stale_in_progress_tasks(Path(self.d, "no.json")), [])


# --------------------------------------------------------------------------- #
# Handoff info
# --------------------------------------------------------------------------- #
class TestHandoffInfo(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.f = Path(self.d, "track-state.json")

    def test_active_track_position(self):
        _write_state(self.d, _state())
        info = self.mod.get_track_handoff_info(self.f)
        self.assertIn("position=P1.T1", info)
        self.assertIn("status=in_progress", info)

    def test_terminal_track_returns_none(self):
        for terminal in ("completed", "archived", "cancelled"):
            _write_state(self.d, _state(status=terminal))
            self.assertIsNone(self.mod.get_track_handoff_info(self.f))

    def test_no_position_shows_na(self):
        _write_state(self.d, _state(current_phase_index=0, current_task_index=0))
        info = self.mod.get_track_handoff_info(self.f)
        self.assertIn("position=N/A", info)

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.mod.get_track_handoff_info(Path(self.d, "no.json")))


# --------------------------------------------------------------------------- #
# GC metrics
# --------------------------------------------------------------------------- #
class TestGcMetrics(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook()

    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cwd)
        self.track = Path(self.cwd, "track-t")
        self.track.mkdir()
        self.cond = self.track / ".conductor"
        self.cond.mkdir()

    def _metrics(self):
        return self.mod.collect_gc_metrics(Path(self.cwd), ["track-t"])

    def test_archived_track_counted_and_skipped(self):
        _write_state(self.track, _state(status="archived"))
        m = self._metrics()
        self.assertEqual(m["archived_tracks"], 1)
        self.assertEqual(m["stale_tasks"], 0)

    def test_stale_task_counted(self):
        _write_state(self.track, _state())  # in_progress task
        m = self._metrics()
        self.assertEqual(m["stale_tasks"], 1)
        self.assertTrue(any("stale" in w for w in m["gc_warnings"]))

    def test_orphaned_result_detected(self):
        # Terminal task (no active) but a result.json present -> orphaned.
        st = _state()
        st["phases"][0]["tasks"][0]["status"] = "completed"
        _write_state(self.track, st)
        (self.cond / "result.json").write_text("{}")
        m = self._metrics()
        self.assertEqual(m["orphaned_results"], 1)
        self.assertTrue(any("orphaned" in w for w in m["gc_warnings"]))

    def test_active_result_not_orphaned(self):
        _write_state(self.track, _state())  # active task
        (self.cond / "result.json").write_text("{}")
        m = self._metrics()
        self.assertEqual(m["orphaned_results"], 0)

    def test_clean_track_no_warnings(self):
        st = _state()
        st["phases"][0]["tasks"][0]["status"] = "completed"
        _write_state(self.track, st)
        m = self._metrics()
        self.assertEqual(m["gc_warnings"], [])
        self.assertEqual(m["stale_tasks"], 0)


# --------------------------------------------------------------------------- #
# Session handoff file writer
# --------------------------------------------------------------------------- #
class TestWriteSessionHandoff(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.handoff = Path(self.d, "session-handoff.md")

    def test_writes_handoff_with_content(self):
        self.mod.write_session_handoff(Path(self.d), "- Track t: active\n",
                                        gc_summary="GC: ok")
        text = self.handoff.read_text()
        self.assertIn("Active tracks:", text)
        self.assertIn("Track t: active", text)
        self.assertIn("GC: ok", text)

    def test_empty_handoff_removes_existing_file(self):
        self.handoff.write_text("stale")
        self.assertTrue(self.handoff.exists())
        self.mod.write_session_handoff(Path(self.d), "")
        self.assertFalse(self.handoff.exists())


# --------------------------------------------------------------------------- #
# main() end-to-end via subprocess
# --------------------------------------------------------------------------- #
class TestMain(TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cwd)
        self.data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data)
        cond = Path(self.cwd, "conductor"); cond.mkdir()
        (cond / "tracks.md").write_text("# Tracks\n- [t](track-t/)\n")
        self.track = Path(self.cwd, "track-t"); self.track.mkdir()
        # main() only processes a track when BOTH state and plan.md exist.
        (self.track / "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")

    def test_main_warns_on_stale_task(self):
        _write_state(self.track, _state())  # in_progress -> stale
        out = _run_hook(self.cwd, self.data)
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("Stale in_progress", ctx)

    def test_main_clean_track_no_context(self):
        st = _state()
        st["phases"][0]["tasks"][0]["status"] = "completed"
        _write_state(self.track, st)
        out = _run_hook(self.cwd, self.data)
        self.assertNotIn("additionalContext", out.get("hookSpecificOutput", {}))

    def test_main_writes_session_handoff(self):
        _write_state(self.track, _state())
        _run_hook(self.cwd, self.data)
        handoff = Path(self.data, "session-handoff.md")
        self.assertTrue(handoff.exists())
        self.assertIn("Active tracks:", handoff.read_text())


if __name__ == "__main__":
    main()
