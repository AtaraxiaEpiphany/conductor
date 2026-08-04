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
- ``spec-planner.md`` teaches the planner to OMIT the directive by default (the
  resolver derives the mode from the phase goal) and to hand-author one ONLY for
  ``adversarial`` — the one mode a goal cannot signal.
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
    def test_planner_omits_directive_by_default(self):
        """Under omit-by-default, the planner writes phase GOALS and OMITS the
        verify directive — ``init-from-plan`` resolves the mode from the goal.
        The worked migration example is directive-less (the resolver derives
        compile / test,start / none from the goals); the ONLY hand-authored
        directive in the example is ``adversarial`` (the one mode a goal cannot
        signal). This inverts the old "emit a directive on every migration
        heading" guidance, which authored wrong modes the resolver then had to
        honor verbatim (explicit wins) — the original bug."""
        # The prescriptive omit-by-default lead.
        self.assertIn("OMIT", SPEC_PLANNER)
        # The worked migration example is directive-less: the headings appear
        # with NO trailing ``<!-- verify:`` comment.
        self.assertIn("## Phase 1: Migrate dependencies\n", SPEC_PLANNER)
        self.assertIn("## Phase 3: Bump the spring-boot parent\n", SPEC_PLANNER)
        # The resolver-derives message ties each goal to its derived mode.
        self.assertIn("compile", SPEC_PLANNER)
        self.assertIn("test,start", SPEC_PLANNER)
        # The only hand-authored directive in the example is adversarial.
        self.assertIn("verify: test,adversarial", SPEC_PLANNER)
        # And the drift warning an authored under-gate would trip is documented.
        self.assertIn("under-gates", SPEC_PLANNER)


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
        # Option A (approved): the planner has NO Bash, so it does NOT call the
        # resolver — it authors the phase goal and MAY omit the directive; init-
        # from-plan resolves any directive-less heading at load by the contract
        # precedence. The planner must document THIS (not a re-encoded 4-step
        # ladder, which is the drift liability the collapse removed).
        self.assertIn("init-from-plan", SPEC_PLANNER)
        # The contract precedence must be stated (explicit > goal > tag > gate).
        self.assertIn("explicit > goal-derived > tag-derived > full gate",
                      SPEC_PLANNER)
        # The planner controls the ONE input the resolver keys on: the phase goal.
        self.assertIn("name the intent", SPEC_PLANNER.lower())
        self.assertIn("phase goal", SPEC_PLANNER.lower())
        # A verify: none outcome is reachable from the goal classifier (debt) —
        # the planner must acknowledge it can be produced, not pretend only
        # compile/test/start exist.
        self.assertIn("none", SPEC_PLANNER.lower())

    def test_planner_documents_none_must_be_closed(self):
        # A verify: none phase is debt-carrying and must be followed by a closing
        # compile/test/start phase — the planner must say so (and the contract
        # must enforce it, see PlanParseNoneClosureTests).
        self.assertIn("debt-carrying", SPEC_PLANNER.lower())


class InjectMissingDirectivesTests(TestCase):
    """``inject_missing_directives``: an authored directive is preserved verbatim
    (explicit wins); a directive-less heading gets the resolver's mode written in
    (or nothing, when the resolver derives the full gate). The spec-planner has no
    Bash, so this injector — not the agent — owns directive resolution at init."""

    BODY = (
        "# Implementation Plan: demo\n"
        # Phase 1: authored compile directive → preserved, NOT re-resolved.
        "## Phase 1: Migrate dependencies <!-- verify: compile -->\n"
        "- [ ] [Migrate] bump parent <!-- AC-1 -->\n"
        "- [ ] [Manual] verify\n"
        # Phase 2: directive-less feature goal → resolver derives [] (full gate)
        # → the injector writes nothing (absence IS the full-gate signal).
        "## Phase 2: Add the payments feature\n"
        "- [ ] add the route <!-- AC-2 -->\n"
        "- [ ] [Manual] verify\n"
        # Phase 3: directive-less debt goal → resolver derives ["none"] → injected.
        "## Phase 3: Bump the spring-boot parent\n"
        "- [ ] [Migrate] bump the parent <!-- AC-3 -->\n"
        "- [ ] [Manual] verify\n"
    )

    def _inject(self):
        from scripts.track_state.plan_parse import parse_plan, inject_missing_directives
        f = NamedTemporaryFile("w", suffix=".md", delete=False)
        f.write(self.BODY)
        f.close()
        parsed = parse_plan(f.name)
        injected = inject_missing_directives(f.name, parsed)
        text = Path(f.name).read_text(encoding="utf-8")
        Path(f.name).unlink()
        return injected, text

    def test_authored_directive_preserved_verbatim(self):
        injected, text = self._inject()
        # The authored compile directive is untouched (explicit wins).
        self.assertIn("## Phase 1: Migrate dependencies <!-- verify: compile -->\n", text)
        # And Phase 1 is NOT in the injection list (it was skipped, not resolved).
        self.assertFalse(any(i["phase"] == 1 for i in injected))

    def test_directive_less_debt_phase_gets_resolver_mode(self):
        injected, text = self._inject()
        # Phase 3 → resolver derives none; the directive is written into the heading.
        self.assertIn("## Phase 3: Bump the spring-boot parent <!-- verify: none -->\n", text)
        match = [i for i in injected if i["phase"] == 3]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["modes"], ["none"])

    def test_directive_less_full_gate_phase_writes_nothing(self):
        injected, text = self._inject()
        # Phase 2 → resolver derives [] (full gate) → no directive written; the
        # heading is unchanged and Phase 2 is absent from the injection list.
        self.assertIn("## Phase 2: Add the payments feature\n", text)
        self.assertFalse(any(i["phase"] == 2 for i in injected))


if __name__ == "__main__":
    main()
