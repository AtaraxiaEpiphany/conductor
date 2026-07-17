"""Tests for the failure_analyze handshake commands (B.2-B.7).

``cmd_failure_analyst_verdict`` is a stamp-only transcribe command, the failure-
analyze counterpart of ``cmd_skip_analyst_verdict``. The teleoperator transcribes
failure-analyst's fixed-format verdict to it, then re-calls ``step``; the spine
routes (``retry_modified`` → reactivate + re-dispatch task-executor with the
modification injected; ``replan`` / ``decompose`` / ``escalate`` → halt). The
agent firewall stays intact; the category→action judgment lives in code.

Fixture reuse: the continuous failed track is ``test_step._failed_exhausted_track``
(retry_count=3, exhausted) and a pre-exhaustion variant built inline.
"""
import io
import json
import shutil
import sys
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.dispatch import (
    cmd_failure_analyst_verdict,
    _failure_analysis_read_marker, _failure_analysis_marker_path,
    _failure_analysis_write_marker,
    _modified_guidance_path, _modified_guidance_read)
from scripts.track_state import cli

from tests.test_step import _failed_exhausted_track, _git_track_dir, _make_state, _step


def _run(fn, *args):
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _run_step_route(fn, track_dir, outcome):
    """Capture a routing helper's emitted leaf (cmd_step emits via stdout)."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(track_dir, outcome, compact=True)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


class FailureAnalystVerdictTests(TestCase):
    def test_writes_analyzed_marker_with_derived_indices(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_failure_analyst_verdict, d,
                 "deterministic_bug", "retry_modified",
                 "null deref in foo()", "add a None guard")
        self.assertTrue(o["ok"])
        self.assertEqual(o["stage"], "analyzed")
        self.assertEqual(o["recommendation"], "retry_modified")
        self.assertEqual(o["category"], "deterministic_bug")
        self.assertEqual(o["analysis_rounds"], 1)
        # phase/task re-derived from the failed task (not teleoperator-passed).
        self.assertEqual(o["phase"], 1)
        self.assertEqual(o["task"], 1)
        m = _failure_analysis_read_marker(d)
        self.assertEqual(m["recommendation"], "retry_modified")
        self.assertEqual(m["modification"], "add a None guard")
        self.assertEqual(m["root_cause"], "null deref in foo()")

    def test_unknown_recommendation_errors(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_failure_analyst_verdict, d,
                 "deterministic_bug", "bogus", "r", "m")
        self.assertIn("error", o)
        self.assertFalse(_failure_analysis_marker_path(d).exists(),
                         "a rejected transcription must not write a marker")

    def test_unknown_category_errors(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_failure_analyst_verdict, d,
                 "bogus", "escalate", "r", "m")
        self.assertIn("error", o)

    def test_retry_modified_requires_modification(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_failure_analyst_verdict, d,
                 "deterministic_bug", "retry_modified", "r", "")
        self.assertIn("error", o)
        self.assertIn("modification", o["error"])

    def test_errors_when_no_failed_task(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_failure_analyst_verdict, d,
                 "stuck", "escalate", "r", "m")
        self.assertIn("error", o)
        self.assertFalse(_failure_analysis_marker_path(d).exists())


class FailureAnalysisRoutingTests(TestCase):
    """``_step_route_failure_analysis`` routes the verdict from the on-disk marker."""

    def _marker(self, track_dir, **fields):
        base = {"phase": 1, "task": 1, "subtask": None, "name": "Task A",
                "stage": "analyzed", "category": "deterministic_bug",
                "recommendation": "retry_modified",
                "root_cause": "r", "modification": "add a None guard",
                "what_was_done": None, "analysis_rounds": 1}
        base.update(fields)
        _failure_analysis_write_marker(track_dir, base)

    def test_retry_modified_reactivates_and_redispatches(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._marker(d, recommendation="retry_modified",
                     modification="add a None guard", root_cause="null deref")
        o = _step(d)
        # Re-dispatches task-executor (the failed task is reactivated to pending).
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["agent"], "task-executor")
        # Modification written to the guidance marker for the hook to inject.
        self.assertIsNotNone(_modified_guidance_read(d, 1, 1, None))
        # Analysis marker cleared (handshake consumed).
        self.assertFalse(_failure_analysis_marker_path(d).exists())
        # Task reactivated and re-locked for the re-dispatch (reactivate set it
        # to pending; prepare_dispatch then locked it to in_progress). Either
        # way it is NOT terminal failed, and retry_count is preserved (budget
        # still counts — reactivate is not a free retry).
        from scripts.track_state.core import load
        tgt = load(d)["phases"][0]["tasks"][0]
        self.assertIn(tgt["status"], ("pending", "in_progress"))
        self.assertEqual(tgt["retry_count"], 3)

    def test_replan_halts_with_root_cause(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._marker(d, category="spec_plan_defect", recommendation="replan",
                     root_cause="AC-2 contradicts AC-1",
                     modification="drop AC-2")
        o = _step(d)
        self.assertEqual(o["action"], "halt")
        self.assertEqual(o["reason"], "replan")
        self.assertEqual(o["reasoning"], "AC-2 contradicts AC-1")
        self.assertEqual(o["modification"], "drop AC-2")
        self.assertIn("spec.md", o["recovery"])
        self.assertFalse(_failure_analysis_marker_path(d).exists())

    def test_decompose_emits_ask_with_split_decision(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._marker(d, category="context_budget", recommendation="decompose",
                     modification="split into 2a + 2b",
                     what_was_done="added the validator")
        o = _step(d)
        # Decompose now routes to an ask (code-applied, human-confirmed split),
        # not a halt. The marker is cleared before the ask.
        self.assertEqual(o["action"], "ask")
        self.assertFalse(_failure_analysis_marker_path(d).exists())
        decision = o["decision"]
        self.assertEqual(decision["header"], "Decompose")
        labels = [opt["label"] for opt in decision["options"]]
        self.assertEqual(labels, ["Apply split", "Skip original only", "Escalate"])
        # Apply split must run the new split CLI verbatim with the proposed names.
        apply_cmds = decision["commands"]["Apply split"]
        self.assertTrue(any("track-state split" in c for c in apply_cmds),
                        f"expected a track-state split line, got {apply_cmds}")
        split_line = next(c for c in apply_cmds if "track-state split" in c)
        # Parsed names from the modification text survive into the command.
        self.assertIn("2a", split_line)
        self.assertIn("2b", split_line)
        # Apply split resumes the spine; Escalate halts.
        self.assertEqual(decision["next"]["Apply split"], "step")
        self.assertEqual(decision["next"]["Escalate"], "HALT")

    def test_escalate_halts(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._marker(d, category="stuck", recommendation="escalate",
                     root_cause="same failure 3x")
        o = _step(d)
        self.assertEqual(o["action"], "halt")
        self.assertEqual(o["reason"], "escalate")
        self.assertIn("Manual escalation", o["recovery"])

    def test_cap_overrun_escalates_instead_of_redispatching(self):
        # Past MAX_ANALYSIS_ROUNDS, retry_modified must fall through to escalate,
        # not re-dispatch (the analyze→retry→fail loop bound).
        from scripts.track_state.constants import MAX_ANALYSIS_ROUNDS
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._marker(d, recommendation="retry_modified",
                     analysis_rounds=MAX_ANALYSIS_ROUNDS + 1)
        o = _step(d)
        self.assertEqual(o["action"], "halt")
        self.assertEqual(o["reason"], "escalate")

    def test_within_cap_still_redispatches(self):
        # With MAX_ANALYSIS_ROUNDS=2, round 2 is still under the cap → the analyst
        # gets its one refinement round (reactivate + re-dispatch), not escalate.
        from scripts.track_state.constants import MAX_ANALYSIS_ROUNDS
        self.assertGreaterEqual(MAX_ANALYSIS_ROUNDS, 2)
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._marker(d, recommendation="retry_modified",
                     modification="different approach", analysis_rounds=2)
        o = _step(d)
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["agent"], "task-executor")


class PreExhaustionTriggerTests(TestCase):
    """B.6 pre-exhaustion tier: a continuous-mode task one attempt from exhaustion
    routes to failure-analyst instead of a blind final retry. Lives in
    ``_step_route_after_finalize`` (the post-finalize FAILURE branch), so driven
    directly with a synthetic outcome rather than via a bare ``step`` call."""

    def test_penultimate_failure_dispatches_failure_analyst(self):
        from scripts.track_state.dispatch import _step_route_after_finalize
        # retry_count = ceiling - 1 = 2 (default ceiling 3): one attempt remains.
        state = _make_state(
            execution_mode="continuous",
            current_phase_index=1, current_task_index=1,
            phases=[{"name": "Phase 1", "status": "in_progress", "tasks": [
                {"name": "Task A", "status": "pending", "retry_count": 2}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        outcome = {"status": "FAILURE", "retry_count": 2,
                   "execution_mode": "continuous", "phase": 1, "task": 1,
                   "subtask": None}
        o = _run_step_route(_step_route_after_finalize, d, outcome)
        self.assertEqual(o["action"], "dispatch_failure_analyst")
        self.assertEqual(o["agent"], "failure-analyst")

    def test_earlier_failure_still_plain_redispatch(self):
        # retry_count < ceiling - 1 → ordinary identical retry (the analyst fires
        # once, on the penultimate failure, not on every failure).
        from scripts.track_state.dispatch import _step_route_after_finalize
        state = _make_state(
            execution_mode="continuous",
            current_phase_index=1, current_task_index=1,
            phases=[{"name": "Phase 1", "status": "in_progress", "tasks": [
                {"name": "Task A", "status": "pending", "retry_count": 0}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        outcome = {"status": "FAILURE", "retry_count": 0,
                   "execution_mode": "continuous", "phase": 1, "task": 1,
                   "subtask": None}
        o = _run_step_route(_step_route_after_finalize, d, outcome)
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["agent"], "task-executor")

    def test_pre_exhaustion_skipped_in_interactive_mode(self):
        # Interactive mode keeps a human in the loop → no analyst; plain retry.
        from scripts.track_state.dispatch import _step_route_after_finalize
        state = _make_state(
            execution_mode="interactive",
            current_phase_index=1, current_task_index=1,
            phases=[{"name": "Phase 1", "status": "in_progress", "tasks": [
                {"name": "Task A", "status": "pending", "retry_count": 2}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        outcome = {"status": "FAILURE", "retry_count": 2,
                   "execution_mode": "interactive", "phase": 1, "task": 1,
                   "subtask": None}
        o = _run_step_route(_step_route_after_finalize, d, outcome)
        self.assertEqual(o["action"], "dispatch")


class CliWiringTests(TestCase):
    def _invoke(self, argv):
        old_argv, old_out = sys.argv, sys.stdout
        sys.argv, sys.stdout = ["track-state"] + argv, io.StringIO()
        try:
            cli.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_out

    def test_failure_analyst_verdict_resolves_via_cli(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._invoke(["failure-analyst-verdict", d,
                      "--category", "deterministic_bug",
                      "--recommendation", "retry_modified",
                      "--root-cause", "null", "--modification", "add guard"])
        m = _failure_analysis_read_marker(d)
        self.assertEqual(m["stage"], "analyzed")
        self.assertEqual(m["recommendation"], "retry_modified")

    def test_commands_listed_in_help_and_group(self):
        grouped = {c for _name, cmds in cli._COMMAND_GROUPS for c in cmds}
        self.assertIn("failure-analyst-verdict", cli.COMMAND_HELP)
        self.assertIn("failure-analyst-verdict", grouped)


if __name__ == "__main__":
    main()
