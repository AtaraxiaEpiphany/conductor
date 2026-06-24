"""Tests for core.transaction (read-modify-write race fix) and the F1 code guard.

Covers the two halves of the Tier-1 integrity fix:
  * ``transaction()`` holds LOCK_EX across load+save so concurrent mutators
    can't clobber each other (the classic lost-update race the old separate
    load()/save() left open).
  * ``_do_lock`` enforces F1 (Global State Lock) in code, not just in prompts:
    at most one in_progress task (or one parent [~] + one active child [~]).
"""
import io
import json
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save, transaction
from scripts.track_state.mutations import _do_lock, _do_complete, F1StateLockError


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_state(**overrides):
    state = {
        "track_id": "test",
        "type": "feature",
        "status": "in_progress",
        "description": "test track",
        "current_phase_index": 1,
        "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{
            "name": "Phase 1",
            "status": "pending",
            "tasks": [
                {"name": "Task A", "status": "pending"},
                {"name": "Task B", "status": "pending"},
            ],
        }],
    }
    state.update(overrides)
    return state


def _state_with_subtasks():
    """One parent task with two pending subtasks."""
    return _make_state(phases=[{
        "name": "Phase 1",
        "status": "pending",
        "tasks": [{
            "name": "Parent Task",
            "status": "pending",
            "subtasks": [
                {"name": "Subtask 1", "status": "pending"},
                {"name": "Subtask 2", "status": "pending"},
            ],
        }],
    }])


def _make_track_dir(state=None):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n- [ ] Task B\n")
    if state:
        save(d, state)
    return d


class TestTransaction(TestCase):
    """transaction() serializes load→mutate→save and aborts cleanly on error."""

    def test_transaction_persists_clean_mutation(self):
        d = _make_track_dir(_make_state())
        with transaction(d) as st:
            st["phases"][0]["tasks"][0]["status"] = "in_progress"
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["status"], "in_progress")

    def test_transaction_aborts_on_exception_leaves_state_unchanged(self):
        d = _make_track_dir(_make_state())
        original_track_id = load(d)["track_id"]
        with self.assertRaises(RuntimeError):
            with transaction(d) as st:
                st["track_id"] = "MUTATED"
                raise RuntimeError("boom")
        # The in-memory mutation is discarded; on-disk state is untouched.
        self.assertEqual(load(d)["track_id"], original_track_id)

    def test_transaction_serializes_concurrent_writes(self):
        """N threads each increment a counter M times via transaction().

        With serialization (LOCK_EX held across read+write) the final count is
        exactly N*M. The old load()/save() pattern released the lock between
        read and write, so concurrent increments lost updates (count < N*M).
        """
        try:
            import fcntl  # noqa: F401
        except ImportError:
            self.skipTest("fcntl unavailable — locking is a no-op on this platform")
        d = _make_track_dir(_make_state())
        s = load(d)
        s["counter"] = 0
        save(d, s)

        n_threads, n_incr = 4, 50

        def worker():
            for _ in range(n_incr):
                with transaction(d) as st:
                    st["counter"] = st.get("counter", 0) + 1

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(load(d)["counter"], n_threads * n_incr)
        shutil.rmtree(d)


class TestF1GlobalStateLock(TestCase):
    """_do_lock enforces F1 in code: one in_progress task, or parent+child."""

    def test_rejects_second_in_progress_task(self):
        d = _make_track_dir(_make_state())
        _do_lock(d, 1, 1)  # Task A → in_progress
        with self.assertRaises(F1StateLockError):
            _do_lock(d, 1, 2)  # Task B is foreign
        # Task B untouched (the violating transaction aborted)
        self.assertEqual(load(d)["phases"][0]["tasks"][1]["status"], "pending")

    def test_rejects_second_sibling_subtask(self):
        d = _make_track_dir(_state_with_subtasks())
        _do_lock(d, 1, 1, 1)  # Subtask 1 → parent [~] + S1 [~]
        with self.assertRaises(F1StateLockError):
            _do_lock(d, 1, 1, 2)  # Subtask 2 is a foreign sibling

    def test_allows_relock_same_task_resume(self):
        d = _make_track_dir(_make_state())
        _do_lock(d, 1, 1)
        _do_lock(d, 1, 1)  # re-locking the already-in_progress task is a resume
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["status"], "in_progress")

    def test_allows_subtask_lock_promotes_parent(self):
        """Locking a subtask is permitted: parent [~] + child [~] is the F1-sanctioned pair."""
        d = _make_track_dir(_state_with_subtasks())
        _do_lock(d, 1, 1, 1)  # no raise
        state = load(d)
        self.assertEqual(state["phases"][0]["tasks"][0]["status"], "in_progress")
        self.assertEqual(state["phases"][0]["tasks"][0]["subtasks"][0]["status"], "in_progress")

    def test_allows_next_sibling_after_prior_completes(self):
        """Sequential subtask flow: after S1 completes (parent still [~]), S2 locks."""
        d = _make_track_dir(_state_with_subtasks())
        _do_lock(d, 1, 1, 1)
        _do_complete(d, 1, 1, 1, "abc1234")  # S1 done; parent NOT auto-completed (S2 pending)
        _do_lock(d, 1, 1, 2)  # parent is in_progress but excluded; S2 is the new target
        self.assertEqual(
            load(d)["phases"][0]["tasks"][0]["subtasks"][1]["status"], "in_progress")

    def test_f1_error_mentions_recovery(self):
        d = _make_track_dir(_make_state())
        _do_lock(d, 1, 1)
        with self.assertRaises(F1StateLockError) as cm:
            _do_lock(d, 1, 2)
        self.assertIn("F1", str(cm.exception))
        self.assertIn("validate", str(cm.exception))


if __name__ == "__main__":
    main()
