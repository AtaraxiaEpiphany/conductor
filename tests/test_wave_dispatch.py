"""Tests for cmd_dispatch_wave / cmd_wave_status / cmd_wave_abort.

Git-backed: exercises the full worktree lifecycle (worktree add, member lock,
ledger + marker write, mutual-exclusion refusal, abort teardown). The
ready-set selection logic is covered separately by test_wave_ready_set.py;
squash-merge integration by test_wave_finalize.py.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.track_state.core import load, save
from scripts.track_state.wave import (
    cmd_dispatch_wave, cmd_wave_status, cmd_wave_abort,
    _wave_ledger_path, WAVE_MARKER_NAME,
)


def _capture(fn, *args, **kwargs):
    """Capture stdout JSON from a cmd. Returns (parsed_json, stderr_text)."""
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
    """Temp git repo (track_dir == repo root) with plan.md + track-state.json."""
    d = tempfile.mkdtemp()
    _git(d, "init")
    _git(d, "config", "user.email", "test@test.com")
    _git(d, "config", "user.name", "Test")
    Path(d, "README.md").write_text("# base\n")
    _git(d, "add", "README.md")
    _git(d, "commit", "-m", "init")
    Path(d, "plan.md").write_text(plan_body)
    save(d, state)
    return d


def _disjoint_plan(n=3):
    """A phase of N file-disjoint deps-declared tasks (each opts in via empty deps)."""
    lines = ["# Plan", "", "## Phase 1: Build"]
    for i in range(1, n + 1):
        lines.append(f"- [ ] Task {i}: t{i} <!-- deps: -->")
    return "\n".join(lines) + "\n"


def _state(n_tasks):
    return {
        "track_id": "wtest", "type": "feature", "status": "in_progress",
        "current_phase_index": 1, "current_task_index": 0,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "phases": [{"name": "Phase 1", "tasks": [
            {"name": f"Task {i}: t{i}", "status": "pending"} for i in range(1, n_tasks + 1)]}],
    }


class _PinnedWaveCap(unittest.TestCase):
    """Pin ``CONDUCTOR_WAVE_SIZE`` for the 3-member wave scenarios below so they
    stay independent of the shipped default (these exercise the worktree
    lifecycle / abort, not the cap knob — test_wave_step.py covers the knob)."""

    def setUp(self):
        self._prev = os.environ.pop("CONDUCTOR_WAVE_SIZE", None)
        os.environ["CONDUCTOR_WAVE_SIZE"] = "4"  # >= the 3-member scenarios

    def tearDown(self):
        if self._prev is not None:
            os.environ["CONDUCTOR_WAVE_SIZE"] = self._prev
        else:
            os.environ.pop("CONDUCTOR_WAVE_SIZE", None)


class TestDispatchWave(_PinnedWaveCap):
    def setUp(self):
        super().setUp()
        self.d = _make_git_track(_state(3), _disjoint_plan(3))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_dispatch_creates_worktrees_locks_members_writes_ledger(self):
        out, err = _capture(cmd_dispatch_wave, self.d)
        self.assertEqual(out["action"], "dispatch_wave", err)
        members = out["wave"]
        self.assertEqual(len(members), 3)
        for m in members:
            # worktree exists on disk + is registered with git
            self.assertTrue(Path(m["worktree"]).exists(), m["worktree"])
            listing = _git(self.d, "worktree", "list").stdout
            self.assertIn(m["worktree"], listing)
            # branch named for the track + loc
            self.assertTrue(m["branch"].startswith("conductor/wave/"))
            self.assertIn(f"P{m['phase']}.T{m['task']}", m["branch"])
            # marker dropped in the worktree's .conductor
            marker = Path(m["worktree_track_dir"], ".conductor", WAVE_MARKER_NAME)
            self.assertTrue(marker.exists(), marker)

        # all three members locked in_progress in one transaction
        st = load(self.d)
        statuses = [t["status"] for t in st["phases"][0]["tasks"]]
        self.assertEqual(statuses, ["in_progress", "in_progress", "in_progress"])
        # cursor untouched (serial spine owns it)
        self.assertEqual(st.get("current_task_index"), 0)

        # ledger persisted
        ledger = json.loads(_wave_ledger_path(self.d).read_text())
        self.assertTrue(ledger["wave_root"])  # outside the repo tree
        self.assertFalse(Path(self.d) in Path(ledger["wave_root"]).parents
                         or Path(ledger["wave_root"]) == Path(self.d))
        self.assertEqual(len(ledger["wave"]), 3)

    def test_status_reflects_active_wave(self):
        _capture(cmd_dispatch_wave, self.d)
        out, _ = _capture(cmd_wave_status, self.d)
        self.assertTrue(out["active"])
        self.assertEqual(len(out["members"]), 3)
        self.assertTrue(all(m["status"] == "in_flight" for m in out["members"]))

    def test_dispatch_wave_emits_slim_consumer_member_shape(self):
        # The wave envelope ships straight to the orchestrator as Bash stdout
        # (unfiltered), so each member must carry ONLY the 6 keys the parallel
        # skill consumes. Ledger-only keys (track_id, base_sha, locked_at,
        # status) bloat the main session context for nothing — regression-guard
        # the slimming so future re-bloat fails CI.
        out, _ = _capture(cmd_dispatch_wave, self.d)
        self.assertEqual(out["action"], "dispatch_wave")
        expected = {"phase", "task", "name",
                    "worktree", "branch", "worktree_track_dir"}
        for m in out["wave"]:
            self.assertEqual(set(m.keys()), expected, m)

    def test_wave_active_refusal_also_emits_slim_members(self):
        _capture(cmd_dispatch_wave, self.d)  # start the first wave
        out, _ = _capture(cmd_dispatch_wave, self.d)  # refused
        self.assertEqual(out["action"], "wave_active")
        expected = {"phase", "task", "name",
                    "worktree", "branch", "worktree_track_dir"}
        for m in out["wave"]:
            self.assertEqual(set(m.keys()), expected, m)

    def test_ledger_still_carries_full_member_dict(self):
        # The slimming is emit-only: the on-disk ledger keeps the full member
        # dict (wave-finalize/wave-abort read base_sha/status/worktree back).
        _capture(cmd_dispatch_wave, self.d)
        ledger = json.loads(_wave_ledger_path(self.d).read_text())
        keys = set(ledger["wave"][0].keys())
        for must in ("track_id", "base_sha", "locked_at", "status",
                     "worktree", "branch", "worktree_track_dir"):
            self.assertIn(must, keys, f"ledger lost {must}: {keys}")

    def test_status_no_ledger(self):
        out, _ = _capture(cmd_wave_status, self.d)
        self.assertFalse(out["active"])

    def test_active_ledger_refuses_new_wave(self):
        first, _ = _capture(cmd_dispatch_wave, self.d)
        self.assertEqual(first["action"], "dispatch_wave")
        second, _ = _capture(cmd_dispatch_wave, self.d)
        self.assertEqual(second["action"], "wave_active")
        # No new worktrees created beyond the first wave's three.
        listing = _git(self.d, "worktree", "list").stdout
        self.assertEqual(listing.count("conductor-wave-"), 3)

    def test_no_ready_tasks_when_no_deps_comments(self):
        d = _make_git_track(_state(2),
                            "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n- [ ] Task B\n")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out, err = _capture(cmd_dispatch_wave, d)
        self.assertEqual(out["action"], "no_ready_tasks")
        self.assertFalse(_wave_ledger_path(d).exists())
        # state untouched
        st = load(d)
        self.assertTrue(all(t["status"] == "pending" for t in st["phases"][0]["tasks"]))

    def test_no_ready_tasks_when_all_phases_terminal(self):
        st = _state(2)
        for t in st["phases"][0]["tasks"]:
            t["status"] = "completed"
        d = _make_git_track(st, _disjoint_plan(2))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out, _ = _capture(cmd_dispatch_wave, d)
        self.assertEqual(out["action"], "no_ready_tasks")
        self.assertEqual(out["phase"], 0)


class TestWaveAbort(_PinnedWaveCap):
    def setUp(self):
        super().setUp()
        self.d = _make_git_track(_state(3), _disjoint_plan(3))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.members = _capture(cmd_dispatch_wave, self.d)[0]["wave"]

    def test_abort_resets_members_and_tears_down(self):
        out, _ = _capture(cmd_wave_abort, self.d)
        self.assertEqual(out["action"], "wave_aborted")
        self.assertEqual(len(out["aborted"]), 3)
        # worktrees gone, branches gone
        for m in self.members:
            self.assertFalse(Path(m["worktree"]).exists())
            rc = subprocess.run(["git", "-C", self.d, "rev-parse", "--verify",
                                 m["branch"]], capture_output=True).returncode
            self.assertNotEqual(rc, 0, f"branch {m['branch']} should be deleted")
        listing = _git(self.d, "worktree", "list").stdout
        self.assertNotIn("conductor-wave-", listing)
        # tasks back to pending
        st = load(self.d)
        self.assertTrue(all(t["status"] == "pending" for t in st["phases"][0]["tasks"]))
        # ledger + wave root gone
        self.assertFalse(_wave_ledger_path(self.d).exists())

    def test_abort_with_no_ledger_is_noop(self):
        _capture(cmd_wave_abort, self.d)  # clear the active wave first
        out, _ = _capture(cmd_wave_abort, self.d)
        self.assertEqual(out["action"], "no_wave")
        self.assertTrue(out["ok"])

    def test_abort_preserves_terminal_members(self):
        # Mark one member finalized in the ledger; abort must leave it alone.
        ledger = json.loads(_wave_ledger_path(self.d).read_text())
        ledger["wave"][0]["status"] = "finalized"
        _wave_ledger_path(self.d).write_text(json.dumps(ledger))
        out, _ = _capture(cmd_wave_abort, self.d)
        # Only the 2 in-flight members are aborted.
        self.assertEqual(len(out["aborted"]), 2)
        locs = [f"P{m['phase']}.T{m['task']}" for m in ledger["wave"]]
        # The finalized member's worktree is NOT torn down by abort (it was
        # already torn down at finalize time in the real flow).
        finalized_wt = self.members[0]["worktree"]
        # finalized member's task keeps completed-style status (we only reset
        # in-flight ones); here it was in_progress, but abort skips it.
        st = load(self.d)
        # finalized member still in_progress (abort didn't touch it); 2 others pending
        statuses = [t["status"] for t in st["phases"][0]["tasks"]]
        self.assertEqual(statuses.count("pending"), 2)


if __name__ == "__main__":
    unittest.main()
