"""Tests for check_f1_rule — the F1/V8 global state-lock backstop.

The linter is the server-side guard against >1 unit of work active at once.
Regression coverage for the parent/child conflation bug: it previously lumped
parents and children into one list and thresholded on ">2", admitting two flat
parents (and two children) — both real V8 violations.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# Hyphenated module name — load by path (matches the repo's hook-test convention).
_spec = importlib.util.spec_from_file_location(
    "lint_track_state",
    Path(__file__).resolve().parent.parent / "scripts" / "lint-track-state.py",
)
_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lint)
check_f1_rule = _lint.check_f1_rule


def _state_file(state):
    tf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(state, tf)
    tf.close()
    return Path(tf.name)


def _state(tasks, parents_in_progress=None):
    """Build a one-phase state. tasks: list of task dicts."""
    return {
        "track_id": "t",
        "status": "in_progress",
        "phases": [{"name": "P1", "status": "in_progress", "tasks": tasks}],
    }


class F1StateLockTests(TestCase):
    def test_no_active_passes(self):
        ok, _ = check_f1_rule(_state_file(_state([
            {"name": "A", "status": "completed"},
            {"name": "B", "status": "pending"},
        ])))
        self.assertTrue(ok)

    def test_one_flat_parent_passes(self):
        ok, _ = check_f1_rule(_state_file(_state([
            {"name": "A", "status": "in_progress"},
        ])))
        self.assertTrue(ok)

    def test_one_parent_plus_one_child_passes(self):
        # The single allowed two-active case: a parent and one of its children.
        ok, _ = check_f1_rule(_state_file(_state([
            {"name": "A", "status": "in_progress",
             "subtasks": [
                 {"name": "A.1", "status": "completed"},
                 {"name": "A.2", "status": "in_progress"},
             ]},
        ])))
        self.assertTrue(ok)

    def test_two_flat_parents_violates(self):
        # BUG REGRESSION: two flat parents summed to 2, which was "not > 2".
        ok, err = check_f1_rule(_state_file(_state([
            {"name": "A", "status": "in_progress"},
            {"name": "B", "status": "in_progress"},
        ])))
        self.assertFalse(ok)
        self.assertIn("2 parent tasks", err)

    def test_two_children_violates(self):
        # BUG REGRESSION: two children summed to 2, which was "not > 2".
        ok, err = check_f1_rule(_state_file(_state([
            {"name": "A", "status": "in_progress",
             "subtasks": [
                 {"name": "A.1", "status": "in_progress"},
                 {"name": "A.2", "status": "in_progress"},
             ]},
        ])))
        self.assertFalse(ok)
        self.assertIn("2 subtasks", err)

    def test_child_under_non_active_parent_violates(self):
        # Corrupt/hand-edited state: a child in_progress whose parent is not
        # (locking a subtask normally marks the parent in_progress too).
        ok, err = check_f1_rule(_state_file(_state([
            {"name": "A", "status": "completed",
             "subtasks": [{"name": "A.1", "status": "in_progress"}]},
            {"name": "B", "status": "pending"},
        ])))
        self.assertFalse(ok)
        self.assertIn("without its parent", err)

    def test_three_active_still_violates(self):
        ok, _ = check_f1_rule(_state_file(_state([
            {"name": "A", "status": "in_progress"},
            {"name": "B", "status": "in_progress"},
            {"name": "C", "status": "in_progress"},
        ])))
        self.assertFalse(ok)

    def test_error_message_lists_offending_tasks(self):
        ok, err = check_f1_rule(_state_file(_state([
            {"name": "A", "status": "in_progress"},
            {"name": "B", "status": "in_progress"},
        ])))
        self.assertFalse(ok)
        self.assertIn("P1.T1", err)
        self.assertIn("P1.T2", err)


if __name__ == "__main__":
    main()
