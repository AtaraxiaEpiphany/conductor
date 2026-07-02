"""Structural tests for the existing spec/plan guard in new-track §2.3.

Re-invoking new-track on a track dir that already has a `plan.md` (no resume
marker, or a genuine collision) used to fail or silently overwrite (issue #2).
§2.3 now validates the pre-existing plan in place via `init-from-plan --check`
and offers Reuse / Regenerate / Cancel.

These pin the guard's contract into the skill body so it can't be silently
removed or restructured.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


class NewTrackGuardWiringTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "new-track" / "SKILL.md").read_text(encoding="utf-8")

    def test_guard_section_present(self):
        self.assertIn("**Existing spec/plan guard", self.skill)

    def test_uses_check_mode(self):
        # --check writes nothing: it validates plan.md in place and prints the
        # derived structure. This is the "spec-plan-view validate" of issue #3.
        self.assertIn("track-state init-from-plan", self.skill)
        self.assertIn("--check", self.skill)

    def test_offers_reuse_regenerate_cancel(self):
        self.assertIn("Reuse existing plan", self.skill)
        self.assertIn("Regenerate", self.skill)
        self.assertIn("Cancel", self.skill)

    def test_reuse_skips_spec_planner(self):
        # Reuse must short-circuit straight to review, not re-dispatch spec-planner.
        self.assertIn("jump to §2.4 review", self.skill)

    def test_malformed_plan_announces_errors(self):
        # ok:false path: a broken plan.md surfaces its errors and is never reused.
        self.assertIn("Announce the reported `errors`", self.skill)
        self.assertIn("Never reuse a broken plan", self.skill)

    def test_resume_skips_guard(self):
        # When resuming via §0.5 with spec_planned done, the existing plan.md is
        # owned by the active run — the guard must not fire.
        self.assertIn("spec_planned", self.skill)


class NewTrackPlanRefuterWiringTests(TestCase):
    """Pin the §2.3b adversarial plan refuter contract.

    The subtle, load-bearing detail is the CLAIM framing: the refuter agent
    defaults to SUSTAINED-when-uncertain globally (pinned in test_refuter_wiring),
    so the per-domain conservative direction is selected by how the orchestrator
    frames the CLAIM. For a plan gate we want proceed-when-uncertain (don't
    hard-block the track on a hunch), so the CLAIM is "the plan is sound" and
    REFUTED (grounded evidence) triggers the regen — NOT SUSTAINED. A future edit
    that flips this mapping would invert the gate's semantics silently.
    """

    def setUp(self):
        self.skill = (ROOT / "skills" / "new-track" / "SKILL.md").read_text(encoding="utf-8")

    def test_refuter_section_present(self):
        self.assertIn("### 2.3b Adversarial Plan Refuter", self.skill)

    def test_dispatches_refuter_domain_plan(self):
        self.assertIn("conductor:refuter", self.skill)
        self.assertIn("DOMAIN=plan", self.skill)

    def test_niche_guard_excludes_deterministic_checks(self):
        # The refuter must not duplicate §2.3's deterministic lane — its value is
        # the semantic layer above those checks.
        self.assertIn("Niche guard", self.skill)
        self.assertIn("do not duplicate", self.skill.lower())

    def test_claim_framed_as_plan_sound(self):
        # The CLAIM is framed so SUSTAINED (default when uncertain) = proceed,
        # not block. This is the conservative direction for a plan gate.
        self.assertIn("semantically sound", self.skill)
        self.assertIn("proceed-when-uncertain", self.skill)

    def test_refuted_triggers_regen_sustained_proceeds(self):
        # The mapping: REFUTED (grounded defect) -> regen; SUSTAINED -> proceed.
        # Asserting both directions so a flip is caught.
        self.assertIn("STATUS: REFUTED", self.skill)
        self.assertIn("re-dispatch `conductor:spec-planner`", self.skill)
        self.assertIn("STATUS: SUSTAINED", self.skill)
        self.assertIn("proceed to §2.4", self.skill)

    def test_failure_is_non_blocking(self):
        # A refuter FAILURE must not hard-block the track — the plan stands.
        self.assertIn("STATUS: FAILURE", self.skill)
        self.assertIn("treat as SUSTAINED", self.skill)

    def test_second_refuted_is_non_blocking(self):
        # If the regen does not satisfy the refuter, the sustained challenge is
        # surfaced to the §2.4 reviewer + user, not a hard halt.
        self.assertIn("non-blocking", self.skill)


if __name__ == "__main__":
    main()
