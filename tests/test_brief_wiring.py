"""Wiring tests for the /conductor:brief feature.

The skill prose can't be executed directly, but the *contract* it depends on is
testable: the scaffold has the machine-anchor headings spec-planner keys on, the
skill writes brief.md INLINE (no writer subagent — collapsed), references the CLI
+ the hand-off to new-track, the grill is one-question-at-a-time via
AskUserQuestion, user references get a home, new-track §2.2b detects the brief,
and spec-planner §3.0 reads it first. These grep-style assertions catch drift the
way the repo's other ``test_*_wiring.py`` files do.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text()


class BriefScaffoldTests(TestCase):
    """The scaffold's section headings are machine anchors (spec-planner §3.0
    honors `## Out of Scope` verbatim; new-track §2.2b reads sections by name).
    Drift here silently breaks consumption."""

    def test_scaffold_has_required_frontmatter(self):
        txt = _read("templates/brief-scaffold.md")
        self.assertIn("track_id:", txt)
        self.assertIn("status: brief", txt)
        self.assertIn("provenance:", txt)

    def test_scaffold_has_all_machine_anchor_headings(self):
        txt = _read("templates/brief-scaffold.md")
        for heading in (
            "## Problem & Motivation",
            "## Goals (in-scope)",
            "## Out of Scope",
            "## Context & Constraints",
            "## Stakeholders / Reviewers",
            "## Open Questions",
            "## Suggested Acceptance Signals",
            "## References",
        ):
            self.assertIn(heading, txt, f"scaffold missing machine-anchor heading: {heading}")


class BriefSkillWiringTests(TestCase):
    def test_skill_writes_brief_inline_no_writer_subagent(self):
        """Regression: the brief used to dispatch a conductor:track-brief-writer
        subagent. It now writes brief.md INLINE in §4 — the grill already read
        every source doc (no context isolation to gain) and a brief is a
        ~1-page scaffold fill (not a large generated surface). The writer agent,
        its BRIEF RESULT contract, and its SubagentStart reminder are GONE. The
        skill must not reference the writer or its result block."""
        txt = _read("skills/brief/SKILL.md")
        self.assertNotIn("track-brief-writer", txt)
        self.assertNotIn("BRIEF RESULT", txt)
        # The inline write step + scaffold fill is the new contract (§4 writes
        # brief.md directly — no writer subagent, no result-block handoff).
        self.assertIn("brief-scaffold.md", txt)
        self.assertIn("Write `{track_dir}/brief.md`", txt)

    def test_skill_uses_derive_name_and_brief_cli(self):
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("track-state derive-name", txt)
        self.assertIn("track-state brief-init", txt)
        self.assertIn("track-state brief-finalize", txt)
        self.assertIn("track-state brief-resume", txt)

    def test_skill_does_not_autochain_new_track(self):
        """The hand-off is manual by design — the skill must NOT invoke
        /conductor:new-track itself."""
        txt = _read("skills/brief/SKILL.md")
        # It prints the hand-off instruction but must not invoke the skill.
        self.assertIn("/conductor:new-track", txt)  # the printed hand-off
        self.assertNotIn("invoke `/conductor:new-track", txt)  # no auto-invoke
        self.assertNotIn("invoke `/conductor:implement", txt)

    def test_skill_handoff_message_present(self):
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("auto-detect", txt.lower())

    def test_skill_grill_is_one_question_at_a_time_via_askuserquestion(self):
        """The grill MUST pose one decision per AskUserQuestion call and wait for
        the answer before the next — never batch, never free-text. Front-loaded
        as a MUST imperative so the model can't miss it buried in loop prose."""
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("MUST", txt)
        self.assertIn("one question at a time", txt.lower())
        self.assertIn("AskUserQuestion", txt)

    def test_skill_has_user_references_decision(self):
        """User-named files/URLs get a canonical home: a References decision in
        the grill tree (USER_REFERENCES), unioned into ## References at write.
        Don't drop them into Open Questions (they'd read as blockers)."""
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("USER_REFERENCES", txt)


class BriefFourQuadrantTests(TestCase):
    """The four-quadrant stance (Part 2): §2.5 preamble + §3 premise-challenge
    pass + Open Questions operationalization. Grep-assertions mirror the existing
    wiring-test pattern — these pin the load-bearing prose so a regression
    (deletion or drift) is caught. Additive: the grill's structure, one-question-
    at-a-time rule, and grill-done gate are unchanged."""

    def test_section_2_5_four_quadrant_preamble_present(self):
        # The posture section framing the 2x2 (you x user x known x unknown),
        # pointing each quadrant at the §3 mechanism that implements it.
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("## 2.5 FOUR-QUADRANT STANCE", txt)
        # All four quadrants named.
        self.assertIn("SHARED-KNOWN", txt)
        self.assertIn("YOUR-KNOWN / USER-UNKNOWN", txt)
        self.assertIn("YOUR-UNKNOWN / USER-KNOWN", txt)
        self.assertIn("SHARED-UNKNOWN", txt)

    def test_premise_challenge_pass_present_and_bounded(self):
        # §3's premise-challenge (Q3): pose at most ONE challenge before the
        # convergent grill. Must be bounded (one challenge total) so the grill
        # stays tight, and must name the Out-of-Scope verbatim propagation as
        # the reason catching it early is high-leverage.
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("Premise-challenge pass", txt)
        self.assertIn("questionable", txt)  # the trigger condition
        self.assertIn("at most one", txt.lower())  # bounded
        self.assertIn("verbatim", txt)  # why early catch matters

    def test_open_questions_operationalized(self):
        # Q4: a shared-unknown decidable by experiment is operationalized
        # (hypothesis / minimal experiment / single variable / success-fail
        # signal) rather than merely confessed. "None identified." stays valid.
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("operationalize", txt)
        self.assertIn("hypothesis", txt.lower())
        self.assertIn("single variable", txt.lower())
        self.assertIn("success/fail signal", txt.lower())
        self.assertIn("None identified.", txt)  # still a valid honest result

    def test_grill_one_at_a_time_rule_preserved(self):
        # Regression guard: the four-quadrant additions must NOT weaken the
        # one-question-at-a-time rule or the grill-done gate. The premise-
        # challenge is ONE AskUserQuestion (counts toward the budget like any
        # other); grill_complete is still the real gate.
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("brief-grill-done", txt)
        self.assertIn("grill_complete", txt)


class BriefWriterAgentRemovedTests(TestCase):
    """The writer agent was collapsed — its file, SubagentStart reminder, and
    hooks.json matcher entry are all gone. Pin the removal so it can't creep
    back (a stale matcher/reminder referencing a deleted agent is a footgun)."""

    def test_writer_agent_file_deleted(self):
        self.assertFalse((ROOT / "agents" / "track-brief-writer.md").exists())

    def test_writer_subagentstart_reminder_removed(self):
        txt = _read("scripts/on-subagent-start.py")
        self.assertNotIn("track-brief-writer", txt)
        self.assertNotIn("BRIEF RESULT", txt)

    def test_writer_hooks_matcher_removed(self):
        txt = _read("hooks/hooks.json")
        self.assertNotIn("track-brief-writer", txt)


class NewTrackConsumesBriefTests(TestCase):
    """new-track §2.2b must detect brief.md, skip Q&A, and feed the Brief to
    spec-planner. Regression guard for the additive integration."""

    def test_new_track_has_brief_detection_section(self):
        txt = _read("skills/new-track/SKILL.md")
        self.assertIn("### 2.2b Brief Detection", txt)
        self.assertIn("brief.md", txt)
        self.assertIn("USER_CONTEXT: brief", txt)

    def test_new_track_existing_track_adoption_note(self):
        """§2.1 must adopt an existing track_id (carrying a brief) rather than
        re-derive — else the Brief gets orphaned under a new dated id."""
        txt = _read("skills/new-track/SKILL.md")
        self.assertIn("Existing-track adoption", txt)

    def test_spec_planner_reads_brief_first(self):
        txt = _read("agents/spec-planner.md")
        self.assertIn("### 3.0 Brief (if present)", txt)
        self.assertIn("brief.md", txt)
        # Out-of-Scope must be honored verbatim (not re-inferred).
        self.assertIn("VERBATIM", txt)
        # USER_CONTEXT in the input table.
        self.assertIn("USER_CONTEXT", txt)


if __name__ == "__main__":
    main()
