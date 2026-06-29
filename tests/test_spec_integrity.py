"""Tests for spec_integrity: AC coverage rates + advisory gate.

The measurable guarantee over Acceptance Criteria — three rates (AC→TC coverage,
AC→plan traceability, AC verification) cross-checked across spec.md, plan.md,
and track-state.json evidence, plus a WARN-only gate. Degrades to None/"N/A"
when spec.md is absent or has no ACs.
"""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.spec_integrity import (
    compute_ac_integrity, _ac_integrity_gate, _measured_tcs)
from scripts.track_state.misc import cmd_spec_integrity


def _track(spec=None, plan=None, state=None):
    """Build a temp track dir with optional spec.md/plan.md/track-state.json."""
    d = tempfile.mkdtemp()
    if spec is not None:
        Path(d, "spec.md").write_text(spec)
    if plan is not None:
        Path(d, "plan.md").write_text(plan)
    if state is not None:
        save(d, state)
    return d


def _state(tasks_p1):
    return {"track_id": "t", "phases": [
        {"name": "Phase 1", "status": "in_progress", "tasks": tasks_p1},
    ]}


# spec with 2 ACs, each with one TC; FR/NFR for inventory counts.
_SPEC_2AC = """\
# Specification: Demo
## Requirements
### Functional Requirements
- FR-1: do thing
### Non-Functional Requirements
- NFR-1: fast
## Acceptance Criteria
- AC-1: crit one
- AC-2: crit two
## Test Scenarios
| ID | AC Ref | S | O |
| -- | ------ | - | - |
| TC-1.1 | AC-1 | x | y |
| TC-2.1 | AC-2 | x | y |
"""

_PLAN_2AC = """\
# Implementation Plan: Demo
## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 -->
- [ ] Task: b <!-- AC-2, TC-2.1 -->
- [ ] [Manual] Task: verify P1
"""


class AllGreenTests(TestCase):
    def test_pass_when_every_ac_has_tc_plan_and_verification(self):
        d = _track(_SPEC_2AC, _PLAN_2AC, _state([
            {"name": "a", "status": "completed",
             "evidence": {"tc_coverage": "TC-1.1", "coverage_pct": 90}},
            {"name": "b", "status": "completed",
             "evidence": {"tc_coverage": "TC-2.1", "coverage_pct": 90}},
            {"name": "[Manual] verify P1", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_tc_coverage_rate"], 100.0)
        self.assertEqual(r["ac_traceability_rate"], 100.0)
        self.assertEqual(r["ac_verification_rate"], 100.0)
        self.assertEqual(r["fr_count"], 1)
        self.assertEqual(r["nfr_count"], 1)
        self.assertEqual(r["ac_integrity_gate"], "PASS")
        self.assertEqual(r["orphan_acs"], [])
        self.assertEqual(r["dangling_ac_refs"], [])


class RateOneTcCoverageTests(TestCase):
    def test_orphan_ac_without_tc_lowers_rate_and_fails_gate(self):
        spec = _SPEC_2AC + ""  # AC-1, AC-2 each have a TC
        # Add an AC-3 with no TC → orphan
        spec = spec.replace("- AC-2: crit two\n",
                            "- AC-2: crit two\n- AC-3: crit three\n")
        plan = _PLAN_2AC.replace("<!-- AC-2, TC-2.1 -->",
                                 "<!-- AC-2, TC-2.1 -->")  # also trace AC-3 so only TC rate fails
        plan = plan.replace("- [ ] Task: b <!-- AC-2, TC-2.1 -->",
                            "- [ ] Task: b <!-- AC-2, TC-2.1, AC-3 -->")
        d = _track(spec, plan, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1 TC-2.1"}},
            {"name": "b", "status": "pending"},
            {"name": "[Manual] verify P1", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["orphan_acs"], ["AC-3"])
        self.assertEqual(r["ac_tc_coverage_rate"], 66.7)
        self.assertIn("without a TC", r["ac_integrity_gate"])


class RateTwoTraceabilityTests(TestCase):
    def test_untraced_ac_lowers_traceability_and_fails_gate(self):
        # Plan references AC-1 only; AC-2 is untraced.
        plan = """\
# Implementation Plan: Demo
## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 -->
- [ ] [Manual] Task: verify P1
"""
        d = _track(_SPEC_2AC, plan, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1 TC-2.1"}},
            {"name": "[Manual] verify P1", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["untraced_acs"], ["AC-2"])
        self.assertEqual(r["ac_traceability_rate"], 50.0)
        self.assertIn("untraced in plan", r["ac_integrity_gate"])

    def test_dangling_plan_ref_flagged(self):
        # Plan references AC-9, which is not in the spec.
        plan = _PLAN_2AC.replace("<!-- AC-2, TC-2.1 -->",
                                 "<!-- AC-2, TC-2.1 -->\n- [ ] Task: ghost <!-- AC-9 -->\n")
        d = _track(_SPEC_2AC, plan, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1 TC-2.1"}},
            {"name": "ghost", "status": "pending"},
            {"name": "[Manual] verify P1", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["dangling_ac_refs"], ["AC-9"])
        self.assertIn("dangling", r["ac_integrity_gate"])


class RateThreeVerificationTests(TestCase):
    def test_partial_and_unverified_classification(self):
        # AC-1: TC-1.1 covered → verified. AC-2: TC-2.1 NOT covered → unverified.
        d = _track(_SPEC_2AC, _PLAN_2AC, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1"}},
            {"name": "b", "status": "pending"},  # not completed → its TC not covered
            {"name": "[Manual] verify P1", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_verification_rate"], 50.0)
        # AC-1 fully verified (TC-1.1 covered); AC-2 unverified (TC-2.1 never covered).
        self.assertEqual(r["unverified_acs"], ["AC-2"])
        self.assertEqual(r["partial_acs"], [])
        # Verification is NOT part of the gate, so gate still PASS here.
        self.assertEqual(r["ac_integrity_gate"], "PASS")

    def test_multi_tc_ac_partial_when_some_covered(self):
        spec = """\
# Specification: Demo
## Acceptance Criteria
- AC-1: crit one
## Test Scenarios
| ID | AC Ref | S | O |
| -- | ------ | - | - |
| TC-1.1 | AC-1 | x | y |
| TC-1.2 | AC-1 | x | y |
"""
        d = _track(spec, _PLAN_2AC, _state([
            # only TC-1.1 covered → AC-1 partial (not all its TCs)
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1"}},
            {"name": "[Manual] verify P1", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["partial_acs"], ["AC-1"])
        self.assertEqual(r["ac_verification_rate"], 0.0)


class DegradationTests(TestCase):
    def test_no_spec_yields_none_rates_and_na_gate(self):
        d = _track(plan=_PLAN_2AC, state=_state([
            {"name": "a", "status": "pending"},
            {"name": "[Manual] verify P1", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertIsNone(r["ac_tc_coverage_rate"])
        self.assertIsNone(r["ac_traceability_rate"])
        self.assertIsNone(r["ac_verification_rate"])
        self.assertEqual(r["ac_integrity_gate"], "N/A")

    def test_spec_with_no_acs_yields_na_gate(self):
        spec = "# Specification: Demo\n## Requirements\n### Functional Requirements\n- FR-1: x\n"
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_integrity_gate"], "N/A")
        self.assertIsNone(r["ac_tc_coverage_rate"])
        self.assertEqual(r["fr_count"], 1)

    def test_no_plan_yields_traceability_none_but_gate_can_pass(self):
        # No plan.md → traceability unmeasured (None); with TC coverage 100% and
        # no dangling refs, the gate is PASS.
        d = _track(_SPEC_2AC, plan=None, state=_state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1 TC-2.1"}},
        ]))
        r = compute_ac_integrity(d)
        self.assertIsNone(r["ac_traceability_rate"])
        self.assertEqual(r["ac_tc_coverage_rate"], 100.0)
        self.assertEqual(r["ac_integrity_gate"], "PASS")

    def test_gate_helper_never_raises_on_garbage(self):
        d = _track()  # completely empty dir
        self.assertEqual(_ac_integrity_gate(d), "N/A")


class CmdSpecIntegrityTests(TestCase):
    def test_emits_single_json_with_gate(self):
        d = _track(_SPEC_2AC, _PLAN_2AC, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1 TC-2.1"}},
            {"name": "b", "status": "completed", "evidence": {}},
            {"name": "[Manual] verify P1", "status": "pending"},
        ]))
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            cmd_spec_integrity(d)
            r = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old
        self.assertEqual(r["ac_integrity_gate"], "PASS")
        self.assertEqual(r["ac_count"], 2)


class MeasuredRateTests(TestCase):
    """The measured Rate-3 twin: AC verification grounded in REAL
    ``def test_TC_{n}_{m}`` functions (not self-report evidence). Composes with
    the self-report rate — the gap between the two is the #3 signal."""

    def _track_with_tests(self, spec, plan, *test_files):
        """Build a track dir and write each (relpath, content) test file."""
        d = _track(spec, plan, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1 TC-2.1"}},
            {"name": "[Manual] verify P1", "status": "pending"},
        ]))
        for relpath, content in test_files:
            p = Path(d, relpath)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return d

    def test_measured_tcs_extracts_tc_from_named_function(self):
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_basic.py", "def test_TC_2_1_basic():\n    pass\n"))
        self.assertEqual(_measured_tcs(d), {"TC-2.1"})

    def test_measured_rate_100_when_both_acs_have_named_tests(self):
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_a.py", "def test_TC_1_1_happy():\n    pass\n"),
            ("tests/test_b.py", "def test_TC_2_1_happy():\n    pass\n"))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_verification_measured_rate"], 100.0)

    def test_measured_rate_50_when_only_one_ac_grounded(self):
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_a.py", "def test_TC_1_1_happy():\n    pass\n"))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_verification_measured_rate"], 50.0)

    def test_measured_rate_none_when_convention_unadopted(self):
        # No test_TC_* functions anywhere → unmeasured (None), not 0%.
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_a.py", "def test_happy():\n    pass\n"))
        r = compute_ac_integrity(d)
        self.assertIsNone(r["ac_verification_measured_rate"])

    def test_multi_digit_tc_not_truncated(self):
        # TC-2.10 must NOT be captured as TC-2.1 (lookahead boundary guard).
        spec = ("# Specification\n## Acceptance Criteria\n- AC-2: crit\n"
                "## Test Scenarios\n| ID | AC Ref | S | O |\n| -- | ------ | - | - |\n"
                "| TC-2.10 | AC-2 | x | y |\n")
        plan = "# Implementation Plan\n## Phase 1: Build\n- [ ] Task: a <!-- AC-2, TC-2.10 -->\n"
        d = self._track_with_tests(spec, plan,
            ("tests/test_a.py", "def test_TC_2_10_many():\n    pass\n"))
        self.assertIn("TC-2.10", _measured_tcs(d))
        self.assertNotIn("TC-2.1", _measured_tcs(d))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_verification_measured_rate"], 100.0)

    def test_commented_out_test_function_not_captured(self):
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_a.py", "# def test_TC_2_1_faked():\n#     pass\n"))
        self.assertEqual(_measured_tcs(d), set())

    def test_measured_diverges_from_self_report(self):
        # The point of #3: agent CLAIMS both TCs (self-report 100%) but wrote no
        # named tests (measured None) — self-report inflation caught.
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_a.py", "def test_something():\n    assert True\n"))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_verification_rate"], 100.0)  # self-report: claimed
        self.assertIsNone(r["ac_verification_measured_rate"])  # measured: ungrounded

    def test_skip_parts_excludes_htmlcov_and_site_packages(self):
        # Generated/vendored subtrees must not leak def test_TC_… strings.
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("htmlcov/test_TC_2_1_cov.py", "def test_TC_2_1_cov():\n    pass\n"),
            ("foo/site-packages/pkg/test_TC_1_1_pkg.py",
             "def test_TC_1_1_pkg():\n    pass\n"))
        self.assertEqual(_measured_tcs(d), set())


if __name__ == "__main__":
    main()
