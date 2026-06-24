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
from scripts.track_state.mutations import _do_fail
from scripts.track_state.constants import MAX_RETRIES


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
        # retry_count goes -1 → 0; 0 < MAX_RETRIES → re-queued as pending.
        d = _track_dir()
        retry_count, state = _do_fail(d, 1, 1, None, "boom")
        self.assertEqual(retry_count, 0)
        self.assertEqual(_task_status(d), "pending")
        self.assertEqual(state, load(d))  # returned state == fresh disk read

    def test_still_pending_just_under_threshold(self):
        # After MAX_RETRIES fails, retry_count = MAX_RETRIES-1 < MAX_RETRIES → pending.
        d = _track_dir()
        for _ in range(MAX_RETRIES):
            _do_fail(d, 1, 1, None, "boom")
        state = load(d)
        task = state["phases"][0]["tasks"][0]
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["retry_count"], MAX_RETRIES - 1)

    def test_flips_to_failed_at_threshold(self):
        # One more fail pushes retry_count to MAX_RETRIES → failed permanently.
        d = _track_dir()
        for _ in range(MAX_RETRIES + 1):
            _do_fail(d, 1, 1, None, "boom")
        task = load(d)["phases"][0]["tasks"][0]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["retry_count"], MAX_RETRIES)

    def test_manual_fail_is_immediately_failed(self):
        # retryable=False (manual CLI `track-state fail`) → failed at once,
        # regardless of how far retry_count is from the threshold.
        d = _track_dir()
        retry_count, state = _do_fail(d, 1, 1, None, "boom", retryable=False)
        self.assertEqual(retry_count, 0)
        self.assertEqual(_task_status(d), "failed")


if __name__ == "__main__":
    main()
