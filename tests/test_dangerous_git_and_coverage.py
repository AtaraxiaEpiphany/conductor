"""Tests for the Tier-1 gate-hardening: segment-aware dangerous-git detection
and the conductor-scoped coverage-trigger.

Both gates previously used unanchored substring matching that false-fired:
  * ``is_dangerous_git_operation`` matched ``"git reset --hard"`` inside a
    ``--grep`` value, an echo'd string, or a heredoc body.
  * ``should_verify_coverage`` matched the bare word ``"conductor"`` and ran the
    F3 coverage gate on any commit that mentioned it (e.g. ``fix(conductor-plugin)``).

These tests pin the segment-aware replacements.
"""
import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, _scripts / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_pcc = _load("pre_command_check", "pre-command-check.py")
_abc = _load("on_batch_complete", "on-batch-complete.py")
_detect = _pcc._detect_dangerous_git
_cov = _abc.should_verify_coverage


class DetectDangerousGitTests(TestCase):
    # --- true positives (real dangerous ops at a command position) ---
    def test_plain_reset_hard(self):
        self.assertEqual(_detect("git reset --hard"), "reset --hard")

    def test_after_chain_boundary(self):
        self.assertEqual(_detect("cd src && git reset --hard HEAD~1"), "reset --hard")

    def test_after_semicolon(self):
        self.assertEqual(_detect("echo hi; git rebase main"), "rebase")

    def test_rebase_clean_filterbranch(self):
        self.assertEqual(_detect("git rebase -i main"), "rebase")
        self.assertEqual(_detect("git clean -fdx"), "clean")
        self.assertEqual(_detect("git filter-branch -- --all"), "filter-branch")

    def test_branch_D_and_checkout_force(self):
        self.assertEqual(_detect("git branch -D old"), "branch -D")
        self.assertEqual(_detect("git checkout -f main"), "checkout --force")
        self.assertEqual(_detect("git checkout --force ."), "checkout --force")

    def test_sudo_prefix(self):
        self.assertEqual(_detect("sudo git reset --hard"), "reset --hard")

    def test_inside_command_substitution(self):
        # No false-negative regression: op hidden in $(...) is still caught.
        self.assertEqual(_detect("OUT=$(git reset --hard)"), "reset --hard")

    # --- false positives that MUST NOT trip (the bug being fixed) ---
    def test_no_match_inside_grep_value(self):
        self.assertIsNone(_detect('git log --grep="git reset --hard"'))

    def test_no_match_inside_echo(self):
        self.assertIsNone(_detect('echo "avoid git reset --hard" >> NOTES.md'))

    def test_no_match_inside_heredoc_body(self):
        cmd = 'cat <<EOF\ndo not run git reset --hard\nEOF'
        self.assertIsNone(_detect(cmd))

    def test_no_match_in_unrelated_git(self):
        self.assertIsNone(_detect("git log --oneline"))
        self.assertIsNone(_detect("git pull"))

    # --- safe forms that the precise arg patterns correctly allow ---
    def test_reset_soft_not_flagged(self):
        self.assertIsNone(_detect("git reset --soft HEAD~1"))

    def test_branch_list_not_flagged(self):
        self.assertIsNone(_detect("git branch -a"))  # -a, not -D

    def test_branch_safe_delete_not_flagged(self):
        self.assertIsNone(_detect("git branch -d merged"))  # lowercase -d is safe


class ShouldVerifyCoverageTests(TestCase):
    def _tc(self, cmd):
        return [{"tool_name": "Bash", "tool_input": {"command": cmd}}]

    def test_fires_for_conductor_scope(self):
        self.assertTrue(_cov(self._tc('git commit -m "chore(conductor): Complete x"')))
        self.assertTrue(_cov(self._tc("git commit -m 'docs(conductor): sync wiki'")))

    def test_fires_for_state_file_pathspec(self):
        self.assertTrue(_cov(self._tc("git commit track-state.json -m x")))

    # --- the false positives that MUST NOT fire (the bug being fixed) ---
    def test_no_fire_for_conductor_plugin_scope(self):
        self.assertFalse(_cov(self._tc('git commit -m "fix(conductor-plugin): typo"')))

    def test_no_fire_for_bare_conductor_word(self):
        self.assertFalse(_cov(self._tc('git commit -m "update conductor docs"')))

    def test_no_fire_for_unrelated_commit(self):
        self.assertFalse(_cov(self._tc('git commit -m "feat(api): add endpoint"')))

    def test_non_commit_command_skipped(self):
        self.assertFalse(_cov(self._tc("git status")))


if __name__ == "__main__":
    main()
