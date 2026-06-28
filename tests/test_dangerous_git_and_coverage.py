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


class BatchGateOrderingTests(TestCase):
    """Gap #9: the cheap V6 checkpoint gate runs BEFORE the expensive F3 coverage
    probe, so a slow/timeout coverage run cannot starve it under the 35s
    PostToolBatch hook budget."""

    def setUp(self):
        # Snapshot the surfaces main() touches so each test restores cleanly.
        self._orig = {
            "scp": _abc.should_verify_checkpoint,
            "scv": _abc.should_verify_coverage,
            "vpc": _abc.verify_phase_checkpoint,
            "vcg": _abc.verify_coverage_gate,
            "ilog": _abc.init_logging,
            "lbm": _abc.log_batch_metrics,
        }
        # Keep main()'s disk I/O out of the test sandbox.
        _abc.init_logging = lambda name: None
        _abc.log_batch_metrics = lambda *a, **k: None

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(_abc,
                    {"scp": "should_verify_checkpoint",
                     "scv": "should_verify_coverage",
                     "vpc": "verify_phase_checkpoint",
                     "vcg": "verify_coverage_gate",
                     "ilog": "init_logging",
                     "lbm": "log_batch_metrics"}[k], v)

    def _run_main(self, tool_calls=None):
        import io
        import json
        old_in, old_out = sys.stdin, sys.stdout
        buf = io.StringIO()
        sys.stdin = io.StringIO(json.dumps({
            "session_id": "t", "cwd": "", "tool_calls": tool_calls or []}))
        sys.stdout = buf
        try:
            _abc.main()
        except SystemExit:
            pass  # write_simple_output exits 0 after emitting
        finally:
            sys.stdin, sys.stdout = old_in, old_out
        return buf.getvalue()

    def test_checkpoint_emits_and_short_circuits_before_coverage(self):
        """When both gates would fire, the checkpoint gate emits first and the
        expensive coverage probe never runs — the ordering guarantee that a slow
        coverage run can't drop the checkpoint signal."""
        calls = []
        _abc.should_verify_checkpoint = lambda tc: True
        _abc.should_verify_coverage = lambda tc: True
        _abc.verify_phase_checkpoint = lambda cwd: (calls.append("ckpt"), "CKPT MSG")[1]
        _abc.verify_coverage_gate = lambda cwd: (calls.append("cov"), "COV MSG")[1]
        out = self._run_main()
        self.assertIn("CKPT MSG", out)
        self.assertEqual(calls, ["ckpt"])  # coverage gate never reached

    def test_coverage_runs_when_checkpoint_has_no_finding(self):
        """No checkpoint finding → execution proceeds to the coverage gate, in
        that order (ckpt evaluated before cov)."""
        calls = []
        _abc.should_verify_checkpoint = lambda tc: True
        _abc.should_verify_coverage = lambda tc: True
        _abc.verify_phase_checkpoint = lambda cwd: (calls.append("ckpt"), None)[1]
        _abc.verify_coverage_gate = lambda cwd: (calls.append("cov"), "COV MSG")[1]
        out = self._run_main()
        self.assertEqual(calls, ["ckpt", "cov"])
        self.assertIn("COV MSG", out)


class CoverageProbeContractTests(TestCase):
    """Gap #9: coverage subprocess timeout is 20s (headroom under the 35s hook
    budget) and a timeout degrades to None rather than crashing the hook."""

    def setUp(self):
        self._orig_dpt = _abc.detect_project_type
        self._orig_run = _abc.subprocess.run

    def tearDown(self):
        _abc.detect_project_type = self._orig_dpt
        _abc.subprocess.run = self._orig_run

    def test_subprocess_timeout_is_20_seconds(self):
        captured = {}

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return _R()

        _abc.detect_project_type = lambda cwd: "python"
        _abc.subprocess.run = fake_run
        _abc.get_coverage_percent(Path("."))
        self.assertEqual(captured["timeout"], 20)

    def test_returns_none_on_timeout(self):
        def raise_timeout(cmd, **kwargs):
            raise _abc.subprocess.TimeoutExpired(
                cmd=cmd, timeout=kwargs.get("timeout", 20))

        _abc.detect_project_type = lambda cwd: "python"
        _abc.subprocess.run = raise_timeout
        self.assertIsNone(_abc.get_coverage_percent(Path(".")))


if __name__ == "__main__":
    main()
