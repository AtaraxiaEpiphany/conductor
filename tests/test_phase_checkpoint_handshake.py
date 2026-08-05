"""Tests for the phase-checkpoint handshake commands (WM2 verdict-on-disk, step 2).

``cmd_phase_verdict`` + ``cmd_phase_checkpoint_review`` are stamp-only transcribe
commands mirroring ``cmd_post_loop_review`` (WM2-1): the teleoperator transcribes
a read-only agent's fixed-format RESULT line to one of these, then re-calls
``step``; the spine re-derives the next leaf from the marker. The agent firewall
stays intact (no read-only agent writes state); the §3.2 parse→assemble and §3.7
stamp/halt judgments the prose hand-off asked the model to make now live in code.

Fixture reuse: the git-backed "phase 1 terminal, no checkpoint" track is exactly
``test_step._phase_complete_track`` — imported rather than duplicated.
"""
import io
import json
import shutil
import sys
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.dispatch import (
    cmd_phase_verdict, cmd_phase_checkpoint_review,
    _phase_cp_read_marker, _phase_cp_marker_path)
from scripts.track_state import cli

# Reuse the git-backed "phase 1 complete, no checkpoint" track fixture + builder.
from tests.test_step import _phase_complete_track, _git_track_dir, _make_state


def _run(fn, *args):
    """Capture a stamp-only command's stdout JSON."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _has_checkpoint(plan_text, phase):
    import re
    return bool(re.search(rf"^##\s+Phase\s+{phase}\b.*\[checkpoint:\s*[0-9a-f]+\]",
                          plan_text, re.MULTILINE))


class PhaseVerdictTests(TestCase):
    def test_writes_synth_pending_marker(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        self.assertTrue(o["ok"])
        self.assertEqual(o["phase"], 1)
        self.assertEqual(o["stage"], "synth_pending")
        m = _phase_cp_read_marker(d)
        self.assertEqual(m["stage"], "synth_pending")
        self.assertEqual(m["ac_verdict"], "passed")
        self.assertEqual(m["l1_status"], "passed")
        self.assertEqual(m["l1_command"], "pytest -q")
        self.assertIsNone(m["ac_gate"])

    def test_build_verdict_threads_into_phase_checker_assignment(self):
        # _build_phase_checker emits the L1_VERIFY_STATUS/COMMAND lines the
        # phase-checker consumes from the marker. Every phase fans out the
        # standard ac-tracer + test-runner pair now, so the assignment always
        # carries the L1 verdict and never any BUILD lines.
        from scripts.track_state.dispatch import _build_phase_checker
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        state = {"track_id": "t", "execution_mode": "interactive"}
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        m = _phase_cp_read_marker(d)
        body = _build_phase_checker(d, state, 1, m)
        self.assertIn("L1_VERIFY_STATUS=passed", body)
        self.assertIn("L1_VERIFY_COMMAND=pytest -q", body)
        self.assertNotIn("BUILD_VERIFY_STATUS", body)

    def test_unknown_ac_verdict_errors(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "bogus", None, None, "passed", "pytest -q")
        self.assertIn("error", o)
        self.assertFalse(_phase_cp_marker_path(d).exists(),
                         "a rejected transcription must not write a marker")

    def test_unknown_l1_status_errors(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "nope", "pytest -q")
        self.assertIn("error", o)

    def test_errors_when_no_pending_checkpoint(self):
        # Phase 1 already has a checkpoint → nothing to synthesize.
        state = _make_state(
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed", "commit_sha": "abc1234"}]}])
        plan = "# Plan\n\n## Phase 1: Build [checkpoint: abc1234]\n- [x] Task A\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        self.assertIn("error", o)
        self.assertFalse(_phase_cp_marker_path(d).exists())

    def test_idempotent_overwrite(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        # A re-fan with fresh verdicts overwrites cleanly (no append/dup).
        _run(cmd_phase_verdict, d, "FAILED", "AC2 bad", None, "failed", "make test")
        m = _phase_cp_read_marker(d)
        self.assertEqual(m["ac_verdict"], "FAILED")
        self.assertEqual(m["ac_gate"], "AC2 bad")
        self.assertEqual(m["l1_status"], "failed")


class PhaseCheckpointReviewTests(TestCase):
    def test_passed_stamps_checkpoint_and_clears_marker(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", "abc1234", None)
        self.assertTrue(o["ok"])
        self.assertTrue(o["stamped"])
        self.assertEqual(o["sha"], "abc1234")
        plan = (Path(d) / "plan.md").read_text()
        self.assertTrue(_has_checkpoint(plan, 1),
                        "PASSED must stamp [checkpoint: <sha>] on the phase heading")
        self.assertFalse(_phase_cp_marker_path(d).exists(),
                         "marker must be cleared so the next step advances")

    def test_passed_missing_sha_errors(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", None, None)
        self.assertIn("error", o)
        self.assertTrue(_phase_cp_marker_path(d).exists(),
                        "a rejected stamp must leave the marker (re-review, not re-fan)")
        plan = (Path(d) / "plan.md").read_text()
        self.assertFalse(_has_checkpoint(plan, 1))

    def test_passed_bad_sha_errors(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", "notahex", None)
        self.assertIn("error", o)

    def test_failed_clears_marker_with_reason(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "FAILED", "AC1 ungrounded", None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "FAILED", None, "AC1 not met")
        self.assertTrue(o["ok"])
        self.assertFalse(o["stamped"])
        self.assertEqual(o["reason"], "AC1 not met")
        plan = (Path(d) / "plan.md").read_text()
        self.assertFalse(_has_checkpoint(plan, 1), "FAILED must not stamp")
        self.assertFalse(_phase_cp_marker_path(d).exists(),
                         "marker cleared so re-invoke re-fans fresh (§3.7 re-run)")

    def test_failed_without_reason_uses_default(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "FAILED", None, None)
        self.assertEqual(o["reason"], "phase-checker FAILED")

    def test_unknown_status_errors(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "MAYBE", None, None)
        self.assertIn("error", o)
        self.assertTrue(_phase_cp_marker_path(d).exists(),
                        "unknown status leaves the marker intact")

    def test_review_when_already_stamped_is_clean(self):
        # A duplicate review call after the checkpoint is already stamped must
        # not crash (no pending checkpoint) and must clear any stray marker.
        state = _make_state(
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed", "commit_sha": "abc1234"}]}])
        plan = "# Plan\n\n## Phase 1: Build [checkpoint: abc1234]\n- [x] Task A\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", "abc1234", None)
        self.assertTrue(o["ok"])
        self.assertFalse(o["stamped"])


class CliWiringTests(TestCase):
    """The two commands resolve through cli.main (which reads sys.argv) with
    their flag parse, and are listed in help + a command group. The sanctioned
    -subcommand drift test asserts the allowlist covers them."""

    def _invoke(self, argv):
        old_argv, old_out = sys.argv, sys.stdout
        sys.argv, sys.stdout = ["track-state"] + argv, io.StringIO()
        try:
            cli.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_out

    def test_phase_verdict_resolves_via_cli(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._invoke(["phase-verdict", d, "--ac-verdict", "passed",
                      "--l1-status", "passed", "--l1-command", "pytest -q"])
        self.assertTrue(_phase_cp_marker_path(d).exists())

    def test_phase_checkpoint_review_resolves_via_cli(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        self._invoke(["phase-checkpoint-review", d, "--status", "PASSED",
                      "--sha", "abc1234"])
        plan = (Path(d) / "plan.md").read_text()
        self.assertTrue(_has_checkpoint(plan, 1))

    def test_commands_listed_in_help_and_group(self):
        grouped = {c for _name, cmds in cli._COMMAND_GROUPS for c in cmds}
        for sub in ("phase-verdict", "phase-checkpoint-review"):
            self.assertIn(sub, cli.COMMAND_HELP)
            self.assertIn(sub, grouped)


if __name__ == "__main__":
    main()
