"""Tests for the stalled-dispatch advisory hint in on-stop-conductor.py.

The implement-step teleoperator can stall between dispatch and the next
`track-state step` call (small-window models drop the prose yield rule). The
Stop hook reads dispatch-lifecycle.log and surfaces an advisory reminder —
non-blocking, via additionalContext. These tests pin the detection logic.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Make scripts/ + scripts/lib importable (mirrors the hook's own sys.path setup).
_PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN / "scripts"))
sys.path.insert(0, str(_PLUGIN / "scripts" / "lib"))

# The hook file is hyphenated (on-stop-conductor.py) — load it as a module via
# importlib so we can call its pure helpers directly.
_scripts = _PLUGIN / "scripts"


class StalledDispatchHintTests(unittest.TestCase):
    def setUp(self):
        self._prior_plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
        self._prior_project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        self._log_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._log_root, ignore_errors=True)
        os.environ["CLAUDE_PLUGIN_DATA"] = self._log_root

    def tearDown(self):
        if self._prior_plugin_data is None:
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        else:
            os.environ["CLAUDE_PLUGIN_DATA"] = self._prior_plugin_data
        if self._prior_project_dir is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_project_dir

    def _load_hook(self):
        # Fresh load each call so the module reads the current env at call time
        # (get_data_dir re-resolves CLAUDE_PLUGIN_DATA per call, but a fresh
        # module load guarantees no cached state).
        spec = importlib.util.spec_from_file_location(
            "on_stop_conductor_stalled_under_test", _scripts / "on-stop-conductor.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _log_path(self):
        # init_logging("dispatch-lifecycle") → <data>/logs/dispatch-lifecycle.log
        return Path(self._log_root) / "logs" / "dispatch-lifecycle.log"

    def _write(self, lines):
        p = self._log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n")

    def _line(self, event, session="s1", phase="1", task="2", subtask="-"):
        return (
            f"dispatch_lifecycle event={event} session={session} agent=task-executor "
            f"phase={phase} task={task} subtask={subtask} marker=- in_flight=- "
            f"decision=- head=abc123 had_result=- gen=1"
        )

    def test_no_log_returns_none(self):
        mod = self._load_hook()
        self.assertIsNone(mod.stalled_dispatch_hint())

    def test_start_with_no_follow_event_is_stall(self):
        self._write([self._line("start")])
        mod = self._load_hook()
        hint = mod.stalled_dispatch_hint()
        self.assertIsNotNone(hint)
        self.assertIn("phase=1", hint)
        self.assertIn("task=2", hint)
        self.assertIn("track-state step", hint)

    def test_start_then_stop_is_not_stall(self):
        self._write([self._line("start"), self._line("stop")])
        mod = self._load_hook()
        self.assertIsNone(mod.stalled_dispatch_hint())

    def test_start_then_probe_is_not_stall(self):
        # A probe means the dedupe guard saw the dispatch proceed — resolved.
        self._write([self._line("start"), self._line("probe")])
        mod = self._load_hook()
        self.assertIsNone(mod.stalled_dispatch_hint())

    def test_most_recent_unresolved_start_wins(self):
        # older closed dispatch, then a fresh unresolved one.
        self._write([
            self._line("start", phase="1", task="1"),
            self._line("stop", phase="1", task="1"),
            self._line("start", phase="2", task="3"),  # unresolved
        ])
        mod = self._load_hook()
        hint = mod.stalled_dispatch_hint()
        self.assertIsNotNone(hint)
        self.assertIn("phase=2", hint)
        self.assertIn("task=3", hint)

    def test_start_with_no_phase_task_is_not_flagged(self):
        # A pre-lock start emit (no resolved indices) is not a teleoperator stall.
        self._write([self._line("start", phase="-", task="-")])
        mod = self._load_hook()
        self.assertIsNone(mod.stalled_dispatch_hint())

    def test_subtask_rendered_when_present(self):
        self._write([self._line("start", subtask="1")])
        mod = self._load_hook()
        hint = mod.stalled_dispatch_hint()
        self.assertIsNotNone(hint)
        self.assertIn("task=2/1", hint)

    def test_never_raises_on_corrupt_log(self):
        # Garbage lines must not crash; a matching start still surfaces.
        self._write([
            "this is not a lifecycle line",
            "@@@ garbage @@@",
            self._line("start"),
        ])
        mod = self._load_hook()
        hint = mod.stalled_dispatch_hint()
        self.assertIsNotNone(hint)


if __name__ == "__main__":
    unittest.main()
