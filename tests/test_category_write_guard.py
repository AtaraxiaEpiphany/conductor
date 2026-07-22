"""Tests for ``scripts/on-category-write-guard.py`` — the PreToolUse:Write guard.

Feeds the guard a synthetic hook payload on stdin and asserts the
permissionDecision. Covers: deny when a category index.md is missing; allow once
it exists (after the helper seeds it); allow for non-category paths; allow for a
Write of the category index.md itself; allow for non-Write tools.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_REPO = Path(__file__).resolve().parent.parent
_GUARD = _REPO / "scripts" / "on-category-write-guard.py"
_SEED = _REPO / "scripts" / "seed-category-doc.py"


def _probe(project_dir, tool, file_path):
    payload = json.dumps(
        {"tool_name": tool, "tool_input": {"file_path": file_path}, "cwd": str(project_dir)}
    )
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    r = subprocess.run(
        [sys.executable, str(_GUARD)], input=payload, capture_output=True, text=True, env=env,
    )
    out = json.loads(r.stdout)
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


def _seed(project_dir, rel):
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    subprocess.run(
        [sys.executable, str(_SEED), rel, "api"],
        capture_output=True, text=True, env=env,
    )


class CategoryWriteGuardTests(TestCase):
    def test_deny_when_category_index_missing(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                _probe(td, "Write", "conductor/design/api-specs/auth.md"), "deny"
            )

    def test_allow_after_helper_seeds_category(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                _probe(td, "Write", "conductor/design/api-specs/auth.md"), "deny"
            )
            _seed(td, "conductor/design/api-specs/auth.md")
            self.assertEqual(
                _probe(td, "Write", "conductor/design/api-specs/auth.md"), "allow"
            )

    def test_allow_non_category_path(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                _probe(td, "Write", "conductor/tracks/x/spec.md"), "allow"
            )

    def test_allow_writing_category_index_itself(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                _probe(td, "Write", "conductor/design/database/index.md"), "allow"
            )

    def test_allow_non_write_tool(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                _probe(td, "Read", "conductor/design/api-specs/auth.md"), "allow"
            )


if __name__ == "__main__":
    main()
