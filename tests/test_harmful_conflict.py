"""Tests for ``verify_mode_profiles.harmful_conflict`` — the residual drift gate.

Once the planner omits verify directives by default (the resolver derives every
mode from the phase goal), the only authored directives left are ``adversarial``
and deliberate overrides. ``harmful_conflict`` answers the one question
``init-from-plan`` could not answer before an authored directive lands: is it
HARMFUL? Harmful = the resolver says this phase needs the BUILD gate
(``compile``/``none`` — the suite is expected red) but the authored directive
runs the SUITE (``test``/``start``/``adversarial``/default). On such a phase the
suite is red by design, so a suite gate fix-and-retries forever — the defect
``compile`` exists to prevent.

Load-bearing invariants:
- Harmful is directional: OVER-gating (resolver → full gate, you → compile) is
  SAFE and never harmful; only UNDER-gating a build/debt phase to the suite is.
- ``adversarial`` is additive but does NOT exempt a build-gated phase: authoring
  ``test,adversarial`` where the resolver wants ``compile`` is still harmful
  (``test`` gates the red suite); the fix is ``compile,adversarial``.
- Agreement (resolver and authored both build-gated, or both not) is not harmful.
- An unknown mode (typo) is reported as ``unknown_mode``; the plan parser warns
  on those separately, this field lets a caller tell the species apart.
"""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

from track_state import verify_mode_profiles as vmp  # noqa: E402


class HarmfulConflictTests(TestCase):
    """The pure classifier: derived (resolver) vs authored (directive)."""

    def test_suite_on_compile_phase_is_harmful(self):
        """``verify: test`` on a Migrate phase whose suite is expected red — the
        canonical harmful case (the original bug)."""
        r = vmp.harmful_conflict(goal="Migrate dependencies", explicit=["test"])
        self.assertTrue(r["harmful"])
        self.assertFalse(r["unknown_mode"])
        self.assertEqual(r["derived"], ["compile"])

    def test_suite_on_none_phase_is_harmful(self):
        """``verify: test`` on a debt-carrying none phase is equally harmful —
        the suite is expected red there too."""
        r = vmp.harmful_conflict(
            goal="Bump the spring-boot parent", explicit=["test"])
        self.assertTrue(r["harmful"])
        self.assertEqual(r["derived"], ["none"])

    def test_agreement_compile_is_not_harmful(self):
        """Resolver says compile, you say compile — redundant but correct."""
        r = vmp.harmful_conflict(
            goal="Migrate dependencies", explicit=["compile"])
        self.assertFalse(r["harmful"])

    def test_agreement_none_is_not_harmful(self):
        r = vmp.harmful_conflict(
            goal="Bump the spring-boot parent", explicit=["none"])
        self.assertFalse(r["harmful"])

    def test_over_gate_is_not_harmful(self):
        """Resolver says full gate (feature work), you say compile — gating MORE
        than needed degrades safely (compile ignores a red suite). Not harmful."""
        r = vmp.harmful_conflict(goal="Add login", explicit=["compile"])
        self.assertFalse(r["harmful"])
        self.assertEqual(r["derived"], [])

    def test_adversarial_on_feature_is_not_harmful(self):
        """``test,adversarial`` on a feature phase (derived = full gate) is
        additive — adversarial tightens the default suite gate. Not harmful."""
        r = vmp.harmful_conflict(
            goal="Harden authN", explicit=["test", "adversarial"])
        self.assertFalse(r["harmful"])

    def test_adversarial_does_not_exempt_build_phase(self):
        """``test,adversarial`` on a Migrate phase is STILL harmful: ``test``
        gates the red suite regardless of the ``adversarial`` rider. The operator
        who wants adversarial scrutiny on a build phase must keep the build floor:
        ``compile,adversarial``."""
        r = vmp.harmful_conflict(
            goal="Migrate to Spring Boot 3", explicit=["test", "adversarial"])
        self.assertTrue(r["harmful"])
        self.assertEqual(r["derived"], ["compile"])

    def test_compile_with_adversarial_on_build_phase_is_not_harmful(self):
        """The correct composition: build floor kept (compile) + adversarial."""
        r = vmp.harmful_conflict(
            goal="Migrate dependencies", explicit=["compile", "adversarial"])
        self.assertFalse(r["harmful"])

    def test_directive_less_is_not_harmful(self):
        """No authored directive (the omit-by-default norm) → resolver owns it."""
        r = vmp.harmful_conflict(goal="Migrate dependencies", explicit=None)
        self.assertFalse(r["harmful"])
        r = vmp.harmful_conflict(goal="Migrate dependencies", explicit=[])
        self.assertFalse(r["harmful"])


class HarmfulConflictUnknownModeTests(TestCase):
    """A typo (``verify: verify``) is unknown, distinct from harmful."""

    def test_typo_is_unknown_not_harmful_on_feature(self):
        r = vmp.harmful_conflict(goal="Add login", explicit=["verify"])
        self.assertTrue(r["unknown_mode"])
        self.assertFalse(r["harmful"])

    def test_known_modes_are_not_unknown(self):
        r = vmp.harmful_conflict(goal="Add login", explicit=["test", "start"])
        self.assertFalse(r["unknown_mode"])

    def test_empty_authored_is_not_unknown(self):
        r = vmp.harmful_conflict(goal="Add login", explicit=[])
        self.assertFalse(r["unknown_mode"])


class HarmfulConflictInitWarningTests(TestCase):
    """End-to-end: an authored under-gating directive surfaces a WARNING at
    ``init-from-plan`` (advisory, never blocks init)."""

    @classmethod
    def setUpClass(cls):
        from track_state import quality
        cls.quality = quality

    def _run_check(self, plan_body):
        """Write plan.md to a temp track dir, run init-from-plan --check,
        return the parsed ``out()`` payload."""
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "plan.md").write_text(plan_body)
            captured = {}
            with patch.object(self.quality, "out",
                              lambda obj: captured.__setitem__("r", obj)):
                self.quality.cmd_init_from_plan(
                    tmp, "demo", "feature", "demo", check=True)
            return captured.get("r", {})

    def test_harmful_directive_surfaces_warning(self):
        r = self._run_check(
            "# Implementation Plan: migrate\n"
            "## Phase 1: Migrate dependencies <!-- verify: test -->\n"
            "- [ ] [Migrate] bump the spring-boot parent <!-- AC-1 -->\n"
            "- [ ] [Manual] verify\n"
        )
        self.assertTrue(r.get("ok"))
        harmful = [w for w in r.get("warnings", []) if "under-gates" in w]
        self.assertEqual(len(harmful), 1, f"expected one harmful warning, got {harmful}")
        # The warning names the phase and the mode the resolver would derive.
        self.assertIn("Phase 1", harmful[0])
        self.assertIn("compile", harmful[0])

    def test_over_gate_directive_emits_no_harmful_warning(self):
        """``verify: compile`` on a feature phase (over-gate) is safe — no
        harmful warning (other parse warnings may be present, but none
        'under-gates')."""
        r = self._run_check(
            "# Implementation Plan: feature\n"
            "## Phase 1: Add login <!-- verify: compile -->\n"
            "- [ ] add the route <!-- AC-1 -->\n"
            "- [ ] [Manual] verify\n"
        )
        self.assertTrue(r.get("ok"))
        harmful = [w for w in r.get("warnings", []) if "under-gates" in w]
        self.assertEqual(harmful, [])

    def test_directive_less_plan_emits_no_harmful_warning(self):
        """The omit-by-default norm: a directive-less plan raises no harmful
        warning (the resolver owns every phase)."""
        r = self._run_check(
            "# Implementation Plan: migrate\n"
            "## Phase 1: Migrate dependencies\n"
            "- [ ] [Migrate] bump the spring-boot parent <!-- AC-1 -->\n"
            "- [ ] [Manual] verify\n"
            "## Phase 2: Bump the spring-boot parent\n"
            "- [ ] [Migrate] bump parent <!-- AC-2 -->\n"
            "- [ ] [Manual] verify\n"
        )
        self.assertTrue(r.get("ok"))
        harmful = [w for w in r.get("warnings", []) if "under-gates" in w]
        self.assertEqual(harmful, [])


if __name__ == "__main__":
    main()
