"""Tests for the skip_analyze handshake commands (WM2 verdict-on-disk, step 3).

``cmd_skip_analyst_verdict`` + ``cmd_skip_refute_review`` are stamp-only transcribe
commands mirroring the WM2-2 phase-checkpoint commands. The teleoperator transcribes
a read-only agent's fixed-format verdict line to one of these, then re-calls
``step``; the spine routes (dispatch_refuter / halt / skip+advance). The agent
firewall stays intact; the §3.6 skip-analyst→refute→route judgment the prose
hand-off asked the model to make now lives in code.

Fixture reuse: the continuous failed+exhausted track is ``test_step._failed_exhausted_track``.
"""
import io
import json
import shutil
import sys
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.dispatch import (
    cmd_skip_analyst_verdict, cmd_skip_refute_review,
    _skip_analysis_read_marker, _skip_analysis_marker_path)
from scripts.track_state import cli

from tests.test_step import _failed_exhausted_track, _git_track_dir, _make_state


def _run(fn, *args):
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


class SkipAnalystVerdictTests(TestCase):
    def test_writes_analyzed_marker_with_derived_indices(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_skip_analyst_verdict, d, "skip", "no deps", "none", "true")
        self.assertTrue(o["ok"])
        self.assertEqual(o["recommendation"], "skip")
        self.assertEqual(o["stage"], "analyzed")
        # phase/task re-derived from the failed+exhausted task (not teleoperator-passed).
        self.assertEqual(o["phase"], 1)
        self.assertEqual(o["task"], 1)
        m = _skip_analysis_read_marker(d)
        self.assertEqual(m["stage"], "analyzed")
        self.assertEqual(m["recommendation"], "skip")
        self.assertEqual(m["reasoning"], "no deps")
        self.assertIs(m["can_skip"], True)

    def test_unknown_recommendation_errors(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_skip_analyst_verdict, d, "bogus", "r", "i", "true")
        self.assertIn("error", o)
        self.assertFalse(_skip_analysis_marker_path(d).exists(),
                         "a rejected transcription must not write a marker")

    def test_errors_when_no_failed_exhausted_task(self):
        # A healthy track with no failed+exhausted task → nothing to skip-analyze.
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_skip_analyst_verdict, d, "skip", "r", "i", "true")
        self.assertIn("error", o)
        self.assertFalse(_skip_analysis_marker_path(d).exists())


class SkipRefuteReviewTests(TestCase):
    def _analyzed(self, recommendation="skip"):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_skip_analyst_verdict, d, recommendation, "r", "i", "true")
        return d

    def test_writes_refuted_marker(self):
        d = self._analyzed()
        o = _run(cmd_skip_refute_review, d, "REFUTED", "safe to skip")
        self.assertTrue(o["ok"])
        self.assertEqual(o["stage"], "refuted")
        self.assertEqual(o["refute_status"], "REFUTED")
        m = _skip_analysis_read_marker(d)
        self.assertEqual(m["stage"], "refuted")
        self.assertEqual(m["refute_status"], "REFUTED")
        self.assertEqual(m["refute_reasoning"], "safe to skip")

    def test_unknown_status_errors(self):
        d = self._analyzed()
        o = _run(cmd_skip_refute_review, d, "MAYBE", "r")
        self.assertIn("error", o)
        # marker untouched (still analyzed) on a rejected transcription.
        self.assertEqual(_skip_analysis_read_marker(d)["stage"], "analyzed")

    def test_errors_when_no_marker(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_skip_refute_review, d, "REFUTED", "r")
        self.assertIn("error", o)


class CliWiringTests(TestCase):
    def _invoke(self, argv):
        old_argv, old_out = sys.argv, sys.stdout
        sys.argv, sys.stdout = ["track-state"] + argv, io.StringIO()
        try:
            cli.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_out

    def test_skip_analyst_verdict_resolves_via_cli(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._invoke(["skip-analyst-verdict", d, "--recommendation", "skip",
                      "--reasoning", "no deps", "--can-skip", "true"])
        m = _skip_analysis_read_marker(d)
        self.assertEqual(m["stage"], "analyzed")

    def test_skip_refute_review_resolves_via_cli(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_skip_analyst_verdict, d, "skip", "r", "i", "true")
        self._invoke(["skip-refute-review", d, "--status", "SUSTAINED",
                      "--reasoning", "unsafe"])
        self.assertEqual(_skip_analysis_read_marker(d)["refute_status"], "SUSTAINED")

    def test_commands_listed_in_help_and_group(self):
        grouped = {c for _name, cmds in cli._COMMAND_GROUPS for c in cmds}
        for sub in ("skip-analyst-verdict", "skip-refute-review"):
            self.assertIn(sub, cli.COMMAND_HELP)
            self.assertIn(sub, grouped)


if __name__ == "__main__":
    main()
