"""Structural tests for the wiki-doctor loop-until-dry + per-finding refute (#6).

`wiki-doctor lint` runs a convergent loop — lint → dedup vs `seen` → refute the
NEW findings → re-lint — stopping on a dry round (loop-until-dry), with each
round's findings adversarially refuted (doc-linter MODE=refute) to strip false
positives. `wiki-doctor diff` runs the same shape — diff → dedup → per-category
refute → re-diff — so its loop is precision AND completeness (wiki-differ gained
a `refute` mode mirroring doc-linter §2.5), and its report is read from
REPORT_PATH (the trimmed stdout block no longer carries the report body).
`doc-linter` and `wiki-differ` gain optional MODE / FINDINGS_JSON params whose
defaults preserve single-pass behavior. These assert the wiring so the loops and
the opt-in refute modes can't be silently removed or restructured.
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


class WikiDifferRefuteModeTests(TestCase):
    def setUp(self):
        self.agent = (ROOT / "agents" / "wiki-differ.md").read_text(encoding="utf-8")

    def test_mode_param_documented(self):
        self.assertIn("`MODE`", self.agent)
        self.assertIn("`full`", self.agent)
        self.assertIn("`refute`", self.agent)

    def test_findings_json_param_documented(self):
        self.assertIn("`FINDINGS_JSON`", self.agent)

    def test_refute_drops_findings_that_dont_hold(self):
        # The precision cure: a drift item that cannot be positively re-confirmed
        # does not survive the refute pass.
        self.assertIn("refuted", self.agent.lower())

    def test_write_tool_present_and_scoped(self):
        # G3: wiki-differ gained Write (to emit its report file) — but it is
        # firewall-scoped to REPORT_PATH ONLY, not a general write capability.
        self.assertIn("Write", self.agent.split("tools:", 1)[1].split("\n", 1)[0])
        self.assertIn("REPORT_PATH", self.agent)
        self.assertIn("`REPORT_PATH` ONLY", self.agent)

    def test_report_lives_at_report_path_not_in_block(self):
        # The stdout block is trimmed (counts + REPORT_PATH pointer); the markdown
        # report body is written to REPORT_PATH, NOT emitted inside the block.
        # This is the context-bloat fix.
        self.assertIn("NOT emitted inside the block", self.agent)
        self.assertIn("markdown report file at `REPORT_PATH`", self.agent)

    def test_only_stale_moved_uncovered_refutable(self):
        # THIN (a coverage gradation) and STRUCTURAL (unverifiable by definition)
        # are NOT refutable — only STALE/MOVED/UNCOVERED are.
        self.assertIn("Only `STALE`, `MOVED`, and `UNCOVERED` are refutable", self.agent)


class WikiDoctorLoopWiringTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "wiki-doctor" / "SKILL.md").read_text(encoding="utf-8")

    def test_lint_loop_present(self):
        self.assertIn("loop-until-dry", self.skill)
        self.assertIn("### 3.1 Dispatch Doc Linter", self.skill)

    def test_diff_loop_present(self):
        # §4.2 (diff) is also wrapped in a loop-until-dry + per-category refute.
        self.assertIn("### 4.2 Dispatch Wiki Differ", self.skill)

    def test_budget_guard_max_3_rounds(self):
        self.assertIn("max 3 rounds", self.skill)

    def test_dedup_via_seen_set(self):
        self.assertIn("`seen`", self.skill)

    def test_lint_dispatches_refute_mode(self):
        # The refute pass dispatches doc-linter with MODE=refute + FINDINGS_JSON
        # (precision cure; §4.2 diff dispatches the same shape on wiki-differ).
        self.assertIn("MODE=refute", self.skill)
        self.assertIn("FINDINGS_JSON", self.skill)

    def test_refute_is_per_field_fan_out(self):
        # §3.1 refutes each non-empty field in its OWN narrow dispatch, all in one
        # message (parallel fan-out), each via a single-field JSON. Guards against
        # silent reversion to one fat refute across all fields — the per-field
        # split is the context/precision win and the parallelism win.
        self.assertIn("per-field", self.skill.lower())
        self.assertIn("ALL in ONE message", self.skill)
        self.assertIn("single-field", self.skill)
        # No agent change: doc-linter's FINDINGS_JSON contract must be cited as
        # accepting the single-field subset (so the fan-out stays rail-A prose).
        self.assertIn("no agent change", self.skill.lower())

    def test_dry_round_exit_condition(self):
        # A round with no NEW findings is the convergence (dry) signal.
        self.assertIn("dry round", self.skill)

    def test_diff_loop_has_refute_fan_out(self):
        # D-ii: wiki-differ gained a refute mode, so §4.2's loop is now precision
        # AND completeness — it dispatches a per-category refute fan-out (not the
        # old completeness-only re-dispatch). Guards against silent reversion to
        # the no-refute diff loop.
        self.assertIn("precision AND completeness", self.skill)
        self.assertIn("per-category", self.skill.lower())

    def test_diff_refute_is_per_category_fan_out(self):
        # §4.2 refutes each refutable category (STALE/MOVED/UNCOVERED) in its OWN
        # narrow dispatch, all in one message (parallel fan-out), each via a
        # single-category JSON — mirroring §3.1's per-field fan-out. THIN and
        # STRUCTURAL are documented as non-refutable.
        self.assertIn("single-category", self.skill)
        self.assertIn("ALL in ONE message", self.skill)
        self.assertIn("wiki-diff-findings-<CAT>.json", self.skill)
        self.assertIn("no agent change", self.skill.lower())

    def test_diff_report_read_from_report_path(self):
        # G3: wiki-differ's stdout block is trimmed (counts + REPORT_PATH); the
        # markdown report lives at REPORT_PATH, which §4.3 reads. Guards against
        # reverting to the block-body report (the context-bloat regression).
        self.assertIn("REPORT_PATH", self.skill)
        self.assertIn("wiki-diff-report.md", self.skill)
        self.assertIn("read from `REPORT_PATH`", self.skill)


if __name__ == "__main__":
    main()
