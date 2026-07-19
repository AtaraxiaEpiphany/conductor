"""Placement coverage + role tests for the Conductor file maps.

Two maps govern doc placement/reading:
- ``templates/claude-md-toc.md``  → creation map (pasted into project CLAUDE.md)
- ``templates/project-index.md``  → read-strategy map (→ ``conductor/index.md``)

The **spine-agreement** invariant between them (every read-map category must
have a creation-map home, and every row must carry a seeded/auto/on-demand
status) is enforced data-driven by ``scripts/check-index-maps.py``, exercised in
``test_index_maps.py``. This file keeps the human-readable placement contract
that the script does NOT cover: the creation map is the intentional superset
(decision records, plugin-provided authoring rules), it states its own role vs.
``conductor/index.md``, and ``doc-conventions.md`` gives every frontmatter
``type`` a canonical folder (closing the ``concept``/``entity`` "no home" gap).
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
TOC = (ROOT / "templates" / "claude-md-toc.md").read_text(encoding="utf-8")
CONVENTIONS = (ROOT / "runtime" / "contracts" / "doc-conventions.md").read_text(encoding="utf-8")


class MapAgreementTests(TestCase):
    def test_creation_map_is_the_superset(self):
        # Placement targets the creation map must surface even when the
        # read-map doesn't single them out: decision records + authoring rules.
        # doc-conventions is plugin-provided (never copied into a project), so
        # the map surfaces it as authoring-rules guidance — NOT a project path,
        # which would dangle (see test_templates_no_dangling_plugin_docs).
        self.assertIn("conductor/design/decision", TOC)
        self.assertIn("doc-conventions", TOC)

    def test_creation_map_states_its_role(self):
        # The TOC must distinguish itself from conductor/index.md so agents
        # don't treat the two maps as interchangeable.
        self.assertIn("conductor/index.md", TOC)


class TypePlacementTests(TestCase):
    def test_type_to_folder_section_exists(self):
        self.assertIn("## Type → Folder Placement", CONVENTIONS)

    def test_every_frontmatter_type_is_named(self):
        for t in ("architecture", "api", "database", "ux", "concept",
                  "entity", "resource", "source", "query"):
            self.assertIn(t, CONVENTIONS, f"frontmatter type {t!r} not present")

    def test_concept_and_entity_are_disambiguated(self):
        # The two types that previously had no home: concept → design (decision
        # records are type: concept), entity → resource (domain inventory).
        self.assertIn("decision records", CONVENTIONS)
        self.assertIn("Domain entities", CONVENTIONS)


if __name__ == "__main__":
    main()
