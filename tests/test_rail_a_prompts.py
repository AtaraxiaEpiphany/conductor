"""Rail A paste-verbatim prompt emission (design D3, campaign 2.4).

Pins the NEW envelope arms so skills/implement + skills/parallel can paste
`prompt` fields verbatim instead of hand-interpolating KEY=value blocks:

  * ``dispatch-finalize`` failure arms: skip-analyst (exhausted, auto-routing),
    failure-analyst (penultimate, auto-routing), plain re-dispatch / ask.
  * ``dispatch-finalize`` SUCCESS arm: the §3.6b ``self_review`` / §3.6c
    ``refactor`` opt-in envelopes (name markers, env flags; DEFAULT OFF).
  * ``phase-verdict``: the phase-checker synth prompt (verdict-first order).
  * ``phase-done``: ``checkpoint_due`` + the pre-assembled ``verifier_wave``.
  * ``skip-analyst-verdict``: the refuter prompt on a skip recommendation.

All captures run through the DEFAULT compact envelope (``cmd_dispatch_finalize``
defaults ``compact=True``), so these also pin the COMPACT_FIELDS registration —
a field missing from the allowlist would be stripped and fail here.
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
    cmd_dispatch_finalize, cmd_phase_verdict, cmd_skip_analyst_verdict)
from scripts.track_state.misc import cmd_phase_done


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_state(**overrides):
    state = {
        "track_id": "raila", "type": "feature", "status": "in_progress",
        "description": "rail a prompt test",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [
            {"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "pending"},
            ]},
        ],
    }
    state.update(overrides)
    return state


def _git_track_dir(state):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text(
        "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    save(d, state)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "-C", d, "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"],
                   check=True, capture_output=True, env=env)
    return d


def _capture(fn, *args, **kwargs):
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _result(track_dir, body):
    cond = Path(track_dir, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / "result.json").write_text(json.dumps(body))


class FinalizeFailurePromptTests(TestCase):
    """The §3.6 FAILURE arms mirror _step_route_after_finalize's routing."""

    def _fail(self, retry_before, **state_over):
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "in_progress",
             "retry_count": retry_before}]}], **state_over)
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _result(d, {"status": "FAILURE", "phase": 1, "task": 1, "subtask": None,
                    "task_name": "Task A", "commit_sha": "",
                    "failure_detail": {"failure_reason": "red"}})
        return d, _capture(cmd_dispatch_finalize, d)

    def test_exhausted_auto_route_emits_skip_analyst_prompt(self):
        # retry_count 2 → fail → 3 == MAX_RETRIES (exhausted); continuous mode
        # auto-routes → the skip-analyst dispatch envelope with prompt.
        d, o = self._fail(2, execution_mode="continuous")
        self.assertEqual(o["next_action"], "dispatch_skip_analyst")
        self.assertEqual(o["agent"], "skip-analyst")
        self.assertIn("TRACK_DIR=", o["prompt"])
        self.assertIn("TASK_NAME=Task A", o["prompt"])

    def test_penultimate_auto_route_emits_failure_analyst_prompt(self):
        # retry_count 1 → fail → 2 == ceiling-1 → the modified-retry analyst.
        d, o = self._fail(1, execution_mode="continuous")
        self.assertEqual(o["next_action"], "dispatch_failure_analyst")
        self.assertEqual(o["agent"], "failure-analyst")
        self.assertIn("RETRY_COUNT=2", o["prompt"])
        self.assertIn("MAX_RETRIES=3", o["prompt"])

    def test_early_failure_routes_plain_redispatch(self):
        # retry_count 0 → fail → 1 < 2: ordinary identical retry via §3.1 —
        # no prompt attached (dispatch-prepare owns it).
        d, o = self._fail(0, execution_mode="continuous")
        self.assertEqual(o["next_action"], "dispatch_executor")
        self.assertNotIn("prompt", o)

    def test_ask_surface_exhausted_routes_ask(self):
        # interactive + no auto recovery_policy → §2.2's Retry/Skip/Block.
        d, o = self._fail(2)
        self.assertEqual(o["next_action"], "ask")
        self.assertNotIn("prompt", o)


class FinalizeSuccessOptInTests(TestCase):
    """The §3.6b/§3.6c opt-in envelopes: DEFAULT OFF, name markers + env flags."""

    def _succeed(self, name="[Review] Task A", env=None):
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": name, "status": "in_progress"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _result(d, {"status": "SUCCESS", "phase": 1, "task": 1, "subtask": None,
                    "task_name": name, "commit_sha": "abc1234"})
        if env:
            os.environ[env[0]] = env[1]
            self.addCleanup(os.environ.pop, env[0], None)
        return d, _capture(cmd_dispatch_finalize, d)

    def test_default_off_no_envelopes(self):
        d, o = self._succeed(name="Task A")
        self.assertNotIn("self_review", o)
        self.assertNotIn("refactor", o)
        self.assertNotIn("next_action", o)

    def test_review_name_marker_emits_self_review(self):
        d, o = self._succeed(name="[Review] Task A")
        self.assertEqual(o["self_review"]["agent"], "code-reviewer")
        self.assertIn("REVISION_RANGE=abc1234~1..abc1234", o["self_review"]["prompt"])

    def test_review_env_flag_opts_in(self):
        d, o = self._succeed(name="Task A", env=("CONDUCTOR_SELF_REVIEW", "1"))
        self.assertIn("self_review", o)

    def test_refactor_name_marker_emits_refactor(self):
        d, o = self._succeed(name="[Refactor] Task A")
        self.assertEqual(o["refactor"]["agent"], "refactorer")
        self.assertIn("REVISION_RANGE=abc1234~1..abc1234", o["refactor"]["prompt"])

    def test_refactor_env_flag_opts_in(self):
        d, o = self._succeed(name="Task A", env=("CONDUCTOR_TASK_REFACTOR", "1"))
        self.assertIn("refactor", o)


class PhaseBoundaryPromptTests(TestCase):
    """phase-done (fan-out) + phase-verdict (synth) carry the §3.2 prompts."""

    def _completed_phase_track(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "in_progress"}]}])
        d = _git_track_dir(state)
        _result(d, {"status": "SUCCESS", "phase": 1, "task": 1, "subtask": None,
                    "task_name": "Task A", "commit_sha": "abc1234"})
        _capture(cmd_dispatch_finalize, d)
        return d

    def test_phase_done_complete_attaches_checkpoint_wave(self):
        d = self._completed_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _capture(cmd_phase_done, d, "1")
        self.assertTrue(o["complete"])
        self.assertTrue(o["checkpoint_due"])
        wave = o["verifier_wave"]
        agents = {m["agent"] for m in wave}
        self.assertIn("ac-tracer", agents)
        for m in wave:
            self.assertIn("TRACK_DIR=", m["prompt"])
            # ac-tracer deliberately omits PHASE_INDEX (pinned by
            # test_step.test_dispatch_batch_ac_tracer_prompt_omits_phase_index);
            # the code tiers carry it.
            if m["agent"] != "ac-tracer":
                self.assertIn("PHASE_INDEX=1", m["prompt"])

    def test_phase_verdict_emits_phase_checker_prompt(self):
        d = self._completed_phase_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _capture(cmd_phase_verdict, d, "passed", None, None,
                     l1_status="passed", build_status="passed")
        self.assertTrue(o["ok"])
        self.assertEqual(o["next_action"], "dispatch_phase_checker")
        self.assertEqual(o["agent"], "phase-checker")
        self.assertIn("AC_TRACE_VERDICT=passed", o["prompt"])
        self.assertIn("L1_VERIFY_STATUS=passed", o["prompt"])
        self.assertIn("BUILD_VERIFY_STATUS=passed", o["prompt"])


class SkipAnalystVerdictPromptTests(TestCase):
    """skip-analyst-verdict carries the refuter prompt on a skip recommendation."""

    def test_skip_recommendation_emits_refuter_prompt(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "failed", "retry_count": 3}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _capture(cmd_skip_analyst_verdict, d, "skip", "cheap fix", "low", "true")
        self.assertEqual(o["next_action"], "dispatch_refuter")
        self.assertEqual(o["agent"], "refuter")
        self.assertIn("DOMAIN=skip", o["prompt"])
        self.assertIn("this skip is UNSAFE", o["prompt"])
        self.assertIn('reasoning: "cheap fix"', o["prompt"])

    def test_pause_recommendation_routes_halt(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "failed", "retry_count": 3}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _capture(cmd_skip_analyst_verdict, d, "pause_and_escalate",
                     "r", "i", "false")
        self.assertEqual(o["next_action"], "halt")
        self.assertNotIn("prompt", o)


if __name__ == "__main__":
    main()
