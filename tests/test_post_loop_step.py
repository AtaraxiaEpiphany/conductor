"""Tests for ``track-state post-loop-step`` (Rail B-min post-loop spine).

``post-loop-step`` collapses the prose post-loop (templates/post-loop.md §5.0–§8.0)
into one leaf action per call. These tests pin the ordered-gate state machine
and the subtle behaviors that make the spine safe for a small-window model:

  - the two-tier doc-sync discriminator (Phase 1 corpus-writer vs Phase 2
    wiki-synthesizer — distinguished by the ``Wiki sync`` commit subject),
  - lossless resume: the reviewed-range sidecar equality skips a re-review,
  - the in-code finalize: ``halt`` on ok:false (incomplete), ``finalize`` leaf
    with ``post`` on ok:true,
  - the deferred gate honoring ``deferred_resolved`` (Keep-deferred advances).
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save, load
from scripts.track_state.dispatch import cmd_post_loop_step

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_state(**overrides):
    """A finalized-ready track: one phase, tasks completed with SHAs."""
    state = {
        "track_id": "pls",
        "type": "feature",
        "status": "completed",
        "description": "post-loop-step test",
        "quality_score": 85,
        "current_phase_index": 0,
        "current_task_index": 0,
        "updated_at": _recent_iso(),
        "phases": [
            {"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed", "commit_sha": "aaa0001"},
                {"name": "Task B", "status": "completed", "commit_sha": "aaa0002"},
            ]},
        ],
    }
    state.update(overrides)
    return state


def _git_track_dir(state):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [x] Task A\n- [x] Task B\n")
    save(d, state)
    subprocess.run(["git", "-C", d, "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"],
                   check=True, capture_output=True, env=_GIT_ENV)
    return d


def _pls(track_dir):
    """Capture cmd_post_loop_step stdout as a dict."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        cmd_post_loop_step(track_dir)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _commit(td, msg):
    subprocess.run(["git", "-C", td, "commit", "-q", "--allow-empty", "-m", msg],
                   check=True, capture_output=True, env=_GIT_ENV)


def _docs_commit(td):
    # Needle = Path(td).name, the SAME derivation docs_synced_for_track uses.
    tid = Path(td).name
    _commit(td, f"docs(conductor): Synchronize docs for track [{tid}]")


def _wiki_commit(td):
    tid = Path(td).name
    _commit(td, f"docs(conductor): Wiki sync for track [{tid}]")


def _sidecar(td, body):
    cond = Path(td, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / "post-loop.json").write_text(json.dumps(body))


class FinalizeGateTests(TestCase):
    def setUp(self):
        self.d = _git_track_dir(_make_state(status="in_progress", quality_score=None,
                                            current_phase_index=1, current_task_index=0))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_not_finalized_emits_finalize_leaf_with_post(self):
        out = _pls(self.d)
        self.assertEqual(out["action"], "finalize")
        # _finalize_track ran in-code and set quality_score → state now finalized.
        self.assertEqual(load(self.d)["status"], "completed")
        self.assertIsInstance(load(self.d)["quality_score"], (int, float))
        self.assertIn("post", out)
        self.assertTrue(any("sync-plan" in c for c in out["post"]))
        self.assertTrue(any("registry-update" in c for c in out["post"]))

    def test_finalize_post_skips_when_already_finalized(self):
        # Re-run after the in-code finalize completed the track.
        _pls(self.d)  # first call finalizes in-code
        out = _pls(self.d)  # second call: finalized gate passes → next gate
        self.assertNotEqual(out["action"], "finalize")


class HaltOnIncompleteTests(TestCase):
    def test_pending_task_halts_with_incomplete(self):
        # Not finalized (in_progress, no score) + a non-terminal task →
        # _finalize_track returns ok:false → halt.
        state = _make_state(status="in_progress", quality_score=None,
                            current_phase_index=1, current_task_index=0)
        state["phases"][0]["tasks"][0]["status"] = "pending"  # Task A unfinished
        state["phases"][0]["tasks"][0].pop("commit_sha", None)
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "halt")
        self.assertIn("incomplete", out)
        self.assertTrue(len(out["incomplete"]) > 0)


class DocSyncGateTests(TestCase):
    def setUp(self):
        self.d = _git_track_dir(_make_state())  # already finalized
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_not_doc_synced_dispatches_corpus_writer(self):
        out = _pls(self.d)
        self.assertEqual(out["action"], "dispatch")
        self.assertEqual(out["agent"], "corpus-writer")
        self.assertIn("TRACK_ID=pls", out["prompt"])

    def test_phase1_only_dispatches_wiki_synthesizer(self):
        # The two-tier discriminator: docs-synced (Phase 1) but no Phase 2 commit.
        _docs_commit(self.d)
        out = _pls(self.d)
        self.assertEqual(out["action"], "dispatch")
        self.assertEqual(out["agent"], "wiki-synthesizer")

    def test_both_phases_done_skips_doc_sync(self):
        _docs_commit(self.d)
        _wiki_commit(self.d)
        out = _pls(self.d)
        # Past doc-sync → review gate (shas present, not reviewed) → code-reviewer.
        self.assertEqual(out["action"], "dispatch")
        self.assertEqual(out["agent"], "code-reviewer")


class ReviewGateTests(TestCase):
    def setUp(self):
        self.d = _git_track_dir(_make_state())
        _docs_commit(self.d)
        _wiki_commit(self.d)
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_shas_not_reviewed_dispatches_code_reviewer_with_post(self):
        out = _pls(self.d)
        self.assertEqual(out["action"], "dispatch")
        self.assertEqual(out["agent"], "code-reviewer")
        self.assertEqual(out["range"], "aaa0001~1..aaa0002")
        self.assertEqual(out["shas_count"], 2)
        self.assertIn("REVISION_RANGE=aaa0001~1..aaa0002", out["prompt"])
        # The stamp `post` writes the reviewed-range sidecar.
        self.assertIn("post", out)
        self.assertTrue(any("post-loop.json" in c for c in out["post"]))
        self.assertTrue(any("reviewed_range" in c for c in out["post"]))

    def test_review_done_skips_to_archive(self):
        _sidecar(self.d, {"schema": 2, "reviewed_range": "aaa0001~1..aaa0002"})
        out = _pls(self.d)
        self.assertEqual(out["action"], "archive_ask")

    def test_changed_range_forces_re_review(self):
        # A stale reviewed_range (range shifted) → re-review, not skip.
        _sidecar(self.d, {"schema": 2, "reviewed_range": "aaa0001~1..aaa0000"})
        out = _pls(self.d)
        self.assertEqual(out["action"], "dispatch")
        self.assertEqual(out["agent"], "code-reviewer")


class NoShasSkipsReviewTests(TestCase):
    def test_zero_shas_skips_review_to_archive(self):
        # Completed tasks carry no commit_sha → _get_all_shas empty → no review.
        state = _make_state()
        for t in state["phases"][0]["tasks"]:
            t.pop("commit_sha", None)
        d = _git_track_dir(state)
        _docs_commit(d)
        _wiki_commit(d)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "archive_ask")


class ArchiveAndDoneTests(TestCase):
    def test_not_archived_emits_archive_ask(self):
        d = _git_track_dir(_make_state())
        _docs_commit(d)
        _wiki_commit(d)
        _sidecar(d, {"schema": 2, "reviewed_range": "aaa0001~1..aaa0002"})
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "archive_ask")
        self.assertIn("decision", out)
        labels = [o["label"] for o in out["decision"]["options"]]
        self.assertEqual(labels, ["Archive", "Keep active", "Delete"])
        # Keep active + Delete HALT; Archive loops.
        self.assertEqual(out["decision"]["next"]["Keep active"], "HALT")
        self.assertEqual(out["decision"]["next"]["Delete"], "HALT")
        self.assertEqual(out["decision"]["next"]["Archive"], "post-loop-step")

    def test_archived_emits_done(self):
        d = _git_track_dir(_make_state(status="archived"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "done")


class DeferredGateTests(TestCase):
    def test_deferred_non_empty_emits_deferred_ask(self):
        state = _make_state()
        state["phases"][0]["tasks"][0]["status"] = "deferred"
        state["phases"][0]["tasks"][0]["commit_sha"] = "ddd0001"
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "deferred_ask")
        self.assertIn("decision", out)
        labels = [o["label"] for o in out["decision"]["options"]]
        self.assertEqual(labels, ["Verify all", "Skip all", "Keep deferred"])

    def test_deferred_resolved_advances_past_gate(self):
        # Keep-deferred stamped the sidecar → gate passes → proceeds to finalize.
        state = _make_state(status="in_progress", quality_score=None,
                            current_phase_index=1, current_task_index=0)
        state["phases"][0]["tasks"][0]["status"] = "deferred"
        state["phases"][0]["tasks"][0]["commit_sha"] = "ddd0001"
        d = _git_track_dir(state)
        _sidecar(d, {"schema": 2, "deferred_resolved": True})
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        # Deferred gate passed → finalize gate (not finalized) → finalize leaf.
        self.assertEqual(out["action"], "finalize")


class ErrorGateTests(TestCase):
    def test_unhealthy_emits_error(self):
        # No track-state.json + no git repo → ensure_healthy returns None → error.
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "error")


if __name__ == "__main__":
    main()
