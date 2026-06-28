r"""Tests for the bounded SubagentStop recovery loop (Gap #1).

A result-file agent (task-executor, explorer) that stops without a fresh
result.json used to get an UNBOUNDED number of forced recovery turns — a
crash-looping agent burned its whole maxTurns budget before Layer-2 synthesis
engaged. The fix: a per-locked-task ``recovery_turns`` counter (reset on lock,
bumped by the hook, bounded by ``lib.recovery.MAX_RECOVERY_TURNS``). Once over
the cap the hook stops blocking → ``dispatch-finalize`` synthesizes a result →
the ``_do_fail`` retry queue takes over.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.mutations import _do_lock, increment_recovery_turns
from scripts.track_state.core import load
from scripts.lib.recovery import MAX_RECOVERY_TURNS, RECOVERY_TURN_FIELD

_scripts = Path(__file__).resolve().parent.parent / "scripts"
_HOOK = _scripts / "on-subagent-stop.py"


def _locked_state(recovery_turns=None):
    """A one-phase/one-task track with the task in_progress at current indices."""
    task = {"name": "Task A", "status": "in_progress", "retry_count": 0}
    if recovery_turns is not None:
        task[RECOVERY_TURN_FIELD] = recovery_turns
    return {
        "track_id": "rec_20260628", "type": "feature", "status": "in_progress",
        "description": "test", "execution_mode": "interactive",
        "current_phase_index": 1, "current_task_index": 1,
        "phases": [{"name": "Phase 1", "status": "pending", "tasks": [task]}],
    }


def _write_track(project_root, state):
    tdir = Path(project_root) / "conductor" / "tracks" / state["track_id"]
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "track-state.json").write_text(json.dumps(state))
    return tdir


class CounterUnitTests(TestCase):
    def test_lock_resets_recovery_turns(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = _write_track(d, _locked_state(recovery_turns=2))
            # Force the task back to pending so _do_lock is a genuine (re)lock.
            st = load(str(tdir))
            st["phases"][0]["tasks"][0]["status"] = "pending"
            from scripts.track_state.core import save
            save(str(tdir), st)
            _do_lock(str(tdir), 1, 1)
            self.assertEqual(load(str(tdir))["phases"][0]["tasks"][0][RECOVERY_TURN_FIELD], 0)

    def test_increment_returns_count_and_grows(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = _write_track(d, _locked_state(recovery_turns=0))
            self.assertEqual(increment_recovery_turns(str(tdir), 1, 1), 1)
            self.assertEqual(increment_recovery_turns(str(tdir), 1, 1), 2)
            self.assertEqual(load(str(tdir))["phases"][0]["tasks"][0][RECOVERY_TURN_FIELD], 2)

    def test_increment_returns_none_when_not_in_progress(self):
        with tempfile.TemporaryDirectory() as d:
            st = _locked_state(recovery_turns=0)
            st["phases"][0]["tasks"][0]["status"] = "completed"
            tdir = _write_track(d, st)
            self.assertIsNone(increment_recovery_turns(str(tdir), 1, 1))

    def test_missing_field_treated_as_zero(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = _write_track(d, _locked_state(recovery_turns=None))  # no field
            self.assertEqual(increment_recovery_turns(str(tdir), 1, 1), 1)


class HookBoundTests(TestCase):
    def _run(self, cwd):
        hook_input = {"agent_type": "task-executor", "session_id": "s",
                      "cwd": cwd, "last_assistant_message": ""}
        proc = subprocess.run([sys.executable, str(_HOOK)],
                              input=json.dumps(hook_input),
                              capture_output=True, text=True)
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc.returncode, out

    def test_blocks_under_cap(self):
        """Two consecutive no-result stops both block (budget 0→1→2, neither > MAX=2)."""
        with tempfile.TemporaryDirectory() as d:
            _write_track(d, _locked_state(recovery_turns=0))
            rc1, _ = self._run(d)
            self.assertEqual(rc1, 2)  # count=1, blocked
            rc2, _ = self._run(d)
            self.assertEqual(rc2, 2)  # count=2, blocked

    def test_allows_stop_when_over_cap(self):
        """A task already at MAX (2) → next stop increments to 3 > MAX → allowed."""
        with tempfile.TemporaryDirectory() as d:
            _write_track(d, _locked_state(recovery_turns=MAX_RECOVERY_TURNS))
            rc, out = self._run(d)
            self.assertEqual(rc, 0)
            self.assertNotIn("decision", out)

    def test_unresolvable_locked_task_falls_back_to_block(self):
        """No tracks under cwd → can't bound → fail-safe toward recovery (block)."""
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run(d)
            self.assertEqual(rc, 2)
            self.assertEqual(out["decision"], "block")


if __name__ == "__main__":
    main()
