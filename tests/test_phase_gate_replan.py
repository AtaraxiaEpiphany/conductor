"""Tests for the phase-gate replanning seam (any-job Track 4).

The once-per-checkpoint handshake: a PASSED checkpoint stamp stages
``.conductor/replan-pass.json`` (single-homed in
``misc._stamp_checkpoint_in_plan`` — both stamp paths, Rail A ``add-checkpoint``
and Rail B ``phase-checkpoint-review``), the orchestrator polls
``track-state replan``, and the re-derive pass ends with ``--ack`` (consumes;
idempotent). The procedure itself (inputs, amendment classes, ONE confirm,
reconcile-plan apply) is skill-driven prose single-homed in
``runtime/contracts/phase-gate-replanning.md`` — what is pinned HERE is the
mechanical half: who stages, when it is due, and that exactly-once holds.
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.misc import (
    _stamp_checkpoint_in_plan, _write_replan_marker_fail_open, cmd_replan,
    cmd_add_checkpoint)
from scripts.track_state.dispatch import (
    cmd_phase_verdict, cmd_phase_checkpoint_review)
from scripts.track_state.quality import _CONDUCTOR_GITIGNORE

# Shared git-backed fixtures (same reuse as test_phase_checkpoint_handshake /
# test_telemetry_feeds).
from tests.test_step import _make_state, _git_track_dir, _phase_complete_track, _head_short


def _run(fn, *args, **kwargs):
    """Capture a command's stdout JSON."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _two_phase_track():
    """Phase 1 fully terminal (commit_sha abc1234), Phase 2 pending — the
    state a PASSED phase-1 checkpoint leaves behind."""
    state = _make_state(
        current_phase_index=0, current_task_index=0,
        phases=[
            {"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed",
                 "commit_sha": "abc1234"}]},
            {"name": "Phase 2", "status": "pending", "tasks": [
                {"name": "Task B", "status": "pending"}]},
        ])
    plan = ("# Plan\n\n## Phase 1: Build\n- [x] Task A [abc1234]\n\n"
            "## Phase 2: Ship\n- [ ] Task B\n")
    return _git_track_dir(state, plan_content=plan)


def _marker_path(track_dir):
    return Path(track_dir, ".conductor", "replan-pass.json")


# --- staging (the stamp home) ---------------------------------------------------


class StagingTests(TestCase):
    def test_stamp_stages_when_phases_remain(self):
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _stamp_checkpoint_in_plan(d, 1, _head_short(d))
        self.assertTrue(o["ok"])
        self.assertEqual(json.loads(_marker_path(d).read_text()),
                         {"phase": 1})

    def test_last_phase_stamp_stages_nothing(self):
        # Single-phase track: the stamp heads the track to finalize, not to a
        # re-derive pass over rows that do not exist.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _stamp_checkpoint_in_plan(d, 1, _head_short(d))
        self.assertTrue(o["ok"])
        self.assertFalse(_marker_path(d).exists())

    def test_later_phase_stamp_beyond_range_noop(self):
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # Phase 2 in a 2-phase plan: not < len(phases) → no marker.
        _write_replan_marker_fail_open(d, 2)
        self.assertFalse(_marker_path(d).exists())

    def test_fail_open_on_unwritable_tree(self):
        # Staging must never raise into the checkpoint advance.
        _write_replan_marker_fail_open("/nonexistent/track/dir", 1)

    def test_rail_a_add_checkpoint_stages(self):
        # The phase-checker agent's stamp path (Rail A) stages through the
        # same home — verified through the CLI wrapper, not the helper.
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_add_checkpoint, d, 1, _head_short(d))
        self.assertTrue(o["ok"])
        self.assertEqual(json.loads(_marker_path(d).read_text()),
                         {"phase": 1})

    def test_rail_b_review_passed_stages(self):
        # The step spine's stamp path (Rail B): phase-verdict → review PASSED.
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", _head_short(d), None)
        self.assertTrue(o["ok"])
        self.assertEqual(json.loads(_marker_path(d).read_text()),
                         {"phase": 1})

    def test_review_failed_stages_nothing(self):
        # No stamp, no offer — FAILED routes recovery, never a re-derive pass.
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "failed", None, None, "failed")
        o = _run(cmd_phase_checkpoint_review, d, "FAILED", None,
                 "coverage gate red")
        self.assertTrue(o["ok"])
        self.assertFalse(_marker_path(d).exists())

    def test_restamp_restages_after_ack(self):
        # A re-verified phase is a new settlement — the offer comes back.
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _stamp_checkpoint_in_plan(d, 1, _head_short(d))
        _run(cmd_replan, d, ack=True)
        o = _stamp_checkpoint_in_plan(d, 1, _head_short(d))
        self.assertTrue(o["ok"])
        self.assertTrue(_marker_path(d).exists())


# --- the replan command (poll + ack) --------------------------------------------


class ReplanCommandTests(TestCase):
    def test_due_after_stamp(self):
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _stamp_checkpoint_in_plan(d, 1, _head_short(d))
        o = _run(cmd_replan, d)
        self.assertTrue(o["ok"])
        self.assertTrue(o["replan_due"])
        self.assertEqual(o["phase"], 1)
        self.assertEqual(o["remaining_phases"], 1)

    def test_not_due_without_marker(self):
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_replan, d)
        self.assertTrue(o["ok"])
        self.assertFalse(o["replan_due"])

    def test_fail_open_on_corrupt_marker(self):
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _marker_path(d).parent.mkdir(parents=True, exist_ok=True)
        _marker_path(d).write_text("{not json", encoding="utf-8")
        o = _run(cmd_replan, d)
        self.assertTrue(o["ok"])
        self.assertFalse(o["replan_due"])

    def test_stale_marker_not_due(self):
        # Marker phase beyond the plan (plan shrank since): not due, stale.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _marker_path(d).parent.mkdir(parents=True, exist_ok=True)
        _marker_path(d).write_text(json.dumps({"phase": 1}),
                                   encoding="utf-8")
        o = _run(cmd_replan, d)
        self.assertTrue(o["ok"])
        self.assertFalse(o["replan_due"])

    def test_ack_consumes_then_not_due(self):
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _stamp_checkpoint_in_plan(d, 1, _head_short(d))
        o = _run(cmd_replan, d, ack=True)
        self.assertTrue(o["ok"])
        self.assertEqual(o["acked"], 1)
        self.assertFalse(_marker_path(d).exists())
        # Exactly-once: the same checkpoint cannot re-offer.
        o = _run(cmd_replan, d)
        self.assertFalse(o["replan_due"])

    def test_ack_idempotent_without_marker(self):
        # The skill's terminal step never wedges: acking with nothing pending
        # is acked: null, not an error.
        d = _two_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_replan, d, ack=True)
        self.assertTrue(o["ok"])
        self.assertIsNone(o["acked"])


# --- the marker is transient ----------------------------------------------------


class TransientCoverageTests(TestCase):
    def test_gitignore_covers_replan_marker(self):
        # The normative ignore body derives from quality's tuple; the drift
        # gate test proves glob-sample matching — this pins the membership.
        self.assertIn("replan-pass.json", _CONDUCTOR_GITIGNORE)


if __name__ == "__main__":
    main()
