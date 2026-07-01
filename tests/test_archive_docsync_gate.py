"""Tests for the archive doc-sync gate (#1).

cmd_archive refuses to archive a track until a doc-sync commit
(``docs(conductor): ... [{TRACK_ID}]``) exists — evidence the post-loop DOC SYNC
phase ran and durable findings reached the wiki corpus. ``--force`` overrides.
The lint backstop (check_docsync_before_archive) catches tracks flipped to
'archived' outside the gate.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.quality import cmd_archive
from scripts.track_state.git_ops import docs_synced_for_track

# Hyphenated module name — load the linter by path (matches test_f1_state_lock_linter).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "lint_track_state",
    Path(__file__).resolve().parent.parent / "scripts" / "lint-track-state.py",
)
_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lint)
check_docsync_before_archive = _lint.check_docsync_before_archive


def _out_captured(fn, *args, **kwargs):
    """Capture stdout JSON from a command function. Returns parsed dict."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _git(repo, *args):
    """Run git in *repo* deterministically (no gpg, fixed identity)."""
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    )


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _state(track_id, status="completed"):
    return {
        "track_id": track_id,
        "type": "feature",
        "status": status,
        "description": "test track",
        "current_phase_index": 0,
        "current_task_index": 0,
        "updated_at": _recent_iso(),
        "phases": [{"name": "P1", "status": status,
                    "tasks": [{"name": "T1", "status": status}]}],
    }


def _make_project(track_id="office-cli_20260618", status="completed"):
    """Temp git project root with conductor/tracks/<id>/ + a completed state."""
    root = tempfile.mkdtemp()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    cond = Path(root) / "conductor"
    cond.mkdir(parents=True)
    # tracks.md at the conductor root lets _resolve_conductor_root locate the
    # archive/ sibling dir — matches real layout.
    (cond / "tracks.md").write_text("# Tracks Registry\n")
    track_dir = cond / "tracks" / track_id
    track_dir.mkdir(parents=True)
    save(str(track_dir), _state(track_id, status))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root, track_dir


def _docs_commit(root, track_id, msg="Wiki sync"):
    """Make a doc-sync commit naming the track (what doc-syncer Phase 2 produces)."""
    # Touch a file so the commit is non-empty.
    (Path(root) / "conductor" / "overview.md").parent.mkdir(parents=True, exist_ok=True)
    (Path(root) / "conductor" / "overview.md").write_text(f"# {msg}\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q",
         "-m", f"docs(conductor): {msg} for track 'desc' [{track_id}]")


class ArchiveDocsyncGateTests(TestCase):
    def test_refuses_without_docsync_commit(self):
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        res = _out_captured(cmd_archive, str(track_dir))
        self.assertFalse(res["ok"])
        self.assertIn("doc-sync commit", res["error"])
        # State must be unchanged.
        self.assertEqual(json.loads((track_dir / "track-state.json").read_text())["status"],
                         "completed")

    def test_succeeds_with_docsync_commit(self):
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        _docs_commit(root, track_dir.name)
        res = _out_captured(cmd_archive, str(track_dir))
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "archived")
        self.assertNotIn("warning", res)
        # Relocated: old dir gone, state now lives under archive/<id>.
        self.assertFalse(track_dir.exists())
        archived_dir = Path(res["track_dir"])
        self.assertEqual(archived_dir, Path(root) / "conductor" / "archive" / track_dir.name)
        self.assertEqual(json.loads((archived_dir / "track-state.json").read_text())["status"],
                         "archived")

    def test_force_archives_without_commit_and_warns(self):
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        res = _out_captured(cmd_archive, str(track_dir), force=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "archived")
        self.assertIn("warning", res)
        # Still relocated even under --force.
        self.assertFalse(track_dir.exists())
        self.assertEqual(Path(res["track_dir"]),
                         Path(root) / "conductor" / "archive" / track_dir.name)

    def test_force_synced_emits_no_warning(self):
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        _docs_commit(root, track_dir.name)
        res = _out_captured(cmd_archive, str(track_dir), force=True)
        self.assertTrue(res["ok"])
        self.assertNotIn("warning", res)
        self.assertFalse(track_dir.exists())
        self.assertEqual(Path(res["track_dir"]),
                         Path(root) / "conductor" / "archive" / track_dir.name)

    def test_refuses_non_completed_status(self):
        root, track_dir = _make_project(status="in_progress")
        self.addCleanup(__import__("shutil").rmtree, root, True)
        res = _out_captured(cmd_archive, str(track_dir))
        self.assertFalse(res["ok"])
        self.assertIn("Cannot archive", res["error"])

    def test_archive_relocates_dir(self):
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        _docs_commit(root, track_dir.name)
        res = _out_captured(cmd_archive, str(track_dir))
        self.assertTrue(res["ok"])
        # Old tracks/<id> is gone; archive/<id> holds the relocated state.
        self.assertFalse(track_dir.exists())
        archived_dir = Path(root) / "conductor" / "archive" / track_dir.name
        self.assertTrue(archived_dir.is_dir())
        self.assertEqual(res["track_dir"], str(archived_dir))
        self.assertEqual(res["archived_dir"], str(archived_dir))
        self.assertEqual(json.loads((archived_dir / "track-state.json").read_text())["status"],
                         "archived")

    def test_archive_idempotent_when_already_moved(self):
        # Simulate re-entry after a move-before-commit interruption: the track
        # is already archived AND already relocated under archive/. Re-running
        # archive must be a no-op (ok), not an error or a re-move.
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        _docs_commit(root, track_dir.name)
        first = _out_captured(cmd_archive, str(track_dir))
        self.assertTrue(first["ok"])
        archived_dir = Path(first["track_dir"])
        second = _out_captured(cmd_archive, str(archived_dir))
        self.assertTrue(second["ok"])
        self.assertEqual(second["status"], "archived")
        self.assertEqual(Path(second["track_dir"]), archived_dir)
        self.assertIn("note", second)

    def test_archive_refuses_destination_collision(self):
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        _docs_commit(root, track_dir.name)
        # Pre-create the archive destination so the move would clobber it.
        dest = Path(root) / "conductor" / "archive" / track_dir.name
        dest.mkdir(parents=True)
        (dest / "blocker").write_text("pre-existing")
        res = _out_captured(cmd_archive, str(track_dir))
        self.assertFalse(res["ok"])
        self.assertIn("destination already exists", res["error"])
        # Original track untouched on collision.
        self.assertTrue(track_dir.exists())
        self.assertEqual(json.loads((track_dir / "track-state.json").read_text())["status"],
                         "completed")


class DocsSyncedHelperTests(TestCase):
    def test_true_when_commit_names_track(self):
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        _docs_commit(root, track_dir.name)
        self.assertTrue(docs_synced_for_track(str(track_dir)))

    def test_false_when_no_docs_commit(self):
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        # A non-docs commit must NOT count.
        (Path(root) / "x").write_text("x")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "chore(conductor): Complete 'T1' [abc1234]")
        self.assertFalse(docs_synced_for_track(str(track_dir)))

    def test_false_when_commit_names_other_track(self):
        root, track_dir = _make_project(track_id="aaa_20260101")
        self.addCleanup(__import__("shutil").rmtree, root, True)
        _docs_commit(root, "bbb_20260202")  # names a different track
        self.assertFalse(docs_synced_for_track(str(track_dir)))

    def test_false_in_non_git_dir(self):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        self.assertFalse(docs_synced_for_track(d))


class LintDocsyncBackstopTests(TestCase):
    def test_warns_archived_without_commit(self):
        root, track_dir = _make_project(status="archived")
        self.addCleanup(__import__("shutil").rmtree, root, True)
        ok, msg = check_docsync_before_archive(track_dir)
        self.assertFalse(ok)
        self.assertIn(track_dir.name, msg)

    def test_passes_archived_with_commit(self):
        root, track_dir = _make_project(status="archived")
        self.addCleanup(__import__("shutil").rmtree, root, True)
        _docs_commit(root, track_dir.name)
        ok, msg = check_docsync_before_archive(track_dir)
        self.assertTrue(ok)
        self.assertIsNone(msg)

    def test_noop_for_non_archived(self):
        root, track_dir = _make_project(status="in_progress")
        self.addCleanup(__import__("shutil").rmtree, root, True)
        ok, msg = check_docsync_before_archive(track_dir)
        self.assertTrue(ok)
        self.assertIsNone(msg)


if __name__ == "__main__":
    main()
