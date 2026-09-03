"""Pins the ``_do_fail`` retry policy as executable documentation.

``_do_fail`` (mutations.py) is the single enforcer of the retry threshold: a
retryable failure re-queues the task as ``"pending"`` while ``retry_count`` stays
below ``MAX_RETRIES``, then flips it to ``"failed"`` at the threshold. The
implement skill, handoff, and dispatch-prepare all read ``MAX_RETRIES`` from
constants / track-state output rather than re-deriving it — these tests import
``MAX_RETRIES`` (never hardcode 3) so a bump keeps the boundary correct.

See also ``test_load_once_contract.py`` for the ``(retry_count, state)`` return
contract, and the ``MAX_RETRIES`` doc comment in ``track_state/constants.py``
for the full consumer list.
"""
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save
from scripts.track_state.mutations import _do_fail, cmd_set_max_retries
from scripts.track_state.constants import MAX_RETRIES, task_max_retries


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _track_dir():
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    save(d, {
        "track_id": "test", "type": "feature", "status": "in_progress",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{"name": "Phase 1", "status": "pending",
                    "tasks": [{"name": "Task A", "status": "pending"}]}],
    })
    return d


def _task_status(d):
    return load(d)["phases"][0]["tasks"][0]["status"]


class RetryPolicyTests(TestCase):
    def test_first_retryable_failure_requeues_as_pending(self):
        # retry_count is 1-based (counts failed attempts): absent → 1;
        # 1 < MAX_RETRIES → re-queued as pending.
        d = _track_dir()
        retry_count, state = _do_fail(d, 1, 1, None, "boom")
        self.assertEqual(retry_count, 1)
        self.assertEqual(_task_status(d), "pending")
        self.assertEqual(state, load(d))  # returned state == fresh disk read

    def test_still_pending_just_under_threshold(self):
        # After MAX_RETRIES-1 fails, retry_count = MAX_RETRIES-1 < MAX_RETRIES
        # → pending (one attempt of the budget remains).
        d = _track_dir()
        for _ in range(MAX_RETRIES - 1):
            _do_fail(d, 1, 1, None, "boom")
        state = load(d)
        task = state["phases"][0]["tasks"][0]
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["retry_count"], MAX_RETRIES - 1)

    def test_flips_to_failed_at_threshold(self):
        # The MAX_RETRIES-th fail pushes retry_count to MAX_RETRIES → failed
        # permanently. task_max_retries is an ATTEMPT budget (constants.py:
        # "how many attempts does this task get") — 1-based retry_count matches
        # it exactly, so the task gets exactly MAX_RETRIES dispatches.
        d = _track_dir()
        for _ in range(MAX_RETRIES):
            _do_fail(d, 1, 1, None, "boom")
        task = load(d)["phases"][0]["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["retry_count"], MAX_RETRIES)

    def test_manual_fail_is_immediately_failed(self):
        # retryable=False (manual CLI `track-state fail`) → failed at once,
        # regardless of how far retry_count is from the threshold.
        d = _track_dir()
        retry_count, state = _do_fail(d, 1, 1, None, "boom", retryable=False)
        self.assertEqual(retry_count, 1)
        self.assertEqual(_task_status(d), "failed")


def _track_dir_with_max_retries(max_retries):
    """A track whose first task carries a per-task ``max_retries`` override."""
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    save(d, {
        "track_id": "test", "type": "feature", "status": "in_progress",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{"name": "Phase 1", "status": "pending",
                    "tasks": [{"name": "Task A", "status": "pending",
                               "max_retries": max_retries}]}],
    })
    return d


class TaskMaxRetriesResolverTests(TestCase):
    def test_override_returned_when_valid(self):
        self.assertEqual(task_max_retries({"max_retries": 5}), 5)
        self.assertEqual(task_max_retries({"max_retries": 1}), 1)

    def test_global_fallback_when_absent(self):
        self.assertEqual(task_max_retries({}), MAX_RETRIES)

    def test_global_fallback_when_invalid(self):
        # Defensive: 0 / negative / non-int must not zero out the retry budget.
        self.assertEqual(task_max_retries({"max_retries": 0}), MAX_RETRIES)
        self.assertEqual(task_max_retries({"max_retries": -3}), MAX_RETRIES)
        self.assertEqual(task_max_retries({"max_retries": "3"}), MAX_RETRIES)
        self.assertEqual(task_max_retries(None), MAX_RETRIES)


class TaskMaxRetriesShapeChainTests(TestCase):
    """The three-tier chain: task.max_retries > shape max_retries > global.

    The shape tier is the per-job-family default (``workflow_shapes.
    max_retries_for``); ``task_max_retries`` resolves it when the caller
    threads the track's workflow shape name. Patched at the accessor (not a
    fabricated registry) so the chain logic is what's under test — the
    accessor's own real-registry behavior is pinned in
    test_workflow_shapes.MaxRetriesTests.
    """
    def _patched(self, shape_budget):
        from unittest import mock
        return mock.patch(
            "scripts.track_state.workflow_shapes.max_retries_for",
            return_value=shape_budget)

    def test_task_budget_wins_over_shape(self):
        with self._patched(1):
            self.assertEqual(task_max_retries({"max_retries": 5}, "migration"), 5)

    def test_shape_budget_used_when_task_absent_or_invalid(self):
        with self._patched(1):
            self.assertEqual(task_max_retries({}, "migration"), 1)
            self.assertEqual(task_max_retries({"max_retries": 0}, "migration"), 1)
            self.assertEqual(task_max_retries(None, "migration"), 1)

    def test_invalid_shape_budget_falls_to_global(self):
        # max_retries_for returns 0 for absent/malformed — 0 = inherit.
        with self._patched(0):
            self.assertEqual(task_max_retries({}, "migration"), MAX_RETRIES)

    def test_no_shape_argument_falls_to_global(self):
        # Back-compat: every pre-shape caller (no shape kwarg) is unchanged.
        self.assertEqual(task_max_retries({}), MAX_RETRIES)
        self.assertEqual(task_max_retries({}, None), MAX_RETRIES)
        self.assertEqual(task_max_retries({}, ""), MAX_RETRIES)


class PerTaskRetryBudgetTests(TestCase):
    def test_raised_budget_requeues_until_override(self):
        # max_retries=5 → requeue (pending) while retry_count < 5.
        d = _track_dir_with_max_retries(5)
        for _ in range(4):
            _do_fail(d, 1, 1, None, "boom")
        state = load(d)
        task = state["phases"][0]["tasks"][0]
        self.assertEqual(task["status"], "pending")  # retry_count 4 < 5
        self.assertEqual(task["retry_count"], 4)
        # The 5th fail flips to failed (retry_count 5 >= 5).
        _do_fail(d, 1, 1, None, "boom")
        self.assertEqual(_task_status(d), "failed")
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["retry_count"], 5)

    def test_lowered_budget_fails_immediately(self):
        # max_retries=1 → the budget IS one attempt: the first failure stores
        # retry_count 1 >= 1 → failed at once, no requeue.
        d = _track_dir_with_max_retries(1)
        _do_fail(d, 1, 1, None, "boom")
        self.assertEqual(_task_status(d), "failed")

    def test_absent_override_matches_global_behavior(self):
        # No max_retries field → identical to the global policy (still pending at
        # MAX_RETRIES-1 fails, failed at MAX_RETRIES).
        d = _track_dir()
        for _ in range(MAX_RETRIES - 1):
            _do_fail(d, 1, 1, None, "boom")
        self.assertEqual(_task_status(d), "pending")
        _do_fail(d, 1, 1, None, "boom")
        self.assertEqual(_task_status(d), "failed")


class SetMaxRetriesCliTests(TestCase):
    def test_writes_override_and_validates(self):
        import contextlib, io
        d = _track_dir()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_set_max_retries(d, 1, 1, None, max_retries=5)
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["max_retries"], 5)

    def test_rejects_non_positive(self):
        import contextlib, io
        d = _track_dir()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_set_max_retries(d, 1, 1, None, max_retries=0)
        out = buf.getvalue()
        self.assertIn("must be a positive integer", out)
        # Field not written on rejection.
        self.assertNotIn("max_retries", load(d)["phases"][0]["tasks"][0])


class ExecutorPromptCeilingTests(TestCase):
    """The task-executor prompt's ``MAX_RETRIES`` line must mirror the per-task
    ceiling (a raised budget), not the global constant — otherwise the executor
    perceives itself past budget on a late attempt even though the state-machine
    budget (the envelope's ``max_retries``) is still open. Both rails thread the
    per-task ceiling into ``pre``; ``_build_executor`` reads it back."""

    def test_prompt_uses_per_task_ceiling_from_pre(self):
        from scripts.track_state.dispatch import build_dispatch_prompt
        pre = dict(phase=1, task=1, name="[X] do work", tags=[], max_retries=5)
        _agent, prompt = build_dispatch_prompt(
            "dispatch_executor", "/td", pre=pre, attempt=4)
        self.assertIn("MAX_RETRIES=5", prompt)
        self.assertIn("ATTEMPT=4", prompt)
        self.assertNotIn(f"MAX_RETRIES={MAX_RETRIES}", prompt)

    def test_prompt_falls_back_to_global_when_pre_lacks_ceiling(self):
        from scripts.track_state.dispatch import build_dispatch_prompt
        pre = dict(phase=1, task=1, name="[X] do work", tags=[])
        _agent, prompt = build_dispatch_prompt(
            "dispatch_executor", "/td", pre=pre, attempt=1)
        self.assertIn(f"MAX_RETRIES={MAX_RETRIES}", prompt)


if __name__ == "__main__":
    main()
