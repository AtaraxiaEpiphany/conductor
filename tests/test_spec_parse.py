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


if __name__ == "__main__":
    main()
