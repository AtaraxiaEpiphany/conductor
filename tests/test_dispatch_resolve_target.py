"""Tests for dispatch._resolve_finalize_target — the extracted result-prep helper.

Covers the input-prep half of dispatch-finalize (read/synthesize, --override
patching, locked in_progress index preference). The synthesis+commit behavior
is already covered end-to-end by TestDispatchFinalizeShaWriteback; these tests
pin the pure-prep logic in isolation.
"""
import json
import sys
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.dispatch import _resolve_finalize_target
from test_track_state import _make_state, _make_track_dir


def _result(track_dir, body):
    cond = Path(track_dir, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / "result.json").write_text(json.dumps(body))


class ResolveTargetTests(TestCase):
    def setUp(self):
        # Default argv: no overrides.
        sys.argv = ["track-state", "dispatch-finalize", "<dir>"]

    def test_result_present_returns_fields(self):
        state = _make_state(current_phase_index=1, current_task_index=1)
        d = _make_track_dir(state)
        self.addCleanup(_rm, d)
        _result(d, {"status": "SUCCESS", "phase": 1, "task": 1, "subtask": None,
                    "task_name": "Task A", "commit_sha": "abc1234"})
        r, p, t, s, name, status = _resolve_finalize_target(d, Path(d, ".conductor", "result.json"))
        self.assertEqual(status, "SUCCESS")
        self.assertEqual(name, "Task A")
        self.assertEqual((p, t, s), ("1", "1", None))

    def test_override_patches_empty_fields_only(self):
        state = _make_state(current_phase_index=1, current_task_index=1)
        d = _make_track_dir(state)
        self.addCleanup(_rm, d)
        _result(d, {"status": "SUCCESS", "phase": 1, "task": 1, "commit_sha": "",
                    "task_name": "Task A"})
        sys.argv = ["track-state", "dispatch-finalize", d, "--override",
                    "commit_sha=deadbee,task_name=Ignored"]
        r, p, t, s, name, status = _resolve_finalize_target(d, Path(d, ".conductor", "result.json"))
        # empty commit_sha patched; non-empty task_name left alone
        self.assertEqual(r["commit_sha"], "deadbee")
        self.assertEqual(name, "Task A")

    def test_locked_in_progress_indices_override_result(self):
        # result.json claims subtask 1, but locked state says subtask 2 is in_progress.
        state = _make_state(
            current_phase_index=1, current_task_index=1, current_subtask_index=2,
            phases=[{"name": "P1", "status": "in_progress", "tasks": [{
                "name": "Parent", "status": "in_progress", "commit_sha": "",
                "subtasks": [
                    {"name": "s1", "status": "completed", "commit_sha": "111aaaa"},
                    {"name": "s2", "status": "in_progress", "commit_sha": ""},
                ],
            }]}],
        )
        d = _make_track_dir(state)
        self.addCleanup(_rm, d)
        _result(d, {"status": "SUCCESS", "phase": 1, "task": 1, "subtask": 1,
                    "task_name": "s1", "commit_sha": "999zzzz"})
        r, p, t, s, name, status = _resolve_finalize_target(d, Path(d, ".conductor", "result.json"))
        self.assertEqual(s, "2", "locked in_progress subtask index must win over stale result")

    def test_returns_none_when_no_result_and_no_locked_task(self):
        state = _make_state(current_phase_index=0, current_task_index=0)
        d = _make_track_dir(state)
        self.addCleanup(_rm, d)
        # no result.json, no in_progress locked task
        self.assertIsNone(_resolve_finalize_target(d, Path(d, ".conductor", "result.json")))


def _rm(d):
    import shutil
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
