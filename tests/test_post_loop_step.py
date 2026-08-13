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
from scripts.track_state.dispatch import (
    cmd_post_loop_step, cmd_post_loop_review,
    _post_loop_stamp_line, _post_loop_advisory_post,
    _post_loop_fix_sentinel,
)

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


def _review(track_dir, status, critical=None, high=None):
    """Capture cmd_post_loop_review stdout as a dict."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        cmd_post_loop_review(track_dir, status, critical=critical, high=high)
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


def _sidecar_post_sync(td, **extra):
    """Sidecar with the §6.0 advisory / §6.5 lint / §7.5 digest gates stamped
    (truthy ⇒ fired) so a test lands PAST them — at §7.0 review or §8.0 archive.
    Caller passes ``reviewed_range=`` / ``digest_shown=False`` etc. to open a
    specific later gate."""
    body = {"schema": 2, "advisory_diff_shown": True, "lint_done": True,
            "digest_shown": True}
    body.update(extra)
    _sidecar(td, body)


def _review_result(td, findings):
    """Write a minimal review-result.json with the given findings list."""
    cond = Path(td, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / "review-result.json").write_text(
        json.dumps({"status": "SUCCESS", "findings": findings}))


def _drain_chunk(td, file_path):
    """Drop the per-chunk ``.done`` sentinel marking ``file_path`` applied."""
    sentinel = _post_loop_fix_sentinel(td, file_path)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.touch()


def _apply_fixes_fixture(findings):
    """Finalized + doc-synced + advisory/lint done + review done (range stamped),
    digest NOT shown, with a review-result.json carrying ``findings``. Lands the
    spine at the §7.0 step-4 apply_fixes gate."""
    d = _git_track_dir(_make_state())
    _docs_commit(d)
    _wiki_commit(d)
    _sidecar_post_sync(d, digest_shown=False,
                       reviewed_range="aaa0001~1..aaa0002")
    if findings is not None:
        _review_result(d, findings)
    return d


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


class CoveragePctTypeTests(TestCase):
    """Regression: ``evidence.coverage_pct`` reaching state as a numeric STRING
    (via result.json propagation / manual edits — the schema does not enforce
    int) must not crash ``_compute_quality_score``'s ``sum()`` with an int+str
    TypeError. Numeric strings still count toward the score; non-numeric junk is
    skipped. The crash surfaced as ``post-loop-step`` failing the first time it
    finalized a track whose evidence held a string coverage_pct."""

    def test_string_coverage_pct_does_not_crash_finalize(self):
        # Not finalized → cmd_post_loop_step runs _finalize_track inline →
        # _compute_quality_score sums evidence.coverage_pct. With a string
        # value, a bare sum() did int(0) + "85" → TypeError (post-loop crash).
        state = _make_state(status="in_progress", quality_score=None,
                            current_phase_index=1, current_task_index=0)
        state["phases"][0]["tasks"][0]["evidence"] = {
            "coverage_pct": "85", "tc_coverage": "", "deviations": 0}
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        # No exception → finalize leaf emitted (ok:true), score computed.
        self.assertEqual(out["action"], "finalize")
        self.assertIsInstance(load(d)["quality_score"], (int, float))

    def test_non_numeric_coverage_pct_is_skipped_not_crash(self):
        # "n/a" / "" / None must be skipped, not summed — and a real numeric
        # value on another task still contributes.
        state = _make_state(status="in_progress", quality_score=None,
                            current_phase_index=1, current_task_index=0)
        state["phases"][0]["tasks"][0]["evidence"] = {
            "coverage_pct": "n/a", "tc_coverage": "", "deviations": 0}
        state["phases"][0]["tasks"][1]["evidence"] = {
            "coverage_pct": "90", "tc_coverage": "", "deviations": 0}
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "finalize")
        score = load(d)["quality_score"]
        self.assertIsInstance(score, (int, float))
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)


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
        # Stamp advisory+lint so the spine lands at §7.0 review (digest stays
        # stamped too — it is past §7.0, irrelevant to reaching the review gate).
        _sidecar_post_sync(self.d)
        out = _pls(self.d)
        # Past doc-sync → advisory → lint → review gate (shas present, not reviewed).
        self.assertEqual(out["action"], "dispatch_review")
        self.assertEqual(out["agent"], "code-reviewer")


class ReviewGateTests(TestCase):
    def setUp(self):
        self.d = _git_track_dir(_make_state())
        _docs_commit(self.d)
        _wiki_commit(self.d)
        # Stamp advisory+lint so the spine lands at §7.0 review (digest stays
        # stamped — it is past §7.0, irrelevant here).
        _sidecar_post_sync(self.d)
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_shas_not_reviewed_emits_dispatch_review_no_post(self):
        out = _pls(self.d)
        self.assertEqual(out["action"], "dispatch_review")
        self.assertEqual(out["agent"], "code-reviewer")
        self.assertEqual(out["range"], "aaa0001~1..aaa0002")
        self.assertEqual(out["shas_count"], 2)
        self.assertIn("REVISION_RANGE=aaa0001~1..aaa0002", out["prompt"])
        # No `post`: the §7.0 gate-stamp is owned by `post-loop-review --status`,
        # not a teleoperator-judged post (the FAILURE→no-stamp call is in code).
        self.assertNotIn("post", out)
        self.assertNotIn("post_on", out)

    def test_review_done_skips_to_archive(self):
        _sidecar_post_sync(self.d, reviewed_range="aaa0001~1..aaa0002")
        out = _pls(self.d)
        self.assertEqual(out["action"], "archive_ask")

    def test_changed_range_forces_re_review(self):
        # A stale reviewed_range (range shifted) → re-review, not skip.
        _sidecar_post_sync(self.d, reviewed_range="aaa0001~1..aaa0000")
        out = _pls(self.d)
        self.assertEqual(out["action"], "dispatch_review")
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
        # Stamp advisory+lint+digest so the no-shas path reaches §8.0 archive.
        _sidecar_post_sync(d)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "archive_ask")


class ArchiveAndDoneTests(TestCase):
    def test_not_archived_emits_archive_ask(self):
        d = _git_track_dir(_make_state())
        _docs_commit(d)
        _wiki_commit(d)
        # Every gate satisfied: advisory + lint + digest + review stamped.
        _sidecar_post_sync(d, reviewed_range="aaa0001~1..aaa0002")
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


class AdvisoryGateTests(TestCase):
    """§6.0 advisory wiki-differ — non-blocking; advances on any return."""

    def setUp(self):
        self.d = _git_track_dir(_make_state())
        _docs_commit(self.d)
        _wiki_commit(self.d)
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_not_advisory_dispatches_wiki_differ_always(self):
        out = _pls(self.d)
        self.assertEqual(out["action"], "dispatch_advisory")
        self.assertEqual(out["agent"], "wiki-differ")
        self.assertIn("PROJECT_DIR=", out["prompt"])
        # Advisory is non-blocking → post_on="always" (advances on FAILURE too).
        self.assertEqual(out["post_on"], "always")
        self.assertIn("post", out)

    def test_advisory_done_advances_to_lint(self):
        _sidecar_post_sync(self.d, advisory_diff_shown=True, lint_done=False,
                           digest_shown=True)
        out = _pls(self.d)
        # Advisory passed → §6.5 lint gate (lint_done False) → doc-linter.
        self.assertEqual(out["action"], "dispatch")
        self.assertEqual(out["agent"], "doc-linter")
        self.assertEqual(out["post_on"], "always")


class LintGateTests(TestCase):
    """§6.5 doc-linter — non-blocking; the gate keys on lint_done."""

    def setUp(self):
        self.d = _git_track_dir(_make_state())
        _docs_commit(self.d)
        _wiki_commit(self.d)
        _sidecar_post_sync(self.d, lint_done=False, digest_shown=True)
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_not_lint_dispatches_doc_linter(self):
        out = _pls(self.d)
        self.assertEqual(out["action"], "dispatch")
        self.assertEqual(out["agent"], "doc-linter")
        self.assertEqual(out["post_on"], "always")
        self.assertTrue(any("lint_done" in c for c in out["post"]))

    def test_lint_done_advances_to_review(self):
        # Stamp lint_done too → spine reaches §7.0 review (shas present, unsynced).
        _sidecar_post_sync(self.d, digest_shown=True)
        out = _pls(self.d)
        self.assertEqual(out["action"], "dispatch_review")
        self.assertEqual(out["agent"], "code-reviewer")


class DigestGateTests(TestCase):
    """§7.5 comprehension digest — no dispatch; announce + stamp."""

    def setUp(self):
        self.d = _git_track_dir(_make_state())
        _docs_commit(self.d)
        _wiki_commit(self.d)
        # Advisory + lint done; review done (matching range); digest NOT shown.
        _sidecar_post_sync(self.d, digest_shown=False,
                           reviewed_range="aaa0001~1..aaa0002")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_not_digest_emits_digest_leaf(self):
        out = _pls(self.d)
        self.assertEqual(out["action"], "digest")
        self.assertEqual(out["post_on"], "always")
        self.assertIn("post", out)
        # Composed from in-context data: the track description + outcome counts.
        self.assertIn("post-loop-step test", out["digest"])
        self.assertIn("Outcome:", out["digest"])
        self.assertIn("2 done", out["digest"])  # both fixture tasks completed

    def test_digest_includes_review_findings_when_present(self):
        # Drop a review-result.json; the digest surfaces the top finding.
        cond = Path(self.d, ".conductor")
        (cond / "review-result.json").write_text(json.dumps({
            "status": "SUCCESS",
            "findings": [
                {"severity": "Critical", "title": "Off-by-one", "file": "src/a.py"},
                {"severity": "Low", "title": "Typo", "file": "docs/b.md"},
            ],
        }))
        out = _pls(self.d)
        self.assertIn("Read this first", out["digest"])
        # Critical ranks above Low → surfaced first.
        self.assertLess(out["digest"].index("Off-by-one"),
                        out["digest"].index("Typo"))

    def test_digest_done_advances_to_archive(self):
        _sidecar_post_sync(self.d,
                           reviewed_range="aaa0001~1..aaa0002")  # digest_shown=True
        out = _pls(self.d)
        self.assertEqual(out["action"], "archive_ask")


class GateOrderingTests(TestCase):
    """The new gates fire in template order: advisory → lint → review → digest."""

    def test_order_advisory_then_lint_then_review(self):
        d = _git_track_dir(_make_state())
        _docs_commit(d)
        _wiki_commit(d)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # No sidecar → advisory first.
        self.assertEqual(_pls(d)["agent"], "wiki-differ")
        # Advisory stamped → lint.
        _sidecar_post_sync(d, advisory_diff_shown=True, lint_done=False,
                           digest_shown=True)
        self.assertEqual(_pls(d)["agent"], "doc-linter")
        # Lint stamped → review (shas present).
        _sidecar_post_sync(d, digest_shown=True)
        out = _pls(d)
        self.assertEqual(out["action"], "dispatch_review")
        self.assertEqual(out["agent"], "code-reviewer")


class ApplyFixesGateTests(TestCase):
    """§7.0 step 4 — chunked apply_fixes (P5). Drains one Critical/High fixable
    chunk per call; the per-file ``.done`` sentinel is the durable marker."""

    _FINDINGS = [
        {"severity": "Critical", "title": "Null deref", "file": "src/b.py",
         "lines": "L10-L12", "suggestion": "guard None"},
        {"severity": "High", "title": "Race", "file": "src/a.py",
         "lines": "L4", "suggestion": "add lock"},
        {"severity": "Low", "title": "Typo", "file": "src/a.py",
         "lines": "L1", "suggestion": "fix spelling"},  # Low → NOT auto-fixable
    ]

    def test_critical_findings_emit_apply_fixes_for_first_file(self):
        # Sorted file order → src/a.py first; the Low finding in a.py is dropped
        # (only Critical/High are fixable), so the chunk carries JUST the High one.
        d = _apply_fixes_fixture(self._FINDINGS)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "apply_fixes")
        self.assertEqual(out["agent"], "apply-fixes")
        self.assertIn("FILE=src/a.py", out["prompt"])
        self.assertIn('"High"', out["prompt"])
        self.assertNotIn('"Low"', out["prompt"])
        # post drops the per-chunk sentinel; post_on defaults to non_failure
        # (a failed chunk is NOT marked done → re-entry re-dispatches it).
        # Sentinel filename is the percent-encoded path (collision-free — see
        # test_sentinel_encoding_is_collision_free).
        self.assertTrue(any("post-loop-fixes" in c for c in out["post"]))
        self.assertTrue(any("src%2Fa.py.done" in c for c in out["post"]))
        self.assertNotIn("post_on", out)

    def test_sentinel_encoding_is_collision_free(self):
        # Two distinct paths must NOT share one sentinel: the old
        # [^A-Za-z0-9._-]→_ sanitizer collapsed `src/lib.py` and `src_lib.py`
        # onto the same `src_lib.py.done`, silently skipping the second chunk
        # (`_post_loop_next_apply_fixes` filters by sentinel.exists()). The
        # percent-encoded encoding is injective on distinct inputs.
        from track_state.dispatch import _post_loop_fix_sentinel
        d = _apply_fixes_fixture(self._FINDINGS)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        a = _post_loop_fix_sentinel(d, "src/lib.py")
        b = _post_loop_fix_sentinel(d, "src_lib.py")
        self.assertNotEqual(a, b)
        self.assertTrue(a.name.endswith(".done"))
        # `/` must survive as %2F (distinct from a literal `_`).
        self.assertIn("%2F", a.name)

    def test_drained_chunk_advances_to_next_file(self):
        d = _apply_fixes_fixture(self._FINDINGS)
        _drain_chunk(d, "src/a.py")  # a.py done → next is b.py
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "apply_fixes")
        self.assertIn("FILE=src/b.py", out["prompt"])

    def test_all_drained_advances_to_digest(self):
        d = _apply_fixes_fixture(self._FINDINGS)
        _drain_chunk(d, "src/a.py")
        _drain_chunk(d, "src/b.py")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        # All chunks drained → §7.5 digest (digest_shown was False in the fixture).
        self.assertEqual(out["action"], "digest")

    def test_medium_low_only_skips_apply_fixes(self):
        # Medium/Low are "approve with comments" — not auto-fixable → straight to digest.
        d = _apply_fixes_fixture([
            {"severity": "Medium", "title": "Style", "file": "src/a.py",
             "lines": "L1", "suggestion": "rename"},
            {"severity": "Low", "title": "Typo", "file": "src/b.py",
             "lines": "L1", "suggestion": "fix"},
        ])
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "digest")

    def test_no_review_result_skips_apply_fixes(self):
        # No review-result.json → nothing fixable → digest (and no crash).
        d = _apply_fixes_fixture(None)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "digest")

    def test_unsuggested_critical_not_fixable(self):
        # A Critical finding with an empty suggestion is not auto-fixable.
        d = _apply_fixes_fixture([
            {"severity": "Critical", "title": "Mystery", "file": "src/a.py",
             "lines": "L1", "suggestion": ""},
        ])
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _pls(d)
        self.assertEqual(out["action"], "digest")


class SidecarMergeTests(TestCase):
    """The stamp `post` MERGES into the sidecar — a later gate's stamp must NOT
    clobber an earlier gate's marker (the lossless-resume invariant)."""

    def test_stamp_line_merges_not_overwrites(self):
        # The stamp is a python3 -c one-liner using dict .update (merge), not a
        # `tee` heredoc (overwrite).
        line = _post_loop_stamp_line("/tmp/x",
                                     {"schema": 2, "advisory_diff_shown": True})
        self.assertIn("python3 -c", line)
        self.assertIn(".update(", line)
        self.assertNotIn("tee ", line)

    def test_advisory_stamp_preserves_reviewed_range(self):
        # Functionally: a sidecar already holding reviewed_range survives the
        # advisory stamp (run the real post line as shell against a temp dir).
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        cond = Path(d, ".conductor")
        cond.mkdir()
        sidecar = cond / "post-loop.json"
        sidecar.write_text(json.dumps(
            {"schema": 2, "reviewed_range": "aaa0001~1..aaa0002"}))
        # Run only the stamp line (post[0]); skip the git commit (post[1]).
        stamp_line = _post_loop_advisory_post(d)[0]
        subprocess.run(["bash", "-c", stamp_line], check=True, capture_output=True)
        merged = json.loads(sidecar.read_text())
        self.assertEqual(merged["reviewed_range"], "aaa0001~1..aaa0002")
        self.assertTrue(merged["advisory_diff_shown"])


class PostLoopReviewCommandTests(TestCase):
    """§7.0 gate-stamp moved into code (WM2 verdict-on-disk, step 1).

    ``cmd_post_loop_review`` owns the FAILURE→no-stamp judgment the
    teleoperator's prose ``post`` rule used to make: a real review
    (APPROVE/APPROVE_WITH_COMMENTS/CHANGES_REQUESTED) MERGE-stamps
    ``reviewed_range``; a FAILURE does not, so the spine re-reviews instead of
    silently treating a crashed review as done.
    """

    def setUp(self):
        self.d = _git_track_dir(_make_state())
        _docs_commit(self.d)
        _wiki_commit(self.d)
        # Advisory + lint + digest stamped → the spine lands at §7.0 review.
        _sidecar_post_sync(self.d)
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def _reviewed_range(self):
        path = Path(self.d, ".conductor", "post-loop.json")
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text()).get("reviewed_range")
        except (ValueError, OSError):
            return None

    def _sidecar_json(self):
        path = Path(self.d, ".conductor", "post-loop.json")
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            return {}

    def test_failure_does_not_stamp_and_spine_re_reviews(self):
        out = _review(self.d, "FAILURE")
        self.assertTrue(out["ok"])
        self.assertFalse(out["stamped"])
        self.assertIsNone(self._reviewed_range())  # no stamp left behind
        # The spine, still unreviewed, re-emits the review (not silently done).
        self.assertEqual(_pls(self.d)["action"], "dispatch_review")

    def test_approve_stamps_current_range(self):
        out = _review(self.d, "APPROVE")
        self.assertTrue(out["ok"])
        self.assertTrue(out["stamped"])
        self.assertEqual(out["reviewed_range"], "aaa0001~1..aaa0002")
        self.assertEqual(self._reviewed_range(), "aaa0001~1..aaa0002")

    def test_changes_requested_also_stamps(self):
        # A review that requested changes still RAN → stamp (apply_fixes follows).
        out = _review(self.d, "CHANGES_REQUESTED")
        self.assertTrue(out["stamped"])
        self.assertEqual(self._reviewed_range(), "aaa0001~1..aaa0002")

    def test_approve_with_comments_stamps(self):
        self.assertTrue(_review(self.d, "APPROVE_WITH_COMMENTS")["stamped"])

    def test_lowercase_status_accepted(self):
        # The command upper-cases — teleoperator transcription is forgiving.
        self.assertTrue(_review(self.d, "approve")["stamped"])

    def test_unknown_status_errors_without_stamp(self):
        out = _review(self.d, "BOGUS")
        self.assertIn("error", out)
        self.assertIsNone(self._reviewed_range())

    def test_after_stamp_spine_advances_past_review(self):
        _review(self.d, "APPROVE")  # stamps reviewed_range
        # Review done + advisory/lint/digest already stamped → reaches §8.0 archive.
        self.assertEqual(_pls(self.d)["action"], "archive_ask")

    # --- verdict + counts persistence (non-blocking audit; "done is a claim") ---
    def test_verdict_and_counts_stamped_to_sidecar(self):
        out = _review(self.d, "CHANGES_REQUESTED", critical="2", high="1")
        sc = self._sidecar_json()
        self.assertEqual(out["review_verdict"], "CHANGES_REQUESTED")
        self.assertEqual(sc["review_verdict"], "CHANGES_REQUESTED")
        self.assertEqual(sc["review_critical"], 2)
        self.assertEqual(sc["review_high"], 1)
        # The gate still advances (non-blocking) — verdict persistence is for audit.
        self.assertTrue(out["stamped"])

    def test_verdict_stamped_without_counts_when_none_passed(self):
        # Absent counts must NOT be fabricated as 0 — only stamp what was given.
        _review(self.d, "APPROVE_WITH_COMMENTS")
        sc = self._sidecar_json()
        self.assertEqual(sc["review_verdict"], "APPROVE_WITH_COMMENTS")
        self.assertNotIn("review_critical", sc)
        self.assertNotIn("review_high", sc)

    def test_unparsable_counts_not_stamped(self):
        # A garbage count transcription must not become a misleading 0.
        _review(self.d, "CHANGES_REQUESTED", critical="abc", high="")
        sc = self._sidecar_json()
        self.assertEqual(sc["review_verdict"], "CHANGES_REQUESTED")
        self.assertNotIn("review_critical", sc)
        self.assertNotIn("review_high", sc)

    def test_failure_does_not_stamp_verdict(self):
        _review(self.d, "FAILURE", critical="3", high="2")
        sc = self._sidecar_json()
        self.assertNotIn("review_verdict", sc)


if __name__ == "__main__":
    main()
