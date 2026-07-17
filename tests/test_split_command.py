"""Tests for ``track-state split`` — the failure-analyst ``decompose`` backing op.

``split`` skips the original task/subtask (commit_sha preserved — the decompose
invariant: committed work is NOT reverted) and appends the named pieces as pending
subtasks under the parent, then splices plan.md + syncs + commits. Covers both depth
cases (task-level and subtask-level split), the SHA-preservation invariant,
validation, plan.md tolerance, and sync reconciliation.
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save
from scripts.track_state.mutations import cmd_split, _do_split
from scripts.track_state import cli

from tests.test_step import _make_state, _git_track_dir


def _run(fn, *args, **kwargs):
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _invoke(argv):
    old_argv, old_out = sys.argv, sys.stdout
    buf = io.StringIO()
    sys.argv, sys.stdout = ["track-state"] + argv, buf
    try:
        cli.main()
    finally:
        sys.argv, sys.stdout = old_argv, old_out
    return json.loads(buf.getvalue())


def _split_track(subtasks=None, task_status="failed", with_sha=True):
    """A track whose Phase 1 Task A is failed (with a partial commit_sha) and
    optionally already has subtasks (for subtask-split tests)."""
    task = {"name": "Task A", "status": task_status, "retry_count": 3}
    if with_sha:
        task["commit_sha"] = "abc1234"
    if subtasks is not None:
        task["subtasks"] = subtasks
    state = _make_state(
        execution_mode="continuous",
        current_phase_index=1, current_task_index=1,
        phases=[{"name": "Phase 1", "status": "in_progress", "tasks": [task]}])
    plan = "# Plan\n\n## Phase 1: Build\n- [!] Task A [abc1234]\n"
    if subtasks:
        for s in subtasks:
            plan += f"  - [!] {s['name']} [abc1234]\n"
    return _git_track_dir(state, plan_content=plan)


class TaskLevelSplitTests(TestCase):
    def test_failed_task_split_into_subtasks(self):
        d = _split_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_split, d, 1, 1, None, ["Part one", "Part two"],
                 note="decomposed")
        self.assertTrue(o["ok"])
        self.assertEqual(o["added"], 2)
        state = load(d)
        task = state["phases"][0]["tasks"][0]
        # Original skipped, SHA preserved (the load-bearing invariant).
        self.assertEqual(task["status"], "skipped")
        self.assertEqual(task["commit_sha"], "abc1234")
        self.assertIn("decomposed", task["skip_analysis"])
        # Two new pending subtasks appended under it.
        self.assertEqual(len(task["subtasks"]), 2)
        self.assertEqual([s["name"] for s in task["subtasks"]],
                         ["Part one", "Part two"])
        self.assertTrue(all(s["status"] == "pending" for s in task["subtasks"]))
        # Current indices point at the parent so dispatch picks up the new piece.
        self.assertEqual(state["current_task_index"], 1)

    def test_plan_md_gets_new_subtask_lines(self):
        d = _split_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_split, d, 1, 1, None, ["Alpha", "Beta"])
        plan = (Path(d) / "plan.md").read_text()
        # New subtask lines present (sync normalized markers from pending → ' ').
        self.assertIn("Alpha", plan)
        self.assertIn("Beta", plan)
        # They are indented (subtask position).
        self.assertIn("- [ ] Alpha", plan)

    def test_split_commit_created(self):
        d = _split_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_split, d, 1, 1, None, ["X", "Y"])
        log = subprocess.run(["git", "-C", d, "log", "--oneline"],
                             capture_output=True, text=True).stdout
        self.assertIn("Decompose", log)


class SubtaskLevelSplitTests(TestCase):
    def test_failed_subtask_split_into_sibling_subtasks(self):
        d = _split_track(subtasks=[
            {"name": "Old S1", "status": "failed", "retry_count": 3,
             "commit_sha": "abc1234"}])
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_split, d, 1, 1, 1, ["Piece A", "Piece B"])
        self.assertTrue(o["ok"])
        state = load(d)
        task = state["phases"][0]["tasks"][0]
        # Original subtask skipped, SHA preserved.
        self.assertEqual(task["subtasks"][0]["status"], "skipped")
        self.assertEqual(task["subtasks"][0]["commit_sha"], "abc1234")
        # New pieces are SIBLINGS (appended under the same parent task).
        self.assertEqual([s["name"] for s in task["subtasks"]],
                         ["Old S1", "Piece A", "Piece B"])
        self.assertEqual(task["subtasks"][1]["status"], "pending")
        self.assertEqual(task["subtasks"][2]["status"], "pending")
        # Parent task itself untouched (still failed — only its children changed).
        # No sub-subtasks created (depth invariant).
        for s in task["subtasks"]:
            self.assertNotIn("subtasks", s)


class ValidationTests(TestCase):
    def test_empty_names_errors(self):
        d = _split_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_split, d, 1, 1, None, [])
        self.assertIn("error", o)

    def test_splitting_completed_task_errors(self):
        d = _split_track(task_status="completed")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_split, d, 1, 1, None, ["X"])
        self.assertIn("error", o)
        self.assertIn("completed", o["error"])

    def test_cli_empty_subtasks_flag_exits_nonzero(self):
        d = _split_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with self.assertRaises(SystemExit):
            _invoke(["split", d, "1", "1", "--subtasks", "  ;  "])


class PlanMdToleranceTests(TestCase):
    def test_missing_plan_md_still_mutates_json(self):
        d = _split_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (Path(d) / "plan.md").unlink()
        old_err = sys.stderr
        sys.stderr = io.StringIO()
        try:
            o = _run(cmd_split, d, 1, 1, None, ["X"])
        finally:
            sys.stderr = old_err
        # JSON mutation succeeds; plan.md splice tolerated (warning to stderr).
        self.assertTrue(o["ok"])
        state = load(d)
        self.assertEqual(state["phases"][0]["tasks"][0]["status"], "skipped")


class SyncReconciliationTests(TestCase):
    def test_validate_clean_after_split(self):
        from scripts.track_state.validate import cmd_validate
        d = _split_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_split, d, 1, 1, None, ["P1", "P2"])
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            cmd_validate(d)
            report = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old
        # No plan/state count mismatch after the splice + sync.
        self.assertNotIn("error", report)


class CliWiringTests(TestCase):
    def test_split_listed_in_help_and_group(self):
        grouped = {c for _name, cmds in cli._COMMAND_GROUPS for c in cmds}
        self.assertIn("split", cli.COMMAND_HELP)
        self.assertIn("split", grouped)

    def test_split_via_cli(self):
        d = _split_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _invoke(["split", d, "1", "1", "--subtasks", "Foo;Bar"])
        state = load(d)
        task = state["phases"][0]["tasks"][0]
        self.assertEqual([s["name"] for s in task["subtasks"]],
                         ["Foo", "Bar"])


if __name__ == "__main__":
    main()
