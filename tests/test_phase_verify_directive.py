"""Wiring tests for the per-phase verify directive (``<!-- verify: ... -->``).

A migration track's intermediate phases have a goal of "compiles," not "tests
green" — the suite is expected red mid-migration. Forcing every phase through
the full test-suite gate flags those phases FAILED and churns fix-attempts
against a half-migrated codebase. The verify directive lets a phase heading
declare its own gate (``compile`` / ``test`` / ``start``); ``phase-checker``
reads it directly from ``plan.md``. These tests pin the plumbing:

- ``plan_parse._extract_verify`` parses the closed mode vocabulary and ignores
  stray ``verify`` inside an AC/TC comment.
- An unknown mode (typo) surfaces as a parse **warning**, not an error — the
  directive is advisory metadata (same posture as ``<!-- deps: -->``).
- ``to_plan_structure`` drops ``verify_modes`` — the directive is NOT persisted
  into ``track-state.json`` (it lives in ``plan.md`` prose, read directly by
  ``phase-checker``).
- ``phase-checker.md`` carries the compile branch (build command from
  dev-commands, ignore the test-runner verdict, no fix-and-retry).
- ``plan-format-contract.md`` documents the directive and its closed vocabulary.
- ``spec-planner.md`` teaches the planner to emit it for staged migrations.
"""
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest import TestCase, main

from scripts.track_state.plan_parse import (
    _extract_verify,
    parse_plan,
    to_plan_structure,
)

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "runtime" / "contracts" / "plan-format-contract.md"
PHASE_CHECKER = (ROOT / "agents" / "phase-checker.md").read_text(encoding="utf-8")
SPEC_PLANNER = (ROOT / "agents" / "spec-planner.md").read_text(encoding="utf-8")


class ExtractVerifyTests(TestCase):
    def test_single_compile_mode(self):
        modes, has, fails = _extract_verify("Migrate deps <!-- verify: compile -->")
        self.assertEqual(modes, ["compile"])
        self.assertTrue(has)
        self.assertEqual(fails, [])

    def test_comma_separated_modes(self):
        modes, has, fails = _extract_verify("Wire up <!-- verify: test, start -->")
        self.assertEqual(sorted(modes), ["start", "test"])
        self.assertTrue(has)
        self.assertEqual(fails, [])

    def test_unknown_mode_is_failure_not_silent(self):
        # A typo must surface, not vanish — phase-checker would otherwise sit on
        # the (safe) full gate with no signal the directive was ignored.
        modes, has, fails = _extract_verify("Phase <!-- verify: buil -->")
        self.assertEqual(modes, [])
        self.assertTrue(has)
        self.assertEqual(fails, ["buil"])

    def test_empty_directive_flagged(self):
        modes, has, fails = _extract_verify("Phase <!-- verify: -->")
        self.assertEqual(modes, [])
        self.assertTrue(has)
        self.assertEqual(fails, [])

    def test_stray_verify_in_ac_comment_ignored(self):
        # A "verify" inside an AC/TC comment must NOT trigger the directive.
        modes, has, fails = _extract_verify("task <!-- AC-1, verify-it -->")
        self.assertEqual(modes, [])
        self.assertFalse(has)
        self.assertEqual(fails, [])

    def test_no_directive(self):
        modes, has, fails = _extract_verify("Plain phase heading")
        self.assertEqual(modes, [])
        self.assertFalse(has)
        self.assertEqual(fails, [])


def _write_plan(text):
    f = NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    f.write(text)
    f.flush()
    return Path(f.name)


class ParsePlanIntegrationTests(TestCase):
    def test_compile_phase_inits_with_directive(self):
        plan = _write_plan(
            "# Implementation Plan: migration\n"
            "## Phase 1: Migrate deps <!-- verify: compile -->\n"
            "- [ ] [Migrate] bump spring-boot <!-- AC-1 -->\n"
            "- [ ] [Manual] verify boot\n"
        )
        parsed = parse_plan(plan)
        self.assertEqual(parsed["errors"], [])
        self.assertEqual(parsed["phases"][0]["verify_modes"], ["compile"])

    def test_unknown_mode_warns_not_errors(self):
        plan = _write_plan(
            "# Implementation Plan: migration\n"
            "## Phase 1: Build <!-- verify: buil -->\n"
            "- [ ] task <!-- AC-1 -->\n"
            "- [ ] [Manual] verify\n"
        )
        parsed = parse_plan(plan)
        # Init must NOT block on a directive typo (advisory posture).
        self.assertEqual(parsed["errors"], [])
        joined = " ".join(parsed["warnings"])
        self.assertIn("unrecognized verify mode 'buil'", joined)

    def test_absent_directive_is_clean(self):
        plan = _write_plan(
            "# Implementation Plan: feature\n"
            "## Phase 1: Feature\n"
            "- [ ] task <!-- AC-1 -->\n"
            "- [ ] [Manual] verify\n"
        )
        parsed = parse_plan(plan)
        self.assertEqual(parsed["errors"], [])
        self.assertEqual(parsed["phases"][0]["verify_modes"], [])
        self.assertFalse(parsed["phases"][0]["verify_has_comment"])


class NonPersistenceTests(TestCase):
    """The directive lives in plan.md prose; it must NOT reach track-state.json."""

    def test_to_plan_structure_drops_verify_modes(self):
        plan = _write_plan(
            "# Implementation Plan: migration\n"
            "## Phase 1: Migrate <!-- verify: compile -->\n"
            "- [ ] [Migrate] work <!-- AC-1 -->\n"
            "- [ ] [Manual] verify\n"
        )
        structure = to_plan_structure(parse_plan(plan))
        phase = structure["phases"][0]
        self.assertEqual(set(phase.keys()), {"name", "tasks"})
        self.assertNotIn("verify_modes", phase)


class AgentDocTests(TestCase):
    def test_phase_checker_has_compile_branch(self):
        # The load-bearing gate change: a compile-only phase runs the BUILD
        # command and IGNORES the test-runner verdict.
        self.assertIn("phase-verify directive branch", PHASE_CHECKER.lower())
        self.assertIn("verify: compile", PHASE_CHECKER)
        self.assertIn("dev-commands", PHASE_CHECKER)
        # It must explicitly tell the agent to disregard the red suite.
        self.assertIn("Ignore the `L1_VERIFY_STATUS`", PHASE_CHECKER)

    def test_phase_checker_report_carries_build_start(self):
        self.assertIn("BUILD:", PHASE_CHECKER)
        self.assertIn("START:", PHASE_CHECKER)

    def test_directive_precedence_documented(self):
        # The directive must take precedence over the migration-phase branch.
        self.assertIn("takes precedence over the migration-phase branch", PHASE_CHECKER)


class ContractDocTests(TestCase):
    def setUp(self):
        self.assertTrue(CONTRACT.exists(), "plan-format-contract.md must exist")
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_contract_documents_directive(self):
        self.assertIn("Phase Verify Directives", self.text)
        self.assertIn("<!-- verify: compile -->", self.text)

    def test_contract_lists_closed_vocabulary(self):
        for mode in ("compile", "test", "start"):
            self.assertIn(f"| `{mode}`", self.text)


class PlannerDocTests(TestCase):
    def test_planner_emits_directive_for_migrations(self):
        self.assertIn("verify: compile", SPEC_PLANNER)
        self.assertIn("verify: test,start", SPEC_PLANNER)


if __name__ == "__main__":
    main()
