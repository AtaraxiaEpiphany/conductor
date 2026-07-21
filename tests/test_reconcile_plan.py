"""Tests for ``track-state reconcile-plan`` — name-keyed post-edit reconciliation.

Covers the gap no other command fills: after a ``git reset`` + hand-edit of
``plan.md`` (tag change, split, reorder, delete), bring ``track-state.json`` back
in sync **by name, not position**, preserving ``commit_sha`` on tasks whose work
survives. Modeled on ``test_split_command.py`` (same ``_make_state`` / ``_git_track_dir``
fixtures, same ``_run`` / ``_invoke`` stdout-capture pattern).

Headline safety test: ``test_reorder_does_not_rebind_shas`` — the positional
``sync-plan`` regression (reorder silently rebinds SHAs to the wrong task) that
reconcile exists to prevent.
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
from scripts.track_state.reconcile import (
    cmd_reconcile_plan, _compute_reconciliation, _stitch_markers, _plan_marker_map)
from scripts.track_state import cli
from scripts.track_state.plan_parse import parse_plan

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


def _completed_task(name, sha="abc1234"):
    return {"name": name, "status": "completed", "commit_sha": sha,
            "completed_at": "2026-07-01T00:00:00+00:00"}


def _diff(track_dir):
    """Run the pure diff (no git liveness, so bogus SHAs don't probe) and return it."""
    state = load(track_dir)
    edited = parse_plan(Path(track_dir) / "plan.md")
    _stitch_markers(edited, _plan_marker_map(Path(track_dir) / "plan.md"))
    return _compute_reconciliation(track_dir, state, edited, liveness=False)


class TagChangePreservesSha(TestCase):
    def test_tag_change_keeps_sha_on_terminal_status(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "in_progress",
                                     "tasks": [_completed_task("Task A")]}])
        # Edit plan: flip marker [x]->[>] and add a [Docs] tag to the name.
        plan = "# Plan\n\n## Phase 1: Build\n- [>] [Docs] Task A [abc1234]\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

        diff = _diff(d)
        self.assertEqual(len(diff["tag_or_status"]), 1)
        item = diff["tag_or_status"][0]
        self.assertEqual(item["new_status"], "skipped")
        self.assertTrue(item["keep_sha"])  # terminal → SHA retained
        self.assertEqual(item["commit_sha"], "abc1234")

        # Apply.
        o = _run(cmd_reconcile_plan, d, apply=True)
        self.assertTrue(o["ok"])
        st = load(d)
        task = st["phases"][0]["tasks"][0]
        self.assertEqual(task["status"], "skipped")
        self.assertEqual(task["name"], "[Docs] Task A")
        self.assertEqual(task["commit_sha"], "abc1234")  # preserved


class SplitAppendsPendingSubtasks(TestCase):
    def test_split_appends_pending_parent_sha_intact(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "in_progress",
                                     "tasks": [_completed_task("Upgrade X")]}])
        plan = ("# Plan\n\n## Phase 1: Build\n"
                "- [x] Upgrade X [abc1234]\n"
                "  - [ ] Step one\n"
                "  - [ ] Step two\n")
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

        diff = _diff(d)
        self.assertEqual(len(diff["split"]), 2)
        self.assertEqual([s["new_subtask"] for s in diff["split"]],
                         ["Step one", "Step two"])

        o = _run(cmd_reconcile_plan, d, apply=True)
        self.assertTrue(o["ok"])
        st = load(d)
        parent = st["phases"][0]["tasks"][0]
        self.assertEqual(parent["status"], "completed")      # untouched
        self.assertEqual(parent["commit_sha"], "abc1234")    # intact
        self.assertEqual([s["name"] for s in parent["subtasks"]],
                         ["Step one", "Step two"])
        self.assertTrue(all(s["status"] == "pending" for s in parent["subtasks"]))


class ReorderDoesNotRebindShas(TestCase):
    """The headline safety test: positional sync-plan rebinds SHAs on reorder;
    reconcile keys by name so each SHA stays on its task."""

    def test_reorder_keeps_sha_on_named_task(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "in_progress", "tasks": [
            _completed_task("Alpha", "1111111"),
            _completed_task("Beta", "2222222"),
        ]}])
        # Swap the ORDER of the two completed tasks in the edited plan.
        plan = ("# Plan\n\n## Phase 1: Build\n"
                "- [x] Beta [2222222]\n"
                "- [x] Alpha [1111111]\n")
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

        diff = _diff(d)
        # Reorder by name → both match by name → both unchanged. Crucially, NO
        # unmatched and NO tag_or_status (a positional sync would have silently
        # rebound each SHA to the wrong task's slot).
        self.assertEqual(diff["unmatched"], [])
        self.assertEqual(diff["tag_or_status"], [])
        self.assertEqual(len(diff["unchanged"]), 2)

        o = _run(cmd_reconcile_plan, d, apply=True)
        self.assertTrue(o["ok"])
        st = load(d)
        tasks = {t["name"]: t for t in st["phases"][0]["tasks"]}
        # Each SHA stayed on its NAMED task, not its old positional slot.
        self.assertEqual(tasks["Alpha"]["commit_sha"], "1111111")
        self.assertEqual(tasks["Beta"]["commit_sha"], "2222222")


class UnmatchedRefused(TestCase):
    def test_dropped_task_with_sha_refused_until_drop_flag(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "in_progress", "tasks": [
            _completed_task("Keep me"),
            _completed_task("Drop me", "deadbeef"),
        ]}])
        # Edited plan deletes "Drop me" entirely.
        plan = "# Plan\n\n## Phase 1: Build\n- [x] Keep me [abc1234]\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

        diff = _diff(d)
        unmatched = [u for u in diff["unmatched"] if u["kind"] == "dropped_task_with_sha"]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["name"], "Drop me")

        # Apply without --drop → refused, nothing written.
        before = load(d)
        o = _run(cmd_reconcile_plan, d, apply=True)
        self.assertIn("error", o)
        self.assertEqual(load(d), before)

        # Apply with --drop → succeeds, node removed.
        o = _run(cmd_reconcile_plan, d, apply=True, drops=["1:Drop me"])
        self.assertTrue(o["ok"])
        st = load(d)
        self.assertEqual([t["name"] for t in st["phases"][0]["tasks"]], ["Keep me"])


class RenameKeepsSha(TestCase):
    def test_rename_keeps_sha(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "in_progress",
                                     "tasks": [_completed_task("Old name")]}])
        plan = "# Plan\n\n## Phase 1: Build\n- [x] New name [abc1234]\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

        # Without --rename, "New name" is unmatched (no state node by that key);
        # "Old name" is a dropped-state unmatched. Both surface pre-resolution.
        diff = _diff(d)
        self.assertEqual(len(diff["unmatched"]), 2)

        # --rename aliases them → apply succeeds, new name persists, SHA survives.
        o = _run(cmd_reconcile_plan, d, apply=True, renames=["1:Old name=New name"])
        self.assertTrue(o["ok"])
        st = load(d)
        task = st["phases"][0]["tasks"][0]
        self.assertEqual(task["name"], "New name")
        self.assertEqual(task["commit_sha"], "abc1234")  # SHA survived the rename


class DanglingSha(TestCase):
    def _track(self, status, sha="deadbee", marker="d"):
        """A track where P1.T1 carries a SHA that is NOT a real commit."""
        task = {"name": "Task A", "status": status, "commit_sha": sha,
                "completed_at": "2026-07-01T00:00:00+00:00"}
        state = _make_state(phases=[{"name": "Phase 1", "status": "in_progress",
                                     "tasks": [task]}])
        plan = f"# Plan\n\n## Phase 1: Build\n- [{marker}] Task A [{sha}]\n"
        return _git_track_dir(state, plan_content=plan)

    def test_dangling_detected_on_liveness_probe(self):
        d = self._track("completed", marker="x")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        state = load(d)
        edited = parse_plan(Path(d) / "plan.md")
        _stitch_markers(edited, _plan_marker_map(Path(d) / "plan.md"))
        diff = _compute_reconciliation(d, state, edited, liveness=True)
        self.assertTrue(diff["dangling_sha"])
        self.assertEqual(diff["dangling_sha"][0]["commit_sha"], "deadbee")

    def test_terminal_dangling_applies_with_warning(self):
        # completed ([x]) + dangling SHA → terminal. Policy: advisory, not
        # blocking — the user chose the terminal marker, so it's respected, with
        # a warning that the SHA is unreachable. (Blocking here would wrongly
        # gate the git-reset recovery case the command exists to serve.)
        d = self._track("completed", marker="x")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_reconcile_plan, d, apply=True)
        self.assertTrue(o["ok"])
        self.assertTrue(any("unreachable" in w for w in o.get("warnings", [])))
        # SHA kept (terminal marker respected), not silently cleared.
        st = load(d)
        self.assertEqual(st["phases"][0]["tasks"][0]["commit_sha"], "deadbee")

    def test_nonterminal_dangling_auto_cleared(self):
        # deferred ([d]) is terminal-with-SHA-marker, but the edited marker here is
        # pending ([ ]) → non-terminal edit → auto-clear to pending on apply.
        task = {"name": "Task A", "status": "deferred", "commit_sha": "deadbee"}
        state = _make_state(phases=[{"name": "Phase 1", "status": "in_progress",
                                     "tasks": [task]}])
        plan = "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_reconcile_plan, d, apply=True)
        self.assertTrue(o["ok"])
        st = load(d)
        task = st["phases"][0]["tasks"][0]
        self.assertEqual(task["status"], "pending")
        self.assertNotIn("commit_sha", task)


class DryRunWritesNothing(TestCase):
    def test_dry_run_leaves_state_untouched(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "in_progress",
                                     "tasks": [_completed_task("Task A")]}])
        plan = "# Plan\n\n## Phase 1: Build\n- [>] [Docs] Task A [abc1234]\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        before = Path(d, "track-state.json").read_text()

        o = _run(cmd_reconcile_plan, d, apply=False)  # dry-run
        self.assertTrue(o["dry_run"])
        self.assertEqual(Path(d, "track-state.json").read_text(), before)


class BookkeepingCommit(TestCase):
    def test_apply_creates_reconcile_commit(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "in_progress",
                                     "tasks": [_completed_task("Task A")]}])
        plan = "# Plan\n\n## Phase 1: Build\n- [>] [Docs] Task A [abc1234]\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

        _run(cmd_reconcile_plan, d, apply=True)
        log = subprocess.run(["git", "-C", d, "log", "--format=%s"],
                             capture_output=True, text=True).stdout
        self.assertIn("Reconcile plan edits", log)
        # The conductor-managed files (track-state.json, plan.md) are committed
        # clean. track-state.json.bak is written by core._read_state on every
        # load and is intentionally NOT staged by conductor commits — ignore it.
        status = subprocess.run(["git", "-C", d, "status", "--porcelain"],
                                capture_output=True, text=True).stdout
        dirty = [l for l in status.splitlines()
                 if l.strip() and not l.strip().endswith("track-state.json.bak")]
        self.assertEqual(dirty, [])


if __name__ == "__main__":
    main()
