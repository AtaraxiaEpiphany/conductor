"""Structural tests for the opt-in self-review ("Ralph Wiggum") loop in the
implement skill (§3.6b).

The loop is DEFAULT OFF — it must not impose latency unless a task opts in.
These assert the opt-in gate + the bounded iteration + escalation contract are
wired into the implement dispatch flow, so the section can't be silently
removed or restructured.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


class SelfReviewLoopWiringTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "implement" / "SKILL.md").read_text(encoding="utf-8")

    def test_section_present(self):
        self.assertIn("### 3.6b Self-Review Loop", self.skill)

    def test_success_path_routes_through_loop(self):
        # §3.6 SUCCESS must hand off to §3.6b (not skip straight to §3.7).
        self.assertIn("**Section 3.6b** (self-review, if the task opted in)", self.skill)

    def test_default_off(self):
        self.assertIn("DEFAULT OFF", self.skill)

    def test_opt_in_signals(self):
        # Per-task marker + global env opt-in.
        self.assertIn("[Review]", self.skill)
        self.assertIn("CONDUCTOR_SELF_REVIEW", self.skill)

    def test_marker_is_not_an_exemption_tag(self):
        # [Review] must NOT enter the Docs/Config/Chore exemption logic — a
        # reviewable task still owes F2/F3.
        self.assertIn("name marker, not a tag", self.skill)

    def test_dispatches_code_reviewer(self):
        self.assertIn("conductor:code-reviewer", self.skill)
        self.assertIn("REVISION_RANGE", self.skill)

    def test_bounded_by_convergence_and_budget(self):
        # No runaway loop — convergence (loop-until-dry) capped by a 3-iteration
        # budget, deduping vs a `seen` set. Replaces the old "ONE fix iteration".
        self.assertIn("loop-until-dry", self.skill)
        self.assertIn("max 3 fix iterations", self.skill)
        self.assertIn("`seen`", self.skill)

    def test_escalates_only_on_residual_judgment(self):
        # Human (AskUserQuestion) is pulled in only for residual Critical, per
        # §3.6b "escalates to human only when judgment is required".
        self.assertIn("AskUserQuestion", self.skill)


if __name__ == "__main__":
    main()
