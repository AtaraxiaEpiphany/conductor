"""Wiring tests for the ``adversarial`` phase-verify mode.

``adversarial`` is the verify pattern most recommended across the agent-
engineering literature (a skeptic prompted to *refute*, default-verdict = FAILED
on uncertainty). The phase-checker loop is already mode-agnostic and
``MODE_VOCAB()`` reads live from the registry, so adding the mode was one JSON
row in ``verify-mode-profiles.json`` — zero Python, zero agent-prose edits. These
tests pin that the row is well-formed and that its load-bearing fields flow:

- ``MODE_VOCAB`` includes ``adversarial`` (parser/validation surface);
- ``runs_for``/``fix_policy_for``/``report_field`` resolve to the declared values
  (``report_field`` via ``_profile``, the resolution path consumers use);
- ``protocol_for`` carries the refuting-stance prose (the load-bearing payload —
  phase-checker emits it verbatim);
- ``adversarial`` is NOT auto-proposed by ``derive_verify_modes`` — it is a
  deliberate stance declared via the directive, never a phase-goal *type*
  (mirrors how ``anchor`` is opt-in). A spurious proposal would be the foot-gun.
"""
import sys
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

from track_state import verify_mode_profiles as vmp  # noqa: E402


class AdversarialModeShapeTests(TestCase):
    """The registry row is present and carries every field the loop reads."""

    def test_adversarial_in_vocab(self):
        self.assertIn("adversarial", vmp.MODE_VOCAB())

    def test_runs_resolve_to_test_suite(self):
        # adversarial's substrate is the suite — it re-runs it looking for
        # flakiness/order-dependence the fleet's single pass missed.
        self.assertEqual(vmp.runs_for("adversarial"), ["test-suite"])

    def test_fix_policy_is_fix_and_retry(self):
        # A refutation that finds a real defect hands the phase back for a fix,
        # bounded by max_fix_attempts like the default test gate.
        self.assertEqual(vmp.fix_policy_for("adversarial"), "fix-and-retry")

    def test_report_field_is_adversarial(self):
        # The CHECKPOINT RESULT line this mode emits. Read via _profile (the
        # accessor the on-subagent-start registry-doc summary uses inline) —
        # matches how every consumer resolves report_field.
        self.assertEqual(vmp._profile("adversarial").get("report_field"), "ADVERSARIAL")

    def test_when_to_use_is_non_empty(self):
        self.assertTrue(vmp.when_to_use_for("adversarial").strip())


class AdversarialProtocolTests(TestCase):
    """The protocol prose is the load-bearing payload — phase-checker emits it
    verbatim, so pin the refuting-stance literals a regression would drop."""

    def test_protocol_carries_refuting_stance(self):
        proto = vmp.protocol_for("adversarial")
        # The asymmetric burden: the checker's job is to REFUTE, not confirm.
        self.assertTrue(
            "REFUTE" in proto or "refute" in proto,
            "adversarial protocol must name the refuting stance",
        )

    def test_protocol_defaults_to_failed_on_uncertainty(self):
        proto = vmp.protocol_for("adversarial").lower()
        # The single most load-bearing clause: uncertainty => FAILED.
        self.assertIn("default to failed", proto)
        self.assertIn("uncertainty", proto)

    def test_protocol_records_verdict_lines(self):
        proto = vmp.protocol_for("adversarial")
        # The two report_field lines the checker must emit (mirrors how every
        # other mode's protocol names its ADVERSARIAL: passed/failed record).
        self.assertIn("ADVERSARIAL: passed", proto)
        self.assertIn("ADVERSARIAL: failed", proto)

    def test_protocol_tightens_not_loosens(self):
        # The guardrail clause: adversarial never relaxes a sibling mode's gate.
        self.assertIn("tightens", vmp.protocol_for("adversarial").lower())

    def test_protocol_composes_with_other_modes(self):
        # adversarial,test and adversarial,anchor are the documented composes.
        proto = vmp.protocol_for("adversarial").lower()
        self.assertIn("composes", proto)


class AdversarialNotAutoDerivedTests(TestCase):
    """``adversarial`` is a deliberate opt-in via the directive — it must NEVER
    be auto-proposed by ``derive_verify_modes`` (a phase-goal *type* is one
    thing; a refuting *stance* is another). Mirrors how ``anchor`` is opt-in."""

    def test_feature_work_does_not_propose_adversarial(self):
        self.assertNotIn("adversarial", vmp.derive_verify_modes("Add the payments feature"))

    def test_high_stakes_goal_does_not_auto_propose_adversarial(self):
        # Even a goal that reads as high-stakes (security/auth) does not get
        # adversarial auto-added — the author opts in deliberately so the stance
        # is never applied by accident.
        self.assertNotIn(
            "adversarial",
            vmp.derive_verify_modes("Harden the authentication flow"),
        )

    def test_plain_refactor_does_not_propose_adversarial(self):
        self.assertNotIn("adversarial", vmp.derive_verify_modes("Refactor for readability"))


if __name__ == "__main__":
    main()
