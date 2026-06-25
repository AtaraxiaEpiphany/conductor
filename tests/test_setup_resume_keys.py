"""Regression tests for /conductor:setup resume state-key consistency.

Guards the bug where the §1.0 resume key chain drifted from the keys actually
saved by each step. The chain once listed ``3.4_track_artifacts_created`` and a
terminal ``3.5_setup_complete`` that **no step ever wrote**, while §3.5 actually
saved ``3.5_track_artifacts_created`` and §3.6 saved nothing. On re-run at §3.5
the unrecognized key made the orchestrator skip the §3.6 commit and prematurely
announce "Run /conductor:implement" — even when the user had chosen to skip the
initial track. These tests pin the chain to the keys each step really saves.
"""
import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")


def _chain():
    """Ordered state keys declared in the §1.0 resume chain, backticks stripped."""
    m = re.search(r"Resume from `last_successful_step` \(keys: (.*?)\)\.", SKILL)
    assert m, "could not find resume key chain in setup SKILL.md"
    return [k.strip().strip("`") for k in m.group(1).split("→")]


class ResumeChainTests(TestCase):
    def test_chain_head_and_tail(self):
        keys = _chain()
        self.assertEqual(keys[0], "2.1_product_guide")
        # The two Phase-3 keys must follow the section numbering (3.5 then 3.6).
        self.assertEqual(keys[-2], "3.5_track_artifacts_created")
        self.assertEqual(keys[-1], "3.6_setup_complete")

    def test_terminal_key_is_the_halt_check(self):
        # "If `3.6_setup_complete` → announce complete → HALT."
        self.assertIn("If `3.6_setup_complete`", SKILL)

    def test_no_phantom_keys_remain(self):
        # The old mis-numbered / never-written keys must be gone entirely.
        for phantom in ("3.4_track_artifacts_created", "3.5_setup_complete"):
            self.assertNotIn(phantom, SKILL,
                             f"phantom state key {phantom!r} still present")


class SavedKeysMatchChainTests(TestCase):
    def test_every_nonterminal_chain_key_is_saved_by_a_step(self):
        """Each chain key except the terminal must be written by a 'Save state:'."""
        for key in _chain()[:-1]:
            self.assertIn(f"Save state: `{key}`", SKILL,
                          f"chain key {key!r} is never saved by any step")

    def test_terminal_key_is_saved_by_final_commit(self):
        # Without this, re-run can never reach 'complete' and skips the commit.
        self.assertIn("Save state: `3.6_setup_complete`", SKILL)

    def test_terminal_save_lives_under_section_3_6(self):
        idx_36 = SKILL.index("### 3.6 Final Commit")
        idx_terminal = SKILL.index("Save state: `3.6_setup_complete`")
        self.assertGreater(idx_terminal, idx_36,
                           "terminal state save must come after the §3.6 heading")


class CommitGuardTests(TestCase):
    def test_final_commit_tolerates_nothing_to_commit(self):
        # Re-running §3.6 after artifacts were already committed must not trip the
        # "validate every tool call" contract — hence the diff-cached guard.
        self.assertIn("git diff --cached --quiet", SKILL)


if __name__ == "__main__":
    main()
