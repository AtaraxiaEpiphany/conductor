"""Wiring tests for ``resolve_phase_gate`` — the dispatch-side gate-plan
composition (#6).

After the verifier fan-out became shape-driven (#4), the checkpoint decision
consults three otherwise-scattered sources: the verifier set (workflow-shape
axis), the phase-verify directive (verify-mode axis, via
``plan_parse._extract_verify``), and gate-group membership (cross-phase
gate-groups axis, via ``helpers._phase_gate_group_membership``).

``resolve_phase_gate`` composes them into one return so the checkpoint branch
in ``cmd_dispatch_next`` reads ONE chokepoint instead of three lookups. These
tests pin the composition for the canonical cases — pure and fail-open to the
safe defaults on a missing/unreadable plan.md.
"""
import json
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import dispatch as d


def _track(plan_text, shape="default"):
    """A temp track dir with a plan.md and a minimal track-state.json."""
    td = tempfile.mkdtemp()
    Path(td, "plan.md").write_text(plan_text, encoding="utf-8")
    Path(td, "track-state.json").write_text(json.dumps({
        "track_id": "test_20260731", "workflow_shape": shape,
    }), encoding="utf-8")
    return td


class ResolvePhaseGateTests(TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        import shutil
        for td in self._dirs:
            shutil.rmtree(td, ignore_errors=True)

    def _track(self, plan_text, shape="default"):
        td = _track(plan_text, shape)
        self._dirs.append(td)
        return td

    def _state(self, shape="default"):
        return {"track_id": "test_20260731", "workflow_shape": shape}

    def test_default_shape_yields_standard_pair_no_directive_no_group(self):
        # The common case: a plain feature phase → standard verifier pair, no
        # directive, no gate group.
        td = self._track("# Plan\n\n## Phase 1: Build\n- [x] T [abc1234]\n")
        gp = d.resolve_phase_gate(td, self._state(), 1)
        self.assertEqual(gp["verifiers"], ("ac-tracer", "test-runner"))
        self.assertEqual(gp["verify_modes"], [])
        self.assertEqual(gp["gate_group"], {"name": None, "is_terminal": False})

    def test_verify_compile_directive_is_parsed_and_routes_compile_runner(self):
        # A migration phase carrying the directive → verify_modes == ["compile"].
        # B3 (dynamic fan-out): a build-gated phase fans out compile-runner
        # (the build verdict), NOT test-runner (the suite verdict). The
        # directive now DOES change the fan-out — that's the whole point of the
        # per-phase substitution in verifiers_for.
        td = self._track(
            "# Plan\n\n## Phase 1: Migrate <!-- verify: compile -->\n"
            "- [ ] [Migrate] bump <!-- AC-1 -->\n")
        gp = d.resolve_phase_gate(td, self._state(), 1)
        self.assertEqual(gp["verify_modes"], ["compile"])
        self.assertEqual(gp["verifiers"], ("ac-tracer", "compile-runner"))

    def test_verify_none_directive_routes_compile_runner(self):
        # A debt-carrying deps phase → fans out compile-runner so the `none`
        # mode's build floor has a BUILD_VERIFY_STATUS to read (without it, the
        # floor degrades to NO_GATE: skipped and the phase passes on nothing).
        td = self._track(
            "# Plan\n\n## Phase 1: Bump parent <!-- verify: none -->\n"
            "- [ ] [Migrate] bump <!-- AC-1 -->\n"
            "## Phase 2: Wire up <!-- verify: test -->\n"
            "- [ ] [Migrate] wire <!-- AC-1 -->\n")
        gp = d.resolve_phase_gate(td, self._state(), 1)
        self.assertEqual(gp["verify_modes"], ["none"])
        self.assertEqual(gp["verifiers"], ("ac-tracer", "compile-runner"))
        # The closing phase (test) fans out the standard pair.
        gp2 = d.resolve_phase_gate(td, self._state(), 2)
        self.assertEqual(gp2["verify_modes"], ["test"])
        self.assertEqual(gp2["verifiers"], ("ac-tracer", "test-runner"))

    def test_verify_test_start_directive_preserves_order(self):
        # The terminal integration phase directive → test,start (suite-gated,
        # so the standard pair — no build substitution).
        td = self._track(
            "# Plan\n\n## Phase 2: Wire up <!-- verify: test,start -->\n"
            "- [ ] [Migrate] wire <!-- AC-1 -->\n")
        gp = d.resolve_phase_gate(td, self._state(), 2)
        self.assertEqual(gp["verify_modes"], ["test", "start"])
        self.assertEqual(gp["verifiers"], ("ac-tracer", "test-runner"))

    def test_gate_group_terminal_membership_resolved(self):
        # A phase that is the terminal member of a gate_group → membership.
        td = self._track(
            "# Plan\n\n"
            "## Phase 1: A <!-- gate_group: migration -->\n- [ ] [Migrate] a\n"
            "## Phase 2: B <!-- gate_group: migration -->\n- [ ] [Migrate] b\n")
        # Phase 2 is the terminal member.
        gp2 = d.resolve_phase_gate(td, self._state(), 2)
        self.assertEqual(gp2["gate_group"]["name"], "migration")
        self.assertTrue(gp2["gate_group"]["is_terminal"])
        # Phase 1 is a non-terminal member.
        gp1 = d.resolve_phase_gate(td, self._state(), 1)
        self.assertEqual(gp1["gate_group"]["name"], "migration")
        self.assertFalse(gp1["gate_group"]["is_terminal"])

    def test_missing_plan_md_fails_open(self):
        # No plan.md → fail-open to the safe defaults (no crash).
        td = tempfile.mkdtemp()
        self._dirs.append(td)
        gp = d.resolve_phase_gate(td, self._state(), 1)
        self.assertEqual(gp["verifiers"], ("ac-tracer", "test-runner"))
        self.assertEqual(gp["verify_modes"], [])
        self.assertEqual(gp["gate_group"], {"name": None, "is_terminal": False})

    def test_unknown_shape_fails_open_verifiers(self):
        # An unknown workflow_shape resolves to default → the standard pair.
        td = self._track("# Plan\n\n## Phase 1: Build\n- [x] T [abc1234]\n")
        gp = d.resolve_phase_gate(td, self._state(shape="typo-shape"), 1)
        self.assertEqual(gp["verifiers"], ("ac-tracer", "test-runner"))


class BuildVerifierWaveTests(TestCase):
    """``_build_verifier_wave`` threads the resolved verifiers through (avoids a
    double-resolve when the caller already has the gate plan)."""

    def setUp(self):
        self._dirs = []

    def tearDown(self):
        import shutil
        for td in self._dirs:
            shutil.rmtree(td, ignore_errors=True)

    def _track(self, plan_text, shape="default"):
        td = _track(plan_text, shape)
        self._dirs.append(td)
        return td

    def test_passing_verifiers_thread_through(self):
        state = {"track_id": "t", "workflow_shape": "default"}
        # Pass a custom verifier tuple — the wave should use it verbatim, NOT
        # re-resolve from the default shape's pair.
        wave = d._build_verifier_wave("/td", state, 2,
                                      verifiers=("ac-tracer",))
        self.assertEqual([m["name"] for m in wave], ["ac-tracer"])

    def test_default_resolution_when_verifiers_omitted(self):
        state = {"track_id": "t", "workflow_shape": "default"}
        wave = d._build_verifier_wave("/td", state, 2)
        self.assertEqual([m["name"] for m in wave], ["ac-tracer", "test-runner"])

    def test_none_branch_is_phase_aware_when_verifiers_omitted(self):
        # The step/Rail-B spine calls _build_verifier_wave WITHOUT verifiers=.
        # The None branch must be phase-aware (re-parse the directive) so a
        # build-gated phase fans out compile-runner — byte-for-byte parity with
        # the resolve_phase_gate path (cmd_dispatch_next).
        td = self._track(
            "# Plan\n\n## Phase 1: Bump parent <!-- verify: none -->\n"
            "- [ ] [Migrate] bump <!-- AC-1 -->\n"
            "## Phase 2: Wire up <!-- verify: test -->\n"
            "- [ ] [Migrate] wire <!-- AC-1 -->\n")
        state = {"track_id": "t", "workflow_shape": "default"}
        # Phase 1 (none) → compile-runner via the None branch.
        wave1 = d._build_verifier_wave(td, state, 1)
        self.assertEqual([m["name"] for m in wave1],
                         ["ac-tracer", "compile-runner"])
        # Phase 2 (test) → standard pair via the None branch.
        wave2 = d._build_verifier_wave(td, state, 2)
        self.assertEqual([m["name"] for m in wave2],
                         ["ac-tracer", "test-runner"])


if __name__ == "__main__":
    main()
