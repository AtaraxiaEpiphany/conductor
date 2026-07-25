"""Tests for ``scripts/on-anchor-write-guard.py`` — the PreToolUse:Write|Edit|MultiEdit
guard that protects the frozen anchor set (``feature-list.json`` + the tests it pins).

Feeds the guard a synthetic hook payload on stdin and asserts the
permissionDecision. Covers:

- allow when no track is locked (orchestrator between tasks).
- allow when the locked track has no frozen ``feature-list.json`` (opt-in guard
  is inert on tracks that never froze).
- deny a direct edit to ``feature-list.json`` (anchor-file integrity).
- deny an Edit that removes an assertion from a frozen test (weakening).
- deny an Edit that adds a skip marker to a frozen test (silencing).
- allow an Edit that *adds* an assertion to a frozen test (strengthening).
- allow an Edit that reformats a frozen test without changing semantics
  (no-op fingerprint — the over-gating false-positive guard).
- allow an Edit to a test file that is NOT a frozen locator.
- allow a non-Write/Edit/MultiEdit tool.
- fail-open: a malformed ``feature-list.json`` still denies a direct edit to it
  (the file exists) but allows edits to frozen locators (locators unreadable).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_REPO = Path(__file__).resolve().parent.parent
_GUARD = _REPO / "scripts" / "on-anchor-write-guard.py"


def _track_state_with_locked_task():
    """A minimal ``track-state.json`` whose cursor points at an in_progress task."""
    return {
        "track_id": "demo_20260724",
        "current_phase_index": 1,
        "current_task_index": 1,
        "phases": [
            {
                "tasks": [
                    {"name": "Implement feature", "status": "in_progress"},
                ]
            }
        ],
    }


def _feature_list(locators):
    """A frozen anchor list whose ``features`` pin the given test locators."""
    feats = []
    for i, loc in enumerate(locators, 1):
        feats.append(
            {
                "id": f"F-AC-{i}",
                "ac_ref": f"AC-{i}",
                "tc_refs": [f"TC-{i}.1"],
                "description": f"feature {i}",
                "assertion_contract": "response.status == 200",
                "test_locator": loc,
                "strength": "strong",
                "passes": "unknown",
            }
        )
    return {"track_id": "demo_20260724", "frozen_at": "2026-07-24T00:00:00Z", "features": feats}


def _setup_track(td, locators=(), frozen=True):
    """Create a locked track under ``td`` and (optionally) freeze a list over it.

    Returns the absolute track_dir so the caller can build target paths.
    """
    track_dir = Path(td) / "conductor" / "tracks" / "demo_20260724"
    (track_dir / ".conductor").mkdir(parents=True, exist_ok=True)
    (track_dir / "track-state.json").write_text(
        json.dumps(_track_state_with_locked_task())
    )
    if frozen:
        (track_dir / ".conductor" / "feature-list.json").write_text(
            json.dumps(_feature_list(locators))
        )
    return track_dir


def _probe(project_dir, tool, file_path, *, content=None, old_string=None, new_string=None, edits=None):
    """Feed the guard a synthetic payload; return its permissionDecision."""
    tool_input = {"file_path": file_path}
    if content is not None:
        tool_input["content"] = content
    if old_string is not None:
        tool_input["old_string"] = old_string
    if new_string is not None:
        tool_input["new_string"] = new_string
    if edits is not None:
        tool_input["edits"] = edits
    payload = json.dumps(
        {"tool_name": tool, "tool_input": tool_input, "cwd": str(project_dir)}
    )
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    r = subprocess.run(
        [sys.executable, str(_GUARD)],
        input=payload, capture_output=True, text=True, env=env,
    )
    out = json.loads(r.stdout)
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


class AnchorWriteGuardTests(TestCase):
    def test_allow_when_no_track_locked(self):
        # Fresh project, no track-state.json anywhere → nothing to protect.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "tests").mkdir()
            self.assertEqual(
                _probe(td, "Edit", "tests/test_users.py",
                       old_string="assert a == 1", new_string="pass"), "allow"
            )

    def test_allow_when_track_has_no_frozen_list(self):
        # Locked task, but the track never froze → guard is inert.
        with tempfile.TemporaryDirectory() as td:
            _setup_track(td, frozen=False)
            self.assertEqual(
                _probe(td, "Edit", "tests/test_users.py",
                       old_string="assert a == 1", new_string="pass"), "allow"
            )

    def test_deny_direct_edit_of_anchor_file(self):
        with tempfile.TemporaryDirectory() as td:
            track_dir = _setup_track(td, locators=["tests/test_users.py::test_TC_1_1"])
            anchor = str(track_dir / ".conductor" / "feature-list.json")
            self.assertEqual(_probe(td, "Edit", anchor,
                                    old_string='"passes": "unknown"',
                                    new_string='"passes": "pass"'), "deny")

    def test_deny_removed_assertion_in_frozen_test(self):
        with tempfile.TemporaryDirectory() as td:
            _setup_track(td, locators=["tests/test_users.py::test_TC_1_1"])
            self.assertEqual(
                _probe(td, "Edit", "tests/test_users.py",
                       old_string="def test_TC_1_1():\n    assert a == 1\n    assert b == 2\n",
                       new_string="def test_TC_1_1():\n    assert a == 1\n"), "deny"
            )

    def test_deny_added_skip_marker_in_frozen_test(self):
        with tempfile.TemporaryDirectory() as td:
            _setup_track(td, locators=["tests/test_users.py::test_TC_1_1"])
            self.assertEqual(
                _probe(td, "Edit", "tests/test_users.py",
                       old_string="def test_TC_1_1():\n    assert a == 1\n",
                       new_string="@pytest.mark.skip\ndef test_TC_1_1():\n    assert a == 1\n"),
                "deny",
            )

    def test_allow_strengthening_frozen_test(self):
        # Adding an assertion is an improvement, not a weakening.
        with tempfile.TemporaryDirectory() as td:
            _setup_track(td, locators=["tests/test_users.py::test_TC_1_1"])
            self.assertEqual(
                _probe(td, "Edit", "tests/test_users.py",
                       old_string="def test_TC_1_1():\n    assert a == 1\n",
                       new_string="def test_TC_1_1():\n    assert a == 1\n    assert b == 2\n"),
                "allow",
            )

    def test_allow_noop_reformat_of_frozen_test(self):
        # Reformatting / adding a trailing comment must NOT count as a removed
        # assertion — the over-gating false-positive the fingerprint guards.
        with tempfile.TemporaryDirectory() as td:
            _setup_track(td, locators=["tests/test_users.py::test_TC_1_1"])
            self.assertEqual(
                _probe(td, "Edit", "tests/test_users.py",
                       old_string="def test_TC_1_1():\n    assert a == 1\n",
                       new_string="def test_TC_1_1():\n    assert  a == 1   # spaced\n"),
                "allow",
            )

    def test_allow_edit_to_non_frozen_test(self):
        # A test file not named by any frozen locator is unprotected.
        with tempfile.TemporaryDirectory() as td:
            _setup_track(td, locators=["tests/test_users.py::test_TC_1_1"])
            self.assertEqual(
                _probe(td, "Edit", "tests/test_orders.py",
                       old_string="assert a == 1", new_string="pass"), "allow"
            )

    def test_allow_non_write_tool(self):
        with tempfile.TemporaryDirectory() as td:
            _setup_track(td, locators=["tests/test_users.py::test_TC_1_1"])
            self.assertEqual(_probe(td, "Read", "tests/test_users.py"), "allow")

    def test_deny_anchor_file_edit_even_when_json_malformed(self):
        # The file exists → protection #1 fires regardless of parseability.
        with tempfile.TemporaryDirectory() as td:
            track_dir = _setup_track(td, locators=[])
            anchor = track_dir / ".conductor" / "feature-list.json"
            anchor.write_text("{ not valid json")  # corrupt it
            self.assertEqual(_probe(td, "Edit", str(anchor),
                                    old_string="x", new_string="y"), "deny")

    def test_allow_multiedit_that_strengthens(self):
        # Two edits, both additive → no weakening signal across the call.
        with tempfile.TemporaryDirectory() as td:
            _setup_track(td, locators=["tests/test_users.py"])
            edits = [
                {"old_string": "assert a == 1\n", "new_string": "assert a == 1\nassert a > 0\n"},
                {"old_string": "assert b == 2\n", "new_string": "assert b == 2\nassert b > 0\n"},
            ]
            self.assertEqual(_probe(td, "MultiEdit", "tests/test_users.py", edits=edits), "allow")

    def test_deny_multiedit_that_removes_assert_in_one_element(self):
        # One weakening edit in a MultiEdit batch denies the whole call.
        with tempfile.TemporaryDirectory() as td:
            _setup_track(td, locators=["tests/test_users.py"])
            edits = [
                {"old_string": "assert a == 1\nassert a > 0\n", "new_string": "assert a == 1\n"},
                {"old_string": "assert b == 2\n", "new_string": "assert b == 2\nassert b > 0\n"},
            ]
            self.assertEqual(_probe(td, "MultiEdit", "tests/test_users.py", edits=edits), "deny")


if __name__ == "__main__":
    main()
