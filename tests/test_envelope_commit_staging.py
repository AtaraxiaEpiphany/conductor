"""Regression tests for the relayed-envelope commit lines.

The post-loop ``post`` lists and the implement-loop ``decision`` blobs hand the
teleoperator bare bash commit lines. Two bugs lived in those lines:

  1. Bare ``git commit -m "..."`` (finalize, deferred Verify/Skip, failed-task
     Retry/Skip/Block, manual-task Defer/Skip, archive, delete): the preceding
     ``track-state ...`` command mutates TRACKED files but never stages them, so
     the commit found nothing staged and failed with "no changes added to
     commit". This first surfaced on the post-loop ``finalize`` leaf — the very
     first commit after the implement loop hands off at ``done``.
  2. ``git commit -am "..."`` (advisory / lint / digest): ``-a`` stages only
     modifications to ALREADY-TRACKED files, so the FIRST sidecar (a brand-new
     untracked file) was left out of its own commit.

All sites now route through ``_bookkeeping_commit_line``:
``git add -A && git diff --cached --quiet || git commit -m "<msg>"`` — stages
new + modified + deleted, and skips the commit (exit 0) when nothing is staged
so idempotent re-entry never hard-fails.

These tests pin (a) the helper's shape, (b) that EVERY envelope commit line
goes through it (a drift guard), and (c) the three real-git failure modes plus
idempotency.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.track_state.dispatch import (
    _bookkeeping_commit_line,
    _post_loop_finalize_post, _post_loop_advisory_post, _post_loop_lint_post,
    _post_loop_digest_post, _post_loop_apply_fixes_post,
    _post_loop_deferred_decision, _post_loop_archive_decision,
    _failed_task_decision, _manual_task_decision,
)

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _assert_robust_commit(self, line):
    """A relayed-envelope commit line must stage, guard empty, and commit."""
    self.assertIn("git add -A", line,
                  "commit line never stages its changes (the original bug)")
    self.assertIn("git diff --cached --quiet ||", line,
                  "commit line lacks the nothing-staged guard (idempotent "
                  "re-entry would hard-fail)")
    self.assertIn("git commit -m", line, "commit line does not commit")
    self.assertNotIn("git commit -am", line,
                     "commit line uses -a, which misses brand-new untracked "
                     "files (sidecars / sentinels) on first run")


def _all_envelope_commit_lines(td):
    """Every commit line the post-loop `post` and implement-loop `decision`
    envelopes emit, as (label, line) pairs. A drop here means a site regressed
    to an ad-hoc constructor or vanished."""
    td = str(td)
    out = []
    for label, post in [
        ("finalize", _post_loop_finalize_post(td)),
        ("advisory", _post_loop_advisory_post(td)),
        ("lint", _post_loop_lint_post(td)),
        ("digest", _post_loop_digest_post(td)),
        ("apply_fixes", _post_loop_apply_fixes_post(td, "src/app.py")),
    ]:
        out += [(label, c) for c in post if "git commit" in c]

    deferred = _post_loop_deferred_decision(
        td, [dict(phase=1, task=1, subtask=None, name="X")])
    for label, cmds in deferred["commands"].items():
        out += [(f"deferred:{label}", c) for c in cmds if "git commit" in c]

    archive = _post_loop_archive_decision(td)
    for label, cmds in archive["commands"].items():
        out += [(f"archive:{label}", c) for c in cmds if "git commit" in c]

    failed = _failed_task_decision(td, 1, 1, None, "X", 3)
    for label, cmds in failed["commands"].items():
        out += [(f"failed:{label}", c) for c in cmds if "git commit" in c]

    manual = _manual_task_decision(td, 1, 1, "X")
    for label, cmds in manual["commands"].items():
        out += [(f"manual:{label}", c) for c in cmds if "git commit" in c]

    return out


class HelperShapeTests(unittest.TestCase):
    def test_helper_stages_and_guards(self):
        line = _bookkeeping_commit_line("chore(conductor): Complete track")
        _assert_robust_commit(self, line)
        # Message survives shlex.quote round-trip intact.
        self.assertIn("chore(conductor): Complete track", line)

    def test_helper_shlex_quotes_embedded_quotes(self):
        # A free-text task name with a single quote must not break the line.
        # Run in an ISOLATED tmp git repo (cwd=tmp) so the commit can't touch
        # the real repo — a bare shell=True with no cwd would `git add -A` the
        # caller's working tree.
        line = _bookkeeping_commit_line("chore(conductor): Skip 'o'brien [P1.T1]")
        _assert_robust_commit(self, line)
        import shutil
        d = tempfile.mkdtemp()
        try:
            subprocess.run(["git", "-C", d, "init", "-q"], check=True,
                           capture_output=True)
            Path(d, "x").write_text("1")
            subprocess.run(["git", "-C", d, "add", "-A"], check=True,
                           capture_output=True)
            subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"],
                           check=True, capture_output=True, env=_GIT_ENV)
            # Dirty the tree so the empty-guard does NOT skip the commit — we
            # want the commit to actually fire and land its embedded-quote msg.
            Path(d, "x").write_text("2")
            r = subprocess.run(line, shell=True, cwd=d, capture_output=True,
                               text=True, env=_GIT_ENV)
            # Shell parsed the line (no "Syntax error") AND the commit landed.
            self.assertNotIn("Syntax error", r.stderr)
            self.assertEqual(r.returncode, 0, f"{r.stdout!r}\n{r.stderr!r}")
            msg = subprocess.run(["git", "-C", d, "log", "-1", "--format=%s"],
                                 capture_output=True, text=True,
                                 env=_GIT_ENV).stdout.strip()
            self.assertEqual(msg, "chore(conductor): Skip 'o'brien [P1.T1]")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class EnvelopeCoverageTests(unittest.TestCase):
    """Drift guard: every envelope commit line must be robust. If someone adds
    a new gate and hand-writes ``git commit -m ...``, this fires."""

    def test_every_envelope_commit_line_stages_and_guards(self):
        lines = _all_envelope_commit_lines("TD")
        # Sanity: we found the known sites (14 distinct constructors surface as
        # 15 command-list appearances — Verify/Skip share one constructor).
        self.assertGreaterEqual(len(lines), 14,
                                f"expected ≥14 envelope commit lines, got "
                                f"{len(lines)}: {lines}")
        labels_seen = {label for label, _ in lines}
        for expected in ("finalize", "advisory", "lint", "digest",
                         "apply_fixes", "deferred:Verify all",
                         "deferred:Keep deferred", "archive:Archive",
                         "archive:Delete", "failed:Retry", "failed:Skip",
                         "failed:Block", "manual:Defer", "manual:Skip"):
            self.assertIn(expected, labels_seen,
                          f"missing envelope commit for {expected!r}")
        for label, line in lines:
            with self.subTest(label=label):
                _assert_robust_commit(self, line)

    def test_no_bare_or_dash_a_commit_in_dispatch_module(self):
        """Source-level guard: the only ``git commit`` emitter in dispatch.py
        is ``_bookkeeping_commit_line``. Catches a future ad-hoc constructor."""
        src = Path("scripts/track_state/dispatch.py").read_text()
        # Strip the helper's own return + its docstring by removing the helper
        # body before scanning — everything else must not build a commit line.
        # (A pragmatic proxy: no line assigns a "git commit -m" / "-am" string.)
        for bad in ('"git commit -m', '"git commit -am"', "'git commit -m",
                    "'git commit -am'"):
            self.assertNotIn(bad, src,
                             f"ad-hoc commit constructor re-introduced: {bad!r}")


class RealGitBehaviorTests(unittest.TestCase):
    """End-to-end: the commit line captures each working-tree change class and
    is a no-op (exit 0) on a clean tree."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        Path(self.d, "track-state.json").write_text('{"v": 1}')
        Path(self.d, "plan.md").write_text("# plan\n")
        subprocess.run(["git", "-C", self.d, "init", "-q"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", self.d, "add", "-A"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", self.d, "commit", "-q", "-m", "init"],
                       check=True, capture_output=True, env=_GIT_ENV)
        self.addCleanup(self._rm, self.d)

    def _rm(self, d):
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def _head_msg(self):
        r = subprocess.run(["git", "-C", self.d, "log", "-1", "--format=%s"],
                           capture_output=True, text=True, env=_GIT_ENV)
        return r.stdout.strip()

    def _run(self, line):
        return subprocess.run(line, shell=True, cwd=self.d,
                              capture_output=True, text=True, env=_GIT_ENV)

    def test_captures_unstaged_tracked_mod(self):
        """The original bug: a tracked file mutated by a track-state command
        sits unstaged; the commit line must stage + record it. A bare
        ``git commit -m`` would exit 1 ('no changes added to commit') here."""
        Path(self.d, "track-state.json").write_text('{"v": 2}')  # unstaged mod
        line = _bookkeeping_commit_line("chore(conductor): Complete track")
        r = self._run(line)
        self.assertEqual(r.returncode, 0, f"{r.stdout!r}\n{r.stderr!r}")
        self.assertEqual(self._head_msg(), "chore(conductor): Complete track")

    def test_captures_brand_new_untracked_file(self):
        """The -am bug: a brand-new sidecar/sentinel is untracked, so ``-a``
        skipped it. ``git add -A`` must stage it."""
        Path(self.d, ".conductor").mkdir()
        Path(self.d, ".conductor", "post-loop.json").write_text('{"schema":2}')
        line = _bookkeeping_commit_line("chore(conductor): Post-loop advisory")
        r = self._run(line)
        self.assertEqual(r.returncode, 0, f"{r.stdout!r}\n{r.stderr!r}")
        self.assertEqual(self._head_msg(), "chore(conductor): Post-loop advisory")
        # The new file is actually IN the commit.
        show = subprocess.run(["git", "-C", self.d, "show", "--stat", "--name-only"],
                              capture_output=True, text=True, env=_GIT_ENV).stdout
        self.assertIn("post-loop.json", show)

    def test_captures_deletion(self):
        """The archive/delete bug: ``rm -rf`` leaves a working-tree deletion
        unstaged; the commit line must stage + record it."""
        Path(self.d, "plan.md").unlink()
        line = _bookkeeping_commit_line("chore(conductor): Delete track")
        r = self._run(line)
        self.assertEqual(r.returncode, 0, f"{r.stdout!r}\n{r.stderr!r}")
        self.assertEqual(self._head_msg(), "chore(conductor): Delete track")
        self.assertFalse(Path(self.d, "plan.md").exists())
        # The deletion is recorded — plan.md is gone from HEAD's tree.
        ls = subprocess.run(["git", "-C", self.d, "ls-tree", "-r", "--name-only", "HEAD"],
                            capture_output=True, text=True, env=_GIT_ENV).stdout
        self.assertNotIn("plan.md", ls)

    def test_idempotent_on_clean_tree(self):
        """Re-running the line on a clean tree must exit 0 and create no commit
        — the lossless-resume / idempotent-re-entry guarantee."""
        line = _bookkeeping_commit_line("chore(conductor): Complete track")
        before = self._head_msg()
        r = self._run(line)
        self.assertEqual(r.returncode, 0, f"{r.stdout!r}\n{r.stderr!r}")
        self.assertEqual(self._head_msg(), before, "clean-tree re-run committed")
        # Exactly one commit (the init) — no empty commit was forced.
        n = subprocess.run(["git", "-C", self.d, "rev-list", "--count", "HEAD"],
                           capture_output=True, text=True, env=_GIT_ENV).stdout.strip()
        self.assertEqual(n, "1")


if __name__ == "__main__":
    unittest.main()
