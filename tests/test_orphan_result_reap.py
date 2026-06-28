r"""Tests for orphan result.json reaping in cmd_recover (Gap #3).

A crashed finalize can leave ``.conductor/result.json`` behind (the
"preserved-on-commit-failure" paths), and ``dispatch-finalize`` reads the file
on existence — so a stale orphan would be misread as the current task's result.
``cmd_recover`` now reaps it whenever there is no active ``in_progress`` task
(no active task, or the resolved target isn't in_progress). The ``in_progress``
case is left untouched — dispatch-finalize owns consuming a legitimate pending
result.
"""
import io
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.dispatch import cmd_recover
from scripts.track_state.core import load


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _write_track(d, task_status):
    tdir = Path(d) / "conductor" / "tracks" / "orphan_20260628"
    tdir.mkdir(parents=True, exist_ok=True)
    state = {
        "track_id": "orphan_20260628", "type": "feature", "status": "in_progress",
        "description": "test", "execution_mode": "interactive",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{"name": "Phase 1", "status": "pending",
                    "tasks": [{"name": "Task A", "status": task_status}]}],
    }
    (tdir / "track-state.json").write_text(json.dumps(state))
    (tdir / "plan.md").write_text("# Plan\n\n## Phase 1: Phase 1\n- [ ] Task A\n")
    res = tdir / ".conductor" / "result.json"
    res.parent.mkdir(parents=True, exist_ok=True)
    res.write_text('{"status":"SUCCESS"}')  # an orphan/leftover
    return tdir, res


def _capture(fn, *a, **kw):
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*a, **kw)
        return sys.stdout.getvalue()
    finally:
        sys.stdout = old


class OrphanReapTests(TestCase):
    def test_recover_reaps_orphan_when_task_not_in_progress(self):
        with tempfile.TemporaryDirectory() as d:
            tdir, res = _write_track(d, "pending")  # not in_progress → reap
            _capture(cmd_recover, str(tdir))
            self.assertFalse(res.exists())

    def test_recover_reaps_orphan_when_no_active_task(self):
        """All-terminal track → auto-fix clears current indices → no active task → reap."""
        with tempfile.TemporaryDirectory() as d:
            tdir, res = _write_track(d, "completed")
            _capture(cmd_recover, str(tdir))
            self.assertFalse(res.exists())

    def test_recover_preserves_result_for_in_progress_task(self):
        """A result.json for the active in_progress task belongs to dispatch-finalize."""
        with tempfile.TemporaryDirectory() as d:
            tdir, res = _write_track(d, "in_progress")
            _capture(cmd_recover, str(tdir))
            self.assertTrue(res.exists())


if __name__ == "__main__":
    main()
