r"""Tests for the stuck-lock reaper (Gap #2).

``_do_lock`` now stamps ``locked_at`` (epoch) on the task — a heartbeat. A task
still ``in_progress`` past ``STALE_LOCK_SECONDS`` (30 min) is a killed-session
orphan: ``_fix_stale_lock`` reaps it to ``pending`` so the next ``recover``
unblocks in minutes instead of waiting for the 24h whole-state reaper (which
still governs legacy state without ``locked_at``). The reap PRESERVES
``retry_count`` / ``last_failure_summary`` / ``commit_sha`` — a stale lock is
recovery, not a reset, so a re-dispatched attempt still counts against the
per-task budget (the explicit ``reset`` command, not the reaper, wipes history).
"""
import io
import json
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.mutations import _do_lock
from scripts.track_state.validate import _fix_stale_lock
from scripts.track_state.dispatch import cmd_recover
from scripts.track_state.core import load
from scripts.track_state.constants import LOCKED_AT_FIELD


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _state(locked_at="skip", task_status="in_progress"):
    """One-phase/one-task track; locked_at='skip' omits the field.

    retry_count=2 + commit_sha set so a surviving value is distinguishable
    from a default/zeroed one (pins the history-preservation contract).
    """
    task = {"name": "Task A", "status": task_status, "retry_count": 2,
            "commit_sha": "abc1234", "last_failure_summary": "boom"}
    if locked_at != "skip":
        task[LOCKED_AT_FIELD] = locked_at
    return {
        "track_id": "stale_20260628", "type": "feature", "status": "in_progress",
        "description": "test", "execution_mode": "interactive",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{"name": "Phase 1", "status": "pending", "tasks": [task]}],
    }


def _write_track(d, state):
    tdir = Path(d) / "conductor" / "tracks" / state["track_id"]
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "track-state.json").write_text(json.dumps(state))
    # plan.md is required by _do_sync_plan (run when recover applies fixes).
    (tdir / "plan.md").write_text("# Plan\n\n## Phase 1: Phase 1\n- [ ] Task A\n")
    return tdir


def _capture(fn, *a, **kw):
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*a, **kw)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


class LockHeartbeatTests(TestCase):
    def test_lock_sets_locked_at(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = _write_track(d, _state(task_status="pending"))
            before = time.time()
            _do_lock(str(tdir), 1, 1)
            after = time.time()
            locked_at = load(str(tdir))["phases"][0]["tasks"][0][LOCKED_AT_FIELD]
            self.assertIsInstance(locked_at, float)
            self.assertGreaterEqual(locked_at, before)
            self.assertLessEqual(locked_at, after)


class StaleLockReaperTests(TestCase):
    def test_stale_lock_is_reaped(self):
        st = _state(locked_at=time.time() - 3600)  # 1h old, past 30min threshold
        fixes = _fix_stale_lock(st)
        reaped = st["phases"][0]["tasks"][0]
        self.assertEqual(reaped["status"], "pending")
        self.assertEqual(len(fixes), 1)
        self.assertIn("stale lock", fixes[0])
        # History survives the reap (the core fix for the user's bug).
        self.assertEqual(reaped["retry_count"], 2)
        self.assertEqual(reaped["commit_sha"], "abc1234")
        self.assertEqual(reaped["last_failure_summary"], "boom")

    def test_reap_preserves_retry_history(self):
        """The regression at the heart of the user's report: a cleared session
        followed by a re-run must NOT zero retry_count/commit_sha. A stale lock
        is recovery, not a reset."""
        st = _state(locked_at=time.time() - 3600)
        _fix_stale_lock(st)
        reaped = st["phases"][0]["tasks"][0]
        self.assertEqual(reaped["status"], "pending")
        self.assertEqual(reaped["retry_count"], 2)
        self.assertEqual(reaped["commit_sha"], "abc1234")
        self.assertNotIn(LOCKED_AT_FIELD, reaped)  # pending → no lock heartbeat

    def test_fresh_lock_not_reaped(self):
        st = _state(locked_at=time.time())
        self.assertEqual(_fix_stale_lock(st), [])
        self.assertEqual(st["phases"][0]["tasks"][0]["status"], "in_progress")

    def test_missing_locked_at_not_reaped(self):
        """Legacy state without locked_at is left to the 24h updated_at reaper."""
        st = _state(locked_at="skip")
        self.assertEqual(_fix_stale_lock(st), [])
        self.assertEqual(st["phases"][0]["tasks"][0]["status"], "in_progress")

    def test_non_numeric_locked_at_not_reaped(self):
        """A corrupt (non-numeric) locked_at is ignored, not crashed on."""
        st = _state(locked_at="oops")
        self.assertEqual(_fix_stale_lock(st), [])

    def test_recover_surfaces_stale_lock_reap(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = _write_track(d, _state(locked_at=time.time() - 3600))
            out = _capture(cmd_recover, str(tdir))
            # The reaped task surfaces as pending (dispatchable), and the fix is
            # reported in fixes_applied so the orchestrator/user sees the recovery.
            self.assertIn("fixes_applied", out)
            self.assertTrue(any("stale lock" in f for f in out["fixes_applied"]))
            reaped = load(str(tdir))["phases"][0]["tasks"][0]
            self.assertEqual(reaped["status"], "pending")
            # History survives the full recover path, not just the unit reaper.
            self.assertEqual(reaped["retry_count"], 2)


if __name__ == "__main__":
    main()
