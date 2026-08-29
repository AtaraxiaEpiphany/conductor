"""Wiring tests for the /conductor:brief feature.

The skill prose can't be executed directly, but the *contract* it depends on is
testable: the scaffold has the machine-anchor headings spec-planner keys on, the
skill writes brief.md INLINE (no writer subagent — collapsed), references the CLI
+ the hand-off to new-track, the grill runs frontier rounds via AskUserQuestion
(batch ≤4 mutually-independent decisions; dependent decisions stay solo), user
references get a home, new-track §2.2b detects the brief,
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

    def test_skill_confirm_gated_invoke(self):
        """The hand-off chain is CONFIRM-GATED (design reversal, 2026-08-29 —
        the old manual halt forced the user to re-type the date-stamped id).
        "Plan now" at the §5 gate invokes /conductor:new-track itself; the
        gate keeps the edit window open (Out-of-Scope propagates verbatim
        into the spec) and the invoke fires only after finalize + scoped
        commit — durability before consumption."""
        txt = _read("skills/brief/SKILL.md")
        # The gate + its three options.
        self.assertIn("Plan now (Recommended)", txt)
        self.assertIn("Review/edit first", txt)
        self.assertIn("Done for now", txt)
        # Re-ask after an edit is bounded to once (over-asking is a tax).
        self.assertIn("re-ask once", txt)
        # Durability before consumption: finalize + commit ordered before the invoke.
        self.assertLess(txt.index("**Commit brief.md scoped**"),
                        txt.index("invoke `/conductor:new-track <track_id>`"))
        # The declined path still prints the hand-off (only that path does).
        self.assertIn("When ready to plan, run: `/conductor:new-track <track_id>`", txt)
        # The invoke runs through the Skill tool, so it must be allowed.
        self.assertIn("Skill", txt)
        fm = txt.split("---")[1]
        self.assertIn("Skill", fm.split("allowed-tools:")[1].splitlines()[0])
        # The brief still never chains past new-track into implement
        # (new-track §2.7 owns the auto-start offer).
        self.assertNotIn("invoke `/conductor:implement", txt)

    def test_skill_handoff_message_present(self):
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("auto-detect", txt.lower())

    def test_skill_grill_is_frontier_rounds_via_askuserquestion(self):
        """The grill MUST pose decisions via AskUserQuestion in frontier rounds —
        batch only the currently-unblocked decisions, at most 4 questions per
        call, recommended-answer-first; a dependent decision stays one question
        at a time, alone. Front-loaded as a MUST imperative so the model can't
        miss it buried in loop prose."""
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("MUST", txt)
        self.assertIn("AskUserQuestion", txt)
        self.assertIn("frontier", txt.lower())
        self.assertIn("at most 4 questions", txt)
        self.assertIn("one question at a time", txt.lower())  # dependent-decision exception

    def test_skill_has_user_references_decision(self):
        """User-named files/URLs get a canonical home: a References decision in
        the grill tree (USER_REFERENCES), unioned into ## References at write.
        Don't drop them into Open Questions (they'd read as blockers)."""
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("USER_REFERENCES", txt)

    def test_reads_glossary_and_decisions_when_present(self):
        """D2: §2 reads the settled-vocabulary globals when present — glossary
        + decision records are SHARED-KNOWN (contract §2/§7), fueling the §3
        recommendations instead of being re-asked."""
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("conductor/resource/glossary.md", txt)
        self.assertIn("conductor/design/decision-*.md", txt)

    def test_crystallization_hook_cites_contract_section_7(self):
        """D2: the crystallization write-back is a ONE-LINE hook citing
        contract §7 — the write discipline stays single-homed there, not
        restated in the skill (prose-style Bucket B)."""
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("Crystallization writes", txt)
        self.assertIn("§7", txt)

    def test_commit_stages_crystallization_writes_conditionally(self):
        """D2: §5's scoped git add extends to the glossary/decision files the
        grill actually touched — the placeholder in the bash block carries
        only paths written this run (the `never git add -A` rule in the same
        step stays the scoped-staging guard)."""
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("crystallization writes", txt.lower())
        self.assertIn("[<crystallization_write_paths>]", txt)


class BriefGrillConsumerTests(TestCase):
    """brief §2.5 + §3.0 are now a THIN CONSUMER of the single-homed grill
    contract. The skill must point at `runtime/contracts/grill-discipline.md`
    (Read-on-demand, like task-executor loads refactor.md) and must NOT restate
    the discipline — a second restated home silently drifts (prose-style Bucket B).
    What stays brief-owned is pinned here: the decision tree's anchor labels, the
    frontier-rounds salience hook, and the brief-grill-done signal."""

    def test_section_2_5_points_at_grill_contract(self):
        # §2.5 keeps the brief-specific framing + a Read-on-demand pointer to the
        # single home, rather than restating the four-quadrant stance.
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("## 2.5 FOUR-QUADRANT STANCE", txt)
        self.assertIn("runtime/contracts/grill-discipline", txt)

    def test_brief_does_not_restate_grill_discipline(self):
        # The canonical label set lives in the contract, NOT in the skill —
        # restating it here recreates the drift surface the extraction closed.
        txt = _read("skills/brief/SKILL.md")
        self.assertNotIn("SHARED-KNOWN", txt)

    def test_brief_keeps_its_decision_tree(self):
        # The 8-node tree is brief-owned and stays; only generic grill *rules*
        # moved out. Node labels must remain as anchors the writer fills.
        txt = _read("skills/brief/SKILL.md")
        self.assertIn("Out of Scope", txt)
        self.assertIn("Acceptance Signals", txt)
        self.assertIn("None identified.", txt)  # honest-empty still valid

    def test_grill_done_signal_rule_preserved(self):
        # Regression guard: the consumer refactor must NOT weaken the grill-done
        # gate. grill_complete is still the real gate (contract §6).
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


class NewTrackPendingBriefScanTests(TestCase):
    """new-track §2.1 step 4 — the pre-derive pending-brief scan. Without it a
    finished brief in another track dir is orphaned: §2.2b checks only the
    just-derived dir, and adoption required the user to type the exact
    date-stamped track_id. The scan is code-owned (the skill stays
    path-agnostic) and runs before derive-name."""

    def test_scan_step_present_and_before_derive_name(self):
        txt = _read("skills/new-track/SKILL.md")
        self.assertIn("track-state pending-briefs", txt)
        # Ordered: the scan must precede derive-name, or the dated id is
        # already minted and the brief orphaned anyway.
        self.assertLess(txt.index("track-state pending-briefs"),
                        txt.index("track-state derive-name"))

    def test_adoption_ask_pins_none_option(self):
        txt = _read("skills/new-track/SKILL.md")
        self.assertIn("Adopt '`<title>`' (`<brief_age_days>`d)", txt)
        self.assertIn("None — fresh track", txt)

    def test_empty_args_scan_before_description_ask(self):
        txt = _read("skills/new-track/SKILL.md")
        self.assertIn("run this scan BEFORE the step-1 description ask", txt)

    def test_adoption_skips_derive_and_reruns_shape_with_brief(self):
        txt = _read("skills/new-track/SKILL.md")
        self.assertIn("skip `derive-name` (step 5)", txt)
        self.assertIn("--brief \"<track_dir>/brief.md\"", txt)


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

    def test_new_track_recommends_brief_before_qa_fallback(self):
        """When no brief is found, §2.2 step 3 must recommend /conductor:brief
        FIRST (the grilled path) before falling back to minimal Q&A — grilling is
        consolidated to one surface (the brief skill), rather than an uncited
        bespoke Q&A script. The Q&A escape hatch stays for trivial tracks, but
        D4 folds it under the grill contract: it cites grill-discipline.md,
        declares its batch-confirm posture, and keeps look-it-up-first +
        recommended-answer-first mandatory (baseline competence, not full-grill
        extras)."""
        txt = _read("skills/new-track/SKILL.md")
        self.assertIn("/conductor:brief", txt)
        self.assertIn("recommended", txt)         # brief-first is the recommended option
        self.assertIn("escape hatch", txt)        # minimal Q&A remains reachable
        self.assertIn("grill-discipline", txt)    # D4: the fallback cites the contract
        self.assertIn("batch-confirm", txt)       # ...and names its posture
        self.assertIn("recommended-answer-first", txt)  # baseline rules stay mandatory

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
