"""Tests for ``track-state post-loop-status`` (post-loop resumability, Strategy 1).

The command emits the durable/cheap gates each post-loop phase skips on when
resuming across a context-budget interruption:

* ``finalized``  — ``status == completed`` AND a numeric ``quality_score``.
* ``doc_synced`` — the ``docs(conductor): ...[{track_id}]`` commit marker.
* ``review.done``— the ``.conductor/post-loop.json`` sidecar's ``reviewed_range``
  equals the current ``{first}~1..{last}``.

Mirrors test_archive_docsync_gate.py (git-backed ``_make_project`` for the
doc-sync grep) + test_quality_snapshot.py (plain fixture for state-only cases).
``_get_all_shas`` reads ``commit_sha`` from state (not git), so the range cases
use a plain fixture with hand-set SHAs.
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.misc import cmd_post_loop_status


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
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    )


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _state(track_id, status="completed", quality_score=None, task_shas=None):
    """Minimal valid track-state.json. ``task_shas`` populates completed tasks
    with commit SHAs (what ``_get_all_shas`` reads)."""
    if task_shas:
        tasks = [{"name": f"T-{sha[:3]}", "status": "completed", "commit_sha": sha}
                 for sha in task_shas]
    else:
        tasks = [{"name": "T1", "status": status}]
    state = {
        "track_id": track_id,
        "type": "feature",
        "status": status,
        "description": "test track",
        "current_phase_index": 0,
        "current_task_index": 0,
        "updated_at": _recent_iso(),
        "phases": [{"name": "P1", "status": status, "tasks": tasks}],
    }
    if quality_score is not None:
        state["quality_score"] = quality_score
    return state


def _plain_track(state):
    """Non-git temp track dir (sufficient for state-only cases)."""
    d = tempfile.mkdtemp()
    track_dir = Path(d) / "office-cli_20260618"
    track_dir.mkdir(parents=True)
    save(str(track_dir), state)
    return d, track_dir


def _git_project(track_id="office-cli_20260618", status="completed"):
    """Temp git project root with conductor/tracks/<id>/ + state (for doc-sync)."""
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
    (Path(root) / "conductor").mkdir(parents=True, exist_ok=True)
    (Path(root) / "conductor" / "overview.md").write_text(f"# {msg}\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", f"docs(conductor): {msg} for track 'desc' [{track_id}]")


def _write_sidecar(track_dir, reviewed_range):
    cond = track_dir / ".conductor"
    cond.mkdir(parents=True, exist_ok=True)
    (cond / "post-loop.json").write_text(
        json.dumps({"reviewed_range": reviewed_range, "schema": 1}))


SHAS = ["aaa111", "bbb222"]
RANGE = f"{SHAS[0]}~1..{SHAS[1]}"


class FinalizedGateTests(TestCase):
    def test_fresh_track_not_finalized_not_synced_not_reviewed(self):
        d, track_dir = _plain_track(_state("t1", status="in_progress"))
        self.addCleanup(shutil.rmtree, d, True)
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertFalse(res["finalized"])
        self.assertFalse(res["doc_synced"])
        self.assertFalse(res["review"]["done"])
        self.assertIsNone(res["review"]["range"])
        self.assertEqual(res["shas_count"], 0)

    def test_finalized_when_completed_with_quality_score(self):
        d, track_dir = _plain_track(
            _state("t2", status="completed", quality_score=80, task_shas=SHAS))
        self.addCleanup(shutil.rmtree, d, True)
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertTrue(res["finalized"])
        self.assertEqual(res["status"], "completed")

    def test_not_finalized_when_completed_without_quality_score(self):
        d, track_dir = _plain_track(_state("t3", status="completed"))
        self.addCleanup(shutil.rmtree, d, True)
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertFalse(res["finalized"])

    def test_not_finalized_when_failed_even_with_quality_score(self):
        d, track_dir = _plain_track(
            _state("t4", status="failed", quality_score=40, task_shas=SHAS))
        self.addCleanup(shutil.rmtree, d, True)
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertFalse(res["finalized"])
        self.assertEqual(res["status"], "failed")


class DocSyncedGateTests(TestCase):
    def test_doc_synced_true_with_commit(self):
        root, track_dir = _git_project()
        self.addCleanup(shutil.rmtree, root, True)
        _docs_commit(root, track_dir.name)
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertTrue(res["doc_synced"])

    def test_doc_synced_false_without_commit(self):
        root, track_dir = _git_project()
        self.addCleanup(shutil.rmtree, root, True)
        (Path(root) / "x").write_text("x")  # a non-docs commit must not count
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "chore(conductor): Complete 'T1' [abc1234]")
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertFalse(res["doc_synced"])

    def test_doc_synced_false_for_other_track_id(self):
        root, track_dir = _git_project(track_id="aaa_20260101")
        self.addCleanup(shutil.rmtree, root, True)
        _docs_commit(root, "bbb_20260202")  # names a different track
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertFalse(res["doc_synced"])


class ReviewRangeGateTests(TestCase):
    def test_review_done_when_marker_matches_range(self):
        d, track_dir = _plain_track(_state("t7", status="completed", task_shas=SHAS))
        self.addCleanup(shutil.rmtree, d, True)
        _write_sidecar(track_dir, RANGE)
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertEqual(res["review"]["range"], RANGE)
        self.assertTrue(res["review"]["done"])
        self.assertEqual(res["review"]["reviewed_range"], RANGE)
        self.assertEqual(res["shas_count"], 2)

    def test_review_stale_when_marker_mismatches(self):
        d, track_dir = _plain_track(_state("t8", status="completed", task_shas=SHAS))
        self.addCleanup(shutil.rmtree, d, True)
        _write_sidecar(track_dir, "zzz999~1..yyy888")
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertFalse(res["review"]["done"])
        self.assertEqual(res["review"]["reviewed_range"], "zzz999~1..yyy888")

    def test_review_absent_when_no_marker(self):
        d, track_dir = _plain_track(_state("t9", status="completed", task_shas=SHAS))
        self.addCleanup(shutil.rmtree, d, True)
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertFalse(res["review"]["done"])
        self.assertIsNone(res["review"]["reviewed_range"])

    def test_review_absent_when_zero_shas(self):
        # Completed tasks but no commit_sha recorded -> empty range.
        d, track_dir = _plain_track(_state("t10", status="completed"))
        self.addCleanup(shutil.rmtree, d, True)
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertIsNone(res["review"]["range"])
        self.assertFalse(res["review"]["done"])
        self.assertEqual(res["shas_count"], 0)

    def test_malformed_post_loop_json_is_ignored(self):
        d, track_dir = _plain_track(_state("t11", status="completed", task_shas=SHAS))
        self.addCleanup(shutil.rmtree, d, True)
        cond = track_dir / ".conductor"
        cond.mkdir(parents=True, exist_ok=True)
        (cond / "post-loop.json").write_text("{not valid json")
        res = _out_captured(cmd_post_loop_status, str(track_dir))
        self.assertFalse(res["review"]["done"])
        self.assertIsNone(res["review"]["reviewed_range"])


if __name__ == "__main__":
    main()
