"""Tests for dispatch._classify_task + dispatch-prepare tag routing.

_classify_task is the single source of truth for the Manual/Explore/default
routing decision shared by cmd_dispatch_next and cmd_dispatch_prepare (which
previously duplicated the tag-routing with two action vocabularies). These
tests pin the canonical category and confirm dispatch-prepare — previously
untested for routing — maps it correctly in both execution modes.
"""
import shutil
from unittest import TestCase, main

from scripts.track_state.dispatch import _classify_task, cmd_dispatch_prepare
# Reuse the canonical state/track-dir harness from the sibling suite.
from test_track_state import _make_state, _make_track_dir, _out_captured


class ClassifyTaskTests(TestCase):
    def test_manual(self):
        self.assertEqual(_classify_task(["Manual"]), "manual")

    def test_explore(self):
        self.assertEqual(_classify_task(["Explore"]), "explore")

    def test_default_executor(self):
        self.assertEqual(_classify_task([]), "executor")
        self.assertEqual(_classify_task(["Docs", "Config"]), "executor")

    def test_manual_takes_precedence_over_explore(self):
        # A task tagged both routes as Manual (human gate wins).
        self.assertEqual(_classify_task(["Manual", "Explore"]), "manual")


def _track(mode, task_name):
    state = _make_state(
        execution_mode=mode,
        current_phase_index=1,
        current_task_index=1,
        phases=[{
            "name": "Phase 1",
            "status": "pending",
            "tasks": [{"name": task_name, "status": "pending"}],
        }],
    )
    plan = f"# Plan\n\n## Phase 1: Build\n- [ ] {task_name}\n"
    return _make_track_dir(state, plan_content=plan)


class DispatchPrepareRoutingTests(TestCase):
    """dispatch-prepare maps _classify_task → its action vocabulary."""

    def _route(self, mode, task_name):
        d = _track(mode, task_name)
        try:
            result, _ = _out_captured(cmd_dispatch_prepare, d)
            return result
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_explore_routes_to_explore(self):
        result = self._route("interactive", "[Explore] map the foo boundary")
        self.assertEqual(result.get("action"), "explore")

    def test_plain_task_routes_to_execute(self):
        result = self._route("interactive", "implement the thing")
        self.assertEqual(result.get("action"), "execute")

    def test_manual_continuous_defers(self):
        result = self._route("continuous", "[Manual] verify UI")
        self.assertEqual(result.get("action"), "defer")

    def test_manual_interactive_surfaces(self):
        result = self._route("interactive", "[Manual] verify UI")
        self.assertEqual(result.get("action"), "manual_task")


if __name__ == "__main__":
    main()
