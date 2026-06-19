r"""Tests for on-test-run.has_test_failure — failure detection precision.

Regression: the bare \bFAILED\b / \bFAILURES\b and \d+\s+failed\b patterns
matched the "0 failed" / "0 failures" / "Failed: 0" lines that all-green
Jest/Vitest/dotnet summaries always print, flagging a passing run as a
failure and injecting "fix the implementation" guidance into a Green run.
"""
import importlib.util
import sys
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
# Production gets scripts/ on sys.path automatically (script dir = sys.path[0]);
# replicate that so the module's `from lib.hook_io import ...` resolves.
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "on_test_run", _scripts / "on-test-run.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
has_test_failure = _mod.has_test_failure


class HasTestFailureTests(TestCase):
    # --- All-green summaries must NOT be flagged (the bug) ---
    def test_jest_passing_zero_failed(self):
        self.assertFalse(has_test_failure(
            "Tests:       3 passed, 0 failed", "", False))

    def test_jest_snapshots_zero_failed(self):
        self.assertFalse(has_test_failure(
            "Snapshots:   0 failed, 0 total", "", False))

    def test_vitest_passing_zero_failed(self):
        self.assertFalse(has_test_failure(
            "Test Files  1 passed (1)\n     Tests  3 passed (3)\n"
            "  0 failed", "", False))

    def test_dotnet_passing_failed_zero(self):
        self.assertFalse(has_test_failure(
            "Passed!  - Passed: 3, Failed: 0, Skipped: 0, Total: 3", "", False))

    def test_zero_failures_word(self):
        self.assertFalse(has_test_failure("0 failures", "", False))

    def test_zero_failing_word(self):
        self.assertFalse(has_test_failure("  0 failing", "", False))

    def test_pytest_clean_pass(self):
        self.assertFalse(has_test_failure("5 passed in 0.12s", "", False))

    # --- Real failures MUST still be flagged ---
    def test_nonzero_failed(self):
        self.assertTrue(has_test_failure("1 failed, 2 passed", "", False))

    def test_nonzero_failures(self):
        self.assertTrue(has_test_failure("3 failures", "", False))

    def test_nonzero_failing(self):
        self.assertTrue(has_test_failure("  1 failing (1ms)", "", False))

    def test_dotnet_failed_nonzero(self):
        self.assertTrue(has_test_failure(
            "Failed!  - Failed: 1, Passed: 2, Skipped: 0, Total: 3", "", False))

    def test_failure_count_after_label(self):
        # "failures: 3" (count after the label)
        self.assertTrue(has_test_failure("failures: 3", "", False))

    def test_assertion_error(self):
        # The structural "assertion error" signal (with whitespace) still fires.
        # NOTE: Python's one-word "AssertionError" (no space) is a separate
        # pre-existing detection gap, not addressed on this branch.
        self.assertTrue(has_test_failure("assertion error: 1 != 2", "", False))

    def test_runtime_error(self):
        self.assertTrue(has_test_failure("runtime error: segfault", "", False))

    def test_interrupted(self):
        self.assertTrue(has_test_failure("", "", interrupted=True))

    # --- Negative controls: no false match on innocent words ---
    def test_test_name_with_fail_word(self):
        # A passing test whose name/description contains "fail".
        self.assertFalse(has_test_failure(
            "✓ should fail when input is invalid", "", False))

    def test_successfully_message(self):
        self.assertFalse(has_test_failure(
            "All tests passed successfully", "", False))


if __name__ == "__main__":
    main()
