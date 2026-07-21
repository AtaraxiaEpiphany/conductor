"""Wiring tests for the /conductor:brief feature.

The skill/agent prose can't be executed directly, but the *contract* it depends
on is testable: the scaffold has the machine-anchor headings spec-planner keys
on, the skill references the writer agent + the CLI + the hand-off to new-track,
the writer agent fills the scaffold and emits the BRIEF RESULT block, new-track
§2.2b detects the brief, and spec-planner §3.0 reads it first. These grep-style
assertions catch drift the way the repo's other ``test_*_wiring.py`` files do.
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
    def test_skill_dispatches_writer_agent(self):
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("conductor:track-brief-writer", txt)
        self.assertIn("BRIEF RESULT", txt)

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


class BriefWriterAgentWiringTests(TestCase):
    def test_agent_frontmatter_narrow_tools(self):
        """The writer must NOT have AskUserQuestion or Agent (gathering is the
        orchestrator's job) — the context-isolation invariant."""
        txt = _read("agents/track-brief-writer.md")
        # Frontmatter tools line must list only read/write/grep/glob.
        self.assertIn("tools: Read, Write, Grep, Glob", txt)
        self.assertNotIn("AskUserQuestion", txt)

    def test_agent_writes_one_file_and_emits_result_block(self):
        txt = _read("agents/track-brief-writer.md")
        self.assertIn("{TRACK_DIR}/brief.md", txt)
        self.assertIn("templates/brief-scaffold.md", txt)
        self.assertIn("---BRIEF RESULT---", txt)
        self.assertIn("---END BRIEF RESULT---", txt)
        self.assertIn("STATUS: SUCCESS", txt)

    def test_agent_honors_out_of_scope_verbatim(self):
        """The writer must carry the user's Out-of-Scope faithfully and not
        contradict purpose.md — the load-bearing provenance contract."""
        txt = _read("agents/track-brief-writer.md")
        self.assertIn("Out of Scope", txt)
        self.assertIn("purpose.md", txt)


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
