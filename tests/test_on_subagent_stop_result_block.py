r"""Tests for on-subagent-stop._has_result_block — the END-marker guard.

Regression: ``main()`` previously truncated last_assistant_message to its first
2000 chars (``[:2000]``). The mandatory result-block close tag
(``---END RESULT---`` / ``---END CHECKPOINT RESULT---``) is emitted at the END
of a subagent's turn (Section 6.2), so any normal-length task-executor turn
(>2KB of explanation/diffs before the block) defeated the head-truncated scan.
The guard then falsely concluded "no result block" and force-blocked a
successful agent for a recovery turn — a spurious [Conductor Recovery] hook
complaint that also churned result.json.

The fix scans the full message via _has_result_block.
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
    "on_subagent_stop", _scripts / "on-subagent-stop.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_has_result_block = _mod._has_result_block


class HasResultBlockTests(TestCase):
    # --- The bug: result block past the first 2000 chars must be detected ---
    def test_result_block_after_long_preamble(self):
        # >2KB of normal turn output, then the result block at the very end.
        preamble = "Implementing the feature step by step. " * 120  # ~3.5KB
        msg = preamble + "\n---TASK RESULT---\nSTATUS: SUCCESS\n---END RESULT---"
        self.assertTrue(_has_result_block(msg))

    def test_checkpoint_result_block_after_long_preamble(self):
        preamble = "Checking phase readiness. " * 150  # >2KB
        msg = preamble + "\n---CHECKPOINT RESULT---\nSTATUS: PASSED\n---END RESULT---"
        self.assertTrue(_has_result_block(msg))

    def test_block_just_over_2kb_boundary(self):
        # Exactly the scenario that broke: marker lands right after byte 2000.
        preamble = "x" * 2000
        msg = preamble + "\n---END RESULT---"
        self.assertTrue(_has_result_block(msg))

    # --- Positive cases: block near the start still works ---
    def test_short_message_with_block(self):
        self.assertTrue(_has_result_block("---TASK RESULT---\nSTATUS: SUCCESS\n---END RESULT---"))

    def test_multitag_close(self):
        self.assertTrue(_has_result_block("...---END DOC SYNC RESULT---"))

    # --- Negative cases: genuinely missing block ---
    def test_no_block_present(self):
        self.assertFalse(_has_result_block("Agent did lots of work but never reported a result."))

    def test_open_tag_without_close_is_not_a_block(self):
        # Only an open tag (---TASK RESULT---) with no ---END ...--- must NOT count.
        self.assertFalse(_has_result_block("---TASK RESULT---\nSTATUS: SUCCESS"))

    def test_whitespace_padded_close_tag_still_counts(self):
        # Regression: a model emitting ``---END  REVIEW RESULT ---`` (stray inner
        # spaces) failed the strict ``---END [A-Z0-9 ]+---`` grammar and was
        # treated as "stopped without a result block," forcing a spurious recovery
        # turn. The grammar now tolerates whitespace around the inner words.
        self.assertTrue(_has_result_block("--- REVIEW RESULT ---\nSTATUS: APPROVED\n---END REVIEW RESULT ---"))
        self.assertTrue(_has_result_block("done\n---END  TASK RESULT ---"))

    def test_empty_message(self):
        self.assertFalse(_has_result_block(""))


if __name__ == "__main__":
    main()
