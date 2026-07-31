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
from scripts.track_state.verify_mode_profiles import MODE_VOCAB, protocol_for

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

    def test_anchor_mode(self):
        # The Goodhart counter-anchor mode — the frozen-subset gate.
        modes, has, fails = _extract_verify("Refactor <!-- verify: anchor -->")
        self.assertEqual(modes, ["anchor"])
        self.assertTrue(has)
        self.assertEqual(fails, [])

    def test_anchor_composes_with_test(self):
        # ``test,anchor`` gates on suite AND frozen subset.
        modes, has, fails = _extract_verify("Phase <!-- verify: test, anchor -->")
        self.assertEqual(sorted(modes), ["anchor", "test"])
        self.assertTrue(has)
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


class PlanParseNoneClosureTests(TestCase):
    """``validate_verify_none_closure`` (wired into ``parse_plan``): a
    debt-carrying ``verify: none`` phase must be closed by a later
    compile/test/start phase, or the debt it stages is never exercised.

    Advisory (warnings, never errors) — same posture as gate-groups/deps. The
    validator is directive-only: it cannot check the closing phase fixes the
    *same* debt (operator responsibility); it only checks a closing gate exists."""

    def _parse(self, *phase_headings):
        body = "# Implementation Plan: m\n"
        for h in phase_headings:
            body += f"{h}\n- [ ] [Migrate] work <!-- AC-1 -->\n- [ ] [Manual] v\n"
        return parse_plan(_write_plan(body))

    def test_terminal_none_warns(self):
        # A none phase as the last phase → none_unclosed_terminal.
        parsed = self._parse("## Phase 1: Bump parent <!-- verify: none -->")
        self.assertEqual(parsed["errors"], [])
        joined = " ".join(parsed["warnings"])
        self.assertIn("none_unclosed_terminal", joined)
        self.assertIn("verify: none closure", joined)

    def test_all_none_run_warns_each(self):
        # A run of none phases, none closed → run warns (earlier) + terminal
        # warns (last).
        parsed = self._parse(
            "## Phase 1: Bump parent <!-- verify: none -->",
            "## Phase 2: Rename <!-- verify: none -->",
        )
        self.assertEqual(parsed["errors"], [])
        joined = " ".join(parsed["warnings"])
        self.assertIn("none_unclosed_run", joined)
        self.assertIn("none_unclosed_terminal", joined)

    def test_closed_by_compile_is_silent(self):
        parsed = self._parse(
            "## Phase 1: Bump parent <!-- verify: none -->",
            "## Phase 2: Fix consumers <!-- verify: compile -->",
        )
        joined = " ".join(parsed["warnings"])
        self.assertNotIn("verify: none closure", joined)

    def test_closed_by_test_start_is_silent(self):
        parsed = self._parse(
            "## Phase 1: Bump parent <!-- verify: none -->",
            "## Phase 2: Boot <!-- verify: test, start -->",
        )
        joined = " ".join(parsed["warnings"])
        self.assertNotIn("verify: none closure", joined)

    def test_closed_across_non_verify_gap_is_silent(self):
        # The closing gate may sit beyond an intermediate non-verify (feature)
        # phase — only that SOME later phase carries a closing mode matters.
        parsed = self._parse(
            "## Phase 1: Bump parent <!-- verify: none -->",
            "## Phase 2: Unrelated feature",
            "## Phase 3: Boot <!-- verify: start -->",
        )
        joined = " ".join(parsed["warnings"])
        self.assertNotIn("verify: none closure", joined)


class AgentDocTests(TestCase):
    def test_phase_checker_has_directive_loop_referencing_registry(self):
        # The per-mode behavior moved OUT of the agent into the registry. The
        # agent must carry the mode-agnostic directive loop and point at the
        # registry as the single source — NOT inline compile/start/anchor prose.
        self.assertIn("phase-verify directive branch", PHASE_CHECKER.lower())
        self.assertIn("Phase-verify directive loop", PHASE_CHECKER)
        self.assertIn("verify-mode-profiles.json", PHASE_CHECKER)
        self.assertIn("verify_mode_profiles.py", PHASE_CHECKER)

    def test_phase_checker_report_carries_build_start(self):
        # The report-field lines (BUILD:/START:/ANCHOR:) survive in the §8.0
        # result block + the loop's report_field mapping.
        self.assertIn("BUILD:", PHASE_CHECKER)
        self.assertIn("START:", PHASE_CHECKER)
        self.assertIn("ANCHOR:", PHASE_CHECKER)

    def test_compile_protocol_lives_in_registry_not_agent(self):
        # The load-bearing gate change: a compile-only phase runs the BUILD
        # command and IGNORES the test-runner verdict. That prose is now in the
        # registry, not inline in the agent.
        p = protocol_for("compile")
        self.assertIn("Ignore the `L1_VERIFY_STATUS`", p)
        self.assertIn("BUILD: passed", p)
        self.assertIn("dev-commands", p)
        # The # compile header-drift fix: the protocol must describe the build
        # command as the trailing `# compile` comment (NOT a heading).
        self.assertIn("trailing `# compile` comment", p)

    def test_start_protocol_lives_in_registry(self):
        p = protocol_for("start")
        self.assertIn("boot smoke", p)
        self.assertIn("START: passed", p)

    def test_anchor_protocol_lives_in_registry_not_agent(self):
        # The Goodhart counter-anchor gate: verify: anchor runs the frozen
        # subset and gates on its measured pass/drift rate. That prose is now in
        # the registry, not inline in the agent.
        p = protocol_for("anchor")
        self.assertIn("anchor-status", p)
        # Gates on the measured pass rate (the antagonistic pair to
        # coverage_pct), not self-report.
        self.assertIn("frozen_anchor_pass_rate", p)
        self.assertIn("frozen_anchor_drift_rate", p)
        self.assertIn("ANCHOR: passed", p)
        # No frozen anchor = no-op, not a failure (graceful degradation).
        self.assertIn("no frozen anchor", p)

    def test_directive_precedence_documented(self):
        # The directive must take precedence over the migration-phase branch.
        self.assertIn("takes precedence over the migration-phase branch", PHASE_CHECKER)

    def test_agent_has_no_inline_per_mode_prose(self):
        # The dedup invariant: the per-mode protocol prose must NOT live inline
        # in the agent — it lives in the registry. If any of these appear in
        # phase-checker.md, the lift was incomplete and we've reintroduced drift.
        for moved in (
            "Ignore the `L1_VERIFY_STATUS`",
            "frozen_anchor_pass_rate",
            "anchor-status",
            "boot smoke check",
        ):
            self.assertNotIn(
                moved, PHASE_CHECKER,
                f"{moved!r} should live in the registry, not phase-checker.md",
            )


class ContractDocTests(TestCase):
    def setUp(self):
        self.assertTrue(CONTRACT.exists(), "plan-format-contract.md must exist")
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_contract_documents_directive(self):
        self.assertIn("Phase Verify Directives", self.text)
        self.assertIn("<!-- verify: compile -->", self.text)

    def test_contract_does_not_enumerate_modes_as_table(self):
        # The collapse: the mode vocabulary lives in the resolved registry
        # (rendered by `track-state registry-doc`), NOT a hand-maintained table
        # in the contract (the drift liability `check-contract-registry-sync`
        # polices). A mode may appear as a grammar/directive example (e.g.
        # `<!-- verify: compile -->`), but never as a table row's first cell.
        self.assertIn("track-state registry-doc", self.text)
        for mode in MODE_VOCAB():
            for ln in self.text.splitlines():
                stripped = ln.strip()
                if stripped.startswith("|"):
                    first_cell = stripped[1:].split("|", 1)[0].strip().strip("`*")
                    self.assertNotEqual(
                        first_cell, mode,
                        f"mode {mode!r} must not appear as a contract table row "
                        f"(registry-sourced, not hand-maintained)",
                    )


class PlannerDocTests(TestCase):
    def test_planner_emits_directive_for_migrations(self):
        self.assertIn("verify: compile", SPEC_PLANNER)
        self.assertIn("verify: test,start", SPEC_PLANNER)


class AuthoringResolutionTests(TestCase):
    """The directive's authoring-time default is resolved by precedence
    (explicit > goal-derived derive_verify_modes > tag-derived default_verify >
    full gate). The goal text is checked BEFORE the tag union because the goal
    is more discriminating — `[Migrate].default_verify = compile` collapses a
    pure deps-bump (won't compile) and a final integration (boots) into one
    value, while the goal can reach `none` (debt-carrying) and `test,start`
    (final) respectively. The tag-derived `compile` survives as the fallback
    only for a bare goal (e.g. "Migrate dependencies") that says nothing finer.

    The planner is pure prose, so these pin the two REDUCER outputs the
    procedure composes for the canonical migration plan — the procedure glues
    them, and the contract documents the precedence."""

    def test_tag_derived_default_for_migrate_phase(self):
        # A phase of [Migrate] tasks → tag-derived default = compile (the
        # intermediate migration phases of the canonical example).
        from scripts.track_state.verify_mode_profiles import default_verify_for_phase
        self.assertEqual(default_verify_for_phase(["Migrate"]), ["compile"])

    def test_goal_derived_default_for_boot_phase(self):
        # The terminal integration phase's goal "wire up and boot" → goal-derived
        # default = test,start (derive_verify_modes already covers this; restated
        # here as the OTHER half of the canonical example's resolution).
        from scripts.track_state.verify_mode_profiles import derive_verify_modes
        self.assertEqual(derive_verify_modes("Wire up and boot the app"),
                         ["test", "start"])

    def test_goal_derived_none_for_debt_carry_phase(self):
        # The deps-bump phase's goal "bump the spring-boot parent" → goal-derived
        # default = none (debt-carrying: the phase deliberately won't compile
        # until a later phase fixes consumers). This is the case that the
        # goal-before-tag precedence exists to reach — a tag-only resolution
        # would wrongly give it compile.
        from scripts.track_state.verify_mode_profiles import derive_verify_modes
        self.assertEqual(derive_verify_modes("Bump the spring-boot parent"), ["none"])

    def test_contract_documents_default_source_precedence(self):
        # The contract must single-source the precedence the planner follows, in
        # the goal-before-tag order.
        text = CONTRACT.read_text(encoding="utf-8")
        self.assertIn("Default source", text)
        self.assertIn("Tag-derived default", text)
        self.assertIn("Goal-derived default", text)
        self.assertIn("Why goal before tag", text)
        self.assertIn("Generator proposes", text)
        # The goal-derived step must list `none` as a reachable outcome.
        goal_step = text.split("Goal-derived default", 1)[1]
        self.assertIn("none", goal_step.lower())

    def test_planner_documents_resolution_procedure(self):
        # The planner must carry the 4-step procedure, not just the old
        # hand-authored "emit verify: compile on migrations" heuristic.
        self.assertIn("resolve", SPEC_PLANNER.lower())
        self.assertIn("Tag-derived default", SPEC_PLANNER)
        self.assertIn("Goal-derived default", SPEC_PLANNER)
        # The goal-derived step must list `none` as a reachable outcome.
        goal_step = SPEC_PLANNER.split("Goal-derived default", 1)[1]
        self.assertIn("none", goal_step.lower())

    def test_planner_documents_none_must_be_closed(self):
        # A verify: none phase is debt-carrying and must be followed by a closing
        # compile/test/start phase — the planner must say so (and the contract
        # must enforce it, see PlanParseNoneClosureTests).
        self.assertIn("debt-carrying", SPEC_PLANNER.lower())


if __name__ == "__main__":
    main()
