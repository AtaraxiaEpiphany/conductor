"""cmd_finalize must refuse false completion.

A track with any non-terminal task (pending/in_progress) must NOT be marked
`completed`. finalize sets it to `in_progress`, emits ok:False with an
`incomplete` list, and skips the quality score. blocked/failed are still
honored; a fully-terminal track (incl. `cancelled`) completes cleanly.

Regression for the `else: state["status"] = "completed"` branch (quality.py)
that let the orchestrator declare a track done with work outstanding.
"""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save
from scripts.track_state.quality import cmd_finalize


def _capture(fn, *args, **kwargs):
    """Capture the JSON a command prints to stdout. Returns the parsed dict."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _track(state):
    d = tempfile.mkdtemp()
    save(d, state)
    return d


def _state(statuses):
    """Build a single-phase state with one task per given status string."""
    return {
        "track_id": "fin_test_20260626",
        "type": "feature",
        "status": "in_progress",
        "description": "finalize test",
        "current_phase_index": 1,
        "current_task_index": 1,
        "updated_at": "2026-06-26T00:00:00+00:00",
        "phases": [{
            "name": "Phase 1",
            "status": "pending",
            "tasks": [
                {"name": f"Task {s}", "status": s} for s in statuses
            ],
        }],
    }


class TestFinalizeRefusesFalseCompletion(TestCase):

    def test_refuses_when_pending(self):
        d = _track(_state(["pending"]))
        result = _capture(cmd_finalize, d)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["status"], "in_progress")
        self.assertIn("incomplete", result)
        self.assertEqual(len(result["incomplete"]), 1)
        self.assertIn("pending", result["incomplete"][0])

        # The track must NOT be completed, so archive would refuse.
        state = load(d)
        self.assertEqual(state["status"], "in_progress")
        self.assertNotIn("quality_score", state)

    def test_refuses_when_in_progress(self):
        d = _track(_state(["completed", "in_progress"]))
        result = _capture(cmd_finalize, d)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["status"], "in_progress")
        # Only the non-terminal task is reported.
        self.assertEqual(len(result["incomplete"]), 1)
        self.assertIn("in_progress", result["incomplete"][0])

    def test_completes_when_all_terminal_incl_cancelled(self):
        # cancelled must count as a terminal-OK end-state — a fully-cancelled
        # track is a legitimate (if void) completion, not a fall-through to the
        # old `else: completed` bug.
        d = _track(_state(["completed", "skipped", "deferred", "cancelled"]))
        result = _capture(cmd_finalize, d)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "completed")
        self.assertIn("quality_score", result)

    def test_blocked_and_failed_paths_unaffected(self):
        d = _track(_state(["completed", "blocked"]))
        result = _capture(cmd_finalize, d)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "blocked")

        d2 = _track(_state(["completed", "failed"]))
        result2 = _capture(cmd_finalize, d2)
        self.assertTrue(result2["ok"], result2)
        self.assertEqual(result2["status"], "failed")


if __name__ == "__main__":
    main()
