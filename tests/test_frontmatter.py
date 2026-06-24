"""Tests for lib/frontmatter.py provenance helpers (O4).

Scoped corpus docs carry YAML frontmatter (type/sources/last_verified) so
staleness checks are evidence-based. These cover the parser, the required-field
check, the exemption rules, and the directory scan the doc-linter prompt + the
SessionStart GC hook both rely on.
"""
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.lib.frontmatter import (
    parse_frontmatter,
    has_frontmatter,
    missing_required_fields,
    is_exempt,
    is_corpus_doc,
    check_corpus_frontmatter,
    REQUIRED_FM_FIELDS,
)


class ParseTests(TestCase):
    def test_parses_scalar_and_list(self):
        text = (
            "---\n"
            "type: architecture\n"
            "sources:\n"
            "  - auth_20260601\n"
            "  - P1T2\n"
            "last_verified: 2026-06-20\n"
            "---\n\n"
            "# System Architecture\n"
        )
        fm = parse_frontmatter(text)
        self.assertIsNotNone(fm)
        self.assertEqual(fm["type"], "architecture")
        self.assertEqual(fm["sources"], ["auth_20260601", "P1T2"])
        self.assertEqual(fm["last_verified"], "2026-06-20")

    def test_no_frontmatter_returns_none(self):
        self.assertIsNone(parse_frontmatter("# Just a heading\n\nbody"))
        self.assertIsNone(parse_frontmatter("---\nno closing fence\nbody"))

    def test_unclosed_fence_returns_none(self):
        self.assertIsNone(parse_frontmatter("---\ntype: x\n"))


class RequiredFieldsTests(TestCase):
    def test_compliant_doc_has_no_missing(self):
        text = (
            "---\ntype: api\nsources:\n  - foo_2026\nlast_verified: 2026-06-20\n---\n# x\n"
        )
        self.assertEqual(missing_required_fields(text), [])

    def test_missing_each_field_reported(self):
        # type present, sources empty list, last_verified absent
        text = "---\ntype: api\nsources: []\n---\n# x\n"
        missing = missing_required_fields(text)
        self.assertIn("last_verified", missing)
        self.assertIn("sources", missing)  # empty list = no provenance

    def test_no_frontmatter_reports_all_required(self):
        text = "# bare doc\n"
        self.assertEqual(set(missing_required_fields(text)), set(REQUIRED_FM_FIELDS))


class ExemptionTests(TestCase):
    def test_synthesis_and_nav_basenames_exempt(self):
        cond = Path("/tmp/proj/conductor")
        for name in ("overview.md", "purpose.md", "log.md", "index.md"):
            self.assertTrue(is_exempt(cond / name), f"{name} should be exempt")

    def test_scoped_doc_not_exempt(self):
        self.assertFalse(is_exempt(Path("/tmp/conductor/design/architecture/system-architecture.md")))

    def test_corpus_doc_detection(self):
        cond = Path("/tmp/proj/conductor")
        self.assertTrue(is_corpus_doc(cond / "design" / "architecture" / "x.md", cond))
        self.assertTrue(is_corpus_doc(cond / "queries" / "y.md", cond))
        # product/ is global, not a provenance dir.
        self.assertFalse(is_corpus_doc(cond / "product" / "product.md", cond))
        self.assertFalse(is_corpus_doc(cond / "overview.md", cond))


class ScanTests(TestCase):
    def _conductor(self):
        root = Path(tempfile.mkdtemp()) / "conductor"
        root.mkdir(parents=True)
        return root

    def test_scan_flags_noncompliant_skips_exempt_and_passes_compliant(self):
        cond = self._conductor()

        # Compliant scoped doc — not flagged.
        (cond / "design" / "architecture").mkdir(parents=True)
        (cond / "design" / "architecture" / "system-architecture.md").write_text(
            "---\ntype: architecture\nsources:\n  - t1\nlast_verified: 2026-06-20\n---\n# x\n"
        )
        # Scoped index.md — exempt even under a provenance dir.
        (cond / "design" / "api-specs").mkdir(parents=True)
        (cond / "design" / "api-specs" / "index.md").write_text("# API Index (no frontmatter)")
        # Non-compliant scoped doc — flagged (missing last_verified; empty sources).
        (cond / "resource").mkdir()
        (cond / "resource" / "glossary.md").write_text("---\ntype: resource\nsources: []\n---\n# g\n")
        # Bare scoped doc (no frontmatter at all) — flagged.
        (cond / "requirement").mkdir()
        (cond / "requirement" / "ux.md").write_text("# ux with no frontmatter")

        findings = check_corpus_frontmatter(cond)
        flagged = {f["file"] for f in findings}
        self.assertIn("resource/glossary.md", flagged)
        self.assertIn("requirement/ux.md", flagged)
        self.assertNotIn("design/architecture/system-architecture.md", flagged)
        self.assertNotIn("design/api-specs/index.md", flagged)  # exempt index

    def test_scan_empty_when_no_corpus(self):
        self.assertEqual(check_corpus_frontmatter(Path("/nonexistent/conductor")), [])


if __name__ == "__main__":
    main()
