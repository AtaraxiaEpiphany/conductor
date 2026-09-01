"""Structural tests for the existing spec/plan guard in new-track §2.3.

Re-invoking new-track on a track dir that already has a `plan.md` (no resume
marker, or a genuine collision) used to fail or silently overwrite (issue #2).
§2.3 now validates the pre-existing plan in place via `init-from-plan --check`
and offers Reuse / Regenerate / Cancel.

These pin the guard's contract into the skill body so it can't be silently
removed or restructured.
"""
from io import StringIO
from contextlib import redirect_stdout
import json
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import dispatch

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

    Rail A paste-verbatim (mirror of test_skip_refute_wiring, the D3 precedent):
    the refuter prompt is now ASSEMBLED IN CODE (`cmd_plan_refute_prompt`,
    exposed as `track-state plan-refute-prompt`) — the skill pastes the returned
    `prompt` field verbatim and never re-derives the CLAIM. The CLAIM framing
    pins therefore assert on the code builder (`dispatch._PLAN_REFUTE_CLAIM` /
    the emitted prompt), and the skill pins assert it points at the subcommand +
    `conductor:refuter`. The builder also embeds the resolved TAG_VOCAB rows —
    the deterministic registry-delivery channel (refuter-registry incident
    2026-08: vocab delivered only by fail-open injection read as a dangling
    pointer, the refuter hunted the plugin and hallucinated the mapping).
    """

    def setUp(self):
        self.skill = (ROOT / "skills" / "new-track" / "SKILL.md").read_text(encoding="utf-8")
        with redirect_stdout(StringIO()) as buf:
            dispatch.cmd_plan_refute_prompt("/tmp/td-plan-refute-wiring")
        self.prompt = json.loads(buf.getvalue())["prompt"]

    def test_refuter_section_present(self):
        self.assertIn("### 2.3b Adversarial Plan Refuter", self.skill)

    def test_dispatches_refuter_domain_plan(self):
        # The prompt source: the code assembler, not a hand-written block in
        # the skill.
        self.assertIn("conductor:refuter", self.skill)
        self.assertIn("plan-refute-prompt", self.skill)
        self.assertNotIn("DOMAIN=plan", self.skill)

    def test_code_builder_frames_claim_as_plan_sound(self):
        # The CLAIM is framed so SUSTAINED (default when uncertain) = proceed,
        # not block — the conservative direction for a plan gate. The framing
        # lives in the code builder now (single source), not the skill prose.
        self.assertIn("DOMAIN=plan", self.prompt)
        self.assertIn("semantically sound", self.prompt)
        self.assertIn("CONTEXT_PATHS=", self.prompt)
        # The tag-exemption clause: the CLAIM makes tag correctness part of
        # what the refuter must challenge (the dangerous over-tag direction).
        self.assertIn("wrongly exempted from TDD", self.prompt)

    def test_code_builder_embeds_resolved_tag_vocab(self):
        # Deterministic delivery: the resolved vocab rows ride the prompt
        # itself, with the no-hunt instruction — the channel that cannot
        # fail-open.
        self.assertIn("TAG_VOCAB", self.prompt)
        self.assertIn("NOT search the project or plugin for a registry", self.prompt)
        # The rows come from the single-home renderer shared with the
        # SubagentStart injection block — at least one profile row must ride
        # the prompt (the shipped registry always has tdd-exempt profiles).
        self.assertIn("tdd-exempt", self.prompt)

    def test_code_builder_computes_ac_evidence(self):
        # AC_EVIDENCE is recomputed at dispatch (the deterministic lane), not
        # trusted from a stale pass; a broken/missing track renders empty.
        self.assertIn("AC_EVIDENCE=", self.prompt)

    def test_skill_states_paste_verbatim_channel(self):
        # The skill must point at the emitted prompt as the delivery channel
        # for the vocab (not "the injection will bring it").
        self.assertIn("verbatim", self.skill)
        self.assertIn("deterministic delivery channel", self.skill)

    def test_niche_guard_excludes_deterministic_checks(self):
        # The refuter must not duplicate §2.3's deterministic lane — its value is
        # the semantic layer above those checks.
        self.assertIn("Niche guard", self.skill)
        self.assertIn("do not duplicate", self.skill.lower())

    def test_claim_direction_rationale_points_at_refuter(self):
        # The why-SUSTAINED-proceeds rationale is single-homed in the agent
        # body; the skill keeps only the pointer.
        self.assertIn("proceed-when-uncertain", self.skill)
        self.assertIn("No decision field", self.skill)

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
