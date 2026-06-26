"""Drift gate + placement coverage for the Conductor file maps.

Two maps govern doc placement/reading:
- ``templates/claude-md-toc.md``  → creation map (pasted into project CLAUDE.md)
- ``templates/project-index.md``  → read-strategy map (→ ``conductor/index.md``)

They group docs differently but MUST list the same first-class spine, else an
agent resolving a path via CLAUDE.md can't find or place a doc the read-map
knows about (the ``purpose.md`` omission bug). ``doc-conventions.md`` must in
turn give every frontmatter ``type`` a canonical folder — closing the
``concept``/``entity`` "no home" gap.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
TOC = (ROOT / "templates" / "claude-md-toc.md").read_text(encoding="utf-8")
INDEX = (ROOT / "templates" / "project-index.md").read_text(encoding="utf-8")
CONVENTIONS = (ROOT / "conductor" / "design" / "doc-conventions.md").read_text(encoding="utf-8")

# First-class docs BOTH maps must list. Adding a spine doc to one map forces
# adding it to the other — this list is the contract that enforces agreement.
SPINE = [
    "conductor/product/product.md",
    "conductor/product/product-guidelines.md",
    "conductor/design/tech-stack.md",
    "conductor/resource/glossary.md",
    "conductor/overview.md",
    "conductor/purpose.md",
    "conductor/log.md",
    "conductor/requirement/ux-ui/design-spec.md",
    "conductor/design/architecture/system-architecture.md",
    "conductor/design/api-specs",
    "conductor/design/database",
    "conductor/workflow/index.md",
    "conductor/workflow/git-flow.md",
    "conductor/workflow/testing/strategy.md",
    "conductor/tracks.md",
]


class MapAgreementTests(TestCase):
    def test_spine_listed_in_both_maps(self):
        for frag in SPINE:
            self.assertIn(frag, TOC,
                          f"creation map (claude-md-toc.md) missing {frag!r}")
            self.assertIn(frag, INDEX,
                          f"read map (project-index.md) missing {frag!r}")

    def test_creation_map_is_the_superset(self):
        # Placement targets the creation map must surface even when the
        # read-map doesn't single them out: decision records + authoring rules.
        self.assertIn("conductor/design/decision", TOC)
        self.assertIn("conductor/design/doc-conventions.md", TOC)

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
