"""Structural wiring tests for the failure-analyst tier (B).

Mirrors test_skip_refute_wiring.py: pins the agent file, the result-format
reminder registration, the SubagentStart matcher, the dispatch-dedupe exclusion
(read-only — not in _WRITE_AGENTS), and that both Rail A prose and the Rail B-min
spine table carry the ``dispatch_failure_analyst`` action. Structural so the tier
can't be silently removed or rewired.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


class FailureAnalystWiringTests(TestCase):
    def setUp(self):
        self.agents = ROOT / "agents"
        self.implement_skill = (ROOT / "skills" / "implement" / "SKILL.md").read_text(encoding="utf-8")
        self.step_skill = (ROOT / "skills" / "implement-step" / "SKILL.md").read_text(encoding="utf-8")
        self.subagent_start = (ROOT / "scripts" / "on-subagent-start.py").read_text(encoding="utf-8")
        self.dedupe = (ROOT / "scripts" / "on-dispatch-dedupe.py").read_text(encoding="utf-8")
        self.hooks = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")

    def test_agent_file_exists(self):
        self.assertTrue((self.agents / "failure-analyst.md").exists())

    def test_result_format_reminder_registered(self):
        self.assertIn('"failure-analyst"', self.subagent_start)
        self.assertIn("---FAILURE ANALYSIS---", self.subagent_start)

    def test_subagent_start_matcher_includes_agent(self):
        # The SubagentStart matcher must list failure-analyst so the reminder fires.
        self.assertIn("failure-analyst", self.hooks)

    def test_dispatch_dedupe_excludes_readonly_agent(self):
        # failure-analyst is read-only → must NOT be in _WRITE_AGENTS (the dedupe
        # single-writer guard is a no-op for it, leaving its dispatches alone).
        self.assertNotIn('"failure-analyst"', self.dedupe)
        self.assertIn("failure-analyst", self.dedupe)  # mentioned in the exclusion comment

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
