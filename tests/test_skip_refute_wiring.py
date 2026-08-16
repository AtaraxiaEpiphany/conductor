"""Structural tests for the implement §3.6 skip refute (continuous mode).

A skip is a consequential one-shot decision made unattended in continuous mode
(the interactive path §2.2 has a human gate). §3.6 now runs an adversarial
`refuter` pass over a `skip` recommendation before acting. These pin the
contract so it can't be silently removed or — critically — so the verdict
mapping can't be silently inverted.

The load-bearing detail (mirror of test_new_track_wiring's plan-refuter class):
the refuter agent defaults to SUSTAINED-when-uncertain globally, so the per-
domain conservative direction is selected by CLAIM framing. For a skip we want
block-when-uncertain (skipping is the riskier action), so the CLAIM is framed as
"the skip is unsafe" and SUSTAINED => override to block. This is the OPPOSITE
direction from the plan gate (new-track §2.3b).

Rail A paste-verbatim (design D3): the refuter prompt is now ASSEMBLED IN CODE
(`_step_assemble_refuter_prompt`, emitted by `skip-analyst-verdict`'s
`dispatch_refuter` envelope) — the skill pastes it verbatim and never re-derives
the CLAIM. So the framing pins assert on the CODE builder, and the skill pins
assert it points at the emitted prompt + agents/refuter.md (the framing's
single home) instead of hand-writing the block.
"""
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import dispatch

ROOT = Path(__file__).resolve().parent.parent


class SkipRefuteWiringTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "implement" / "SKILL.md").read_text(encoding="utf-8")
        self.builder = dispatch._step_assemble_refuter_prompt.__doc__ or ""

    def test_skip_refute_section_present(self):
        self.assertIn("**Skip refute (continuous mode only).**", self.skill)

    def test_dispatches_refuter_domain_skip(self):
        self.assertIn("conductor:refuter", self.skill)
        # The prompt source: skip-analyst-verdict's emitted envelope, not a
        # hand-written block in the skill.
        self.assertIn("dispatch_refuter", self.skill)
        self.assertNotIn("DOMAIN=skip", self.skill)

    def test_code_builder_frames_claim_as_skip_unsafe(self):
        # Framed so SUSTAINED (default when uncertain) = block — the conservative
        # direction for a consequential unattended skip. The framing lives in
        # the code builder now (single source), not the skill prose.
        marker = {"phase": 1, "task": 2, "name": "t", "reasoning": "r"}
        prompt = dispatch._step_assemble_refuter_prompt("/tmp/td", marker)
        self.assertIn("DOMAIN=skip", prompt)
        self.assertIn("this skip is UNSAFE", prompt)
        self.assertIn("CONTEXT_PATHS=", prompt)

    def test_builder_doc_states_sustained_direction(self):
        self.assertIn("SUSTAINED-when-uncertain", self.builder)
        self.assertIn("block-when-uncertain", self.builder)

    def test_sustained_overrides_to_block(self):
        self.assertIn("STATUS: SUSTAINED", self.skill)
        self.assertIn("override to block", self.skill)
        self.assertIn("pause_and_escalate", self.skill)

    def test_refuted_lets_skip_stand(self):
        self.assertIn("STATUS: REFUTED", self.skill)
        self.assertIn("let the skip stand", self.skill)

    def test_failure_defers_to_skip_analyst(self):
        # A backup crash is not new evidence the skip is safe — defer to the
        # primary verdict, with a visible announce (not a silent skip, not a halt).
        self.assertIn("STATUS: FAILURE", self.skill)
        self.assertIn("defer to skip-analyst", self.skill)

    def test_continuous_mode_only(self):
        # Interactive §2.2 has its own human gate; the refute must be scoped to
        # the unattended continuous path.
        self.assertIn("continuous mode only", self.skill.lower())

    def test_framing_home_is_refuter_agent(self):
        # The CLAIM-direction rationale is single-homed in agents/refuter.md
        # (§1.0 "No decision field" — the three callers' opposite framings);
        # the skill points there instead of restating it.
        self.assertIn("agents/refuter.md", self.skill)


if __name__ == "__main__":
    main()
