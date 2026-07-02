"""Structural tests for the parallel §4.15 seam refute (before the human gate).

A single cross-member code-reviewer pass over an integrated wave can misread a
seam interaction. §4.15 now runs an adversarial `refuter` pass over the
Critical/High seam findings BEFORE routing to `AskUserQuestion`, so single-
reviewer false positives don't reach the human as integration defects.

The load-bearing detail (same family as the plan-gate and skip-gate tests): the
refuter agent defaults to SUSTAINED-when-uncertain globally, so the conservative
direction is selected by CLAIM framing. Here the CLAIM is "the findings are
real", so SUSTAINED => keep-when-uncertain — a possible integration defect is
surfaced to the human rather than silently dropped. REFUTED => grounded misread
=> drop.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


class ParallelSeamRefuteWiringTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "parallel" / "SKILL.md").read_text(encoding="utf-8")

    def test_seam_refute_section_present(self):
        self.assertIn("**Seam refute (before the human gate).**", self.skill)

    def test_dispatches_refuter_domain_seam(self):
        self.assertIn("conductor:refuter", self.skill)
        self.assertIn("DOMAIN=seam", self.skill)

    def test_claim_framed_as_findings_real(self):
        # Framed so SUSTAINED (default when uncertain) = keep — a possible
        # integration defect reaches the human rather than being silently dropped.
        self.assertIn("findings are real", self.skill)
        self.assertIn("keep-when-uncertain", self.skill)

    def test_refute_precedes_askuserquestion(self):
        # The refute strips misreads BEFORE the human gate, not after.
        self.assertIn("before the human gate", self.skill.lower())

    def test_survivors_route_to_human_gate(self):
        self.assertIn("Survivors remain", self.skill)
        self.assertIn("AskUserQuestion", self.skill)

    def test_all_refuted_proceeds_with_announced_count(self):
        # No silent caps: if every finding is refuted, the count is announced
        # (not hidden) and the wave proceeds.
        self.assertIn("No survivors", self.skill)
        self.assertIn("all refuted on re-examination", self.skill)

    def test_failure_keeps_all_findings(self):
        # A crashed backup must not drop findings it never vetted — route the
        # originals to the human.
        self.assertIn("STATUS: FAILURE", self.skill)
        self.assertIn("keep all original findings", self.skill)


if __name__ == "__main__":
    main()
