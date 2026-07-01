"""Tests for the wave F1 guards: validate, lint, dispatch refusal, SubagentStop.

Exercises the four sites that reconcile F1 (Global State Lock) with wave mode:
  - validate._fix_stale_lock exempts wave members from the stale-lock reaper;
  - validate._validate_state_consistency suppresses the multi-in_progress warning;
  - lint-track-state.check_f1_rule exempts wave parents + emits a relaxation note;
  - dispatch cmd_dispatch_next/prepare/recover refuse with wave_active;
  - on-subagent-stop._wave_agent_track_dir detects the marker → allow stop.

conftest puts scripts/ on sys.path, so ``track_state`` and ``lib`` import as in
production; the standalone scripts (lint-track-state, on-subagent-stop) load via
importlib from that same path.
"""
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from track_state.core import save, load
from track_state.quality import _CONDUCTOR_GITIGNORE
from track_state.wave import cmd_dispatch_wave, _wave_ledger_path
from track_state.validate import _fix_stale_lock, _validate_state_consistency
from track_state.dispatch import (
    cmd_dispatch_next, cmd_dispatch_prepare, cmd_recover,
)

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_script(name):
    """Load a standalone scripts/ module (lint-track-state, on-subagent-stop)."""
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _capture(fn, *args, **kwargs):
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue()), sys.stderr.getvalue()
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _git(d, *args):
    return subprocess.run(["git", "-C", str(d), *args],
                          capture_output=True, text=True, check=True)


def _make_git_track(state, plan_body):
    d = tempfile.mkdtemp()
    _git(d, "init")
    _git(d, "config", "user.email", "test@test.com")
    _git(d, "config", "user.name", "Test")
    Path(d, "README.md").write_text("# base\n")
    _git(d, "add", "README.md")
    _git(d, "commit", "-m", "init")
    Path(d, "plan.md").write_text(plan_body)
    cond = Path(d, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / ".gitignore").write_text(_CONDUCTOR_GITIGNORE)
    save(d, state)
    return d


def _plan(n):
    lines = ["# Plan", "", "## Phase 1: Build"]
    for i in range(1, n + 1):
        lines.append(f"- [ ] Task {i}: t{i} <!-- deps: -->")
    return "\n".join(lines) + "\n"


def _state(n):
    return {
        "track_id": "f1test", "type": "feature", "status": "in_progress",
        "current_phase_index": 1, "current_task_index": 0,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "phases": [{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": f"Task {i}: t{i}", "status": "pending"} for i in range(1, n + 1)]}],
    }


class _WaveFixture(unittest.TestCase):
    """A git track with an active 3-member wave; members' locks aged stale."""
    def _wave(self, n=3):
        d = _make_git_track(_state(n), _plan(n))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        wave = _capture(cmd_dispatch_wave, d)[0]
        assert wave["action"] == "dispatch_wave", wave
        # Age every member's lock so the stale-lock reaper WOULD reap absent
        # the wave exemption.
        st = load(d)
        for t in st["phases"][0]["tasks"]:
            t["locked_at"] = time.time() - 99999
        save(d, st)
        return d


class TestValidateStaleLockExemption(_WaveFixture):
    def test_wave_member_not_reaped(self):
        d = self._wave()
        st = load(d)
        fixes = _fix_stale_lock(st, track_dir=d)
        self.assertEqual(fixes, [], f"wave members should be exempt, got: {fixes}")
        self.assertTrue(all(t["status"] == "in_progress"
                            for t in st["phases"][0]["tasks"]))

    def test_non_wave_stale_lock_still_reaped(self):
        d = self._wave()
        st = load(d)
        st["phases"].append({"name": "Phase 2", "tasks": [
            {"name": "lonely", "status": "in_progress",
             "locked_at": time.time() - 99999}]})
        save(d, st)
        st = load(d)
        fixes = _fix_stale_lock(st, track_dir=d)
        self.assertTrue(any("lonely" in f for f in fixes))
        self.assertFalse(any("Task" in f for f in fixes))  # wave members untouched
        self.assertEqual(st["phases"][1]["tasks"][0]["status"], "pending")
        self.assertTrue(all(t["status"] == "in_progress"
                            for t in st["phases"][0]["tasks"]))


class TestValidateConsistencyWarning(_WaveFixture):
    def test_no_multi_in_progress_warning_under_wave(self):
        d = self._wave()
        errors, warnings = [], []
        _validate_state_consistency(load(d), errors, warnings, track_dir=d)
        self.assertFalse(any("multiple in_progress" in w for w in warnings))

    def test_warning_fires_without_wave(self):
        d = self._wave()
        _wave_ledger_path(d).unlink()  # dissolve the wave
        errors, warnings = [], []
        _validate_state_consistency(load(d), errors, warnings, track_dir=d)
        self.assertTrue(any("multiple in_progress" in w for w in warnings))


class TestLintF1Exemption(_WaveFixture):
    def test_check_f1_passes_under_wave_with_note(self):
        linter = _load_script("lint-track-state.py")
        d = self._wave()
        state_file = Path(d, "track-state.json")
        valid, error = linter.check_f1_rule(state_file)
        self.assertTrue(valid, f"F1 should pass under a wave: {error}")
        note = linter.wave_relaxation_note(state_file)
        self.assertIsNotNone(note)
        self.assertIn("3 in_flight", note)

    def test_check_f1_fires_after_wave_dissolved(self):
        linter = _load_script("lint-track-state.py")
        d = self._wave()
        _wave_ledger_path(d).unlink()
        valid, error = linter.check_f1_rule(Path(d, "track-state.json"))
        self.assertFalse(valid)
        self.assertIn("parent tasks in_progress", error)


class TestDispatchMutualExclusion(_WaveFixture):
    def test_dispatch_next_refuses_under_wave(self):
        d = self._wave()
        self.assertEqual(_capture(cmd_dispatch_next, d)[0]["action"], "wave_active")

    def test_dispatch_prepare_refuses_under_wave(self):
        d = self._wave()
        self.assertEqual(_capture(cmd_dispatch_prepare, d)[0]["action"], "wave_active")

    def test_recover_refuses_under_wave(self):
        d = self._wave()
        self.assertEqual(_capture(cmd_recover, d)[0]["status"], "wave_active")

    def test_serial_spine_resumes_after_drain(self):
        d = self._wave()
        ledger = json.loads(_wave_ledger_path(d).read_text())
        for m in ledger["wave"]:
            m["status"] = "finalized"
        _wave_ledger_path(d).write_text(json.dumps(ledger))
        out = _capture(cmd_dispatch_next, d)[0]
        self.assertNotEqual(out.get("action"), "wave_active")


class TestSubagentStopMarker(unittest.TestCase):
    def test_marker_resolves_track_dir(self):
        hook = _load_script("on-subagent-stop.py")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        track = Path(d, "work", "conductor", "tracks", "feat")
        (track / ".conductor").mkdir(parents=True)
        (track / ".conductor" / "wave-agent.marker").write_text("{}")
        # cwd at the track dir → resolves to it.
        self.assertEqual(hook._wave_agent_track_dir(str(track)), str(track))
        # cwd deep under the track dir → walks up to the track dir.
        deep = track / "src" / "pkg"
        deep.mkdir(parents=True)
        self.assertEqual(hook._wave_agent_track_dir(str(deep)), str(track))

    def test_no_marker_returns_none(self):
        hook = _load_script("on-subagent-stop.py")
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertIsNone(hook._wave_agent_track_dir(d))


if __name__ == "__main__":
    unittest.main()
