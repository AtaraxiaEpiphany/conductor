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
    compute_ac_integrity, _ac_integrity_gate, _measured_tcs,
    _measured_tcs_with_locations, compute_ac_evidence_map,
    _attested_acs, compute_review_ac_evidence_map)
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


# spec with 2 ACs, each with one TC; FR/NFR for inventory counts. Requirements
# are EARS-compliant (mandatory ``shall``) so the "all green" fixture is green on
# every axis — the EARS WARN path is covered separately in EarsLintTests.
_SPEC_2AC = """\
# Specification: Demo
## Requirements
### Functional Requirements
- FR-1: The system shall do the thing.
### Non-Functional Requirements
- NFR-1: The system shall respond within 200 ms.
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
        self.assertIsNone(r["ac_integrity_reason"])  # ACs present → gate carries the signal
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
        # No spec.md at all → the "intentionally spec-less" N/A reason (clean).
        self.assertEqual(r["ac_integrity_reason"], "spec_missing")

    def test_spec_with_no_acs_yields_na_gate(self):
        spec = "# Specification: Demo\n## Requirements\n### Functional Requirements\n- FR-1: x\n"
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_integrity_gate"], "N/A")
        self.assertIsNone(r["ac_tc_coverage_rate"])
        self.assertEqual(r["fr_count"], 1)
        # spec.md exists but has no ## Acceptance Criteria → the weak-model
        # anchor-drift N/A reason (new-track §2.3 re-dispatches, not clean).
        self.assertEqual(r["ac_integrity_reason"], "no_acs")

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

    def test_missing_state_does_not_crash(self):
        # new-track §2.3 runs spec-integrity BEFORE §2.6 creates track-state.json.
        # A pre-execution track has no completed tasks ⇒ empty covered set ⇒
        # Rate 3 self-report is 0.0; the gate (Rate 1/2 only) still reflects
        # authoring quality. Must not raise FileNotFoundError.
        d = _track(_SPEC_2AC, _PLAN_2AC)  # NO state=
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_integrity_gate"], "PASS")  # both ACs traced + covered
        self.assertEqual(r["ac_tc_coverage_rate"], 100.0)
        self.assertEqual(r["ac_traceability_rate"], 100.0)
        self.assertEqual(r["ac_verification_rate"], 0.0)  # no completed tasks yet
        self.assertIn("ac_evidence", r)  # enrichment still emits


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
        # The additive per-AC evidence trace rides the CLI forwarding unchanged.
        self.assertIn("ac_evidence", r)


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

    # --- located map view (the ac_evidence substrate) ------------------------

    def test_measured_with_locations_records_test_and_fileline(self):
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_basic.py", "def test_TC_2_1_basic():\n    pass\n"))
        loc = _measured_tcs_with_locations(d)
        self.assertEqual(loc["TC-2.1"]["test"], "test_TC_2_1_basic")
        self.assertEqual(loc["TC-2.1"]["location"], "tests/test_basic.py:1")

    def test_locations_distinct_lines_in_multifn_file(self):
        # Two grounding fns in one file must each report their own line number.
        content = ("def test_TC_1_1_a():\n    pass\n\n"
                   "def test_TC_2_1_b():\n    pass\n")
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_a.py", content))
        loc = _measured_tcs_with_locations(d)
        self.assertEqual(loc["TC-1.1"]["location"], "tests/test_a.py:1")
        self.assertEqual(loc["TC-2.1"]["location"], "tests/test_a.py:4")
        self.assertEqual(loc["TC-1.1"]["test"], "test_TC_1_1_a")

    def test_bare_test_name_has_empty_suffix(self):
        # ``def test_TC_2_1()`` (no descriptor) still records the bare name.
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_a.py", "def test_TC_2_1():\n    pass\n"))
        loc = _measured_tcs_with_locations(d)
        self.assertEqual(loc["TC-2.1"]["test"], "test_TC_2_1")

    def test_map_view_strips_comments_and_skips_parts(self):
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_a.py", "# def test_TC_2_1_faked():\n"),
            ("htmlcov/test_TC_1_1_cov.py", "def test_TC_1_1_cov():\n    pass\n"))
        self.assertEqual(_measured_tcs_with_locations(d), {})

    def test_measured_tcs_set_equals_locations_keys(self):
        # The wrapper must preserve the exact set[str] contract (regression net).
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_a.py", "def test_TC_1_1_happy():\n    pass\n"),
            ("tests/test_b.py", "def test_TC_2_1_happy():\n    pass\n"))
        self.assertEqual(_measured_tcs(d), set(_measured_tcs_with_locations(d)))


class AcEvidenceMapTests(TestCase):
    """The per-AC evidence trace (completeness-critic substrate): each AC's TCs
    classified measured / claimed / missing. ``compute_ac_evidence_map`` is a pure
    function over its inputs (no FS); the integration path (``ac_evidence`` key)
    is covered here too."""

    def test_evidence_map_classifies_all_three_statuses(self):
        acs = ["AC-1", "AC-2"]
        tc_to_ac = {"TC-1.1": "AC-1", "TC-1.2": "AC-1", "TC-2.1": "AC-2"}
        covered = {"TC-1.1"}                       # claimed only
        measured_map = {"TC-1.2": {                # grounded by a real test
            "test": "test_TC_1_2_x", "location": "t.py:3"}}
        emap = compute_ac_evidence_map(acs, tc_to_ac, covered, measured_map)
        by_ac = {e["ac"]: {t["id"]: t for t in e["tcs"]} for e in emap}
        # TC-1.2 measured (wins over nothing), TC-1.1 claimed, TC-2.1 missing.
        self.assertEqual(by_ac["AC-1"]["TC-1.2"]["status"], "measured")
        self.assertEqual(by_ac["AC-1"]["TC-1.1"]["status"], "claimed")
        self.assertEqual(by_ac["AC-2"]["TC-2.1"]["status"], "missing")
        # measured carries test + location; claimed/missing carry neither.
        self.assertEqual(by_ac["AC-1"]["TC-1.2"]["test"], "test_TC_1_2_x")
        self.assertEqual(by_ac["AC-1"]["TC-1.2"]["location"], "t.py:3")
        self.assertNotIn("test", by_ac["AC-1"]["TC-1.1"])
        self.assertNotIn("location", by_ac["AC-2"]["TC-2.1"])

    def test_evidence_map_measured_wins_over_claimed(self):
        # A TC both claimed (covered) AND measured reports measured.
        acs = ["AC-1"]
        tc_to_ac = {"TC-1.1": "AC-1"}
        covered = {"TC-1.1"}
        measured_map = {"TC-1.1": {"test": "test_TC_1_1_a", "location": "t.py:1"}}
        emap = compute_ac_evidence_map(acs, tc_to_ac, covered, measured_map)
        self.assertEqual(emap[0]["tcs"][0]["status"], "measured")

    def test_evidence_map_orphan_ac_has_empty_tcs(self):
        # An AC with no TCs (orphan) carries an empty tcs list, not a missing entry.
        acs = ["AC-1", "AC-2"]
        tc_to_ac = {"TC-1.1": "AC-1"}              # AC-2 has no TC
        emap = compute_ac_evidence_map(acs, tc_to_ac, set(), {})
        by_ac = {e["ac"]: e["tcs"] for e in emap}
        self.assertEqual(by_ac["AC-2"], [])

    def test_ac_integrity_includes_ac_evidence_enrichment(self):
        d = self._track_with_tests(_SPEC_2AC, _PLAN_2AC,
            ("tests/test_a.py", "def test_TC_1_1_happy():\n    pass\n"))
        r = compute_ac_integrity(d)
        self.assertIn("ac_evidence", r)
        self.assertEqual({e["ac"] for e in r["ac_evidence"]}, {"AC-1", "AC-2"})
        by_ac = {e["ac"]: {t["id"]: t["status"] for t in e["tcs"]}
                 for e in r["ac_evidence"]}
        # State fixture claims TC-1.1 + TC-2.1; only TC-1.1 is grounded by a test.
        self.assertEqual(by_ac["AC-1"]["TC-1.1"], "measured")
        self.assertEqual(by_ac["AC-2"]["TC-2.1"], "claimed")

    def test_ac_evidence_empty_when_no_spec(self):
        d = _track()  # empty dir → degraded _empty() result
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_evidence"], [])

    def _track_with_tests(self, spec, plan, *test_files):
        """Shared fixture builder (mirrors MeasuredRateTests._track_with_tests)."""
        d = _track(spec, plan, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1 TC-2.1"}},
            {"name": "[Manual] verify P1", "status": "pending"},
        ]))
        for relpath, content in test_files:
            p = Path(d, relpath)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return d


class EarsLintTests(TestCase):
    """The EARS advisory lint: every FR/NFR must carry a mandatory EARS response
    verb (English ``shall`` or a localized equivalent — see ``_EARS_SHALL``) and
    must avoid negation (``shall not``). ACs are criteria, not requirements, so
    they are never linted. WARN-only — ``ears_gate`` never blocks; it rides the
    same advisory channel as ``ac_integrity_gate``."""

    def test_pass_when_all_requirements_have_shall(self):
        spec = ("# Specification: Demo\n## Requirements\n"
                "### Functional Requirements\n"
                "- FR-1: When a user logs in, the system shall issue a token.\n"
                "### Non-Functional Requirements\n"
                "- NFR-1: The system shall respond within 200 ms.\n"
                "## Acceptance Criteria\n- AC-1: crit\n"
                "## Test Scenarios\n| ID | AC Ref | S | O |\n| -- | ------ | - | - |\n"
                "| TC-1.1 | AC-1 | x | y |\n")
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1"}},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ears_gate"], "PASS")
        self.assertEqual(r["ears_warnings"], [])

    def test_warn_lists_ids_missing_shall(self):
        # FR-1 is EARS; FR-2 and NFR-1 lack 'shall' → flagged, in document order.
        spec = ("# Specification: Demo\n## Requirements\n"
                "### Functional Requirements\n"
                "- FR-1: The system shall do the thing.\n"
                "- FR-2: fast login\n"
                "### Non-Functional Requirements\n"
                "- NFR-1: snappy\n"
                "## Acceptance Criteria\n- AC-1: crit\n- AC-2: crit two\n"
                "## Test Scenarios\n| ID | AC Ref | S | O |\n| -- | ------ | - | - |\n"
                "| TC-1.1 | AC-1 | x | y |\n| TC-2.1 | AC-2 | x | y |\n")
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1"}},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual([w["id"] for w in r["ears_warnings"]], ["FR-2", "NFR-1"])
        self.assertTrue(r["ears_gate"].startswith("WARN"))
        self.assertIn("FR-2", r["ears_gate"])
        self.assertIn("NFR-1", r["ears_gate"])
        # AC integrity is unaffected — EARS is a separate axis.
        self.assertEqual(r["ac_integrity_gate"], "PASS")

    def test_negation_anti_pattern_flagged(self):
        spec = ("# Specification: Demo\n## Requirements\n"
                "### Functional Requirements\n"
                "- FR-1: The system shall not crash on bad input.\n"
                "## Acceptance Criteria\n- AC-1: crit\n"
                "## Test Scenarios\n| ID | AC Ref | S | O |\n| -- | ------ | - | - |\n"
                "| TC-1.1 | AC-1 | x | y |\n")
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1"}},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual([w["id"] for w in r["ears_warnings"]], ["FR-1"])
        self.assertIn("negation", r["ears_warnings"][0]["reason"])

    def test_acceptance_criteria_are_not_linted(self):
        # AC-1 has no 'shall' and that is fine — ACs are criteria, not EARS reqs.
        spec = ("# Specification: Demo\n## Requirements\n"
                "### Functional Requirements\n"
                "- FR-1: The system shall do the thing.\n"
                "## Acceptance Criteria\n- AC-1: login completes within 1s\n"
                "## Test Scenarios\n| ID | AC Ref | S | O |\n| -- | ------ | - | - |\n"
                "| TC-1.1 | AC-1 | x | y |\n")
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1"}},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ears_gate"], "PASS")
        self.assertEqual(r["ears_warnings"], [])

    def test_ears_computed_even_when_spec_has_no_acs(self):
        # No ACs → ac_integrity_gate is N/A, but EARS still lints the FRs present.
        spec = ("# Specification: Demo\n## Requirements\n"
                "### Functional Requirements\n- FR-1: fast login\n")
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_integrity_gate"], "N/A")
        self.assertTrue(r["ears_gate"].startswith("WARN"))
        self.assertEqual([w["id"] for w in r["ears_warnings"]], ["FR-1"])

    def test_ears_gate_na_when_no_spec(self):
        d = _track(plan=_PLAN_2AC, state=_state([
            {"name": "a", "status": "pending"},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ears_gate"], "N/A")
        self.assertEqual(r["ears_warnings"], [])

    def test_ears_keys_always_present(self):
        d = _track()  # completely empty dir
        r = compute_ac_integrity(d)
        self.assertIn("ears_warnings", r)
        self.assertIn("ears_gate", r)
        self.assertEqual(r["ears_gate"], "N/A")

    def test_helper_never_raises_on_garbage(self):
        d = _track()  # empty dir
        from scripts.track_state.spec_integrity import _ears_gate
        self.assertEqual(_ears_gate(d), "N/A")

    # --- multilingual EARS: the mandatory verb need not be English 'shall' ----

    def test_multilingual_latin_verbs_accepted(self):
        # The canonical obligation modal in FR/DE/ES lints clean — Unicode
        # case-folded, \b-anchored. Localized requirements must not false-WARN.
        spec = ("# Specification: Demo\n## Requirements\n"
                "### Functional Requirements\n"
                "- FR-1: Le système doit authentifier l'utilisateur.\n"
                "- FR-2: Das System MUSS Passwörter mit bcrypt hashen.\n"
                "- FR-3: El sistema deberá responder en menos de 200 ms.\n"
                "## Acceptance Criteria\n- AC-1: crit\n"
                "## Test Scenarios\n| ID | AC Ref | S | O |\n| -- | ------ | - | - |\n"
                "| TC-1.1 | AC-1 | x | y |\n")
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1"}},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ears_gate"], "PASS")
        self.assertEqual(r["ears_warnings"], [])

    def test_multilingual_cjk_verbs_accepted(self):
        # ZH/JA verbs match WITHOUT \b — no boundary fires between ideographs,
        # so '系统应当响应' and 'トークンを発行すること' must lint clean.
        spec = ("# Specification: Demo\n## Requirements\n"
                "### Functional Requirements\n"
                "- FR-1: 系统应当在一秒内颁发令牌。\n"
                "- FR-2: システムはトークンを発行すること。\n"
                "## Acceptance Criteria\n- AC-1: crit\n"
                "## Test Scenarios\n| ID | AC Ref | S | O |\n| -- | ------ | - | - |\n"
                "| TC-1.1 | AC-1 | x | y |\n")
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1"}},
        ]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ears_gate"], "PASS")
        self.assertEqual(r["ears_warnings"], [])

    def test_non_english_requirement_without_verb_still_warns(self):
        # Multilingual is not "accept anything" — a ZH requirement lacking any
        # obligation modal (应/应当/必须) still WARNs, same as 'fast login'.
        spec = ("# Specification: Demo\n## Requirements\n"
                "### Functional Requirements\n"
                "- FR-1: 快速登录。\n"
                "## Acceptance Criteria\n- AC-1: crit\n"
                "## Test Scenarios\n| ID | AC Ref | S | O |\n| -- | ------ | - | - |\n"
                "| TC-1.1 | AC-1 | x | y |\n")
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1"}},
        ]))
        r = compute_ac_integrity(d)
        self.assertTrue(r["ears_gate"].startswith("WARN"))
        self.assertEqual([w["id"] for w in r["ears_warnings"]], ["FR-1"])
        self.assertIn("mandatory", r["ears_warnings"][0]["reason"])

    def test_env_var_extends_ears_verbs(self):
        # CONDUCTOR_EARS_VERBS appends project-specific verbs at regex-build time.
        # Without it 'zall' is unknown → WARN; with it the requirement lints PASS.
        import os
        from unittest import mock
        from scripts.track_state import spec_integrity as si
        spec = ("# Specification: Demo\n## Requirements\n"
                "### Functional Requirements\n"
                "- FR-1: The system zall the tokens within 1 second.\n"
                "## Acceptance Criteria\n- AC-1: crit\n"
                "## Test Scenarios\n| ID | AC Ref | S | O |\n| -- | ------ | - | - |\n"
                "| TC-1.1 | AC-1 | x | y |\n")
        d = _track(spec, _PLAN_2AC, _state([
            {"name": "a", "status": "completed", "evidence": {"tc_coverage": "TC-1.1"}},
        ]))
        # Baseline (module-level regex built at import, env unset): WARNs.
        os.environ.pop("CONDUCTOR_EARS_VERBS", None)
        r0 = compute_ac_integrity(d)
        self.assertTrue(r0["ears_gate"].startswith("WARN"))
        # With env set + rebuilt regex patched in: PASS.
        with mock.patch.dict(os.environ, {"CONDUCTOR_EARS_VERBS": "zall"}):
            with mock.patch.object(si, "_EARS_SHALL",
                                   si._build_ears_shall_regex()):
                r1 = compute_ac_integrity(d)
        self.assertEqual(r1["ears_gate"], "PASS")
        self.assertEqual(r1["ears_warnings"], [])


# --- Track B2: review-grounded (deliverable) integrity -------------------------
# A non-code shape grounds ACs by artifact anchors + review attestations, not
# test_TC_* functions. Rate 1 = AC→anchor coverage; Rate 3 = AC→attestation
# (positive verdict in evidence.review_attestations). Rate 2 (plan traceability)
# is shared. Grounding is shape-driven when state exists; spec-inferred (##
# Artifact Anchors present) when state is absent (planning time). All test-grounded
# fixtures above (no workflow_shape, no anchors) stay on the test branch unchanged.

_SPEC_REVIEW = """\
# Specification: Design Doc
## Requirements
### Functional Requirements
- FR-1: The system shall document the API.
## Acceptance Criteria
- AC-1: API design doc covers all endpoints
- AC-2: Migration runbook includes rollback
## Artifact Anchors
| AC Ref | Artifact | Location |
| ------ | -------- | -------- |
| AC-1   | API design doc | docs/api.md |
| AC-2   | migration runbook | docs/run.md |
"""

_PLAN_REVIEW = """\
# Implementation Plan: Design Doc
## Phase 1: Author
- [ ] Task: write API doc <!-- AC-1 -->
- [ ] Task: write runbook <!-- AC-2 -->
"""


def _review_state(tasks):
    return {"track_id": "t", "workflow_shape": "deliverable", "phases": [
        {"name": "Phase 1", "status": "in_progress", "tasks": tasks},
    ]}


def _attest(ac, verdict="pass", by="spec-reviewer", anchor="docs/x.md"):
    """A completed task whose evidence carries a review attestation for ``ac``."""
    return {"name": f"deliver {ac}", "status": "completed",
            "evidence": {"review_attestations": {
                ac: {"anchor": anchor, "attested_by": by, "verdict": verdict}}}}


class ReviewGroundingTests(TestCase):
    def test_review_spec_passes_when_anchored_traced_attested(self):
        d = _track(_SPEC_REVIEW, _PLAN_REVIEW, _review_state([
            _attest("AC-1"), _attest("AC-2")]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_grounding"], "review")
        self.assertEqual(r["ac_tc_coverage_rate"], 100.0)   # every AC anchored
        self.assertEqual(r["ac_traceability_rate"], 100.0)  # every AC traced
        self.assertEqual(r["ac_verification_rate"], 100.0)  # every AC attested
        self.assertIsNone(r["ac_verification_measured_rate"])  # no measured twin
        self.assertEqual(r["anchor_count"], 2)
        self.assertEqual(r["tc_count"], 0)  # literal TC count (review spec has none)
        self.assertEqual(r["orphan_acs"], [])
        self.assertEqual(r["ac_integrity_gate"], "PASS")

    def test_review_orphan_ac_without_anchor_fails_gate(self):
        spec = _SPEC_REVIEW.replace(
            "- AC-2: Migration runbook includes rollback\n",
            "- AC-2: Migration runbook includes rollback\n- AC-3: glossary\n")
        # Plan traces AC-3 so only the anchor rate fails (mirrors the test-branch
        # orphan test, but on the anchor axis).
        plan = _PLAN_REVIEW.replace("<!-- AC-2 -->", "<!-- AC-2, AC-3 -->")
        d = _track(spec, plan, _review_state([_attest("AC-1"), _attest("AC-2")]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["orphan_acs"], ["AC-3"])
        self.assertEqual(r["ac_tc_coverage_rate"], 66.7)
        self.assertIn("without an artifact anchor", r["ac_integrity_gate"])

    def test_review_untraced_ac_fails_traceability(self):
        # Plan traces AC-1 only; AC-2 untraced (grounding-agnostic Rate 2).
        plan = "# Implementation Plan\n## Phase 1\n- [ ] Task: a <!-- AC-1 -->\n"
        d = _track(_SPEC_REVIEW, plan, _review_state([_attest("AC-1")]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["untraced_acs"], ["AC-2"])
        self.assertEqual(r["ac_traceability_rate"], 50.0)
        self.assertIn("untraced in plan", r["ac_integrity_gate"])

    def test_review_attestation_rate_not_gated(self):
        # Only AC-1 attested → verification 50%, but the gate is Rate 1/2 only.
        d = _track(_SPEC_REVIEW, _PLAN_REVIEW, _review_state([_attest("AC-1")]))
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_verification_rate"], 50.0)
        self.assertEqual(r["unverified_acs"], ["AC-2"])
        self.assertEqual(r["ac_integrity_gate"], "PASS")

    def test_review_ac_evidence_shape(self):
        d = _track(_SPEC_REVIEW, _PLAN_REVIEW, _review_state([_attest("AC-1")]))
        r = compute_ac_integrity(d)
        by_ac = {e["ac"]: e for e in r["ac_evidence"]}
        self.assertEqual(by_ac["AC-1"]["status"], "attested")
        self.assertEqual(by_ac["AC-1"]["anchor"], "API design doc")
        self.assertEqual(by_ac["AC-1"]["location"], "docs/api.md")
        self.assertEqual(by_ac["AC-2"]["status"], "unattested")  # anchor, no attest
        self.assertEqual(by_ac["AC-2"]["anchor"], "migration runbook")

    def test_review_orphan_ac_evidence_status(self):
        # An AC with no anchor carries status "orphan" in the evidence trace.
        spec = _SPEC_REVIEW.replace(
            "- AC-2: Migration runbook includes rollback\n",
            "- AC-2: Migration runbook includes rollback\n- AC-3: glossary\n")
        d = _track(spec, _PLAN_REVIEW, _review_state([_attest("AC-1")]))
        r = compute_ac_integrity(d)
        by_ac = {e["ac"]: e for e in r["ac_evidence"]}
        self.assertEqual(by_ac["AC-3"]["status"], "orphan")
        self.assertEqual(by_ac["AC-3"]["anchor"], "")

    def test_review_grounding_spec_inferred_when_no_state(self):
        # Planning time (new-track §2.3): no track-state.json yet. A spec with
        # ## Artifact Anchors is recognized as review-grounded even with no state
        # to read the shape from — the chicken-and-egg that would otherwise force
        # a deliverable spec through the test branch (and fail it for "no TCs").
        d = _track(_SPEC_REVIEW, _PLAN_REVIEW)  # NO state=
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_grounding"], "review")
        self.assertEqual(r["ac_tc_coverage_rate"], 100.0)
        self.assertEqual(r["ac_integrity_gate"], "PASS")

    def test_shape_driven_grounding_wins_over_spec_when_state_exists(self):
        # State declares workflow_shape=default (test) but the spec carries
        # anchors. Shape-driven resolution wins → test branch → the anchors are
        # irrelevant and (with no TCs) every AC is a TC-orphan. Pins that the
        # declaration is authoritative, not the spec structure, once state exists.
        state = {"track_id": "t", "workflow_shape": "default", "phases": [
            {"name": "P1", "status": "in_progress",
             "tasks": [{"name": "x", "status": "pending"}]}]}
        d = _track(_SPEC_REVIEW, _PLAN_REVIEW, state)
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_grounding"], "test")
        # Test branch: AC-1/AC-2 have no TCs → both orphan.
        self.assertEqual(r["orphan_acs"], ["AC-1", "AC-2"])
        self.assertIn("without a TC", r["ac_integrity_gate"])

    def test_no_state_no_anchors_is_test_grounding(self):
        # The fail-open default: no state + a test-grounded spec (no anchors) →
        # test branch, byte-identical to a legacy track.
        d = _track(_SPEC_2AC, _PLAN_2AC)  # NO state=
        r = compute_ac_integrity(d)
        self.assertEqual(r["ac_grounding"], "test")
        self.assertEqual(r["ac_integrity_gate"], "PASS")

    def test_attested_acs_reads_positive_verdicts_from_evidence(self):
        # _attested_acs (the review twin of _covered_tcs): only positive verdicts
        # count; a "fail" verdict does not attest the AC.
        state = _review_state([
            {"name": "a", "status": "completed", "evidence": {"review_attestations": {
                "AC-1": {"verdict": "pass"}, "AC-2": {"verdict": "fail"}}}},
            {"name": "b", "status": "pending", "evidence": {"review_attestations": {
                "AC-2": {"verdict": "pass"}}}},  # pending task — ignored
        ])
        self.assertEqual(_attested_acs(state), {"AC-1"})

    def test_review_ac_evidence_map_pure_function(self):
        anchors = [{"ac": "AC-1", "artifact": "doc", "location": "d.md"}]
        emap = compute_review_ac_evidence_map(["AC-1", "AC-2"], anchors, {"AC-1"})
        by_ac = {e["ac"]: e for e in emap}
        self.assertEqual(by_ac["AC-1"]["status"], "attested")
        self.assertEqual(by_ac["AC-2"]["status"], "orphan")  # no anchor declared


if __name__ == "__main__":
    main()
