"""Tests for ``track-state amend-task`` (B3) — the sanctioned mid-flight
task-class mutation.

The user-facing generalization of the misroute reroute: a wrong ``[Tag]`` on a
top-level task is fixed by amending the AUTHORITATIVE name in both homes
(plan.md line + state mirror with ``task_type`` re-derived, subtasks
inheriting) — never by a dispatch-time override. ``--tag`` validates against
the LIVE registry vocab (hard-reject unknown); per-task persona was DECLINED
in favor of ``tag add <Tag> --agent <persona>`` + ``amend-task`` composition
(conductor/design/decision-amend-task.md).
"""
import io
import json
import shutil
import sys
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load
from scripts.track_state.mutations import cmd_amend_task
from scripts.track_state import cli
from tests.test_step import _git_track_dir, _make_state


def _run(fn, *args):
    """Capture a command's stdout JSON (the one ``out(...)`` it emits)."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _track():
    """Phase 1 pending with one untagged task + a split parent scenario shape."""
    state = _make_state(
        current_phase_index=1, current_task_index=1,
        phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"},
            {"name": "Task B", "status": "pending"},
        ]}])
    plan = "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n- [ ] Task B\n"
    return _git_track_dir(state, plan_content=plan)


def _track_with_subtasks():
    state = _make_state(
        current_phase_index=1, current_task_index=1,
        phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "in_progress",
             "subtasks": [
                 {"name": "Task A.1", "status": "completed",
                  "commit_sha": "abc1234", "task_type": "default"},
                 {"name": "Task A.2", "status": "pending",
                  "task_type": "default"},
             ]},
        ]}])
    plan = ("# Plan\n\n## Phase 1: Build\n- [~] Task A\n"
            "  - [x] Task A.1\n  - [ ] Task A.2\n")
    return _git_track_dir(state, plan_content=plan)


class AmendTaskTests(TestCase):
    def test_amends_plan_line_and_state_mirror(self):
        d = _track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_amend_task, d, 1, 2, "Explore")
        self.assertTrue(o["ok"])
        self.assertEqual(o["previous"], "Task B")
        self.assertEqual(o["name"], "[Explore] Task B")
        self.assertEqual(o["task_type"], "explore")
        plan = (Path(d) / "plan.md").read_text()
        self.assertIn("- [ ] [Explore] Task B", plan)
        self.assertIn("- [ ] Task A\n", plan, "sibling line untouched")
        tgt = load(d)["phases"][0]["tasks"][1]
        self.assertEqual(tgt["name"], "[Explore] Task B")
        self.assertEqual(tgt["task_type"], "explore")

    def test_unknown_tag_hard_rejects(self):
        # The live registry vocab is the only source — a typo must not strand
        # the task outside every class profile.
        d = _track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_amend_task, d, 1, 1, "Nope")
        self.assertIn("error", o)
        self.assertIn("unknown tag", o["error"])
        self.assertIn("tag add", o["hint"])
        self.assertNotIn("[Nope]", (Path(d) / "plan.md").read_text())
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["name"], "Task A")

    def test_idempotent_when_tag_present(self):
        d = _track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_amend_task, d, 1, 1, "Explore")
        o = _run(cmd_amend_task, d, 1, 1, "Explore")
        self.assertTrue(o["ok"])
        self.assertEqual(o["name"], "[Explore] Task A")
        plan = (Path(d) / "plan.md").read_text()
        self.assertEqual(plan.count("[Explore] Task A"), 1,
                         "re-amend must not double-tag")

    def test_subtasks_inherit_parent_type(self):
        d = _track_with_subtasks()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_amend_task, d, 1, 1, "Explore")
        self.assertTrue(o["ok"])
        tgt = load(d)["phases"][0]["tasks"][0]
        self.assertEqual(tgt["task_type"], "explore")
        for sub in tgt["subtasks"]:
            self.assertEqual(sub["task_type"], "explore",
                             "subtasks inherit the parent's tag, never their own")

    def test_missing_task_errors_without_write(self):
        d = _track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_amend_task, d, 1, 9, "Explore")
        self.assertIn("error", o)
        self.assertIn("not found", o["error"])
        self.assertNotIn("[Explore]", (Path(d) / "plan.md").read_text())

    def test_non_integer_indices_error_cleanly(self):
        d = _track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_amend_task, d, "one", 1, "Explore")
        self.assertIn("error", o)
        self.assertIn("integer", o["error"])


class CliWiringTests(TestCase):
    """``amend-task`` resolves through cli.main with the index-command
    preamble (positional or named indices) and the subtask rejection."""

    def _invoke(self, argv):
        old_argv, old_out = sys.argv, sys.stdout
        sys.argv, sys.stdout = ["track-state"] + argv, io.StringIO()
        try:
            cli.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_out

    def test_positional_indices(self):
        d = _track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._invoke(["amend-task", d, "1", "1", "--tag", "Explore"])
        self.assertIn("- [ ] [Explore] Task A",
                      (Path(d) / "plan.md").read_text())

    def test_named_flags(self):
        d = _track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._invoke(["amend-task", d, "--phase", "1", "--task", "1",
                      "--tag", "Explore"])
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["task_type"],
                         "explore")

    def test_subtask_rejected(self):
        d = _track_with_subtasks()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with self.assertRaises(SystemExit):
            self._invoke(["amend-task", d, "--phase", "1", "--task", "1",
                          "--subtask", "1", "--tag", "Explore"])
        self.assertNotIn("[Explore]",
                         (Path(d) / "plan.md").read_text())

    def test_listed_in_help_group_and_sanctioned(self):
        from scripts.track_state.commands import (
            COMMAND_GROUPS, INDEX_COMMANDS, SANCTIONED_SUBCOMMANDS)
        grouped = {c for _name, cmds in COMMAND_GROUPS for c in cmds}
        self.assertIn("amend-task", cli.COMMAND_HELP)
        self.assertIn("amend-task", grouped)
        self.assertIn("amend-task", INDEX_COMMANDS)
        self.assertIn("amend-task", SANCTIONED_SUBCOMMANDS)


if __name__ == "__main__":
    main()
