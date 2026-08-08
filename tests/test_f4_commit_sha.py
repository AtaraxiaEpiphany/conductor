"""Tests for ``check_f4_rule`` — the F4 "SHA must exist for terminal tasks" guard.

F4 is shape-agnostic: it only checks that a terminal task (``completed``,
``skipped``, ``deferred``, ``blocked``, ``cancelled`` — ``failed`` excluded
because ``_do_fail`` never sets ``commit_sha``) carries a non-empty
``commit_sha``. This is the integrity guarantee that a "verified against AC-N"
stamp stays load-bearing: a task marked done must point at the real commit that
did the work.

Track B5 adds the non-code regression: a ``deliverable`` (review-grounded) track
produces its artifact as a real ``docs(conductor)``/chore commit via
``cmd_complete`` (``allow_empty=True``), so the same F4 rule holds unchanged —
no code-specific assumption in the guard, no special-case needed. These tests
lock that guarantee in: the non-code path does NOT trip F4.
"""
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest import TestCase, main

# Hyphenated module name — load by path (matches test_f1_state_lock_linter.py).
_spec = importlib.util.spec_from_file_location(
    "lint_track_state",
    Path(__file__).resolve().parent.parent / "scripts" / "lint-track-state.py",
)
_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lint)
check_f4_rule = _lint.check_f4_rule


def _state_file(state):
    tf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(state, tf)
    tf.close()
    return Path(tf.name)


def _state(tasks, shape="default"):
    """A one-phase track-state. ``shape`` is recorded on the track (F4 ignores
    it — the guard is shape-agnostic — but it makes the deliverable regression
    honest: the very state that would trip a code-specific guard passes here)."""
    return {
        "track_id": "t",
        "status": "in_progress",
        "workflow_shape": shape,
        "phases": [{"name": "P1", "status": "in_progress", "tasks": tasks}],
    }


class F4CoreTests(TestCase):
    """The guard: a terminal task without a commit_sha is a violation."""

    def test_completed_with_sha_passes(self):
        ok, _ = check_f4_rule(_state_file(_state([
            {"name": "A", "status": "completed", "commit_sha": "abc123"},
        ])))
        self.assertTrue(ok)

    def test_completed_without_sha_violates(self):
        ok, err = check_f4_rule(_state_file(_state([
            {"name": "A", "status": "completed"},
        ])))
        self.assertFalse(ok)
        self.assertIn("Missing commit SHAs", err)
        self.assertIn("P1.T1", err)

    def test_non_terminal_task_not_checked(self):
        # pending / in_progress tasks need no SHA.
        ok, _ = check_f4_rule(_state_file(_state([
            {"name": "A", "status": "pending"},
            {"name": "B", "status": "in_progress"},
        ])))
        self.assertTrue(ok)

    def test_failed_excluded(self):
        # failed is not terminal here — _do_fail never sets commit_sha, so
        # checking it would always be a false positive (see check_f4_rule doc).
        ok, _ = check_f4_rule(_state_file(_state([
            {"name": "A", "status": "failed"},
        ])))
        self.assertTrue(ok)

    def test_other_terminal_statuses_require_sha(self):
        for status in ("skipped", "deferred", "blocked", "cancelled"):
            ok, err = check_f4_rule(_state_file(_state([
                {"name": "A", "status": status}])))
            self.assertFalse(ok, f"{status} must require a SHA")
            self.assertIn("Missing commit SHAs", err)

    def test_completed_subtask_without_sha_violates(self):
        ok, err = check_f4_rule(_state_file(_state([
            {"name": "A", "status": "in_progress",
             "subtasks": [{"name": "A.1", "status": "completed"}]},
        ])))
        self.assertFalse(ok)
        self.assertIn("P1.T1.S1", err)

    def test_completed_subtask_with_sha_passes(self):
        ok, _ = check_f4_rule(_state_file(_state([
            {"name": "A", "status": "in_progress",
             "subtasks": [{"name": "A.1", "status": "completed",
                           "commit_sha": "deadbee"}]},
        ])))
        self.assertTrue(ok)


class F4DeliverableRegressionTests(TestCase):
    """B5: the non-code (review-grounded) path does NOT trip F4. A deliverable
    task still produces a real commit via ``cmd_complete`` (the artifact is the
    commit's content), so the non-empty-``commit_sha`` rule holds unchanged.
    The guard has no code-specific assumption, so this passes by construction —
    these tests pin that it stays that way."""

    def test_deliverable_completed_with_sha_passes(self):
        ok, _ = check_f4_rule(_state_file(_state([
            {"name": "Author design doc", "status": "completed",
             "commit_sha": "docs123"},
        ], shape="deliverable")))
        self.assertTrue(ok)

    def test_deliverable_terminal_without_sha_still_violates(self):
        # The guard is shape-agnostic: a deliverable task that marked itself
        # terminal WITHOUT a commit is STILL a violation — the freedom a
        # deliverable shape takes (no tests) never extends to "no commit". This
        # is the invariant: every freedom declares an integrity substitute, and
        # the deliverable's substitute is a real commit, not an exemption.
        ok, err = check_f4_rule(_state_file(_state([
            {"name": "Author design doc", "status": "completed"},
        ], shape="deliverable")))
        self.assertFalse(ok)
        self.assertIn("Missing commit SHAs", err)

    def test_deliverable_track_all_terminal_with_shas_passes(self):
        # A realistic completed deliverable phase: every task done with a real
        # commit (docs/chore). F4 is clean.
        ok, _ = check_f4_rule(_state_file(_state([
            {"name": "Author API design", "status": "completed",
             "commit_sha": "aaa111"},
            {"name": "Author runbook", "status": "completed",
             "commit_sha": "bbb222"},
        ], shape="deliverable")))
        self.assertTrue(ok)


if __name__ == "__main__":
    main()
