"""Tests for the read-only log views (track-state log-path / subagent-log).

Covers the join + grouping + filtering in ``track_state.logs_read`` by feeding
fixture lifecycle/recovery lines via ``CLAUDE_PLUGIN_DATA`` (the explicit
override tier — deterministic, no cwd/env coupling).
"""
import os
import shutil
import sys
import tempfile
import unittest
from importlib import reload
from pathlib import Path

# Make scripts/ importable so `from track_state import logs_read` works.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))


def _lifecycle_line(ts, event, agent, phase="-", task="-", had_result="-", gen="-", session="s1"):
    return (
        f"{ts} [INFO] dispatch_lifecycle event={event} session={session} agent={agent} "
        f"phase={phase} task={task} subtask=- marker=- in_flight=- decision=- "
        f"head=- had_result={had_result} gen={gen}"
    )


def _recovery_line(ts, agent, outcome, reason="", session="s1"):
    reason_part = f" reason={reason}" if reason else ""
    return f"{ts} [INFO] session={session} agent={agent} outcome={outcome}{reason_part}"


class LogReaderTests(unittest.TestCase):
    def setUp(self):
        self._prior = {
            k: os.environ.get(k)
            for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR")
        }
        self._tmp = tempfile.mkdtemp()
        os.environ["CLAUDE_PLUGIN_DATA"] = self._tmp
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        (Path(self._tmp) / "logs").mkdir(parents=True)
        # Reload so module-level state / the one-shot warning guard resets.
        import lib.env as env
        reload(env)
        from track_state import logs_read
        reload(logs_read)
        self.logs_read = logs_read

    def tearDown(self):
        for k, v in self._prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, name, lines):
        (Path(self._tmp) / "logs" / name).write_text("\n".join(lines) + "\n")

    def test_log_path_reports_resolved_dir_and_files(self):
        self._write("dispatch-lifecycle.log", [_lifecycle_line(
            "2026-07-22T04:05:00.000000+00:00", "start", "task-executor")])
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.logs_read.cmd_log_path()
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn(self._tmp, out)
        self.assertIn("dispatch-lifecycle.log", out)

    def test_subagent_log_groups_and_filters_by_phase_task(self):
        self._write("dispatch-lifecycle.log", [
            _lifecycle_line("2026-07-22T04:05:00.000000+00:00", "probe", "task-executor", "1", "1", gen="1"),
            _lifecycle_line("2026-07-22T04:05:01.000000+00:00", "start", "task-executor", "1", "1", gen="1"),
            _lifecycle_line("2026-07-22T04:05:02.000000+00:00", "stop", "task-executor", "1", "1", had_result="1"),
            # A different task that the filter must EXCLUDE.
            _lifecycle_line("2026-07-22T04:06:00.000000+00:00", "start", "task-executor", "2", "1", gen="1"),
        ])
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.logs_read.cmd_subagent_log(phase=1, task=1)
        out = buf.getvalue()
        self.assertIn("P1.T1", out)
        self.assertIn("probe", out)
        self.assertIn("→ ok", out)
        # The P2.T1 event must not leak into a P1.T1 filter.
        self.assertNotIn("P2.T1", out)

    def test_subagent_log_attaches_recovery_outcome_to_failed_stop(self):
        self._write("dispatch-lifecycle.log", [
            _lifecycle_line("2026-07-22T04:05:00.000000+00:00", "start", "task-executor", "1", "1", gen="1"),
            _lifecycle_line("2026-07-22T04:05:01.000000+00:00", "stop", "task-executor", "1", "1", had_result="0"),
        ])
        self._write("result-recovery.log", [
            _recovery_line("2026-07-22T04:05:01.000000+00:00", "task-executor",
                           "recovered", "no_fresh_result"),
        ])
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.logs_read.cmd_subagent_log(phase=1, task=1)
        out = buf.getvalue()
        self.assertIn("had_result=0", out)
        self.assertIn("recovered", out)
        self.assertIn("no_fresh_result", out)

    def test_subagent_log_no_file_is_not_a_crash(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.logs_read.cmd_subagent_log()
        self.assertEqual(rc, 0)
        self.assertIn("log-path", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
