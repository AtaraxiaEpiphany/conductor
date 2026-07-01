"""Tests for cmd_wave_finalize: squash-merge integration + transition + teardown.

Git-backed end-to-end: dispatch a wave, simulate each member's task-executor
working in its own worktree (commit code + write result.json), then finalize.
Covers SUCCESS (linear squash integration), FAILURE, missing-result, and the
declared-disjoint-but-overlapping conflict path.
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.track_state.core import save, load
from scripts.track_state.quality import _CONDUCTOR_GITIGNORE
from scripts.track_state.wave import (
    cmd_dispatch_wave, cmd_wave_finalize, _wave_ledger_path,
)


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
    # Mirror production: .conductor/.gitignore excludes runtime artifacts
    # (result.json, parallel.json, wave-agent.marker) so the conductor commit
    # never sweeps them in. Without this the ledger would get committed.
    cond = Path(d, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / ".gitignore").write_text(_CONDUCTOR_GITIGNORE)
    save(d, state)
    return d


def _disjoint_plan(n):
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
            {"name": f"Task {i}: t{i}", "status": "pending"}
            for i in range(1, n_tasks + 1)]}],
    }


def _simulate_agent(worktree, member, files, status="SUCCESS", summary="done"):
    """Pretend to be the task-executor: commit code + write result.json.

    ``files``: {repo_rel_path: content} committed on the member's branch inside
    the worktree. The result.json lands in the worktree's own .conductor/.
    """
    tip = None
    for path, content in files.items():
        p = Path(worktree, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    if files:
        _git(worktree, "add", ".")
        _git(worktree, "commit", "-m", f"feat: implement {member['name']}")
        tip = _git(worktree, "rev-parse", "--short=7", "HEAD").stdout.strip()
    cond = Path(worktree, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / "result.json").write_text(json.dumps({
        "status": status, "commit_sha": tip or "N/A", "summary": summary,
        "phase": member["phase"], "task": member["task"], "subtask": None,
        "task_name": member["name"],
    }))
    return tip


class TestWaveFinalizeSuccess(unittest.TestCase):
    def setUp(self):
        self.d = _make_git_track(_state(2), _disjoint_plan(2))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        wave = _capture(cmd_dispatch_wave, self.d)[0]
        self.members = wave["wave"]

    def test_success_integrates_and_completes(self):
        m = self.members[0]
        tip = _simulate_agent(m["worktree"], m, {f"feat_{m['task']}.py": "A\n"})
        out, err = _capture(cmd_wave_finalize, self.d, m["phase"], m["task"])
        self.assertEqual(out["action"], "wave_finalized", err)
        self.assertEqual(out["member_status"], "finalized")
        self.assertEqual(out["status"], "success")
        self.assertFalse(out["drained"])  # one member still in flight

        # The agent's file is now in the MAIN worktree (squash integrated it).
        self.assertTrue(Path(self.d, f"feat_{m['task']}.py").exists())
        # Task marked completed with a real commit_sha that resolves in the repo.
        st = load(self.d)
        sha = st["phases"][0]["tasks"][m["task"] - 1]["commit_sha"]
        self.assertEqual(len(sha), 7)
        rc = subprocess.run(["git", "-C", self.d, "cat-file", "-e", sha],
                            capture_output=True).returncode
        self.assertEqual(rc, 0, f"stored SHA {sha} must exist in the repo")
        # Worktree + branch torn down.
        self.assertFalse(Path(m["worktree"]).exists())
        rc = subprocess.run(["git", "-C", self.d, "rev-parse", "--verify",
                             m["branch"]], capture_output=True).returncode
        self.assertNotEqual(rc, 0)
        # Member ledger status updated.
        ledger = json.loads(_wave_ledger_path(self.d).read_text())
        self.assertEqual(ledger["wave"][0]["status"], "finalized")
        self.assertEqual(ledger["wave"][1]["status"], "in_flight")

    def test_drained_when_last_member_finalizes(self):
        m0, m1 = self.members
        _simulate_agent(m0["worktree"], m0, {f"f{m0['task']}.py": "0\n"})
        _capture(cmd_wave_finalize, self.d, m0["phase"], m0["task"])
        _simulate_agent(m1["worktree"], m1, {f"f{m1['task']}.py": "1\n"})
        out, _ = _capture(cmd_wave_finalize, self.d, m1["phase"], m1["task"])
        self.assertTrue(out["drained"])

    def test_success_no_commits_routes_to_failure(self):
        # Agent reported SUCCESS but committed nothing → FAILURE transition.
        m = self.members[0]
        _simulate_agent(m["worktree"], m, files={}, status="SUCCESS")
        out, err = _capture(cmd_wave_finalize, self.d, m["phase"], m["task"])
        self.assertEqual(out["member_status"], "failed")
        self.assertEqual(out["status"], "failure")
        # No code integrated; task re-queued (pending, retry_count bumped) not completed.
        st = load(self.d)
        self.assertEqual(st["phases"][0]["tasks"][m["task"] - 1]["status"], "pending")
        self.assertFalse(Path(self.d, f"feat_{m['task']}.py").exists())


class TestWaveFinalizeFailure(unittest.TestCase):
    def setUp(self):
        self.d = _make_git_track(_state(1), _disjoint_plan(1))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.m = _capture(cmd_dispatch_wave, self.d)[0]["wave"][0]

    def test_failure_result_no_integration(self):
        # Agent committed partial work but reported FAILURE → not integrated.
        _simulate_agent(self.m["worktree"], self.m,
                        {f"f{self.m['task']}.py": "partial\n"}, status="FAILURE",
                        summary="tests failed")
        out, _ = _capture(cmd_wave_finalize, self.d, self.m["phase"], self.m["task"])
        self.assertEqual(out["member_status"], "failed")
        self.assertEqual(out["status"], "failure")
        # Failed work NOT integrated into the main branch.
        self.assertFalse(Path(self.d, f"f{self.m['task']}.py").exists())
        st = load(self.d)
        self.assertEqual(st["phases"][0]["tasks"][0]["status"], "pending")

    def test_missing_result_routes_to_failure(self):
        # Agent produced commits but no result.json at all → FAILURE synthesis.
        m = self.m
        Path(m["worktree"], "lonely.py").write_text("x\n")
        _git(m["worktree"], "add", ".")
        _git(m["worktree"], "commit", "-m", "feat: orphan")
        out, _ = _capture(cmd_wave_finalize, self.d, m["phase"], m["task"])
        self.assertEqual(out["member_status"], "failed")
        # Worktree still torn down on the failure path.
        self.assertFalse(Path(m["worktree"]).exists())


class TestWaveFinalizeConflict(unittest.TestCase):
    def test_overlapping_files_conflict_fails_member(self):
        # Two members falsely declare disjoint (empty deps) but edit the SAME
        # file. The first integrates cleanly; the second's squash-merge conflicts.
        d = _make_git_track(_state(2), _disjoint_plan(2))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        wave = _capture(cmd_dispatch_wave, d)[0]
        m0, m1 = wave["wave"]

        # Member 0 edits shared.txt and finalizes successfully.
        _simulate_agent(m0["worktree"], m0, {"shared.txt": "from member 0\n"})
        out0, _ = _capture(cmd_wave_finalize, d, m0["phase"], m0["task"])
        self.assertEqual(out0["member_status"], "finalized")

        # Member 1 edited the same file from the same base → conflict on merge.
        _simulate_agent(m1["worktree"], m1, {"shared.txt": "from member 1\n"})
        out1, err = _capture(cmd_wave_finalize, d, m1["phase"], m1["task"])
        self.assertEqual(out1["member_status"], "conflict", err)
        self.assertEqual(out1["status"], "failure")
        # Member 1's conflicting content did NOT overwrite member 0's.
        self.assertEqual(Path(d, "shared.txt").read_text(), "from member 0\n")
        # No lingering merge state after the abort (the conductor leaves only its
        # normal untracked runtime noise — handoff.md/.lock/.bak — never a
        # conflicted index or MERGE_HEAD).
        self.assertFalse(Path(d, ".git", "MERGE_HEAD").exists())
        porcelain = _git(d, "status", "--porcelain").stdout
        self.assertNotIn("UU ", porcelain)  # no unmerged paths
        self.assertNotIn("shared.txt", porcelain)  # shared.txt cleanly at member 0's version
        # Member 1 re-queued for retry, not completed.
        st = load(d)
        self.assertEqual(st["phases"][0]["tasks"][m1["task"] - 1]["status"], "pending")
        # Both worktrees torn down.
        self.assertFalse(Path(m1["worktree"]).exists())


if __name__ == "__main__":
    unittest.main()
