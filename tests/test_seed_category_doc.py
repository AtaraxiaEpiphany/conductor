"""Tests for ``scripts/seed-category-doc.py`` — the category-seed helper.

Covers the idempotent mkdir + category-index-create contract and the exit
discipline (bad type → exit 1). Runs the script end-to-end against a tmp project
root (``CLAUDE_PROJECT_DIR``) so no repo state is touched. Mirrors the subprocess
style of ``test_index_maps.py``.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "seed-category-doc.py"


def _run(project_dir, doc_path, doc_type):
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), doc_path, doc_type],
        capture_output=True, text=True, env=env,
    )


class SeedTests(TestCase):
    def test_creates_dir_and_category_index_on_first_seed(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(td, "conductor/design/api-specs/auth-login.md", "api")
            self.assertEqual(r.returncode, 0, r.stderr)
            summary = json.loads(r.stdout)
            self.assertEqual(summary["category_index"], "created")
            self.assertEqual(summary["category"], "conductor/design/api-specs")
            idx = Path(td) / "conductor" / "design" / "api-specs" / "index.md"
            self.assertTrue(idx.exists())
            self.assertIn("# API Specifications", idx.read_text())
            self.assertIn("| Name | Path | Summary |", idx.read_text())

    def test_second_seed_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            _run(td, "conductor/design/api-specs/a.md", "api")
            r = _run(td, "conductor/design/api-specs/b.md", "api")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["category_index"], "existing")

    def test_non_category_doc_reports_none(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(td, "conductor/design/tech-stack.md", "concept")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["category_index"], "none")

    def test_bad_type_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run(td, "conductor/design/api-specs/x.md", "bogus")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("unknown frontmatter type", r.stderr)


if __name__ == "__main__":
    main()
