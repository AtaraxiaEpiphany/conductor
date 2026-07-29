"""Phase 2: structured verdict objects inside ---RESULT--- blocks.

Agents now emit a fenced ```` ```json ```` verdict object inside their result
blocks; ``parse_result_block`` extracts it as a machine-branchable dict so the
loop-back edge (phase-checker FAILED → re-dispatch) can branch on
``verdict["status"]`` instead of regex-mining ``STATUS:`` prose.

Invariants pinned here:

  1. A fenced JSON object with ``status`` inside a result block parses.
  2. The regex fallback path is untouched: a block with NO JSON still returns
     ``None`` (callers fall back to ``extract_result_blocks`` prose), and a
     malformed/missing-``status`` object also returns ``None`` — a missing
     structured verdict never breaks extraction.
  3. A JSON fence OUTSIDE a result block is ignored (can't masquerade as a
     verdict).
  4. Each agent family's block (task-executor / phase-checker / test-runner /
     ac-tracer) parses to a status-bearing dict; the ``report_field`` mirrors
     the verify-mode registry (``BUILD``/``L1_VERIFY``/``START``/``ANCHOR``).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lib"))

from recovery import parse_result_block  # noqa: E402


class ParseResultBlockTests(unittest.TestCase):
    """The structured-verdict extractor + its fail-open fallback."""

    def test_parses_fenced_json_inside_result_block(self):
        text = (
            "---CHECKPOINT RESULT---\n"
            "STATUS: FAILED\n"
            "FAILURE_REASON: tests failed\n"
            "```json\n"
            '{"status": "FAILED", "failure_reason": "tests failed"}\n'
            "```\n"
            "---END RESULT---"
        )
        verdict = parse_result_block(text)
        self.assertEqual(verdict["status"], "FAILED")
        self.assertEqual(verdict["failure_reason"], "tests failed")

    def test_no_json_returns_none(self):
        # The fallback path: prose-only block yields no structured verdict.
        text = (
            "---TASK RESULT---\n"
            "STATUS: SUCCESS\n"
            "COMMIT_SHA: abc1234\n"
            "---END RESULT---"
        )
        self.assertIsNone(parse_result_block(text))

    def test_no_result_block_returns_none(self):
        self.assertIsNone(parse_result_block("just prose, no block"))
        self.assertIsNone(parse_result_block(""))

    def test_malformed_json_returns_none(self):
        text = (
            "---TASK RESULT---\n"
            "```json\n"
            "{not valid json\n"
            "```\n"
            "---END RESULT---"
        )
        self.assertIsNone(parse_result_block(text))

    def test_missing_status_returns_none(self):
        # An object without ``status`` is not a verdict — fall back to prose.
        text = (
            "---TASK RESULT---\n"
            "```json\n"
            '{"commit_sha": "abc1234"}\n'
            "```\n"
            "---END RESULT---"
        )
        self.assertIsNone(parse_result_block(text))

    def test_json_fence_outside_result_block_ignored(self):
        text = (
            "Here is some JSON in prose:\n"
            "```json\n"
            '{"status": "FAILED", "failure_reason": "decoy"}\n'
            "```\n"
            "but no result block wraps it."
        )
        self.assertIsNone(parse_result_block(text))

    def test_tolerant_fence_whitespace_and_case(self):
        # ```` ```JSON ```` with surrounding whitespace still matches.
        text = (
            "---L1 VERIFY RESULT---\n"
            "```JSON\n"
            '{"status": "failed", "report_field": "L1_VERIFY"}\n'
            "```\n"
            "---END RESULT---"
        )
        verdict = parse_result_block(text)
        self.assertEqual(verdict["status"], "failed")

    def test_first_statused_object_wins_across_multiple_blocks(self):
        text = (
            "---AC TRACE RESULT---\n"
            "```json\n"
            '{"status": "FAILED", "report_field": "AC_TRACE"}\n'
            "```\n"
            "---END RESULT---\n"
            "---CHECKPOINT RESULT---\n"
            "```json\n"
            '{"status": "PASSED"}\n'
            "```\n"
            "---END RESULT---"
        )
        verdict = parse_result_block(text)
        self.assertEqual(verdict["status"], "FAILED")


class AgentFamilyParityTests(unittest.TestCase):
    """Each agent family's emitted block parses to a status-bearing verdict
    whose ``report_field`` mirrors the verify-mode registry."""

    def test_task_executor_success(self):
        text = (
            "---TASK RESULT---\n"
            "STATUS: SUCCESS\n"
            "```json\n"
            '{"status": "SUCCESS", "commit_sha": "abc1234", "summary": "done"}\n'
            "```\n"
            "---END RESULT---"
        )
        v = parse_result_block(text)
        self.assertEqual(v["status"], "SUCCESS")
        self.assertEqual(v["commit_sha"], "abc1234")

    def test_task_executor_failure(self):
        text = (
            "---TASK RESULT---\n"
            "STATUS: FAILURE\n"
            "```json\n"
            '{"status": "FAILED", "failure_reason": "boom", '
            '"fix_directives": "retry"}\n'
            "```\n"
            "---END RESULT---"
        )
        v = parse_result_block(text)
        self.assertEqual(v["status"], "FAILED")
        self.assertEqual(v["fix_directives"], "retry")

    def test_phase_checker_passed_with_report_fields(self):
        text = (
            "---CHECKPOINT RESULT---\n"
            "STATUS: PASSED\n"
            "```json\n"
            '{"status": "PASSED", "checkpoint_sha": "abc1234", '
            '"report": {"L1_VERIFY": "passed", "BUILD": "skipped"}}\n'
            "```\n"
            "---END RESULT---"
        )
        v = parse_result_block(text)
        self.assertEqual(v["status"], "PASSED")
        self.assertEqual(v["report"]["L1_VERIFY"], "passed")

    def test_test_runner_report_field_is_l1_verify(self):
        text = (
            "---L1 VERIFY RESULT---\n"
            "STATUS: failed\n"
            "```json\n"
            '{"status": "failed", "report_field": "L1_VERIFY", '
            '"command": "pytest -q"}\n'
            "```\n"
            "---END RESULT---"
        )
        v = parse_result_block(text)
        self.assertEqual(v["report_field"], "L1_VERIFY")

    def test_ac_tracer_failed_carries_gate(self):
        text = (
            "---AC TRACE RESULT---\n"
            "VERDICT: FAILED\n"
            "```json\n"
            '{"status": "FAILED", "report_field": "AC_TRACE", '
            '"failure_reason": "AC-2 ungrounded"}\n'
            "```\n"
            "---END RESULT---"
        )
        v = parse_result_block(text)
        self.assertEqual(v["status"], "FAILED")
        self.assertEqual(v["report_field"], "AC_TRACE")


if __name__ == "__main__":
    unittest.main()
