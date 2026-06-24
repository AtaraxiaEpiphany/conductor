"""Tests for background garbage collection (#10).

Covers the shared safe-GC primitive, the `gc` regression, the `gc-all` sweep,
and the Stop hook wiring that promotes orphaned-artifact cleanup from
manual-per-track to automatic-every-session-end. Stale in_progress locks are
deliberately left to explicit recover/validate --fix — the safe subset only.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.quality import _gc_safe_artifacts, _has_in_progress_task, cmd_gc
from scripts.track_state.misc import cmd_gc_all, _registry_track_dirs

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "scripts" / "state-consistency-check.py"


def _capture(fn, *args, **kwargs):
    """Run a track_state command that calls out(); return parsed stdout JSON."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _state(task_status="completed", updated_hours_ago=0):
    updated = (datetime.now(timezone.utc) - timedelta(hours=updated_hours_ago)).isoformat()
    return {
        "track_id": "t", "status": "in_progress" if task_status == "in_progress" else "completed",
        "current_phase_index": 1, "current_task_index": 1, "updated_at": updated,
        "phases": [{"name": "Phase 1", "status": "in_progress",
                    "tasks": [{"name": "Task A", "status": task_status,
                               "commit_sha": "abc1234"}]}],
    }


def _make_track(task_status="completed", updated_hours_ago=0):
    """Temp track dir with track-state.json + .conductor/. No plan.md."""
    d = tempfile.mkdtemp()
    (Path(d) / "track-state.json").write_text(json.dumps(_state(task_status, updated_hours_ago)))
    (Path(d) / ".conductor").mkdir()
    return d


def _run_hook(cwd, data_dir):
    payload = json.dumps({"hook_event_name": "Stop", "cwd": str(cwd)})
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    proc = subprocess.run(
        ["python3", str(HOOK)], input=payload, capture_output=True,
        text=True, env=env, cwd=str(REPO))
    assert proc.returncode == 0, f"hook failed: {proc.stderr}"
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------- #
# _has_in_progress_task
# --------------------------------------------------------------------------- #
class TestHasInProgress(TestCase):
    def test_parent_in_progress(self):
        self.assertTrue(_has_in_progress_task({"phases": [{"tasks": [{"status": "in_progress"}]}]}))

    def test_subtask_in_progress(self):
        self.assertTrue(_has_in_progress_task(
            {"phases": [{"tasks": [{"status": "completed",
                                     "subtasks": [{"status": "in_progress"}]}]}]}))

    def test_all_terminal(self):
        self.assertFalse(_has_in_progress_task(
            {"phases": [{"tasks": [{"status": "completed"}]}]}))

    def test_empty(self):
        self.assertFalse(_has_in_progress_task({"phases": []}))


# --------------------------------------------------------------------------- #
# _gc_safe_artifacts
# --------------------------------------------------------------------------- #
class TestGcSafeArtifacts(TestCase):
    def test_removes_orphaned_temp_files(self):
        d = _make_track()
        self.addCleanup(shutil.rmtree, d)
        (Path(d, ".track-state.json.tmp.1")).write_text("x")
        (Path(d, ".result.tmp.2")).write_text("x")
        (Path(d, ".conductor", ".result.tmp.3")).write_text("x")
        fixes = _gc_safe_artifacts(d)
        self.assertEqual(len(fixes), 3)
        self.assertFalse(Path(d, ".track-state.json.tmp.1").exists())
        self.assertFalse(Path(d, ".result.tmp.2").exists())
        self.assertFalse(Path(d, ".conductor", ".result.tmp.3").exists())

    def test_removes_orphaned_result_json_when_no_active(self):
        d = _make_track(task_status="completed")
        self.addCleanup(shutil.rmtree, d)
        rj = Path(d, ".conductor", "result.json"); rj.write_text("{}")
        fixes = _gc_safe_artifacts(d)
        self.assertTrue(any("result.json" in f for f in fixes))
        self.assertFalse(rj.exists())

    def test_preserves_result_json_when_task_in_progress(self):
        d = _make_track(task_status="in_progress")
        self.addCleanup(shutil.rmtree, d)
        rj = Path(d, ".conductor", "result.json"); rj.write_text("{}")
        fixes = _gc_safe_artifacts(d)
        self.assertFalse(any("Removed" in f and "result.json" in f for f in fixes))
        self.assertTrue(rj.exists())

    def test_preserves_result_json_when_state_unreadable(self):
        # Conservative: cannot confirm no active task -> do not delete.
        d = _make_track()
        self.addCleanup(shutil.rmtree, d)
        # Corrupt the state (no .bak to fall back to).
        (Path(d, "track-state.json")).write_text("{not json")
        rj = Path(d, ".conductor", "result.json"); rj.write_text("{}")
        fixes = _gc_safe_artifacts(d)
        self.assertTrue(rj.exists())

    def test_idempotent(self):
        d = _make_track(task_status="completed")
        self.addCleanup(shutil.rmtree, d)
        (Path(d, ".conductor", "result.json")).write_text("{}")
        first = _gc_safe_artifacts(d)
        second = _gc_safe_artifacts(d)
        self.assertTrue(first)            # cleaned something
        self.assertEqual(second, [])      # nothing left to clean

    def test_no_conductor_dir_no_crash(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        (Path(d, "track-state.json")).write_text(json.dumps(_state()))
        self.assertEqual(_gc_safe_artifacts(d), [])


# --------------------------------------------------------------------------- #
# cmd_gc regression (behavior preserved after refactor)
# --------------------------------------------------------------------------- #
class TestCmdGc(TestCase):
    def test_removes_orphaned_result_and_no_stale(self):
        d = _make_track(task_status="completed", updated_hours_ago=1)
        self.addCleanup(shutil.rmtree, d)
        (Path(d, ".conductor", "result.json")).write_text("{}")
        out = _capture(cmd_gc, d)
        self.assertEqual(out["stale_count"], 0)
        self.assertTrue(any("result.json" in f for f in out["fixes"]))
        self.assertIn("age_hours", out)

    def test_reports_stale_lock_without_resetting(self):
        d = _make_track(task_status="in_progress", updated_hours_ago=25)
        self.addCleanup(shutil.rmtree, d)
        out = _capture(cmd_gc, d)
        self.assertEqual(out["stale_count"], 1)
        self.assertTrue(any("Stale in_progress" in f for f in out["fixes"]))
        # Lock NOT reset — still in_progress.
        st = json.loads((Path(d, "track-state.json")).read_text())
        self.assertEqual(st["phases"][0]["tasks"][0]["status"], "in_progress")

    def test_preserves_result_when_active(self):
        d = _make_track(task_status="in_progress")
        self.addCleanup(shutil.rmtree, d)
        rj = Path(d, ".conductor", "result.json"); rj.write_text("{}")
        out = _capture(cmd_gc, d)
        self.assertTrue(any("Skipped" in f and "active task" in f for f in out["fixes"]))
        self.assertTrue(rj.exists())


# --------------------------------------------------------------------------- #
# gc-all sweep
# --------------------------------------------------------------------------- #
class TestGcAll(TestCase):
    def _project(self, tracks):
        """tracks: dict rel -> (task_status, extras:list[Path->write])."""
        cwd = tempfile.mkdtemp()
        cond = Path(cwd, "conductor"); cond.mkdir()
        lines = ["# Tracks\n"]
        for rel in tracks:
            lines.append(f"- [{rel}]({rel}/)\n")
            td = Path(cwd, rel); td.mkdir(); (td / ".conductor").mkdir()
            status, extras = tracks[rel]
            (td / "track-state.json").write_text(json.dumps(_state(status)))
            for maker in extras:
                maker(td)
        (cond / "tracks.md").write_text("".join(lines))
        return cwd

    def test_sweeps_multiple_tracks(self):
        cwd = self._project({
            "track-a": ("completed", [lambda td: (td / ".conductor" / "result.json").write_text("{}")]),
            "track-b": ("completed", [lambda td: (td / ".track-state.json.tmp.x").write_text("x")]),
        })
        self.addCleanup(shutil.rmtree, cwd)
        out = _capture(cmd_gc_all, cwd)
        self.assertEqual(out["tracks_scanned"], 2)
        self.assertEqual(out["tracks_cleaned"], 2)
        self.assertEqual(out["total_fixes"], 2)

    def test_no_registry(self):
        cwd = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, cwd)
        out = _capture(cmd_gc_all, cwd)
        self.assertEqual(out["tracks_scanned"], 0)
        self.assertEqual(out["tracks_cleaned"], 0)

    def test_skips_dirs_without_state(self):
        cwd = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, cwd)
        cond = Path(cwd, "conductor"); cond.mkdir()
        (cond / "tracks.md").write_text("# Tracks\n- [ghost](ghost/)\n")
        (Path(cwd, "ghost")).mkdir()  # no track-state.json
        out = _capture(cmd_gc_all, cwd)
        self.assertEqual(out["tracks_scanned"], 0)

    def test_preserves_active_result(self):
        cwd = self._project({
            "track-a": ("in_progress", [lambda td: (td / ".conductor" / "result.json").write_text("{}")]),
        })
        self.addCleanup(shutil.rmtree, cwd)
        out = _capture(cmd_gc_all, cwd)
        self.assertEqual(out["tracks_cleaned"], 0)  # active -> not cleaned
        self.assertTrue((Path(cwd, "track-a", ".conductor", "result.json")).exists())

    def test_registry_track_dirs_helper(self):
        cwd = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, cwd)
        cond = Path(cwd, "conductor"); cond.mkdir()
        (cond / "tracks.md").write_text(
            "# Tracks\n- [a](a/)\n- [ext](https://x)\n- [abs](/abs)\n")
        self.assertEqual(_registry_track_dirs(cwd), ["a/"])


# --------------------------------------------------------------------------- #
# Stop hook background GC (subprocess)
# --------------------------------------------------------------------------- #
class TestStopHookGc(TestCase):
    def _project(self, task_status, with_result=False, with_temp=False):
        cwd = tempfile.mkdtemp()
        cond = Path(cwd, "conductor"); cond.mkdir()
        (cond / "tracks.md").write_text("# Tracks\n- [a](track-a/)\n")
        td = Path(cwd, "track-a"); td.mkdir(); (td / ".conductor").mkdir()
        (td / "plan.md").write_text("# Plan\n\n## Phase 1\n- [ ] Task A\n")
        (td / "track-state.json").write_text(json.dumps(_state(task_status)))
        rj = None
        if with_result:
            rj = td / ".conductor" / "result.json"; rj.write_text("{}")
        tmp = None
        if with_temp:
            tmp = td / ".track-state.json.tmp.9"; tmp.write_text("x")
        return cwd, rj, tmp

    def test_hook_cleans_orphaned_result(self):
        cwd, rj, _ = self._project("completed", with_result=True)
        self.addCleanup(shutil.rmtree, cwd)
        data = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, data)
        _run_hook(cwd, data)
        self.assertFalse(rj.exists())

    def test_hook_preserves_result_when_active(self):
        cwd, rj, _ = self._project("in_progress", with_result=True)
        self.addCleanup(shutil.rmtree, cwd)
        data = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, data)
        _run_hook(cwd, data)
        self.assertTrue(rj.exists())

    def test_hook_cleans_temp_file(self):
        cwd, _, tmp = self._project("in_progress", with_temp=True)
        self.addCleanup(shutil.rmtree, cwd)
        data = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, data)
        _run_hook(cwd, data)
        self.assertFalse(tmp.exists())


if __name__ == "__main__":
    main()
