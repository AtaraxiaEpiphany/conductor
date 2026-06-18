"""Tests for lib.validation.is_dangerous_git_operation.

Regression coverage for the case-folding bug: ``git branch -D`` (force-delete)
was never detected because the command was lowercased but the needle kept its
uppercase D, collapsing -D to -d. The fix matches the -D op against the
original command casing, so the safe ``git branch -d`` must NOT be flagged.
"""
from unittest import TestCase, main

from scripts.lib.validation import is_dangerous_git_operation


class TestDangerousGitDetection(TestCase):
    def test_all_dangerous_ops_detected(self):
        for op in [
            "git reset --hard HEAD~1",
            "git rebase main",
            "git clean -fd",
            "git filter-branch -- --all",
            "git checkout --force main",
            "git branch -D feature",
        ]:
            self.assertTrue(is_dangerous_git_operation(op), f"should flag: {op}")

    def test_force_delete_D_detected_in_compound_command(self):
        # The bug: this used to pass through undetected.
        self.assertTrue(is_dangerous_git_operation("cd repo && git branch -D old && git status"))

    def test_safe_delete_d_NOT_flagged(self):
        # -d (delete merged only) is safe; must not be a false positive.
        self.assertFalse(is_dangerous_git_operation("git branch -d feature"))

    def test_subcommand_case_insensitivity_preserved(self):
        # Lowercase ops still match despite odd casing in the invocation.
        self.assertTrue(is_dangerous_git_operation("Git RESET --hard"))
        self.assertTrue(is_dangerous_git_operation("GIT REBASE"))

    def test_benign_commands_not_flagged(self):
        for ok in [
            "git status",
            "git commit -m 'feat: x'",
            "git push",
            "git log --oneline",
            "git branch",            # list branches
            "git branch feature",    # create branch
            "git checkout main",     # plain checkout (not --force)
            "ls -la",
        ]:
            self.assertFalse(is_dangerous_git_operation(ok), f"should not flag: {ok}")

    def test_empty_and_non_git(self):
        self.assertFalse(is_dangerous_git_operation(""))
        self.assertFalse(is_dangerous_git_operation("rm -rf /tmp/x"))
        # Note: a command that merely CONTAINS the dangerous substring (e.g.
        # `echo git branch -D`) IS flagged — the matcher is a deliberate
        # substring scan, same as for every other op.


if __name__ == "__main__":
    main()
