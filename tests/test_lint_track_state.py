"""Tests for scripts/lint-track-state.py — boundary-enforcement linter (F1/F4).

The linter is the CI/pre-commit enforcer for two Execution Firewall rules:
  F1 — at most 2 in_progress tasks (1 parent + 1 subtask) per track (state lock)
  F4 — terminal tasks must carry a commit SHA (failed excluded: _do_fail never
       sets one, so requiring it would be a perpetual false positive)
Plus state-consistency (delegates to track-state validate) and stale-state
warnings. Previously 0% covered. Hyphenated module loaded by path; main()
exercised via subprocess.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "scripts" / "lint-track-state.py"

_SCRIPTS_DIR = str(REPO / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _load_linter():
    spec = importlib.util.spec_from_file_location("lint_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(d, state):
    (Path(d) / "track-state.json").write_text(json.dumps(state))


def _run_main(cwd):
    proc = subprocess.run(
        ["python3", str(HOOK), str(cwd)],
        capture_output=True, text=True, cwd=str(REPO))
    return proc.returncode, proc.stdout


def _clean_state():
    return {
        "track_id": "t", "type": "feature", "status": "in_progress",
        "description": "d", "current_phase_index": 1, "current_task_index": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "phases": [{"name": "Phase 1", "status": "in_progress",
                    "tasks": [{"name": "Task A", "status": "in_progress"}]}],
    }


# --------------------------------------------------------------------------- #
# F1: Global State Lock (max 2 in_progress)
# --------------------------------------------------------------------------- #
class TestF1Rule(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_linter()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.f = Path(self.d, "track-state.json")

    def test_two_in_progress_allowed(self):
        st = _clean_state()
        st["phases"][0]["tasks"][0]["subtasks"] = [
            {"name": "Sub", "status": "in_progress"}]
        _write(self.d, st)  # 1 parent + 1 subtask = 2 -> OK
        ok, err = self.mod.check_f1_rule(self.f)
        self.assertTrue(ok, err)
        self.assertIsNone(err)

    def test_three_in_progress_violation(self):
        st = _clean_state()
        st["phases"][0]["tasks"].append({"name": "Task B", "status": "in_progress"})
        st["phases"][0]["tasks"][0]["subtasks"] = [
            {"name": "Sub", "status": "in_progress"}]
        _write(self.d, st)  # 2 parents + 1 subtask = 3 -> VIOLATION
        ok, err = self.mod.check_f1_rule(self.f)
        self.assertFalse(ok)
        self.assertIn("3 in_progress", err)
        self.assertIn("max 2", err)

    def test_missing_required_fields(self):
        _write(self.d, {"track_id": "t"})  # no status/phases
        ok, err = self.mod.check_f1_rule(self.f)
        self.assertFalse(ok)

    def test_unreadable_file(self):
        ok, err = self.mod.check_f1_rule(Path(self.d, "nope.json"))
        self.assertFalse(ok)


# --------------------------------------------------------------------------- #
# F4: SHA must exist for terminal tasks
# --------------------------------------------------------------------------- #
class TestF4Rule(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_linter()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.f = Path(self.d, "track-state.json")

    def test_terminal_with_sha_ok(self):
        for status in ("completed", "skipped", "deferred", "blocked", "cancelled"):
            st = _clean_state()
            st["phases"][0]["tasks"][0] = {
                "name": "T", "status": status, "commit_sha": "abc1234"}
            _write(self.d, st)
            ok, err = self.mod.check_f4_rule(self.f)
            self.assertTrue(ok, f"{status}: {err}")

    def test_terminal_missing_sha_violation(self):
        st = _clean_state()
        st["phases"][0]["tasks"][0] = {"name": "T", "status": "completed"}
        _write(self.d, st)
        ok, err = self.mod.check_f4_rule(self.f)
        self.assertFalse(ok)
        self.assertIn("Missing commit SHAs", err)

    def test_failed_missing_sha_ok(self):
        # Carve-out: _do_fail never sets commit_sha, so 'failed' is excluded.
        st = _clean_state()
        st["phases"][0]["tasks"][0] = {"name": "T", "status": "failed"}
        _write(self.d, st)
        ok, _ = self.mod.check_f4_rule(self.f)
        self.assertTrue(ok)

    def test_subtask_terminal_missing_sha_violation(self):
        st = _clean_state()
        st["phases"][0]["tasks"][0]["subtasks"] = [
            {"name": "Sub", "status": "completed"}]
        _write(self.d, st)
        ok, err = self.mod.check_f4_rule(self.f)
        self.assertFalse(ok)
        self.assertIn("P1.T1.S1: Sub", err)

    def test_unreadable_file(self):
        ok, _ = self.mod.check_f4_rule(Path(self.d, "nope.json"))
        self.assertFalse(ok)


# --------------------------------------------------------------------------- #
# Stale state warning
# --------------------------------------------------------------------------- #
class TestStaleState(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_linter()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.f = Path(self.d, "track-state.json")

    def _age(self, hours):
        old = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
        os.utime(self.f, (old, old))

    def test_fresh_active_ok(self):
        _write(self.d, _clean_state())  # in_progress + recent
        ok, _ = self.mod.check_stale_state(self.f)
        self.assertTrue(ok)

    def test_old_active_warns(self):
        _write(self.d, _clean_state())
        self._age(25)
        ok, msg = self.mod.check_stale_state(self.f)
        self.assertFalse(ok)
        self.assertIn("hours old", msg)

    def test_old_without_active_ok(self):
        st = _clean_state()
        st["phases"][0]["tasks"][0]["status"] = "completed"
        _write(self.d, st)
        self._age(25)
        # Stale file but no active task -> not a stale LOCK, so no warning.
        ok, _ = self.mod.check_stale_state(self.f)
        self.assertTrue(ok)


# --------------------------------------------------------------------------- #
# State consistency (delegates to track-state validate)
# --------------------------------------------------------------------------- #
class TestStateConsistency(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_linter()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.f = Path(self.d, "track-state.json")

    def test_valid_state_passes(self):
        _write(self.d, _clean_state())
        ok, err = self.mod.check_state_consistency(self.f)
        self.assertTrue(ok, err)


# --------------------------------------------------------------------------- #
# main() end-to-end via subprocess (exit code contract)
# --------------------------------------------------------------------------- #
class TestMain(TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cwd)
        Path(self.cwd, "conductor").mkdir()
        (Path(self.cwd, "conductor", "tracks.md")).write_text(
            "# Tracks\n- [t](track-t/)\n")
        self.track = Path(self.cwd, "track-t"); self.track.mkdir()
        (self.track / "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")

    def test_main_f1_violation_exits_1(self):
        st = _clean_state()
        st["phases"][0]["tasks"].append({"name": "B", "status": "in_progress"})
        st["phases"][0]["tasks"][0]["subtasks"] = [
            {"name": "Sub", "status": "in_progress"}]
        _write(self.track, st)
        code, out = _run_main(self.cwd)
        self.assertEqual(code, 1)
        self.assertIn("[F1 ERROR]", out)

    def test_main_clean_track_exits_0(self):
        _write(self.track, _clean_state())
        code, out = _run_main(self.cwd)
        self.assertEqual(code, 0)
        self.assertIn("[F1 PASS]", out)
        self.assertIn("[F4 PASS]", out)

    def test_main_no_tracks_md_exits_0(self):
        empty = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, empty)
        code, _ = _run_main(empty)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    main()
