"""Tests for spec_parse.parse_spec: FR/NFR/AC/TC inventory extraction.

Section-scoped: IDs are captured only inside their owning heading, so a mention
in prose (Constraints/References) is ignored. Mirrors the scaffold in
templates/spec-scaffold.md.
"""
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.spec_parse import parse_spec

_SPEC = """\
# Specification: Demo

## Overview
A demo.

## Type
feature

## Requirements

### Functional Requirements
- FR-1: User can reset password [(ref)](conductor/design/auth.md)
- FR-2: User can log out

### Non-Functional Requirements
- NFR-1: Responses under 200ms
- NFR-2: 99.9% uptime

## Acceptance Criteria
- AC-1: Password reset email arrives
- AC-2: Logout clears session

## Test Scenarios

| ID     | AC Ref | Scenario          | Expected Outcome |
| ------ | ------ | ----------------- | ---------------- |
| TC-1.1 | AC-1   | happy path        | email sent       |
| TC-1.2 | AC-1   | expired token     | error            |
| TC-2.1 | AC-2   | happy path        | session cleared  |

## Constraints
- AC-1 must hold even if FR-2 changes.  # prose mentions, must NOT be captured
"""


def _write(body):
    d = tempfile.mkdtemp()
    Path(d, "spec.md").write_text(body)
    return Path(d, "spec.md")


class SpecParseTests(TestCase):
    def test_extracts_frs_nfrs_acs(self):
        inv = parse_spec(_write(_SPEC))
        self.assertEqual(inv["frs"], ["FR-1", "FR-2"])
        self.assertEqual(inv["nfrs"], ["NFR-1", "NFR-2"])
        self.assertEqual(inv["acs"], ["AC-1", "AC-2"])

    def test_extracts_tc_table_and_map(self):
        inv = parse_spec(_write(_SPEC))
        self.assertEqual([t["id"] for t in inv["tcs"]], ["TC-1.1", "TC-1.2", "TC-2.1"])
        self.assertEqual(inv["tc_to_ac"],
                         {"TC-1.1": "AC-1", "TC-1.2": "AC-1", "TC-2.1": "AC-2"})

    def test_section_scoping_ignores_prose_mentions(self):
        # The Constraints line names "AC-1" and "FR-2" in prose — not captured.
        inv = parse_spec(_write(_SPEC))
        self.assertEqual(inv["acs"].count("AC-1"), 1)
        self.assertEqual(inv["frs"].count("FR-2"), 1)

    def test_fr_with_inline_ref_link_still_parsed(self):
        inv = parse_spec(_write(_SPEC))
        # FR-1 carries a `[(ref)](path)` link; the ID still extracts cleanly.
        self.assertIn("FR-1", inv["frs"])

    def test_missing_sections_yield_empty_inventory_with_warning(self):
        inv = parse_spec(_write("# Spec\n\n## Overview\nNothing here.\n"))
        self.assertEqual(inv["frs"], [])
        self.assertEqual(inv["acs"], [])
        self.assertEqual(inv["tc_to_ac"], {})
        self.assertTrue(any("no FR/NFR/AC" in w for w in inv["warnings"]))

    def test_empty_file_does_not_crash(self):
        inv = parse_spec(_write(""))
        self.assertEqual(inv["acs"], [])
        self.assertEqual(inv["errors"], [])


class RequirementBodyTests(TestCase):
    """parse_spec also exposes the FR/NFR body text (fr_items/nfr_items) so the
    EARS lint in spec_integrity can inspect wording. ID lists stay unchanged."""

    def test_fr_items_capture_body_text(self):
        inv = parse_spec(_write(_SPEC))
        bodies = {it["id"]: it["text"] for it in inv["fr_items"]}
        self.assertEqual(bodies["FR-1"],
                         "User can reset password [(ref)](conductor/design/auth.md)")
        self.assertEqual(bodies["FR-2"], "User can log out")

    def test_nfr_items_capture_body_text(self):
        inv = parse_spec(_write(_SPEC))
        bodies = {it["id"]: it["text"] for it in inv["nfr_items"]}
        self.assertEqual(bodies["NFR-1"], "Responses under 200ms")
        self.assertEqual(bodies["NFR-2"], "99.9% uptime")

    def test_item_ids_match_id_lists_in_order(self):
        inv = parse_spec(_write(_SPEC))
        self.assertEqual([it["id"] for it in inv["fr_items"]], inv["frs"])
        self.assertEqual([it["id"] for it in inv["nfr_items"]], inv["nfrs"])

    def test_items_empty_when_no_requirements(self):
        inv = parse_spec(_write("# Spec\n\n## Overview\nNothing here.\n"))
        self.assertEqual(inv["fr_items"], [])
        self.assertEqual(inv["nfr_items"], [])


class AnchorParseTests(TestCase):
    """The review-grounding substrate: a ``## Artifact Anchors`` table maps each
    AC to a concrete deliverable (artifact + location) a reviewer attests. The
    twin of the Test Scenarios table for non-code shapes (Track B2)."""

    _ANCHOR_SPEC = """\
# Specification: Demo
## Acceptance Criteria
- AC-1: API design documented
- AC-2: Migration runbook delivered
## Artifact Anchors
| AC Ref | Artifact | Location |
| ------ | -------- | -------- |
| AC-1   | API design doc | docs/api.md |
| AC-2   | migration runbook | docs/run.md |
"""

    def test_extracts_anchors_with_artifact_and_location(self):
        inv = parse_spec(_write(self._ANCHOR_SPEC))
        self.assertEqual([a["ac"] for a in inv["anchors"]], ["AC-1", "AC-2"])
        by_ac = {a["ac"]: a for a in inv["anchors"]}
        self.assertEqual(by_ac["AC-1"]["artifact"], "API design doc")
        self.assertEqual(by_ac["AC-1"]["location"], "docs/api.md")
        self.assertEqual(by_ac["AC-2"]["artifact"], "migration runbook")

    def test_test_grounded_spec_has_no_anchors(self):
        # _SPEC (Test Scenarios) parses with an empty anchors list — the review
        # substrate is absent, so a test-grounded spec is untouched.
        inv = parse_spec(_write(_SPEC))
        self.assertEqual(inv["anchors"], [])
        # and the TC inventory still flows unchanged.
        self.assertEqual(len(inv["tcs"]), 3)

    def test_anchor_with_empty_location_cell(self):
        spec = ("# Specification\n## Acceptance Criteria\n- AC-1: crit\n"
                "## Artifact Anchors\n"
                "| AC Ref | Artifact | Location |\n| ------ | -------- | -------- |\n"
                "| AC-1 | design doc |  |\n")
        inv = parse_spec(_write(spec))
        self.assertEqual(inv["anchors"], [{"ac": "AC-1",
                                           "artifact": "design doc",
                                           "location": ""}])

    def test_anchor_section_scoping(self):
        # An anchor-shaped row OUTSIDE ## Artifact Anchors is not captured
        # (section-scoped, like TC/AC/FR/NFR).
        spec = ("# Specification\n## Overview\n| AC-1 | not an anchor | x |\n"
                "## Acceptance Criteria\n- AC-1: crit\n"
                "## Artifact Anchors\n"
                "| AC Ref | Artifact | Location |\n| -- | -- | -- |\n"
                "| AC-1 | real | docs/a.md |\n")
        inv = parse_spec(_write(spec))
        self.assertEqual([a["ac"] for a in inv["anchors"]], ["AC-1"])
        self.assertEqual(inv["anchors"][0]["artifact"], "real")

    def test_header_and_separator_rows_not_captured(self):
        # The header (| AC Ref | …) and separator (| ----- | …) lack AC-<digit>,
        # so neither matches _ANCHOR_ROW.
        spec = ("# Specification\n## Acceptance Criteria\n- AC-1: crit\n"
                "## Artifact Anchors\n"
                "| AC Ref | Artifact | Location |\n| ------ | -------- | -------- |\n")
        inv = parse_spec(_write(spec))
        self.assertEqual(inv["anchors"], [])


if __name__ == "__main__":
    main()
