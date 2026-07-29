"""Wiring tests for the plan-format-contract extraction (#4).

spec-planner's inline <rules> / <task-type-tags> / <subtask-rules> blocks were
relocated to runtime/contracts/plan-format-contract.md — a plugin-internal
behavioral contract spec-planner reads via ${CLAUDE_PLUGIN_ROOT} (sibling of
core-contract.md / subagent-firewall.md, never copied into a project). These
tests guard:
- the contract doc exists at its runtime/contracts/ home;
- it carries compliant provenance frontmatter naming spec-planner as a source
  (retained by convention — runtime/contracts/ is NOT scanned by the SessionStart
  corpus-frontmatter GC, which scopes the project's conductor/{design,resource}
  corpus dirs, so the frontmatter here is documentation guarded by test, not
  hook enforcement);
- the relocated content (status-marker rules, the tag table, subtask rules) lives there;
- spec-planner points at it via ${CLAUDE_PLUGIN_ROOT} and no longer carries the
  inline blocks (dedup happened).
"""
from pathlib import Path
from unittest import TestCase, main

from scripts.lib.frontmatter import missing_required_fields
from scripts.track_state.task_profiles import TAG_VOCAB

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "runtime" / "contracts" / "plan-format-contract.md"
REGISTRY = ROOT / "templates" / "workflow" / "task-type-profiles.json"
SPEC_PLANNER = (ROOT / "agents" / "spec-planner.md").read_text(encoding="utf-8")

# The six tags the contract table documents (the human-readable view of the
# registry). Drift between this list, the contract table, and the registry is
# what the RegistryDriftTests class guards.
CONTRACT_TAGS = ("Explore", "Docs", "Config", "Chore", "Manual", "Migrate")


class ContractDocTests(TestCase):
    def setUp(self):
        self.assertTrue(CONTRACT.exists(), "plan-format-contract.md must exist")
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_frontmatter_compliant(self):
        # The contract retains provenance frontmatter (type/sources/last_verified)
        # naming its consumers. runtime/contracts/ is NOT scanned by the SessionStart
        # corpus-frontmatter GC (which scopes the project's conductor/{design,resource}
        # corpus dirs), so this is documentation guarded by test, not hook enforcement.
        self.assertEqual(missing_required_fields(self.text), [])

    def test_lists_spec_planner_as_source(self):
        self.assertIn("agents/spec-planner", self.text)

    def test_status_marker_rules_relocated(self):
        # The parser-silent-drop + checkbox-vs-tag rule is the load-bearing invariant.
        self.assertIn("silently dropped by the parser", self.text)
        self.assertIn("Status Markers", self.text)
        self.assertIn("AC Traceability", self.text)

    def test_task_type_tag_table_relocated(self):
        # All six dispatch tags + the TDD-required column.
        for tag in ("[Explore]", "[Docs]", "[Config]", "[Chore]", "[Manual]", "[Migrate]"):
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

    def test_unknown_tag_error_documented(self):
        # The silent-drift fix: an unrecognized tag is now a hard error at
        # init-from-plan, not a silent default. The contract must say so.
        self.assertIn("Unknown tags are a hard error", self.text)
        self.assertIn("task-type-profiles.json", self.text)

    def test_override_layer_documented(self):
        # The project-local override: a project may drop
        # conductor/workflow/task-type-profiles.json to merge over the plugin
        # baseline. The contract must document the baseline ⊕ overlay resolution.
        self.assertIn("project overlay", self.text.lower())
        self.assertIn("conductor/workflow/task-type-profiles.json", self.text)
        self.assertIn("opt-in", self.text.lower())


class RegistryDriftTests(TestCase):
    """Guard drift between the registry (source of truth), the contract table
    (human-readable view), and the in-code vocab. Adding a tag is meant to be a
    one-row registry change; these tests ensure the other surfaces don't lag."""

    def setUp(self):
        self.assertTrue(REGISTRY.exists(), "task-type-profiles.json registry must exist")

    def test_registry_keys_superset_of_contract_tags(self):
        # Every tag the contract table documents must be registered — otherwise
        # init-from-plan would reject a plan.md using a documented tag.
        vocab = set(TAG_VOCAB())
        for tag in CONTRACT_TAGS:
            self.assertIn(tag, vocab, f"contract tag [{tag}] missing from registry")

    def test_registry_has_semantics_for_every_tag(self):
        # Each registered tag must declare route + both exemption flags (the
        # fields the code reads). A row missing a key silently inherits the
        # default profile — assert the intent is explicit so a future editor
        # sees the full picture per row.
        import json
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for tag, prof in data["tags"].items():
            for field in ("route", "tdd_exempt", "coverage_exempt"):
                self.assertIn(
                    field, prof,
                    f"registry tag [{tag}] missing required field '{field}'",
                )

    def test_vocab_matches_registry_keys(self):
        # The in-code TAG_VOCAB() is derived from the registry keys — they must
        # be identical (the dedup contract: one source of truth).
        import json
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(set(TAG_VOCAB()), set(data["tags"].keys()))


class SpecPlannerPointerTests(TestCase):
    def test_points_at_contract_doc_via_plugin_root(self):
        # spec-planner resolves plugin-internal files via ${CLAUDE_PLUGIN_ROOT}
        # (sibling idiom to core-contract.md / templates/spec-scaffold.md).
        self.assertIn(
            "${CLAUDE_PLUGIN_ROOT}/runtime/contracts/plan-format-contract.md",
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
