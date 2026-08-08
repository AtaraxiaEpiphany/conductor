r"""Tests for ``recovery_policy`` — the failed-task recovery decision DECOUPLED from
``execution_mode`` (Track A1).

The spine used to overload one field: ``execution_mode=interactive`` meant both
"pause at checkpoints" AND "surface a Retry/Skip/Block ask on a failed+exhausted
task". A1 splits them. A new ``recovery_policy`` (``ask`` | ``auto``) owns the
ask/auto-route decision; ``execution_mode`` keeps checkpoint pausing. One
resolver — ``dispatch._auto_route_failure`` — is read at every failed+exhausted
decision site, so the policy can't drift between them.

Byte-identical invariant: a track without ``recovery_policy`` (every existing
track) reads as ``ask`` and falls through to the legacy execution_mode rule, so
its behavior is unchanged.
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save, load
from scripts.track_state.dispatch import cmd_recover, _auto_route_failure
from scripts.track_state.quality import cmd_init_from_plan, cmd_set_recovery_policy
from scripts.track_state.constants import MAX_RETRIES, RECOVERY_POLICIES

from tests.test_step import _git_track_dir, _make_state, _step


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _out(fn, *args, **kwargs):
    """Capture stdout (a single JSON object) from a dispatch/quality command."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _failed_exhausted_state(execution_mode="interactive", recovery_policy=None,
                            checkpoint=False):
    """A track whose P1.T1 is failed+exhausted (retry_count=MAX_RETRIES).

    ``recovery_policy`` is omitted by default so the byte-identical (legacy) path
    is the default under test; pass ``"auto"``/``"ask"`` to exercise the new
    field. ``checkpoint`` stamps Phase 1 so a post-skip advance reaches ``done``
    rather than ``dispatch_batch``."""
    overrides = dict(
        execution_mode=execution_mode,
        current_phase_index=0, current_task_index=0,
        phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "failed", "retry_count": MAX_RETRIES,
             "commit_sha": "abc1234"}]}])
    if recovery_policy is not None:
        overrides["recovery_policy"] = recovery_policy
    state = _make_state(**overrides)
    cp = " [checkpoint: abc1234]" if checkpoint else ""
    plan = f"# Plan\n\n## Phase 1: Build{cp}\n- [!] Task A [abc1234]\n"
    return _git_track_dir(state, plan_content=plan)


class AutoRouteResolverTests(TestCase):
    """``_auto_route_failure`` is the single predicate read at every failed+
    exhausted decision site. Two independent switches grant auto-routing."""

    def test_absent_field_falls_through_to_execution_mode(self):
        # The byte-identical invariant: no recovery_policy → legacy rule.
        self.assertFalse(_auto_route_failure({"execution_mode": "interactive"}))
        self.assertTrue(_auto_route_failure({"execution_mode": "continuous"}))

    def test_ask_falls_through_to_execution_mode(self):
        self.assertFalse(_auto_route_failure(
            {"execution_mode": "interactive", "recovery_policy": "ask"}))
        self.assertTrue(_auto_route_failure(
            {"execution_mode": "continuous", "recovery_policy": "ask"}))

    def test_auto_routes_regardless_of_execution_mode(self):
        # The headline: an INTERACTIVE track with auto-policy auto-routes — the
        # whole point of decoupling recovery from checkpoint pausing.
        self.assertTrue(_auto_route_failure(
            {"execution_mode": "interactive", "recovery_policy": "auto"}))
        self.assertTrue(_auto_route_failure(
            {"execution_mode": "continuous", "recovery_policy": "auto"}))

    def test_empty_state_defaults_interactive_ask(self):
        self.assertFalse(_auto_route_failure({}))


class RecoverDecisionSuppressionTests(TestCase):
    """``cmd_recover`` attaches a Retry/Skip/Block decision blob only when the
    track surfaces human asks. ``recovery_policy=auto`` suppresses it even on an
    interactive track (the orchestrator runs ``step`` for the skip-analyst
    handshake instead)."""

    def _recover_track(self, execution_mode="interactive", recovery_policy=None,
                       name="Task A"):
        d = tempfile.mkdtemp()
        Path(d, "plan.md").write_text(f"# Plan\n\n## Phase 1: Build\n- [ ] {name}\n")
        state = {
            "track_id": "test", "type": "feature", "status": "in_progress",
            "description": "t", "current_phase_index": 1, "current_task_index": 1,
            "execution_mode": execution_mode,
            "updated_at": _recent_iso(),
            "phases": [{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": name, "status": "failed", "retry_count": MAX_RETRIES,
                 "last_failure_summary": "boom"}]}],
        }
        if recovery_policy is not None:
            state["recovery_policy"] = recovery_policy
        save(d, state)
        return d

    def test_decision_present_when_interactive_and_policy_absent(self):
        # Byte-identical: legacy interactive track still gets the blob.
        d = self._recover_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertIn("decision", _out(cmd_recover, d))

    def test_decision_present_when_interactive_and_ask(self):
        d = self._recover_track(recovery_policy="ask")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertIn("decision", _out(cmd_recover, d))

    def test_decision_suppressed_when_auto_and_interactive(self):
        # The new behavior: auto-policy on an INTERACTIVE track suppresses the
        # ask — recovery is now automated without giving up checkpoint pausing.
        d = self._recover_track(execution_mode="interactive", recovery_policy="auto")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertNotIn("decision", _out(cmd_recover, d))


class StepExhaustionRoutingTests(TestCase):
    """The step-spine exhaustion sites (``_step_emit_exhausted`` +
    ``_emit_quiescent_leaf``) route to ``dispatch_skip_analyst`` on an auto-
    routing track and to an ``ask`` on an ask-surface track."""

    def test_interactive_ask_surfaces_ask_blob(self):
        d = _failed_exhausted_state(execution_mode="interactive", recovery_policy="ask")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "ask")
        self.assertIn("decision", o)

    def test_interactive_absent_surfaces_ask_blob_byte_identical(self):
        # Legacy interactive track (no recovery_policy) — unchanged behavior.
        d = _failed_exhausted_state(execution_mode="interactive")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "ask")
        self.assertIn("decision", o)

    def test_auto_interactive_routes_to_skip_analyst(self):
        # Headline: interactive track, auto policy → skip-analyst, NOT an ask.
        d = _failed_exhausted_state(execution_mode="interactive", recovery_policy="auto")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "dispatch_skip_analyst")
        self.assertEqual(o["agent"], "skip-analyst")
        # execution_mode is carried through unchanged (still interactive — the
        # track still pauses at checkpoints; only recovery was automated).
        self.assertEqual(o["execution_mode"], "interactive")

    def test_auto_continuous_routes_to_skip_analyst(self):
        d = _failed_exhausted_state(execution_mode="continuous", recovery_policy="auto")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "dispatch_skip_analyst")


class NewTrackDefaultTests(TestCase):
    """A freshly-initialized track defaults to ``recovery_policy=auto``."""

    def _init_track(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "-C", d, "init", "-q"], check=True, capture_output=True)
        Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
        return d

    def test_init_defaults_to_auto(self):
        d = self._init_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _out(cmd_init_from_plan, d, "default_20260808", "feature", "t")
        self.assertEqual(load(d).get("recovery_policy"), "auto")


class SetRecoveryPolicyTests(TestCase):
    """``cmd_set_recovery_policy`` validates against the closed vocab and mutates."""

    def setUp(self):
        d = tempfile.mkdtemp()
        Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
        save(d, {"track_id": "t", "type": "feature", "status": "in_progress",
                 "description": "t", "current_phase_index": 1,
                 "current_task_index": 1, "updated_at": _recent_iso(),
                 "execution_mode": "interactive", "recovery_policy": "auto",
                 "phases": [{"name": "P1", "status": "pending", "tasks": [
                     {"name": "Task A", "status": "pending"}]}]})
        self.d = d
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

    def test_valid_policy_mutates_and_reports_previous(self):
        o = _out(cmd_set_recovery_policy, self.d, "ask")
        self.assertTrue(o["ok"])
        self.assertEqual(o["recovery_policy"], "ask")
        self.assertEqual(o["previous"], "auto")
        self.assertEqual(load(self.d).get("recovery_policy"), "ask")

    def test_absent_field_previous_reads_as_ask(self):
        # A legacy track (no field) → previous reads as "ask" (byte-identical
        # default), even though new tracks now init to "auto".
        save(self.d, {"track_id": "t", "type": "feature", "status": "in_progress",
                      "description": "t", "current_phase_index": 1,
                      "current_task_index": 1, "updated_at": _recent_iso(),
                      "execution_mode": "interactive",
                      "phases": [{"name": "P1", "status": "pending", "tasks": [
                          {"name": "Task A", "status": "pending"}]}]})
        o = _out(cmd_set_recovery_policy, self.d, "auto")
        self.assertEqual(o["previous"], "ask")
        self.assertEqual(o["recovery_policy"], "auto")

    def test_invalid_policy_rejected(self):
        o = _out(cmd_set_recovery_policy, self.d, "bogus")
        self.assertFalse(o["ok"])  # error response, ok: False
        self.assertIn("recovery_policy", o["error"])
        # State untouched on rejection.
        self.assertEqual(load(self.d).get("recovery_policy"), "auto")

    def test_missing_policy_rejected(self):
        o = _out(cmd_set_recovery_policy, self.d, None)
        self.assertFalse(o["ok"])
        self.assertEqual(load(self.d).get("recovery_policy"), "auto")

    def test_vocab_constant_is_ask_auto(self):
        self.assertEqual(RECOVERY_POLICIES, ("ask", "auto"))


if __name__ == "__main__":
    main()
