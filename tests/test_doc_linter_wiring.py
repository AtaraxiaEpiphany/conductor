"""Wiring tests for agents/doc-linter.md.

Locks down the §4-check ↔ §6.0-result-field agreement so the historical drift
cannot return: doc-linter once advertised a ``CONTRADICTIONS`` result field with
no §4 check behind it (it silently always returned 0) and branded itself a
"5-check" audit while §4 actually defined six checks. These tests make a
field-with-no-check (and a check-with-no-field) fail the suite.

Mirrors the idiom of test_doc_syncer_wiring.py / test_toc_completeness.py:
plain Path.read_text() + structural asserts, no fixtures.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_LINTER = (ROOT / "agents" / "doc-linter.md").read_text(encoding="utf-8")
WIKI_DOCTOR = (ROOT / "skills" / "wiki-doctor" / "SKILL.md").read_text(encoding="utf-8")
WIKI = (ROOT / "skills" / "wiki" / "SKILL.md").read_text(encoding="utf-8")

# Single source of truth: every §4 check → the §6.0 result-block field it
# populates. Two checks (4.1 orphan refs, 4.2 dangling backlinks) report under
# ORPHANS; that many-to-one is intentional and documented in §4.2.
CHECK_TO_FIELD = {
    "4.1": "ORPHANS",
    "4.2": "ORPHANS",
    "4.3": "STALE_CLAIMS",
    "4.4": "GAPS",
    "4.5": "LOG_ISSUES",
    "4.6": "MISSING_FRONTMATTER",
    "4.7": "CONTRADICTIONS",
}


def _section_four_checks(text):
    """Set of §4.x check ids actually defined (### headings only), e.g. '4.7'."""
    return {m.group(1) for m in re.finditer(r"^### (4\.\d+)\s", text, re.MULTILINE)}


def _result_block_fields(text):
    """Set of data fields in the FIRST ---DOC LINT RESULT--- block.

    STATUS and SUMMARY are bookkeeping, not check outputs, so excluded.
    """
    blocks = re.findall(
        r"---DOC LINT RESULT---\n(.*?)\n---END RESULT---", text, re.DOTALL
    )
    assert blocks, "no ---DOC LINT RESULT--- block found in doc-linter.md"
    fields = set(re.findall(r"^([A-Z_]+):", blocks[0], re.MULTILINE))
    return fields - {"STATUS", "SUMMARY"}


class DocLinterWiring(unittest.TestCase):
    def test_section_four_checks_match_canonical_map(self):
        """Every §4.x check is accounted for in the map (no surprise check)."""
        self.assertEqual(_section_four_checks(DOC_LINTER), set(CHECK_TO_FIELD))

    def test_every_check_field_exists_in_result_block(self):
        """Every mapped field is actually emitted in the §6.0 result block."""
        fields = _result_block_fields(DOC_LINTER)
        missing = set(CHECK_TO_FIELD.values()) - fields
        self.assertFalse(missing, f"checks emit fields missing from result block: {missing}")

    def test_no_vapor_result_field(self):
        """Every §6.0 result field is populated by >=1 check.

        This is the guard that would have caught CONTRADICTIONS being
        advertised with no check behind it.
        """
        fields = _result_block_fields(DOC_LINTER)
        vapor = fields - set(CHECK_TO_FIELD.values())
        self.assertFalse(vapor, f"result fields with no check behind them: {vapor}")

    def test_contradiction_check_present(self):
        checks = _section_four_checks(DOC_LINTER)
        self.assertIn("4.7", checks)
        self.assertIn("### 4.7 Cross-Doc Contradictions", DOC_LINTER)
        self.assertIn("CONTRADICTIONS", _result_block_fields(DOC_LINTER))

    def test_no_hardcoded_check_count_in_body(self):
        """§4.0 must not pin a stale count ('five checks' once rotted to six)."""
        self.assertIsNone(
            re.search(r"\bfive\s+checks\b", DOC_LINTER, re.IGNORECASE),
            "doc-linter body pins a stale check count",
        )

    def test_no_5_check_branding_in_skill_copy(self):
        """Skill copy must not re-enumerate a hard-coded check count."""
        for label, text in (("wiki-doctor", WIKI_DOCTOR), ("wiki", WIKI)):
            with self.subTest(skill=label):
                self.assertIsNone(
                    re.search(r"5-check|five\s*check", text, re.IGNORECASE),
                    f"{label} skill still has hard-coded check-count branding",
                )

    def test_result_delimiters_present(self):
        self.assertIn("---DOC LINT RESULT---", DOC_LINTER)
        self.assertIn("---END RESULT---", DOC_LINTER)


if __name__ == "__main__":
    unittest.main()
