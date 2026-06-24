"""Structural tests for the purpose.md wiki-soul addition (O2).

purpose.md is the wiki's directional intent (goals, thesis, decisions) — distinct
from the structural overview.md. These tests guard the *wiring*: the template
exists with the co-evolved sections, setup seeds it, the index lists it, the
wiki skill routes a `purpose` subcommand, doc-syncer regenerates its LLM-owned
sections in Phase 2, and spec-planner reads it for direction.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


class PurposeTemplateTests(TestCase):
    def test_template_exists_and_has_required_sections(self):
        tpl = ROOT / "templates" / "wiki-purpose.md"
        self.assertTrue(tpl.exists(), "templates/wiki-purpose.md must exist")
        text = tpl.read_text(encoding="utf-8")
        # Co-evolved sections: Goals/Scope are user-authored; Thesis/Decisions/
        # Key Questions are LLM-maintained by doc-syncer Phase 2.
        for section in (
            "## Goals",
            "## Key Questions",
            "## Evolving Thesis",
            "## In Scope",
            "## Out of Scope",
            "## Active Decisions",
        ):
            self.assertIn(section, text, f"purpose template missing section {section!r}")
        self.assertIn("{TIMESTAMP}", text, "template must carry a {TIMESTAMP} placeholder")


class IndexWiringTests(TestCase):
    def test_project_index_lists_purpose(self):
        text = (ROOT / "templates" / "project-index.md").read_text(encoding="utf-8")
        self.assertIn("conductor/purpose.md", text)
        self.assertIn("Wiki Purpose", text)

    def test_setup_seeds_purpose(self):
        text = (ROOT / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("templates/wiki-purpose.md", text)
        self.assertIn("conductor/purpose.md", text)


class WikiSkillRoutingTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "wiki" / "SKILL.md").read_text(encoding="utf-8")

    def test_purpose_subcommand_routed(self):
        self.assertIn("`purpose`", self.skill)
        self.assertIn("**Section 3.5**", self.skill)

    def test_purpose_setup_check_and_section_present(self):
        self.assertIn("conductor/purpose.md", self.skill)
        self.assertIn("## 3.5 PURPOSE", self.skill)


class DocSyncerPhase2Tests(TestCase):
    def setUp(self):
        self.agent = (ROOT / "agents" / "doc-syncer.md").read_text(encoding="utf-8")

    def test_loads_purpose_in_infrastructure(self):
        self.assertIn("conductor/purpose.md", self.agent)
        self.assertIn("Wiki Purpose", self.agent)

    def test_phase2_updates_purpose_preserving_user_sections(self):
        # The LLM-maintained update step, distinct from the wholesale overview rewrite.
        self.assertIn("### 7.1b Update", self.agent)
        self.assertIn("Evolving Thesis", self.agent)
        # Must NOT wholesale-replace purpose.md (co-evolved, unlike overview.md).
        self.assertIn("never", self.agent.lower())

    def test_purpose_log_operation_and_report_field(self):
        self.assertIn("PURPOSE_UPDATE", self.agent)
        self.assertIn("PURPOSE_UPDATED: true|false", self.agent)


class SpecPlannerConsumesPurposeTests(TestCase):
    def test_spec_planner_reads_purpose_for_direction(self):
        text = (ROOT / "agents" / "spec-planner.md").read_text(encoding="utf-8")
        self.assertIn("Wiki Purpose", text)
        self.assertIn("conductor/purpose.md", text)


if __name__ == "__main__":
    main()
