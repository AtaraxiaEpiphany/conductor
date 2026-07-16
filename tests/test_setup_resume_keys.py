"""Regression tests for /conductor:setup resume state-key consistency.

Guards the bugs where the §1.0 resume key chain drifted from the keys actually
saved by each step. History: the chain once listed ``3.4_track_artifacts_created``
and a terminal ``3.5_setup_complete`` that **no step ever wrote**. A later fix
renumbered to ``3.5_track_artifacts_created`` + ``3.6_setup_complete``. The
delegation refactor (setup → /conductor:new-track) then removed the
artifact-creation step entirely, so ``3.5_track_artifacts_created`` is now a
phantom too — the chain drops it and ``3.6_setup_complete`` is the only §3 key.

Also pins the issue #1 fix: the terminal resume key must be saved BEFORE the
final commit, so ``setup_state.json`` lands in the committed tree instead of
being left dirty on the working tree.
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
        # Delegation removed the §3.x artifact step, so the chain's Phase-3 tail
        # is just the terminal key, preceded by the last Phase-2 key.
        self.assertEqual(keys[-2], "2.5_finalization")
        self.assertEqual(keys[-1], "3.6_setup_complete")

    def test_terminal_key_is_the_halt_check(self):
        # "If `3.6_setup_complete` → announce complete → HALT."
        self.assertIn("If `3.6_setup_complete`", SKILL)

    def test_no_phantom_keys_remain(self):
        # All historical / removed keys must be gone entirely.
        for phantom in (
            "3.4_track_artifacts_created",  # never written (original bug)
            "3.5_setup_complete",           # never written (original bug)
            "3.5_track_artifacts_created",  # removed by new-track delegation
        ):
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

    def test_terminal_save_precedes_commit(self):
        # Issue #1 regression guard: the terminal Save state must come BEFORE the
        # git commit, so setup_state.json is staged and committed (clean tree)
        # rather than left dirty. Earlier the order was reversed.
        # Scoped to §3.6 because §2.5's "create track later" branch ALSO uses a
        # guarded commit (an earlier occurrence of the same string); the order
        # invariant only applies to the terminal §3.6 commit.
        idx_36 = SKILL.index("### 3.6 Final Commit")
        idx_terminal = SKILL.index("Save state: `3.6_setup_complete`")
        idx_commit = SKILL.index("git diff --cached --quiet", idx_36)
        self.assertLess(idx_terminal, idx_commit,
                        "terminal Save state must precede the final git commit")


class DelegationTests(TestCase):
    def test_setup_delegates_initial_track_to_new_track(self):
        # §3.0 must hand initial-track creation to /conductor:new-track rather
        # than running its own spec-planner/spec-reviewer/init.
        self.assertIn("/conductor:new-track", SKILL)
        self.assertIn("### 3.2 Delegate to /conductor:new-track", SKILL)


if __name__ == "__main__":
    main()
