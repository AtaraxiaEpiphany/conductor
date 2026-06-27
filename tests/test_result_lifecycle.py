"""Tests for result.json lifecycle hardening.

Two guarantees added in the result-reliability campaign:

1. **Stale-result clear** (``dispatch._clear_stale_result``): dispatch-prepare
   removes any ``.conductor/result.json`` left by a prior attempt before locking
   the next task. Without this, an agent that stops without writing a fresh file
   could leave finalize (which reads on *existence*, not freshness) reading a
   STALE result from a previous run as the current task's result.

2. **Recovery-fire counter** (``on-subagent-stop._log_result_event``): the
   SubagentStop hook now records BOTH outcomes for result-file agents — ``ok``
   (fresh result present) and ``recovered`` (hook had to fire) — to a dedicated
   ``result-recovery.log``. Previously only the recovery side was logged, so the
   recovery-fire *rate* (the campaign's key metric) was unmeasurable.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.dispatch import _clear_stale_result, cmd_dispatch_prepare
from test_track_state import _make_state, _make_track_dir, _out_captured

# Load on-subagent-stop.py as a standalone module (it is not a package module —
# production gets scripts/ on sys.path via script-dir = sys.path[0]).
_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))
_spec = importlib.util.spec_from_file_location(
    "on_subagent_stop", _scripts / "on-subagent-stop.py",
)
_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hook)
_log_result_event = _hook._log_result_event


def _drop_result(track_dir, body):
    cond = Path(track_dir, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / "result.json").write_text(json.dumps(body))


class ClearStaleResultTests(TestCase):
    """_clear_stale_result removes a prior attempt's result.json."""

    def test_removes_existing_result(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        _drop_result(d, {"status": "SUCCESS", "summary": "stale prior attempt"})
        self.assertTrue((Path(d) / ".conductor" / "result.json").exists())

        _clear_stale_result(d)

        self.assertFalse((Path(d) / ".conductor" / "result.json").exists())

    def test_noop_when_absent(self):
        # A fresh task has no result.json — clearing must not error.
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        Path(d, ".conductor").mkdir(parents=True)

        _clear_stale_result(d)  # should not raise

        self.assertFalse((Path(d) / ".conductor" / "result.json").exists())

    def test_creates_conductor_dir_if_missing(self):
        # conductor_dir mkdir's .conductor; unlink(missing_ok) must not fail
        # even when the directory was just created empty.
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)

        _clear_stale_result(d)  # no .conductor dir yet

        self.assertTrue((Path(d) / ".conductor").is_dir())


class DispatchPrepareClearsStaleResultTests(TestCase):
    """dispatch-prepare clears a stale result.json on the execute path."""

    def _track(self, task_name="implement the thing"):
        state = _make_state(
            execution_mode="interactive",
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

    def test_execute_path_clears_stale_result(self):
        d = self._track()
        self.addCleanup(shutil.rmtree, d, True)
        _drop_result(d, {"status": "SUCCESS", "summary": "prior attempt"})
        self.assertTrue((Path(d) / ".conductor" / "result.json").exists())

        result, _ = _out_captured(cmd_dispatch_prepare, d)

        # Routed to execute AND the stale result is gone.
        self.assertEqual(result.get("action"), "execute")
        self.assertFalse((Path(d) / ".conductor" / "result.json").exists())


class LogResultEventTests(TestCase):
    """_log_result_event records both outcomes to result-recovery.log."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # _log_result_event writes to Path(log_file).parent / "result-recovery.log".
        self.log_file = Path(self.tmp, "on-subagent-stop.log")

    def _read_recovery_log(self):
        return (Path(self.tmp) / "result-recovery.log").read_text()

    def test_logs_ok_outcome(self):
        _log_result_event(self.log_file, "sess-1", "task-executor",
                          "ok", "fresh_result_present")
        line = self._read_recovery_log()
        self.assertIn("outcome=ok", line)
        self.assertIn("reason=fresh_result_present", line)
        self.assertIn("agent=task-executor", line)

    def test_logs_recovered_outcome(self):
        _log_result_event(self.log_file, "sess-2", "explorer",
                          "recovered", "no_fresh_result")
        line = self._read_recovery_log()
        self.assertIn("outcome=recovered", line)
        self.assertIn("reason=no_fresh_result", line)
        self.assertIn("agent=explorer", line)

    def test_writes_to_dedicated_log_not_failures_log(self):
        # The rate metric needs both outcomes in ONE file; confirm it lands in
        # result-recovery.log, not the operational subagent-failures.log.
        _log_result_event(self.log_file, "sess-3", "task-executor",
                          "ok", "fresh_result_present")
        self.assertTrue((Path(self.tmp) / "result-recovery.log").exists())
        self.assertFalse((Path(self.tmp) / "subagent-failures.log").exists())

    def test_appends_so_rate_is_computable(self):
        # Both outcomes accumulate in one file so recovered/(ok+recovered) works.
        _log_result_event(self.log_file, "s", "task-executor", "ok", "fresh_result_present")
        _log_result_event(self.log_file, "s", "task-executor", "ok", "fresh_result_present")
        _log_result_event(self.log_file, "s", "task-executor", "recovered", "no_fresh_result")
        lines = [l for l in self._read_recovery_log().splitlines() if l.strip()]
        ok = sum("outcome=ok" in l for l in lines)
        recovered = sum("outcome=recovered" in l for l in lines)
        self.assertEqual((ok, recovered), (2, 1))


if __name__ == "__main__":
    main()
