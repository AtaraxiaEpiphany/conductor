"""Tests for wave._ready_set / _current_phase / _dep_satisfied (pure logic).

No git required — these exercise the deps-resolution + eligibility filters that
select which pending tasks may run in one worktree wave. The end-to-end
worktree lifecycle is covered by test_wave_dispatch.py / test_wave_finalize.py.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.track_state.core import save
from scripts.track_state.plan_parse import parse_plan
from scripts.track_state.wave import (
    _ready_set, _eligible_members, _current_phase, _dep_satisfied,
    _pending_ineligibility, DEFAULT_WAVE_SIZE,
)


def _state(phases):
    """Build a state dict from a compact phase spec.

    ``phases``: list of phase dicts; each task is ``{name, status, subtasks?}``.
    """
    return {
        "track_id": "t", "type": "feature", "status": "in_progress",
        "current_phase_index": 1, "current_task_index": 0,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "phases": [{"name": ph["name"],
                    "tasks": [dict(t) for t in ph["tasks"]]}
                   for ph in phases],
    }


def _parsed_with(plan_body):
    """Write plan_body to a temp plan.md, parse it, return (parsed, tmpdir)."""
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text(plan_body)
    return parse_plan(Path(d, "plan.md")), d


class TestDepSatisfied(unittest.TestCase):
    def test_completed_skipped_deferred_satisfy(self):
        st = _state([{"name": "P1", "tasks": [
            {"name": "T1", "status": "completed"},
            {"name": "T2", "status": "skipped"},
            {"name": "T3", "status": "deferred"},
            {"name": "T4", "status": "failed"},
            {"name": "T5", "status": "blocked"},
            {"name": "T6", "status": "pending"},
        ]}])
        for ti in (1, 2, 3):
            self.assertTrue(_dep_satisfied(st, 1, ti), f"T{ti} should satisfy")
        # failed / blocked / pending do NOT release a dependent.
        for ti in (4, 5, 6):
            self.assertFalse(_dep_satisfied(st, 1, ti), f"T{ti} should NOT satisfy")

    def test_dangling_dep_not_satisfied(self):
        st = _state([{"name": "P1", "tasks": [{"name": "T1", "status": "completed"}]}])
        self.assertFalse(_dep_satisfied(st, 9, 9))  # no such task


class TestCurrentPhase(unittest.TestCase):
    def test_first_phase_with_work(self):
        st = _state([
            {"name": "P1", "tasks": [{"name": "T1", "status": "completed"}]},
            {"name": "P2", "tasks": [{"name": "T2", "status": "pending"}]},
        ])
        self.assertEqual(_current_phase(st), 2)

    def test_all_terminal_returns_zero(self):
        st = _state([
            {"name": "P1", "tasks": [{"name": "T1", "status": "completed"},
                                     {"name": "T2", "status": "skipped"}]}])
        self.assertEqual(_current_phase(st), 0)

    def test_failed_is_terminal_for_phase_progression(self):
        # A failed (retries-exhausted) task releases the phase for progression.
        st = _state([{"name": "P1", "tasks": [{"name": "T1", "status": "failed"}]}])
        self.assertEqual(_current_phase(st), 0)


class TestReadySet(unittest.TestCase):
    def _plan(self, body):
        parsed, d = _parsed_with(body)
        self.addCleanup(shutil.rmtree, d)
        return parsed

    def test_eligible_task_with_satisfied_deps(self):
        body = ("# Plan\n\n## Phase 1: Build\n"
                "- [ ] Task A: foundation\n"
                "- [ ] Task B: consumer <!-- deps: P1.T1 -->\n")
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task A: foundation", "status": "completed"},
            {"name": "Task B: consumer", "status": "pending"}]}])
        ready = _ready_set(st, parsed, 1)
        self.assertEqual([(m["task"], m["name"]) for m in ready], [(2, "Task B: consumer")])

    def test_empty_deps_comment_opts_in_as_independent(self):
        # <!-- deps: --> with no refs = "I have no deps" → eligible.
        body = ("# Plan\n\n## Phase 1: Build\n"
                "- [ ] Task A: standalone <!-- deps: -->\n")
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task A: standalone", "status": "pending"}]}])
        ready = _ready_set(st, parsed, 1)
        self.assertEqual(len(ready), 1)

    def test_no_deps_comment_excluded(self):
        # Conservative: a task with no <!-- deps: --> comment is assumed
        # serial-order-dependent and kept on the serial spine.
        body = "# Plan\n\n## Phase 1: Build\n- [ ] Task A: plain\n"
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task A: plain", "status": "pending"}]}])
        self.assertEqual(_ready_set(st, parsed, 1), [])

    def test_unsatisfied_dep_excluded(self):
        body = ("# Plan\n\n## Phase 1: Build\n"
                "- [ ] Task A: foundation\n"
                "- [ ] Task B: consumer <!-- deps: P1.T1 -->\n")
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task A: foundation", "status": "pending"},  # not done
            {"name": "Task B: consumer", "status": "pending"}]}])
        self.assertEqual(_ready_set(st, parsed, 1), [])

    def test_failed_dep_excluded(self):
        body = ("# Plan\n\n## Phase 1: Build\n"
                "- [ ] Task A: foundation\n"
                "- [ ] Task B: consumer <!-- deps: P1.T1 -->\n")
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task A: foundation", "status": "failed"},
            {"name": "Task B: consumer", "status": "pending"}]}])
        self.assertEqual(_ready_set(st, parsed, 1), [])

    def test_manual_and_explore_excluded(self):
        body = ("# Plan\n\n## Phase 1: Build\n"
                "- [ ] [Manual] Task A: m <!-- deps: -->\n"
                "- [ ] [Explore] Task B: e <!-- deps: -->\n"
                "- [ ] Task C: x <!-- deps: -->\n")
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "[Manual] Task A: m", "status": "pending"},
            {"name": "[Explore] Task B: e", "status": "pending"},
            {"name": "Task C: x", "status": "pending"}]}])
        ready = _ready_set(st, parsed, 1)
        self.assertEqual([m["task"] for m in ready], [3])

    def test_subtasked_task_excluded(self):
        body = ("# Plan\n\n## Phase 1: Build\n"
                "- [ ] Task A: parent <!-- deps: -->\n"
                "  - [ ] Subtask: one\n")
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task A: parent", "status": "pending",
             "subtasks": [{"name": "Subtask: one", "status": "pending"}]}]}])
        self.assertEqual(_ready_set(st, parsed, 1), [])

    def test_non_pending_excluded(self):
        body = "# Plan\n\n## Phase 1: Build\n- [ ] Task A: x <!-- deps: -->\n"
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task A: x", "status": "in_progress"}]}])
        self.assertEqual(_ready_set(st, parsed, 1), [])

    def test_cross_phase_dep_satisfied(self):
        # A dep may target an EARLIER phase's task.
        body = ("# Plan\n\n## Phase 1: Foundation\n"
                "- [ ] Task A: base\n\n## Phase 2: Build\n"
                "- [ ] Task B: consumer <!-- deps: P1.T1 -->\n")
        parsed = self._plan(body)
        st = _state([
            {"name": "P1", "tasks": [{"name": "Task A: base", "status": "completed"}]},
            {"name": "P2", "tasks": [{"name": "Task B: consumer", "status": "pending"}]}])
        ready = _ready_set(st, parsed, 2)
        self.assertEqual([m["task"] for m in ready], [1])

    def test_cap_at_default_wave_size(self):
        lines = ["# Plan", "", "## Phase 1: Build"]
        for i in range(DEFAULT_WAVE_SIZE + 3):
            lines.append(f"- [ ] Task {i}: t{i} <!-- deps: -->")
        parsed = self._plan("\n".join(lines) + "\n")
        st = _state([{"name": "P1", "tasks": [
            {"name": f"Task {i}: t{i}", "status": "pending"}
            for i in range(DEFAULT_WAVE_SIZE + 3)]}])
        ready = _ready_set(st, parsed, 1)
        self.assertEqual(len(ready), DEFAULT_WAVE_SIZE)

    def test_eligible_members_is_uncapped(self):
        # _eligible_members returns EVERY eligible task — the cap lives in the
        # _ready_set wrapper, so the deferred overflow can be surfaced.
        n = DEFAULT_WAVE_SIZE + 3
        lines = ["# Plan", "", "## Phase 1: Build"]
        for i in range(n):
            lines.append(f"- [ ] Task {i}: t{i} <!-- deps: -->")
        parsed = self._plan("\n".join(lines) + "\n")
        st = _state([{"name": "P1", "tasks": [
            {"name": f"Task {i}: t{i}", "status": "pending"} for i in range(n)]}])
        eligible = _eligible_members(st, parsed, 1)
        self.assertEqual(len(eligible), n)

    def test_ready_set_is_capped_slice_of_eligible(self):
        # The cap is separable: ready = eligible[:cap], deferred = eligible[cap:].
        n = DEFAULT_WAVE_SIZE + 3
        lines = ["# Plan", "", "## Phase 1: Build"]
        for i in range(n):
            lines.append(f"- [ ] Task {i}: t{i} <!-- deps: -->")
        parsed = self._plan("\n".join(lines) + "\n")
        st = _state([{"name": "P1", "tasks": [
            {"name": f"Task {i}: t{i}", "status": "pending"} for i in range(n)]}])
        eligible = _eligible_members(st, parsed, 1)
        ready = _ready_set(st, parsed, 1)
        deferred = eligible[DEFAULT_WAVE_SIZE:]
        self.assertEqual(ready, eligible[:DEFAULT_WAVE_SIZE])
        self.assertEqual(len(deferred), 3)  # the overflow beyond the cap
        # Deferred carries the same {phase, task, name} shape as ready members.
        self.assertEqual(deferred[0]["phase"], 1)
        self.assertIn("name", deferred[0])
        # No overlap: every deferred task is beyond the capped ready-set.
        ready_tasks = {m["task"] for m in ready}
        deferred_tasks = {m["task"] for m in deferred}
        self.assertTrue(ready_tasks.isdisjoint(deferred_tasks))

    def test_no_deferred_when_under_cap(self):
        # Fewer eligible than the cap → empty deferred, no false announcement.
        body = "# Plan\n\n## Phase 1: Build\n- [ ] Task A: x <!-- deps: -->\n"
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task A: x", "status": "pending"}]}])
        eligible = _eligible_members(st, parsed, 1)
        self.assertEqual(eligible[DEFAULT_WAVE_SIZE:], [])

    def test_invalid_phase_returns_empty(self):
        body = "# Plan\n\n## Phase 1: Build\n- [ ] Task A: x <!-- deps: -->\n"
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task A: x", "status": "pending"}]}])
        self.assertEqual(_ready_set(st, parsed, 5), [])


class TestPendingIneligibility(unittest.TestCase):
    """_pending_ineligibility classifies WHY each pending task was rejected.

    Mirror of _eligible_members: each pending task that failed a gate gets a
    {phase, task, name, reason} dict, reason = the FIRST failing gate in the
    same check order (subtasked → non_executor → no_deps_comment →
    deps_unsatisfied). cmd_dispatch_wave surfaces this on a no_ready_tasks so
    the orchestrator tells the author which gate killed each candidate.
    """
    def _plan(self, body):
        parsed, d = _parsed_with(body)
        self.addCleanup(shutil.rmtree, d)
        return parsed

    def _reasons(self, st, parsed, phase):
        return {m["task"]: m["reason"] for m in
                _pending_ineligibility(st, parsed, phase)}

    def test_empty_when_all_eligible(self):
        body = "# Plan\n\n## Phase 1: Build\n- [ ] Task A: x <!-- deps: -->\n"
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task A: x", "status": "pending"}]}])
        self.assertEqual(_pending_ineligibility(st, parsed, 1), [])

    def test_each_gate_reported(self):
        # One task per reason code + one eligible task.
        body = ("# Plan\n\n## Phase 1: Build\n"
                "- [ ] Task: indep <!-- deps: -->\n"            # eligible
                "- [ ] Task: nooptin\n"                          # no_deps_comment
                "- [ ] Task: big <!-- deps: -->\n"              # subtasked
                "  - [ ] Subtask: one\n"
                "- [ ] [Manual] Task: m <!-- deps: -->\n"        # non_executor
                "- [ ] Task: waits <!-- deps: P1.T1 -->\n")      # deps_unsatisfied (T1 pending)
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task: indep", "status": "pending"},
            {"name": "Task: nooptin", "status": "pending"},
            {"name": "Task: big", "status": "pending",
             "subtasks": [{"name": "Subtask: one", "status": "pending"}]},
            {"name": "[Manual] Task: m", "status": "pending"},
            {"name": "Task: waits", "status": "pending"}]}])
        self.assertEqual(self._reasons(st, parsed, 1), {
            2: "no_deps_comment", 3: "subtasked",
            4: "non_executor", 5: "deps_unsatisfied"})

    def test_first_failing_gate_wins(self):
        # A subtasked task that ALSO lacks a deps comment reports "subtasked"
        # (the flat check precedes the opt-in check), matching _eligible_members.
        body = ("# Plan\n\n## Phase 1: Build\n"
                "- [ ] Task: big-nooptin\n"
                "  - [ ] Subtask: one\n")
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task: big-nooptin", "status": "pending",
             "subtasks": [{"name": "Subtask: one", "status": "pending"}]}]}])
        self.assertEqual(self._reasons(st, parsed, 1), {1: "subtasked"})

    def test_non_pending_not_reported(self):
        # in_progress / terminal tasks are not candidates — never classified.
        body = "# Plan\n\n## Phase 1: Build\n- [ ] Task: a\n- [ ] Task: b <!-- deps: -->\n"
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task: a", "status": "completed"},      # terminal, skipped
            {"name": "Task: b", "status": "in_progress"}]}])  # not pending, skipped
        self.assertEqual(_pending_ineligibility(st, parsed, 1), [])

    def test_entry_shape(self):
        body = "# Plan\n\n## Phase 1: Build\n- [ ] Task: a\n"
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task: a", "status": "pending"}]}])
        out = _pending_ineligibility(st, parsed, 1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0], {"phase": 1, "task": 1,
                                  "name": "Task: a", "reason": "no_deps_comment"})

    def test_invalid_phase_returns_empty(self):
        body = "# Plan\n\n## Phase 1: Build\n- [ ] Task: a <!-- deps: -->\n"
        parsed = self._plan(body)
        st = _state([{"name": "P1", "tasks": [
            {"name": "Task: a", "status": "pending"}]}])
        self.assertEqual(_pending_ineligibility(st, parsed, 9), [])


if __name__ == "__main__":
    unittest.main()
