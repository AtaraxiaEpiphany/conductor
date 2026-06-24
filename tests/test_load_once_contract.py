"""Pins the load-once contract: _do_complete / _do_fail return their post-
transaction ``state`` dict alongside their scalar result, so the dispatch /
process-result hot paths can use it directly instead of re-loading.

The returned ``state`` must be exactly what a fresh ``load()`` returns — that
equivalence is what makes dropping the "reload state after _do_complete/_do_fail"
calls safe. If a future change reverts the mutators to a scalar return, or lets
the returned dict drift from disk, these tests fail.
"""
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save
from scripts.track_state.mutations import _do_complete, _do_fail


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _track_dir(state):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    save(d, state)
    return d


def _base_state():
    return {
        "track_id": "test", "type": "feature", "status": "in_progress",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{"name": "Phase 1", "status": "pending",
                    "tasks": [{"name": "Task A", "status": "pending"}]}],
    }


class MutatorReturnsStateTests(TestCase):
    def test_do_complete_returns_bool_plus_authoritative_state(self):
        d = _track_dir(_base_state())
        parent_completed, state = _do_complete(d, 1, 1, None, sha="abc1234")
        self.assertFalse(parent_completed)  # no parent to auto-complete
        # The returned dict must equal a fresh disk read — that's the
        # invariant that lets callers skip the post-mutation reload.
        self.assertEqual(state, load(d))
        self.assertEqual(state["phases"][0]["tasks"][0]["status"], "completed")

    def test_do_fail_returns_int_plus_authoritative_state(self):
        d = _track_dir(_base_state())
        retry_count, state = _do_fail(d, 1, 1, None, "boom")
        self.assertIsInstance(retry_count, int)
        self.assertEqual(state, load(d))
        # retryable + under MAX_RETRIES → re-queued as pending (see _do_fail)
        self.assertEqual(state["phases"][0]["tasks"][0]["status"], "pending")
        self.assertEqual(state["phases"][0]["tasks"][0]["retry_count"], retry_count)

    def test_returned_state_is_a_dict_not_a_tuple_element_leak(self):
        # Guard against accidentally returning e.g. (retry_count, None).
        d = _track_dir(_base_state())
        _, state = _do_complete(d, 1, 1, None, sha="abc1234")
        self.assertIsInstance(state, dict)
        self.assertIn("phases", state)


if __name__ == "__main__":
    main()
