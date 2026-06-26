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


if __name__ == "__main__":
    main()
