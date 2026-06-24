"""Tests for the SessionStart wiki-drift GC scan (O5).

get_wiki_drift_warnings runs at session start and surfaces advisory drift
signals (stale overview, missing provenance frontmatter, broken overview
wikilinks) as additional_context — the harness-engineering GC pillar applied
continuously rather than only post-loop.
"""
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

# Hyphenated module name — load by path with scripts/ (for `from lib.*`) on sys.path.
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("session_start", str(SCRIPTS / "session-start.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
get_wiki_drift_warnings = _mod.get_wiki_drift_warnings


def _fresh_conductor():
    root = Path(tempfile.mkdtemp())
    cond = root / "conductor"
    cond.mkdir()
    (cond / "overview.md").write_text("# Overview\n\nLast updated: now\n")
    return root, cond


def _age_file(path: Path, days: int):
    old = time.time() - days * 86400
    os.utime(path, (old, old))


class WikiDriftTests(TestCase):
    def test_no_conductor_returns_empty(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(get_wiki_drift_warnings(root), "")

    def test_clean_wiki_returns_empty(self):
        root, cond = _fresh_conductor()
        # A compliant scoped doc + overview with only valid links.
        arch = cond / "design" / "architecture"
        arch.mkdir(parents=True)
        (arch / "system-architecture.md").write_text(
            "---\ntype: architecture\nsources:\n  - t1\nlast_verified: 2026-06-20\n---\n# x\n"
        )
        self.assertEqual(get_wiki_drift_warnings(root), "")

    def test_stale_overview_warns(self):
        root, cond = _fresh_conductor()
        _age_file(cond / "overview.md", 40)
        out = get_wiki_drift_warnings(root)
        self.assertIn("Wiki drift", out)
        self.assertIn("stale", out)

    def test_missing_frontmatter_warns(self):
        root, cond = _fresh_conductor()
        (cond / "resource").mkdir()
        (cond / "resource" / "glossary.md").write_text("# no frontmatter")
        out = get_wiki_drift_warnings(root)
        self.assertIn("provenance frontmatter", out)

    def test_broken_overview_wikilink_warns(self):
        root, cond = _fresh_conductor()
        (cond / "overview.md").write_text(
            "# Overview\n\nSee [[conductor/design/does-not-exist]] for details.\n"
        )
        out = get_wiki_drift_warnings(root)
        self.assertIn("broken [[wikilink]]", out)

    def test_resolved_wikilink_not_flagged(self):
        root, cond = _fresh_conductor()
        (cond / "design").mkdir()
        (cond / "design" / "real.md").write_text("# real\n")
        (cond / "overview.md").write_text(
            "# Overview\n\nSee [[conductor/design/real]] for details.\n"
        )
        self.assertNotIn("broken [[wikilink]]", get_wiki_drift_warnings(root))


if __name__ == "__main__":
    main()
