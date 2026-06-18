"""Tests for the F3 coverage gate in cmd_dispatch_finalize (scripts/track_state/dispatch.py).

A non-exempt code task reporting a real result.json must supply coverage_pct >=
COVERAGE_THRESHOLD, else finalize refuses to complete (task stays in_progress,
result.json removed, handoff note recorded). Exempt tags and synthesized
results skip the gate.
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase

from scripts.track_state.core import load, save
from scripts.track_state.dispatch import cmd_dispatch_finalize


def _out_captured(fn, *args, **kwargs):
    """Capture stdout JSON from a direct function call."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue()), sys.stderr.getvalue()
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_git_track_dir(task_name="Task A"):
    """Temp dir with git repo + plan.md + track-state.json (task 1 in_progress)."""
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text(f"# Plan\n\n## Phase 1: Build\n- [ ] {task_name}\n")
    state = {
        "track_id": "test", "type": "feature", "status": "in_progress",
        "description": "test track",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{"name": "Phase 1", "status": "pending",
                    "tasks": [{"name": task_name, "status": "in_progress"}]}],
    }
    save(d, state)
    for args in (
        ["git", "init", d],
        ["git", "-C", d, "config", "user.email", "test@test.com"],
        ["git", "-C", d, "config", "user.name", "Test"],
    ):
        subprocess.run(args, capture_output=True, check=True)
    Path(d, "README.md").write_text("# test")
    subprocess.run(["git", "-C", d, "add", "README.md"], capture_output=True, check=True)
    subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, check=True)
    return d


def _write_result(d, **fields):
    """Write .conductor/result.json; returns its path. Defaults to a SUCCESS result."""
    cond = Path(d, ".conductor")
    cond.mkdir(exist_ok=True)
    result = {
        "status": "SUCCESS", "commit_sha": "abc1234", "summary": "Done",
        "phase": 1, "task": 1, "subtask": None, "task_name": "Task A",
    }
    result.update(fields)
    path = cond / "result.json"
    path.write_text(json.dumps(result))
    return path


def _task0(d):
    return load(d)["phases"][0]["tasks"][0]


class TestCoverageGate(TestCase):
    def setUp(self):
        self.d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_passes_when_coverage_above_threshold(self):
        _write_result(self.d, coverage_pct=94)
        out, _ = _out_captured(cmd_dispatch_finalize, self.d)
        self.assertEqual(out.get("status"), "success")
        self.assertEqual(_task0(self.d)["status"], "completed")

    def test_refused_when_coverage_below_threshold(self):
        result_path = _write_result(self.d, coverage_pct=45, files_changed="src/app.py")
        out, _ = _out_captured(cmd_dispatch_finalize, self.d)
        self.assertEqual(out.get("status"), "coverage_gate_failed")
        # No state mutation, no commit, result.json removed.
        self.assertEqual(_task0(self.d)["status"], "in_progress")
        self.assertEqual(_task0(self.d).get("commit_sha", ""), "")
        self.assertFalse(result_path.exists())
        # The gap was recorded in a handoff file under .conductor.
        texts = [p.read_text() for p in Path(self.d, ".conductor").rglob("*.md")]
        self.assertTrue(any("Coverage gate failed" in t for t in texts),
                        "coverage gap not recorded in handoff")

    def test_refused_when_coverage_missing(self):
        result_path = _write_result(self.d)  # no coverage_pct at all
        out, _ = _out_captured(cmd_dispatch_finalize, self.d)
        self.assertEqual(out.get("status"), "coverage_gate_failed")
        self.assertFalse(result_path.exists())
        self.assertEqual(_task0(self.d)["status"], "in_progress")

    def test_exempt_task_passes_without_coverage(self):
        d = _make_git_track_dir(task_name="Tidy up [Chore]")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_result(d, task_name="Tidy up [Chore]")  # exempt: no coverage_pct needed
        out, _ = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(out.get("status"), "success")
        self.assertEqual(_task0(d)["status"], "completed")

    def test_synthesized_result_skips_gate(self):
        # No result.json; a real code commit makes the synthesized result SUCCESS.
        Path(self.d, "code.ts").write_text("// impl")
        subprocess.run(["git", "-C", self.d, "add", "code.ts"], capture_output=True, check=True)
        subprocess.run(["git", "-C", self.d, "commit", "-m", "feat: impl"],
                       capture_output=True, check=True)
        out, _ = _out_captured(cmd_dispatch_finalize, self.d)
        self.assertEqual(out.get("status"), "success")
        self.assertEqual(_task0(self.d)["status"], "completed")
