"""Tests for track-state quality-snapshot: aggregate per-track quality grading.

Read-only command, so each test builds a state dict, captures stdout, and
asserts the computed grades (completion / coverage / evidence / deviations).
"""
import io
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.misc import cmd_quality_snapshot


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_state(phases, **overrides):
    state = {
        "track_id": "test",
        "type": "feature",
        "status": "in_progress",
        "description": "test track",
        "current_phase_index": 1,
        "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": phases,
    }
    state.update(overrides)
    return state


def _task(name, status="pending", evidence=None, subtasks=None):
    t = {"name": name, "status": status}
    if evidence is not None:
        t["evidence"] = evidence
    if subtasks:
        t["subtasks"] = subtasks
    return t


def _make_track_dir(state):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n")
    save(d, state)
    return d


def _capture(fn, *args, **kwargs):
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


class TestCompletionBreakdown(TestCase):
    def test_status_counts_and_completion_pct(self):
        phases = [{"name": "P1", "status": "in_progress", "tasks": [
            _task("A", "completed"),
            _task("B", "completed"),
            _task("C", "pending"),
            _task("D", "failed"),
        ]}]
        d = _make_track_dir(_make_state(phases))
        r = _capture(cmd_quality_snapshot, d)
        self.assertEqual(r["total_units"], 4)
        self.assertEqual(r["by_status"], {"completed": 2, "pending": 1, "failed": 1})
        self.assertEqual(r["completion_pct"], 50.0)

    def test_empty_track(self):
        d = _make_track_dir(_make_state([]))
        r = _capture(cmd_quality_snapshot, d)
        self.assertEqual(r["total_units"], 0)
        self.assertEqual(r["by_status"], {})
        self.assertEqual(r["completion_pct"], 0.0)
        self.assertIsNone(r["coverage_mean"])
        self.assertIsNone(r["coverage_pass_pct"])


class TestCoverageAggregate(TestCase):
    def test_coverage_mean_and_pass_pct_over_non_exempt(self):
        phases = [{"name": "P1", "status": "in_progress", "tasks": [
            _task("A", "completed", evidence={"coverage_pct": 90}),
            _task("B", "completed", evidence={"coverage_pct": 60}),
            _task("C", "completed", evidence={"coverage_pct": 80}),
        ]}]
        d = _make_track_dir(_make_state(phases))
        r = _capture(cmd_quality_snapshot, d)
        # mean of [90,60,80] = 76.7
        self.assertEqual(r["coverage_mean"], 76.7)
        # 2 of 3 at >= 80
        self.assertEqual(r["coverage_pass_pct"], 66.7)
        self.assertEqual(r["code_tasks_completed"], 3)

    def test_exempt_tasks_excluded_from_coverage(self):
        phases = [{"name": "P1", "status": "in_progress", "tasks": [
            _task("[Docs] Write docs", "completed", evidence={"coverage_pct": 100}),
            _task("[Config] Tune", "completed", evidence={"coverage_pct": 100}),
            _task("Real code", "completed", evidence={"coverage_pct": 90}),
        ]}]
        d = _make_track_dir(_make_state(phases))
        r = _capture(cmd_quality_snapshot, d)
        # Only "Real code" counts toward coverage
        self.assertEqual(r["code_tasks_completed"], 1)
        self.assertEqual(r["coverage_mean"], 90.0)
        self.assertEqual(r["coverage_pass_pct"], 100.0)

    def test_incomplete_tasks_not_counted_as_code_tasks(self):
        # Coverage is aggregated only over completed non-exempt tasks.
        phases = [{"name": "P1", "status": "in_progress", "tasks": [
            _task("A", "completed", evidence={"coverage_pct": 90}),
            _task("B", "pending", evidence={"coverage_pct": 90}),
        ]}]
        d = _make_track_dir(_make_state(phases))
        r = _capture(cmd_quality_snapshot, d)
        self.assertEqual(r["code_tasks_completed"], 1)
        self.assertEqual(r["coverage_mean"], 90.0)

    def test_no_completed_code_tasks_yields_none_coverage(self):
        phases = [{"name": "P1", "status": "in_progress", "tasks": [
            _task("A", "pending"),
            _task("[Docs] B", "completed", evidence={"coverage_pct": 100}),
        ]}]
        d = _make_track_dir(_make_state(phases))
        r = _capture(cmd_quality_snapshot, d)
        self.assertIsNone(r["coverage_mean"])
        self.assertIsNone(r["coverage_pass_pct"])
        self.assertEqual(r["code_tasks_completed"], 0)


class TestEvidenceAndDeviations(TestCase):
    def test_tasks_missing_evidence(self):
        # completed task with no evidence dict counts as a gap;
        # completed task WITH evidence dict (even if coverage_pct absent) does not.
        phases = [{"name": "P1", "status": "in_progress", "tasks": [
            _task("A", "completed"),  # no evidence → gap
            _task("B", "completed", evidence={"coverage_pct": 90}),  # ok
            _task("C", "completed", evidence={"deviations": 0}),  # ok (evidence present)
        ]}]
        d = _make_track_dir(_make_state(phases))
        r = _capture(cmd_quality_snapshot, d)
        self.assertEqual(r["tasks_missing_evidence"], 1)

    def test_spec_deviation_sum_includes_subtasks(self):
        phases = [{"name": "P1", "status": "in_progress", "tasks": [
            _task("A", "completed", evidence={"coverage_pct": 90, "deviations": 2},
                  subtasks=[_task("A.1", "completed",
                                  evidence={"coverage_pct": 90, "deviations": 1})]),
            _task("B", "completed", evidence={"coverage_pct": 90, "deviations": 0}),
        ]}]
        d = _make_track_dir(_make_state(phases))
        r = _capture(cmd_quality_snapshot, d)
        # 2 (A) + 1 (A.1) + 0 (B) = 3
        self.assertEqual(r["spec_deviations"], 3)


class TestSubtasks(TestCase):
    def test_subtasks_counted_as_units(self):
        phases = [{"name": "P1", "status": "in_progress", "tasks": [
            _task("A", "in_progress",
                  subtasks=[_task("A.1", "completed", evidence={"coverage_pct": 90}),
                            _task("A.2", "pending")]),
        ]}]
        d = _make_track_dir(_make_state(phases))
        r = _capture(cmd_quality_snapshot, d)
        # parent + 2 subtasks = 3 units; 1 completed
        self.assertEqual(r["total_units"], 3)
        self.assertEqual(r["by_status"]["completed"], 1)
        self.assertEqual(r["completion_pct"], 33.3)
        self.assertEqual(r["coverage_mean"], 90.0)


class TestACIntegrityFields(TestCase):
    """quality-snapshot now carries AC coverage rates + advisory gate (None/N/A
    when no spec.md, computed when present)."""

    def test_ac_fields_none_when_no_spec(self):
        d = _make_track_dir(_make_state([{"name": "P1", "status": "in_progress", "tasks": [
            _task("A", "completed", evidence={"coverage_pct": 90}),
        ]}]))
        r = _capture(cmd_quality_snapshot, d)
        self.assertIsNone(r["ac_tc_coverage_rate"])
        self.assertIsNone(r["ac_traceability_rate"])
        self.assertIsNone(r["ac_verification_rate"])
        self.assertEqual(r["ac_integrity_gate"], "N/A")

    def test_ac_fields_computed_when_spec_present(self):
        phases = [{"name": "Phase 1", "status": "in_progress", "tasks": [
            _task("Task a", "completed",
                  evidence={"coverage_pct": 90, "tc_coverage": "TC-1.1"}),
            _task("[Manual] verify P1", "pending"),
        ]}]
        d = _make_track_dir(_make_state(phases))
        Path(d, "spec.md").write_text(
            "# S\n## Acceptance Criteria\n- AC-1: x\n"
            "## Test Scenarios\n| ID | AC Ref | S | O |\n| -- | -- | -- | -- |\n| TC-1.1 | AC-1 | x | y |\n")
        Path(d, "plan.md").write_text(
            "# P\n## Phase 1: B\n- [ ] Task a <!-- AC-1, TC-1.1 -->\n- [ ] [Manual] verify P1\n")
        r = _capture(cmd_quality_snapshot, d)
        self.assertEqual(r["ac_tc_coverage_rate"], 100.0)
        self.assertEqual(r["ac_traceability_rate"], 100.0)
        self.assertEqual(r["ac_verification_rate"], 100.0)
        self.assertEqual(r["ac_integrity_gate"], "PASS")


if __name__ == "__main__":
    main()
