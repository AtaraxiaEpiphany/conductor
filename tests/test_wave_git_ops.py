"""Tests for wave parallelism git helpers: worktree lifecycle + squash cherry-pick.

Mirrors the inline git-fixture pattern from test_track_state.py
(``_make_git_track_dir``): each test builds its own temp git repo and drives the
``_git_*`` helpers directly, asserting on real git state. These tests double as
the worktree-mechanics spike — they prove ``git worktree add`` / cherry-pick /
remove behave as the wave scheduler assumes in this environment.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.track_state.git_ops import (
    _git_rev_parse_toplevel, _git_worktree_add, _git_branch_tip,
    _git_range_commit_count, _git_merge_squash,
    _git_worktree_remove, _git_branch_delete, _git_head_sha,
)


def _git(d, *args):
    """Run a git command in ``d``, return CompletedProcess (check on failure)."""
    return subprocess.run(["git", "-C", str(d), *args],
                          capture_output=True, text=True, check=True)


def _make_git_repo():
    """Temp git repo with one initial commit. Returns (repo_root, cleanup)."""
    d = tempfile.mkdtemp()
    _git(d, "init")
    _git(d, "config", "user.email", "test@test.com")
    _git(d, "config", "user.name", "Test")
    Path(d, "README.md").write_text("# base\n")
    _git(d, "add", "README.md")
    _git(d, "commit", "-m", "init")
    return d, lambda: shutil.rmtree(d, ignore_errors=True)


class TestWorktreeLifecycle(unittest.TestCase):
    def setUp(self):
        self.repo, self.cleanup = _make_git_repo()
        self.addCleanup(self.cleanup)
        self.base = _git_head_sha(self.repo)
        # Worktrees MUST live outside the repo tree so the main branch's
        # `git status` stays clean (an in-repo worktree would show as untracked).
        # This mirrors the production policy wave.py follows.
        self.wt_root = tempfile.mkdtemp(prefix="conductor-wt-test-")
        self.addCleanup(lambda: shutil.rmtree(self.wt_root, ignore_errors=True))

    def test_rev_parse_toplevel_resolves_repo_root(self):
        # track_dir is a subdir; git walks up to the worktree root.
        sub = Path(self.repo, "conductor", "tracks", "x")
        sub.mkdir(parents=True)
        self.assertEqual(_git_rev_parse_toplevel(str(sub)), self.repo)

    def test_worktree_add_creates_branch_and_checkout(self):
        wt = str(Path(self.wt_root, "wt1"))
        branch = "conductor/wave/x/P1.T1"
        self.assertTrue(_git_worktree_add(self.repo, wt, branch, self.base))
        self.assertTrue(Path(wt, "README.md").exists())
        # The branch exists at base.
        self.assertEqual(_git_branch_tip(self.repo, branch), self.base)
        # And it is registered as a worktree.
        listing = _git(self.repo, "worktree", "list").stdout
        self.assertIn(wt, listing)

    def test_worktree_remove_and_branch_delete(self):
        wt = str(Path(self.wt_root, "wt2"))
        branch = "conductor/wave/x/P1.T2"
        self.assertTrue(_git_worktree_add(self.repo, wt, branch, self.base))
        self.assertTrue(_git_worktree_remove(self.repo, wt))
        self.assertFalse(Path(wt).exists())
        self.assertTrue(_git_branch_delete(self.repo, branch))
        # Branch is gone.
        self.assertIsNone(_git_branch_tip(self.repo, branch))


class TestSquashCherryPick(unittest.TestCase):
    def setUp(self):
        self.repo, self.cleanup = _make_git_repo()
        self.addCleanup(self.cleanup)
        self.base = _git_head_sha(self.repo)
        self.wt_root = tempfile.mkdtemp(prefix="conductor-wt-test-")
        self.addCleanup(lambda: shutil.rmtree(self.wt_root, ignore_errors=True))

    def _commit_in_worktree(self, wt, path, content, msg):
        """Simulate a wave agent: edit a file in the worktree and commit."""
        Path(wt, path).write_text(content)
        _git(wt, "add", path)
        _git(wt, "commit", "-m", msg)
        return _git_head_sha(wt)

    def test_squash_integrates_disjoint_commit_linearly(self):
        wt = str(Path(self.wt_root, "wA"))
        branch = "conductor/wave/x/P1.T1"
        self.assertTrue(_git_worktree_add(self.repo, wt, branch, self.base))
        # Agent adds a disjoint file + two commits (red/green WIP).
        self._commit_in_worktree(wt, "feature_a.py", "A1\n", "red")
        tip = self._commit_in_worktree(wt, "feature_a.py", "A2\n", "green")
        self.assertEqual(_git_range_commit_count(self.repo, self.base, tip), 2)

        # merge --squash collapses both into ONE commit on the main branch.
        new_sha = _git_merge_squash(self.repo, branch, "feat: task A")
        self.assertIsNotNone(new_sha)
        # Squash is one commit on top of base.
        self.assertEqual(_git_range_commit_count(self.repo, self.base, new_sha), 1)
        # Content integrated onto the main branch (the final green state).
        self.assertEqual(Path(self.repo, "feature_a.py").read_text(), "A2\n")
        # Linear history: HEAD's parent is base.
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD~1").stdout.strip(),
                         _git(self.repo, "rev-parse", self.base).stdout.strip())

    def test_conflict_returns_none_and_leaves_index_clean(self):
        wt = str(Path(self.wt_root, "wB"))
        branch = "conductor/wave/x/P1.T2"
        self.assertTrue(_git_worktree_add(self.repo, wt, branch, self.base))
        # Agent edits README.md (same file the main branch will also edit).
        self._commit_in_worktree(wt, "README.md", "agent\n", "agent edit")
        # Main branch diverges on the SAME file → guaranteed merge conflict.
        Path(self.repo, "README.md").write_text("main\n")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "commit", "-m", "main edit")

        result = _git_merge_squash(self.repo, branch, "feat: task B")
        self.assertIsNone(result)
        # Index must be clean (no half-applied merge state, no untracked residue).
        status = _git(self.repo, "status", "--porcelain").stdout
        self.assertEqual(status.strip(), "")
        # And no MERGE_HEAD lingering.
        self.assertFalse(Path(self.repo, ".git", "MERGE_HEAD").exists())


if __name__ == "__main__":
    unittest.main()
