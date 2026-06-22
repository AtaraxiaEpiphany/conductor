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
    track_dir = Path(root) / "conductor" / "tracks" / track_id
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
        self.assertEqual(json.loads((track_dir / "track-state.json").read_text())["status"],
                         "archived")

    def test_force_archives_without_commit_and_warns(self):
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        res = _out_captured(cmd_archive, str(track_dir), force=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "archived")
        self.assertIn("warning", res)

    def test_force_synced_emits_no_warning(self):
        root, track_dir = _make_project()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        _docs_commit(root, track_dir.name)
        res = _out_captured(cmd_archive, str(track_dir), force=True)
        self.assertTrue(res["ok"])
        self.assertNotIn("warning", res)

    def test_refuses_non_completed_status(self):
        root, track_dir = _make_project(status="in_progress")
        self.addCleanup(__import__("shutil").rmtree, root, True)
        res = _out_captured(cmd_archive, str(track_dir))
        self.assertFalse(res["ok"])
        self.assertIn("Cannot archive", res["error"])


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
