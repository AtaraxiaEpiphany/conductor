r"""Tests for git-notes-query.py — the audit-query utility.

The git subprocess layer (``get_git_notes_ref_list`` / ``get_git_note``) is
patched so the filtering / formatting logic is exercised deterministically
without a real notes ref. The two low-level subprocess parsers are tested
directly against canned ``git`` output.
"""
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "git_notes_query", _scripts / "git-notes-query.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Two conductor notes (different tracks) + one non-conductor note (filtered out).
_NOTES = {
    "aaa1111": {"conductor": {"track_id": "auth", "session_id": "s1"},
                "implementation": {"files_added": ["a.py"], "summary": "add auth"}},
    "bbb2222": {"conductor": {"track_id": "billing", "session_id": "s2"},
                "implementation": {"summary": "add billing"}},
    "ccc3333": {"unrelated": True},
}


def _patched():
    return (
        patch.object(_mod, "get_git_notes_ref_list", return_value=list(_NOTES)),
        patch.object(_mod, "get_git_note", side_effect=lambda r: _NOTES.get(r)),
    )


class GetAllConductorNotesTests(TestCase):
    def test_filters_out_non_conductor_notes(self):
        p1, p2 = _patched()
        with p1, p2:
            notes = _mod.get_all_conductor_notes()
        refs = {n["commit_ref"] for n in notes}
        self.assertEqual(refs, {"aaa1111", "bbb2222"})  # ccc3333 dropped
        for n in notes:
            self.assertIn("conductor", n)


class QueryFiltersTests(TestCase):
    def test_query_by_track(self):
        p1, p2 = _patched()
        buf = io.StringIO()
        with p1, p2, redirect_stdout(buf):
            _mod.query_by_track("auth")
        out = buf.getvalue()
        self.assertIn("auth", out)
        self.assertNotIn("billing", out)

    def test_query_by_session(self):
        p1, p2 = _patched()
        buf = io.StringIO()
        with p1, p2, redirect_stdout(buf):
            _mod.query_by_session("s2")
        out = buf.getvalue()
        self.assertIn("billing", out)
        self.assertNotIn("auth", out)


class SubprocessParserTests(TestCase):
    def test_ref_list_takes_second_column(self):
        # `git notes list` output: "<note-sha> <commit-sha>" per line.
        with patch("subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout="noteSHA commitAAA\nnoteSHA commitBBB\n")):
            self.assertEqual(_mod.get_git_notes_ref_list(),
                             ["commitAAA", "commitBBB"])

    def test_ref_list_failure_returns_empty(self):
        with patch("subprocess.run", return_value=SimpleNamespace(
                returncode=1, stdout="")):
            self.assertEqual(_mod.get_git_notes_ref_list(), [])

    def test_get_note_invalid_json_returns_none(self):
        with patch("subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout="not json{")):
            self.assertIsNone(_mod.get_git_note("deadbeef"))

    def test_get_note_missing_returns_none(self):
        with patch("subprocess.run", return_value=SimpleNamespace(
                returncode=1, stdout="")):
            self.assertIsNone(_mod.get_git_note("deadbeef"))


if __name__ == "__main__":
    main()
