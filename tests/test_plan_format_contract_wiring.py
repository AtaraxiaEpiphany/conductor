"""Wiring tests for the plan-format-contract extraction (#4).

spec-planner's inline <rules> / <task-type-tags> / <subtask-rules> blocks were
relocated to conductor/design/plan-format-contract.md — a plugin-side reference
doc spec-planner reads via ${CLAUDE_PLUGIN_ROOT} (same idiom it already uses for
spec-scaffold.md). These tests guard:
- the contract doc exists with compliant provenance frontmatter (it lives under a
  provenance dir, so the doc-linter + SessionStart GC enforce type/sources/last_verified);
- the relocated content (status-marker rules, the tag table, subtask rules) lives there;
- spec-planner points at it and no longer carries the inline blocks (dedup happened).
"""
from pathlib import Path
from unittest import TestCase, main

from scripts.lib.frontmatter import missing_required_fields

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "conductor" / "design" / "plan-format-contract.md"
SPEC_PLANNER = (ROOT / "agents" / "spec-planner.md").read_text(encoding="utf-8")


class ContractDocTests(TestCase):
    def setUp(self):
        self.assertTrue(CONTRACT.exists(), "plan-format-contract.md must exist")
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_frontmatter_compliant(self):
        # Lives under a provenance dir (conductor/design/), so it must carry the
        # required provenance fields the doc-linter + SessionStart GC enforce.
        self.assertEqual(missing_required_fields(self.text), [])

    def test_lists_spec_planner_as_source(self):
        self.assertIn("agents/spec-planner", self.text)

    def test_status_marker_rules_relocated(self):
        # The parser-silent-drop + checkbox-vs-tag rule is the load-bearing invariant.
        self.assertIn("silently dropped by the parser", self.text)
        self.assertIn("Status Markers", self.text)
        self.assertIn("AC Traceability", self.text)

    def test_task_type_tag_table_relocated(self):
        # All five dispatch tags + the TDD-required column.
        for tag in ("[Explore]", "[Docs]", "[Config]", "[Chore]", "[Manual]"):
            self.assertIn(tag, self.text)
        self.assertIn("TDD Required", self.text)

    def test_subtask_rules_relocated(self):
        self.assertIn("minimum 2, recommended maximum 5", self.text)
        self.assertIn("When to use subtasks", self.text)

    def test_dependency_annotation_section_present(self):
        # The optional <!-- deps: P{n}.T{n} --> substrate (parser-validated via
        # plan_parse.validate_deps, inert in v1) is documented in the contract.
        self.assertIn("Inter-Task Dependencies", self.text)
        self.assertIn("deps:", self.text)
        self.assertIn("P{n}.T{n}", self.text)


class SpecPlannerPointerTests(TestCase):
    def test_points_at_contract_doc_via_plugin_root(self):
        # spec-planner resolves plugin-side files via ${CLAUDE_PLUGIN_ROOT}
        # (same idiom it already uses for templates/spec-scaffold.md).
        self.assertIn(
            "${CLAUDE_PLUGIN_ROOT}/conductor/design/plan-format-contract.md",
            SPEC_PLANNER,
        )

    def test_inline_tag_table_removed_from_body(self):
        # The tag table moved to the contract doc; the agent body must not still
        # carry a duplicate ("TDD Required" is unique to that table header).
        self.assertNotIn("TDD Required", SPEC_PLANNER)

    def test_inline_rules_block_removed_from_body(self):
        # The <rules> pseudo-block moved out of the agent body.
        self.assertNotIn("**<rules>**", SPEC_PLANNER)

    def test_directs_dependency_declaration(self):
        # spec-planner nudges declaring <!-- deps: --> when tasks aren't
        # file-disjoint — the upstream input any future parallelism depends on.
        self.assertIn("<!-- deps:", SPEC_PLANNER)


if __name__ == "__main__":
    main()
