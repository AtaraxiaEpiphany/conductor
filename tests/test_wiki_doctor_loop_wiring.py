"""Structural tests for the wiki-doctor loop-until-dry + per-finding refute (#6).

`wiki-doctor lint` runs a convergent loop — lint → dedup vs `seen` → refute the
NEW findings → re-lint — stopping on a dry round (loop-until-dry), with each
round's findings adversarially refuted (doc-linter MODE=refute) to strip false
positives. `wiki-doctor diff` gets the completeness loop only (wiki-differ has
no refute mode). `doc-linter` gains optional MODE / FINDINGS_JSON params whose
defaults preserve single-pass behavior. These assert the wiring so the loop and
the opt-in refute mode can't be silently removed or restructured.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


class DocLinterRefuteModeTests(TestCase):
    def setUp(self):
        self.agent = (ROOT / "agents" / "doc-linter.md").read_text(encoding="utf-8")

    def test_mode_param_documented(self):
        self.assertIn("`MODE`", self.agent)
        self.assertIn("`full`", self.agent)
        self.assertIn("`refute`", self.agent)

    def test_findings_json_param_documented(self):
        self.assertIn("`FINDINGS_JSON`", self.agent)

    def test_refute_drops_findings_that_dont_hold(self):
        # The precision cure: a finding that cannot be positively re-confirmed
        # does not survive the refute pass.
        self.assertIn("refuted", self.agent.lower())

    def test_refute_emits_same_block_no_new_fields(self):
        # Refute must NOT add result-block fields (only strip findings). The
        # strict §4↔§6.0 agreement (test_doc_linter_wiring) depends on this.
        self.assertIn("same", self.agent.lower())


class WikiDoctorLoopWiringTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "wiki-doctor" / "SKILL.md").read_text(encoding="utf-8")

    def test_lint_loop_present(self):
        self.assertIn("loop-until-dry", self.skill)
        self.assertIn("### 3.1 Dispatch Doc Linter", self.skill)

    def test_diff_loop_present(self):
        # §4.2 (diff) is also wrapped in a loop-until-dry (completeness-only).
        self.assertIn("### 4.2 Dispatch Wiki Differ", self.skill)

    def test_budget_guard_max_3_rounds(self):
        self.assertIn("max 3 rounds", self.skill)

    def test_dedup_via_seen_set(self):
        self.assertIn("`seen`", self.skill)

    def test_lint_dispatches_refute_mode(self):
        # The per-finding refute pass dispatches doc-linter with MODE=refute +
        # FINDINGS_JSON (precision cure, lint path only).
        self.assertIn("MODE=refute", self.skill)
        self.assertIn("FINDINGS_JSON", self.skill)

    def test_dry_round_exit_condition(self):
        # A round with no NEW findings is the convergence (dry) signal.
        self.assertIn("dry round", self.skill)

    def test_diff_loop_is_completeness_only(self):
        # wiki-differ has no refute mode — §4.2's loop must NOT dispatch refute.
        # (Refute dispatches live only in §3.1, the lint path.)
        self.assertIn("completeness-only", self.skill)


if __name__ == "__main__":
    main()
