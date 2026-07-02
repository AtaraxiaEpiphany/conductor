"""Structural tests for the adversarial 3-pass review flow (#2).

`conductor:review` runs a serial producer → refuter → critic sequence over the
shared `code-reviewer` analysis core, then synthesizes. `code-reviewer` gains
optional MODE / FINDINGS_JSON / RESULT_PATH params whose defaults preserve the
single-pass behavior (so the post-loop auto-review and "Apply Fixes" path stay
green). These assert the wiring so the 3-pass structure and the opt-in params
can't be silently removed or restructured.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


class CodeReviewerParamTests(TestCase):
    def setUp(self):
        self.agent = (ROOT / "agents" / "code-reviewer.md").read_text(encoding="utf-8")

    def test_mode_param_documented(self):
        self.assertIn("`MODE`", self.agent)
        self.assertIn("`full`", self.agent)
        self.assertIn("`refute`", self.agent)
        self.assertIn("`critique`", self.agent)

    def test_findings_json_and_result_path_documented(self):
        self.assertIn("`FINDINGS_JSON`", self.agent)
        self.assertIn("`RESULT_PATH`", self.agent)

    def test_result_path_defaults_to_canonical_location(self):
        # The default MUST stay review-result.json — the post-loop auto-review
        # and the §3.0 "Apply Fixes" path both read that exact file.
        self.assertIn(
            "{TRACK_DIR}/.conductor/review-result.json", self.agent)

    def test_refute_defaults_to_refuted_when_uncertain(self):
        # The cure for self-preferential bias: a finding that cannot be
        # positively re-confirmed does not survive.
        self.assertIn("refuted", self.agent.lower())

    def test_critique_reports_only_new_classes(self):
        # The completeness-critic surfaces what the producer missed, not
        # duplicates of what it already caught.
        self.assertIn("missed", self.agent.lower())


class ReviewThreePassWiringTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8")

    def test_three_pass_section_present(self):
        self.assertIn("### 2.3 Dispatch Code Review", self.skill)
        self.assertIn("producer", self.skill.lower())
        self.assertIn("refuter", self.skill.lower())
        self.assertIn("critic", self.skill.lower())

    def test_refute_pass_gated_on_critical_high(self):
        # The refute pass runs only when the producer found Critical/High —
        # Medium/Low aren't worth the latency.
        self.assertIn("Critical", self.skill)
        self.assertIn("MODE=refute", self.skill)
        self.assertIn("FINDINGS_JSON", self.skill)

    def test_critique_pass_uses_mode_param(self):
        self.assertIn("MODE=critique", self.skill)
        self.assertIn("RESULT_PATH", self.skill)

    def test_distinct_result_paths_per_pass(self):
        # Serial, not concurrent: the three passes must write distinct files so
        # they don't collide, then synthesis merges into review-result.json.
        self.assertIn("review-refute.json", self.skill)
        self.assertIn("review-critique.json", self.skill)
        self.assertIn("review-result.json", self.skill)

    def test_synthesis_merges_survivors_and_new(self):
        # Merged findings = refute survivors ∪ critic's new classes.
        self.assertIn("B ∪ C", self.skill)

    def test_apply_fixes_path_unchanged(self):
        # §3.0 still reads the canonical review-result.json (now the merged file).
        self.assertIn("Apply Fixes", self.skill)


if __name__ == "__main__":
    main()
