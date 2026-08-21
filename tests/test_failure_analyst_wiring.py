"""Structural wiring tests for the failure-analyst tier (B).

Mirrors test_skip_refute_wiring.py: pins the agent file, the result-format
roster row (the fence), the single-writer exclusion (read-only — not a
single_writer row), and that both Rail A prose and the Rail B-min
spine table carry the ``dispatch_failure_analyst`` action. Structural so the tier
can't be silently removed or rewired.
"""
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from track_state import agent_roster as ar  # noqa: E402


class FailureAnalystWiringTests(TestCase):
    def setUp(self):
        self.agents = ROOT / "agents"
        self.implement_skill = (ROOT / "skills" / "implement" / "SKILL.md").read_text(encoding="utf-8")
        self.step_skill = (ROOT / "skills" / "implement-step" / "SKILL.md").read_text(encoding="utf-8")

    def test_agent_file_exists(self):
        self.assertTrue((self.agents / "failure-analyst.md").exists())

    def test_result_format_reminder_registered(self):
        reminder = ar.reminder_for("failure-analyst")
        self.assertIsNotNone(reminder)
        self.assertIn("---FAILURE ANALYSIS---", reminder)

    def test_dispatch_dedupe_excludes_readonly_agent(self):
        # failure-analyst is read-only → must NOT be a single-writer roster row
        # (the dedupe single-writer guard is a no-op for it, leaving its
        # dispatches alone).
        self.assertFalse(ar.is_single_writer("failure-analyst"))
        self.assertNotIn("failure-analyst", ar.single_writers())

    def test_rail_a_prose_carries_dispatch_action(self):
        self.assertIn("conductor:failure-analyst", self.implement_skill)
        self.assertIn("retry_modified", self.implement_skill)
        self.assertIn("failure-analyst-verdict", self.implement_skill)

    def test_rail_b_spine_carries_dispatch_action(self):
        self.assertIn("dispatch_failure_analyst", self.step_skill)
        self.assertIn("failure-analyst-verdict", self.step_skill)

    def test_rail_b_lock_invariant_lists_dispatch(self):
        # The §3.0 "never stop between dispatch and verdict" list must include
        # the failure-analyst handshake so a verdict can't be dropped.
        self.assertIn("dispatch_failure_analyst", self.step_skill)
        self.assertIn("failure-analyst-verdict", self.step_skill)


if __name__ == "__main__":
    main()
