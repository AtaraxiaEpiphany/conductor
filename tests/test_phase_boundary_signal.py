"""Tests for fix/phase-boundary-checkpoint-signal.

Uniform contract: every phase-routing command (phase-done, complete, skip) surfaces
`phase_checkpoint_pending` + `next_action` when a transition concludes a phase that
lacks a `[checkpoint: sha]` marker in plan.md. A checkpointed phase does not re-signal.
Also: `cmd_complete` (the recovery-route completion path) now self-commits so the
working tree is left clean instead of dirty for a manual "Fix state consistency".
"""
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save
from scripts.track_state.misc import cmd_phase_done, cmd_add_checkpoint
from scripts.track_state.cmd_complete import cmd_complete
from scripts.track_state.mutations import cmd_skip


def _out_captured(fn, *args, **kwargs):
    """Capture stdout JSON from a command fn. Returns (result_dict, stderr_text)."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue()), sys.stderr.getvalue()
    finally:
        sys.stdout, sys.stderr = old_out, old_err


_PLAN = "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n"


def _phase_with(task_status, sha=""):
    """Single-phase, single-task state."""
    task = {"name": "Task A", "status": task_status}
    if sha:
        task["commit_sha"] = sha
    return {
        "track_id": "test", "type": "feature", "status": "in_progress",
        "description": "test", "current_phase_index": 1, "current_task_index": 1,
        "updated_at": "2026-06-19T00:00:00+00:00",
        "phases": [{"name": "Phase 1", "status": "pending", "tasks": [task]}],
    }


def _make_track(state, plan=_PLAN):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text(plan)
    save(d, state)
    return d


def _git_init(d):
    """Init a git repo + initial commit so conductor self-commits succeed."""
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=d, check=True)


class TestPhaseDoneSignal(TestCase):
    def test_surfaces_signal_when_phase_complete_uncheckpointed(self):
        d = _make_track(_phase_with("completed", "abc1234"))
        res, _ = _out_captured(cmd_phase_done, d, 1)
        self.assertTrue(res["complete"])
        self.assertEqual(res["phase_checkpoint_pending"], 1)
        self.assertEqual(res["next_action"], "dispatch_phase_checker")

    def test_no_signal_when_phase_incomplete(self):
        d = _make_track(_phase_with("pending"))
        res, _ = _out_captured(cmd_phase_done, d, 1)
        self.assertFalse(res["complete"])
        self.assertNotIn("phase_checkpoint_pending", res)
        self.assertNotIn("next_action", res)

    def test_no_signal_after_checkpoint_marker(self):
        d = _make_track(_phase_with("completed", "abc1234"))
        # add-checkpoint writes [checkpoint: sha] on the phase heading; phase-done
        # reads plan.md directly (no sync in between) so the marker is honored.
        _out_captured(cmd_add_checkpoint, d, 1, "deadbee")
        res, _ = _out_captured(cmd_phase_done, d, 1)
        self.assertTrue(res["complete"])
        self.assertNotIn("phase_checkpoint_pending", res)


class TestCompleteSelfCommitAndSignal(TestCase):
    def test_self_commits_and_leaves_clean_tree(self):
        d = _make_track(_phase_with("pending"))
        _git_init(d)
        res, _ = _out_captured(cmd_complete, d, 1, 1, None, "abc1234")
        self.assertTrue(res["ok"])
        self.assertTrue(res["committed"])
        # The conductor-managed state files MUST be committed (the point of the
        # self-commit). track-state.json.bak is a save() backup artifact that
        # _git_commit intentionally does not stage, so it may churn — exclude it.
        st = subprocess.run(["git", "status", "--porcelain"], cwd=d,
                            capture_output=True, text=True)
        dirty = {line[3:] for line in st.stdout.splitlines() if len(line) > 3}
        self.assertNotIn("track-state.json", dirty, "track-state.json must be committed")
        self.assertNotIn("plan.md", dirty, "plan.md must be committed")
        log = subprocess.run(["git", "log", "--oneline"], cwd=d,
                             capture_output=True, text=True)
        self.assertIn("chore(conductor): Complete 'Task A'", log.stdout)

    def test_surfaces_signal_when_concluding_uncheckpointed_phase(self):
        d = _make_track(_phase_with("pending"))
        _git_init(d)
        res, _ = _out_captured(cmd_complete, d, 1, 1, None, "abc1234")
        self.assertEqual(res["phase_checkpoint_pending"], 1)
        self.assertEqual(res["next_action"], "dispatch_phase_checker")

    def test_no_signal_when_phase_already_checkpointed(self):
        d = _make_track(_phase_with("pending"))
        _git_init(d)
        # Checkpoint the phase heading BEFORE completing → concluding it won't signal.
        _out_captured(cmd_add_checkpoint, d, 1, "deadbee")
        res, _ = _out_captured(cmd_complete, d, 1, 1, None, "abc1234")
        self.assertTrue(res["ok"])
        self.assertNotIn("phase_checkpoint_pending", res)


class TestSkipSignal(TestCase):
    def test_surfaces_signal_when_concluding_phase(self):
        d = _make_track(_phase_with("pending"))
        res, _ = _out_captured(cmd_skip, d, 1, 1, None, "not needed")
        self.assertTrue(res["ok"])
        self.assertEqual(res["phase_checkpoint_pending"], 1)
        self.assertEqual(res["next_action"], "dispatch_phase_checker")

    def test_no_signal_when_phase_still_has_work(self):
        state = _phase_with("pending")
        state["phases"][0]["tasks"] = [
            {"name": "Task A", "status": "pending"},
            {"name": "Task B", "status": "pending"},
        ]
        d = _make_track(state)
        res, _ = _out_captured(cmd_skip, d, 1, 1, None, "not needed")
        self.assertNotIn("phase_checkpoint_pending", res)


if __name__ == "__main__":
    main()
