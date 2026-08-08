"""Tests for ``track-state view`` — the dashboard backend (read-only join).

Locks the envelope contract the renderer consumes and the one code-owned join a
dashboard/status skill renders from. ``cmd_view`` must assemble its envelope
from the EXISTING registry accessors (workflow_shapes / task_profiles), so a
project overlay shape / gate set renders for free — these tests pin that the
resolution flows through those accessors and not a re-derivation.
"""
import io
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.misc import cmd_view


def _out_captured(fn, *args, **kwargs):
    """Capture stdout that must be a single JSON object. Returns parsed dict."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _str_captured(fn, *args, **kwargs):
    """Capture stdout as a raw string (for the --render Unicode path)."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return sys.stdout.getvalue()
    finally:
        sys.stdout, sys.stderr = old_out, old_err


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
            "tasks": [{"name": "Task A", "status": "pending"}],
        }],
    }
    state.update(overrides)
    return state


def _make_track_dir(state):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    save(d, state)
    return d


class TestViewDefaultShape(TestCase):
    def test_default_shape_topology(self):
        env = _out_captured(cmd_view, _make_track_dir(_make_state()))
        rw = env["resolved_workflow"]
        self.assertEqual(rw["shape"], "default")
        self.assertEqual(rw["nodes"], ["spec-planner", "task-executor", "phase-checker"])
        self.assertEqual(rw["verifiers"], ["ac-tracer", "test-runner"])
        self.assertEqual(rw["gates"], ["tdd", "coverage", "checkpoint"])
        self.assertEqual(rw["verify_policy"], "checkpoint")
        # Track C4: the envelope carries the load-bearing verification paradigm
        # so a dashboard renders it from ONE join (never a 2nd parser). Default
        # shape = run + test (the code-track defaults).
        self.assertEqual(rw["checkpoint_policy"], "run")
        self.assertEqual(rw["ac_grounding"], "test")
        self.assertEqual(env["track"]["shape"], "default")

    def test_track_block_carries_identity(self):
        env = _out_captured(cmd_view, _make_track_dir(_make_state()))
        t = env["track"]
        self.assertEqual(t["track_id"], "test")
        self.assertEqual(t["type"], "feature")
        self.assertEqual(t["status"], "in_progress")
        self.assertEqual(t["shape"], "default")


class TestViewMigrationShape(TestCase):
    def test_migration_drops_tdd_coverage_gates_keeps_checkpoint(self):
        env = _out_captured(
            cmd_view, _make_track_dir(_make_state(workflow_shape="migration")))
        rw = env["resolved_workflow"]
        self.assertEqual(rw["shape"], "migration")
        # Spine topology is unchanged — only the gates differ.
        self.assertEqual(rw["nodes"], ["spec-planner", "task-executor", "phase-checker"])
        self.assertEqual(rw["gates"], ["checkpoint"])
        self.assertNotIn("tdd", rw["gates"])
        self.assertNotIn("coverage", rw["gates"])
        # Migration is NOT a code-free shape — test-runner still fans out.
        self.assertIn("test-runner", rw["verifiers"])


class TestViewDeliverableShape(TestCase):
    def test_deliverable_envelope_carries_review_grounding(self):
        # Track C4: the envelope surfaces the load-bearing verification paradigm.
        # A deliverable is review-grounded (ac_grounding=review) and runs its
        # checkpoint by default (checkpoint_policy=run, inherited).
        env = _out_captured(
            cmd_view, _make_track_dir(_make_state(workflow_shape="deliverable")))
        rw = env["resolved_workflow"]
        self.assertEqual(rw["ac_grounding"], "review")
        self.assertEqual(rw["checkpoint_policy"], "run")
        # ac-tracer only (test-runner dropped — no tests on a deliverable).
        self.assertEqual(rw["verifiers"], ["ac-tracer"])


class TestViewCodeFreePhase(TestCase):
    def test_code_free_current_phase_drops_test_runner(self):
        state = _make_state()
        # Current phase (1) is all [Config] tasks → code-free → no test-runner.
        state["phases"][0]["tasks"] = [
            {"name": "[Config] Set env vars", "status": "pending"},
            {"name": "[Config] Tweak defaults", "status": "pending"},
        ]
        env = _out_captured(cmd_view, _make_track_dir(state))
        rw = env["resolved_workflow"]
        self.assertNotIn("test-runner", rw["verifiers"])
        self.assertIn("ac-tracer", rw["verifiers"])

    def test_code_bearing_current_phase_keeps_test_runner(self):
        state = _make_state()
        # A [Refactor] task produces code → NOT code-free → test-runner stays.
        state["phases"][0]["tasks"] = [
            {"name": "[Config] Tweak defaults", "status": "pending"},
            {"name": "[Refactor] Extract module", "status": "pending"},
        ]
        env = _out_captured(cmd_view, _make_track_dir(state))
        self.assertIn("test-runner", env["resolved_workflow"]["verifiers"])


class TestViewPosition(TestCase):
    def test_in_progress_task_positions_at_it(self):
        state = _make_state()
        state["phases"] = [
            {"name": "Phase 1", "status": "completed",
             "tasks": [{"name": "Task 1.1", "status": "completed",
                        "commit_sha": "a1b2c3d"}]},
            {"name": "Phase 2", "status": "in_progress",
             "tasks": [{"name": "Task 2.1", "status": "completed",
                        "commit_sha": "d4e5f6g"},
                       {"name": "Task 2.2", "status": "in_progress"}]},
        ]
        state["current_phase_index"] = 2
        state["current_task_index"] = 2
        env = _out_captured(cmd_view, _make_track_dir(state))
        pos = env["resolved_workflow"]["position"]
        self.assertEqual(pos["phase"], 2)
        self.assertEqual(pos["task"], 2)
        self.assertEqual(pos["name"], "Task 2.2")
        self.assertEqual(pos["kind"], "task")

    def test_in_progress_parent_with_active_subtask_drills_to_subtask(self):
        state = _make_state()
        state["phases"] = [{
            "name": "Phase 1", "status": "in_progress",
            "tasks": [{"name": "Task 1.1", "status": "in_progress",
                       "subtasks": [
                           {"name": "Sub 1.1.1", "status": "completed"},
                           {"name": "Sub 1.1.2", "status": "in_progress"},
                       ]}],
        }]
        env = _out_captured(cmd_view, _make_track_dir(state))
        pos = env["resolved_workflow"]["position"]
        self.assertEqual((pos["phase"], pos["task"], pos["subtask"]), (1, 1, 2))
        self.assertEqual(pos["name"], "Sub 1.1.2")
        self.assertEqual(pos["kind"], "subtask")

    def test_no_active_task_falls_back_to_cursor(self):
        # All pending, no in_progress → cursor fallback.
        env = _out_captured(cmd_view, _make_track_dir(_make_state()))
        pos = env["resolved_workflow"]["position"]
        self.assertEqual(pos["phase"], 1)
        self.assertEqual(pos["task"], 1)
        self.assertEqual(pos["kind"], "cursor")


class TestViewTaskTree(TestCase):
    def test_tree_walks_phases_tasks_subtasks(self):
        state = _make_state()
        state["phases"] = [
            {"name": "Phase 1", "status": "completed",
             "tasks": [{"name": "[Config] Task 1.1", "status": "completed",
                        "commit_sha": "a1b2c3d", "retry_count": 0,
                        "max_retries": 3, "task_type": "Config"}]},
            {"name": "Phase 2", "status": "in_progress",
             "tasks": [{"name": "Task 2.1", "status": "in_progress",
                        "subtasks": [
                            {"name": "Sub 2.1.1", "status": "pending"},
                            {"name": "Sub 2.1.2", "status": "completed",
                             "commit_sha": "d4e5f6g"}],
                        "retry_count": 1, "max_retries": 3}]},
        ]
        env = _out_captured(cmd_view, _make_track_dir(state))
        tree = env["task_tree"]
        self.assertEqual(len(tree), 2)
        self.assertEqual(tree[0]["index"], 1)
        self.assertEqual(tree[0]["status"], "completed")
        t11 = tree[0]["tasks"][0]
        self.assertEqual(t11["status"], "completed")
        self.assertEqual(t11["commit_sha"], "a1b2c3d")
        self.assertEqual(t11["task_type"], "Config")
        # Subtask recursion + per-unit fields.
        sub12 = tree[1]["tasks"][0]["subtasks"][1]
        self.assertEqual(sub12["status"], "completed")
        self.assertEqual(sub12["commit_sha"], "d4e5f6g")
        self.assertEqual(tree[1]["tasks"][0]["retry_count"], 1)
        self.assertEqual(tree[1]["tasks"][0]["max_retries"], 3)


class TestViewQuality(TestCase):
    def test_completion_and_ac_integrity_keys(self):
        state = _make_state()
        state["phases"][0]["tasks"] = [
            {"name": "Task A", "status": "completed"},
            {"name": "Task B", "status": "pending"},
            {"name": "Task C", "status": "completed"},
        ]
        env = _out_captured(cmd_view, _make_track_dir(state))
        q = env["quality"]
        # 2 of 3 done (tasks only).
        self.assertAlmostEqual(q["completion_pct"], 66.7, places=1)
        # No spec.md in the temp dir → AC-integrity degrades (key present, not a crash).
        self.assertIn("ac_integrity", q)


class TestViewRender(TestCase):
    def test_render_contains_nodes_gates_position(self):
        state = _make_state()
        state["phases"] = [
            {"name": "Phase 1", "status": "completed",
             "tasks": [{"name": "Task 1.1", "status": "completed",
                        "commit_sha": "a1b2c3d"}]},
            {"name": "Phase 2", "status": "in_progress",
             "tasks": [{"name": "Task 2.2", "status": "in_progress"}]},
        ]
        state["current_phase_index"] = 2
        state["current_task_index"] = 1
        rendered = _str_captured(cmd_view, _make_track_dir(state), render=True)
        # Spine node names render from the resolved shape (not hardcoded prose).
        for node in ("spec-planner", "task-executor", "phase-checker"):
            self.assertIn(node, rendered)
        # Gates row with on/off glyphs.
        self.assertIn("F2", rendered)
        self.assertIn("F5", rendered)
        self.assertTrue("▣" in rendered or "▢" in rendered)
        # Position line points at the active task.
        self.assertIn("Phase 2", rendered)
        self.assertIn("Task 2.2", rendered)

    def test_render_migration_shape_gates_dim(self):
        rendered = _str_captured(
            cmd_view, _make_track_dir(_make_state(workflow_shape="migration")),
            render=True)
        # F5 checkpoint on; F2/F3 off for migration.
        self.assertIn("F5", rendered)
        self.assertIn("▢", rendered)

    def test_render_surfaces_nondefault_paradigm_not_default(self):
        # Track C4: the dashboard graph surfaces a NON-default verification
        # paradigm (so a review-grounded track is visible) but does NOT clutter a
        # standard code track (no paradigm line when both fields are default).
        deliverable = _str_captured(
            cmd_view, _make_track_dir(_make_state(workflow_shape="deliverable")),
            render=True)
        self.assertIn("ac_grounding: review", deliverable)
        default = _str_captured(
            cmd_view, _make_track_dir(_make_state()), render=True)
        self.assertNotIn("ac_grounding:", default)
        self.assertNotIn("checkpoint_policy:", default)


if __name__ == "__main__":
    main()
