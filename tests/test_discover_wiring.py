"""Wiring tests for the /conductor:discover skill.

discover is the front door before specification: it reads the git log +
``dispatch-lifecycle.log`` + ``.conductor/`` signals to find recurring frictions,
grill-triages them one question at a time (per the single-homed grill-discipline
contract), and writes ``conductor/discoveries/<date>-proposals.md`` — a triage
list the user feeds to ``/conductor:brief`` per accepted proposal. These
grep-style assertions pin the load-bearing contract: the output surface, the
Read-on-demand pointer to the grill contract, the brief hand-off (NOT auto-chain),
the discovery inputs, and the deliberate "no brief-* marker machinery" decision
(discover reuses none of it — proposals.md is not brief.md).
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text()


SKILL = "skills/discover/SKILL.md"


def _allowed_tools(txt):
    """Extract the ``allowed-tools:`` frontmatter line."""
    for line in txt.splitlines():
        if line.startswith("allowed-tools:"):
            return line
    return ""


class DiscoverFrontmatterTests(TestCase):
    def test_frontmatter_basics(self):
        txt = _read(SKILL)
        self.assertIn("name: discover", txt)
        self.assertIn("model: sonnet", txt)
        self.assertIn("argument-hint:", txt)
        self.assertIn("when_to_use:", txt)

    def test_grills_via_askuserquestion_single_context(self):
        # discover grills (needs AskUserQuestion) but stays single-context — no
        # Agent/writer subagent (mirrors brief post-collapse). Asserted on the
        # allowed-tools line so prose mentions of "agents" don't false-trip.
        at = _allowed_tools(_read(SKILL))
        self.assertIn("AskUserQuestion", at)
        self.assertIn("Bash", at)
        self.assertNotIn("Agent", at)  # no writer/dispatch subagent

    def test_no_edit_tool(self):
        # Each run writes a fresh dated proposals.md — Edit is not needed.
        at = _allowed_tools(_read(SKILL))
        self.assertNotIn("Edit", at)


class DiscoverGrillConsumerTests(TestCase):
    """discover is a THIN CONSUMER of the single-homed grill contract, like brief
    §2.5 — it must point at the contract and follow it, not restate the discipline
    (a second restated home drifts, prose-style Bucket B)."""

    def test_points_at_grill_contract(self):
        txt = _read(SKILL)
        self.assertIn("runtime/contracts/grill-discipline", txt)

    def test_grill_is_one_question_at_a_time(self):
        txt = _read(SKILL)
        self.assertIn("one question at a time", txt.lower())
        self.assertIn("AskUserQuestion", txt)

    def test_does_not_restate_grill_labels(self):
        # The canonical four-quadrant labels live in the contract, not here.
        self.assertNotIn("SHARED-KNOWN", _read(SKILL))

    def test_no_brief_marker_machinery(self):
        # KEY DESIGN DECISION: proposals.md does NOT reuse the brief-* resume
        # markers or tripwire (those are tied to brief.md). discover runs
        # prose-only against the contract — no track-state commands at all.
        txt = _read(SKILL)
        for brief_cmd in ("brief-init", "brief-finalize", "brief-resume",
                          "brief-grill-done"):
            self.assertNotIn(brief_cmd, txt)
        self.assertNotIn("track-state", txt)


class DiscoverOutputTests(TestCase):
    def test_writes_dated_proposals_under_discoveries(self):
        txt = _read(SKILL)
        self.assertIn("conductor/discoveries/", txt)
        self.assertIn("proposals.md", txt)
        # Dated filename via `date`, not a track-state command.
        self.assertIn("date +%Y-%m-%d", txt)

    def test_output_is_triage_not_spec(self):
        # The division of labor: discover TRIAGES, brief SPECIFIES. proposals.md
        # is a triage list, not a spec — pin the framing so it doesn't drift into
        # a second brief.
        txt = _read(SKILL)
        self.assertIn("triage", txt.lower())
        self.assertIn("not a spec", txt.lower())

    def test_no_fabricate_directive(self):
        txt = _read(SKILL)
        self.assertIn("fabricate", txt.lower())


class DiscoverHandoffTests(TestCase):
    def test_handoff_to_brief_per_proposal(self):
        txt = _read(SKILL)
        self.assertIn("/conductor:brief", txt)

    def test_does_not_autochain_into_brief(self):
        # The hand-off is manual — discover prints it but must not invoke brief
        # itself (mirrors brief's no-autochain-into-new-track contract).
        txt = _read(SKILL)
        self.assertIn("/conductor:brief", txt)             # the printed hand-off
        self.assertNotIn("invoke `/conductor:brief", txt)  # no auto-invoke

    def test_reads_discovery_signals_first(self):
        # The asymmetric-knowledge value: read the logs/markers BEFORE grilling.
        txt = _read(SKILL)
        self.assertIn("git log", txt)
        self.assertIn("dispatch-lifecycle.log", txt)
        self.assertIn(".conductor", txt)


if __name__ == "__main__":
    main()
