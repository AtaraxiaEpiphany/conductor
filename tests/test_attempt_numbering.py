"""Tests for state-owned attempt numbering in the handoff execution record.

``_append_execution_record`` derives the ``### Attempt {n}/{m}`` label from the
task's ``retry_count`` in the post-transition state — never from the
executor's result.json self-report (``--attempt``/``--max-retries`` were
removed from the write-result contract: an under-reported attempt was the
duplicate-"Attempt 1/3" bug, since every retry defaulted back to 1).

FAILURE renders the just-incremented ``retry_count``; SUCCESS renders
``retry_count + 1`` (the attempt that succeeded); the ceiling comes from
``task_max_retries`` (task-level override → shape → global). A corrupt state
target falls back to the legacy result fields rather than crashing.
"""
import tempfile
from pathlib import Path
from shutil import rmtree
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.handoff import _append_execution_record


def _make_track(retry_count=0, max_retries=None, status="pending"):
    """Fabricated track whose P1.T1 carries the given retry budget state."""
    d = tempfile.mkdtemp()
    task = {"name": "Task A", "status": status, "retry_count": retry_count}
    if max_retries is not None:
        task["max_retries"] = max_retries
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    save(d, {
        "track_id": "auth", "type": "feature", "status": "in_progress",
        "description": "Add token refresh", "current_phase_index": 1,
        "current_task_index": 1, "updated_at": "2026-09-03T00:00:00+00:00",
        "phases": [{"name": "Phase 1", "status": "pending",
                    "tasks": [task, {"name": "Task B", "status": "pending"}]}],
    })
    return d


def _record(self, d, result):
    """Append the record and return the handoff body."""
    _append_execution_record(d, 1, 1, None, result)
    return (Path(d) / ".conductor" / "handoff" / "P1T1.md").read_text()


class AttemptNumberingTests(TestCase):
    def setUp(self):
        self.d = _make_track()
        self.addCleanup(rmtree, self.d, ignore_errors=True)

    def test_failure_fresh_renders_attempt_1(self):
        # Post-_do_fail state: retry_count already incremented to 1.
        state_task_retry = _make_track(retry_count=1, status="failed")
        self.addCleanup(rmtree, state_task_retry, ignore_errors=True)
        body = _record(self, state_task_retry, {
            "status": "FAILURE", "task_name": "Task A",
            "failure_detail": {"what_was_done": "w", "failure_reason": "r",
                               "suggested_next_step": "s"},
        })
        self.assertIn("### Attempt 1/3 |", body)
        self.assertIn("❌", body)

    def test_failure_retry_renders_attempt_2_not_self_report(self):
        # Second failure: state says retry_count=2; the executor's stale
        # self-report says 1 — state must win.
        d2 = _make_track(retry_count=2, status="failed")
        self.addCleanup(rmtree, d2, ignore_errors=True)
        body = _record(self, d2, {
            "status": "FAILURE", "task_name": "Task A", "attempt": 1,
            "failure_detail": {"what_was_done": "w", "failure_reason": "r",
                               "suggested_next_step": "s"},
        })
        self.assertIn("### Attempt 2/3 |", body)
        self.assertNotIn("### Attempt 1/3 |", body)

    def test_success_after_retry_renders_next_attempt(self):
        # One failure used (retry_count=1), then SUCCESS on attempt 2.
        d2 = _make_track(retry_count=1, status="pending")
        self.addCleanup(rmtree, d2, ignore_errors=True)
        body = _record(self, d2, {
            "status": "SUCCESS", "task_name": "Task A",
            "commit_sha": "abc1234", "summary": "done",
        })
        self.assertIn("### Attempt 2/3 |", body)
        self.assertIn("✅", body)

    def test_success_fresh_renders_attempt_1(self):
        body = _record(self, self.d, {
            "status": "SUCCESS", "task_name": "Task A",
            "commit_sha": "abc1234", "summary": "done",
        })
        self.assertIn("### Attempt 1/3 |", body)

    def test_task_level_budget_honored(self):
        d5 = _make_track(retry_count=2, max_retries=5, status="failed")
        self.addCleanup(rmtree, d5, ignore_errors=True)
        body = _record(self, d5, {
            "status": "FAILURE", "task_name": "Task A",
            "failure_detail": {"what_was_done": "w", "failure_reason": "r",
                               "suggested_next_step": "s"},
        })
        self.assertIn("### Attempt 2/5 |", body)

    def test_self_report_flags_ignored_entirely(self):
        # attempt/max_retries in result_data disagree with state on both axes;
        # the record must show the state-derived numbers only.
        d2 = _make_track(retry_count=1, status="pending")
        self.addCleanup(rmtree, d2, ignore_errors=True)
        body = _record(self, d2, {
            "status": "SUCCESS", "task_name": "Task A",
            "attempt": 9, "max_retries": 9, "commit_sha": "abc1234",
        })
        self.assertIn("### Attempt 2/3 |", body)
        self.assertNotIn("/9", body)

    def test_unresolvable_target_falls_back_to_result_field(self):
        # Phase beyond the plan → target() raises IndexError → the legacy
        # result-derived label keeps the write alive (fail-open).
        _append_execution_record(self.d, 9, 1, None, {
            "status": "FAILURE", "task_name": "Task A", "attempt": 4,
            "max_retries": 6,
            "failure_detail": {"what_was_done": "w", "failure_reason": "r",
                               "suggested_next_step": "s"},
        })
        body = (Path(self.d) / ".conductor" / "handoff" / "P9T1.md").read_text()
        self.assertIn("### Attempt 4/6 |", body)


if __name__ == "__main__":
    main()
