r"""Tests for the doc-gardening heartbeat (Pillar 3 entropy-fight on a cadence).

``on-batch-complete.py`` nudges a one-shot ``conductor:doc-linter`` dispatch at
most once per ``CONDUCTOR_DOC_LINT_HEARTBEAT_H`` hours (default 24), keyed off a
``track-state dispatch-finalize`` batch — the sanctioned per-cycle seat
(decision-loop-heartbeat.md; a wall-clock cron is explicitly rejected there, so
this rides a deterministic event). The throttle is a project-global
``{data_dir}/doc-lint-heartbeat.json`` (``last_run_iso``); the hook nudges only
and never dispatches the agent itself (35s PostToolBatch budget vs doc-linter's
30 maxTurns). Advisory and non-blocking.

Pure helpers are exercised directly via importlib; ``main()`` is driven via
subprocess (stdin JSON -> stdout JSON), mirroring test_budget_yield.py.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "on_batch_complete", _scripts / "on-batch-complete.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_threshold_h = _mod._doc_lint_heartbeat_threshold_h
_due = _mod._doc_lint_heartbeat_due
_mark_run = _mod._doc_lint_mark_run
_message = _mod.doc_lint_heartbeat_message
DOC_LINT_HEARTBEAT_FILE = _mod.DOC_LINT_HEARTBEAT_FILE
DEFAULT_DOC_LINT_HEARTBEAT_H = _mod.DEFAULT_DOC_LINT_HEARTBEAT_H

_HOOK = _scripts / "on-batch-complete.py"


def _finalize_call() -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": 'track-state dispatch-finalize "result body"'},
    }


def _run_hook(data_dir: Path, payload: dict, env_h: int = None) -> dict:
    """Run the hook with given stdin JSON + CLAUDE_PLUGIN_DATA; return stdout JSON."""
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    # These tests isolate the DOC-LINT heartbeat; disable the context-budget yield
    # gate (a sibling dispatch-finalize gate in the same hook) so it can't emit on
    # the same batch and mask the heartbeat assertion. test_budget_yield.py
    # reciprocally isolates from this gate.
    env["CONDUCTOR_BUDGET_YIELD_N"] = "99"
    if env_h is not None:
        env["CONDUCTOR_DOC_LINT_HEARTBEAT_H"] = str(env_h)
    else:
        env.pop("CONDUCTOR_DOC_LINT_HEARTBEAT_H", None)
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


class ThresholdTests(TestCase):
    def setUp(self):
        self._prev = os.environ.pop("CONDUCTOR_DOC_LINT_HEARTBEAT_H", None)

    def tearDown(self):
        if self._prev is not None:
            os.environ["CONDUCTOR_DOC_LINT_HEARTBEAT_H"] = self._prev
        else:
            os.environ.pop("CONDUCTOR_DOC_LINT_HEARTBEAT_H", None)

    def test_default_is_24h(self):
        self.assertEqual(_threshold_h(), DEFAULT_DOC_LINT_HEARTBEAT_H)

    def test_env_overrides(self):
        os.environ["CONDUCTOR_DOC_LINT_HEARTBEAT_H"] = "6"
        self.assertEqual(_threshold_h(), 6)

    def test_non_integer_falls_back_to_default(self):
        os.environ["CONDUCTOR_DOC_LINT_HEARTBEAT_H"] = "daily"
        self.assertEqual(_threshold_h(), DEFAULT_DOC_LINT_HEARTBEAT_H)

    def test_zero_disables(self):
        os.environ["CONDUCTOR_DOC_LINT_HEARTBEAT_H"] = "0"
        self.assertEqual(_threshold_h(), 0)


class HeartbeatDueTests(TestCase):
    def setUp(self):
        self._prev = os.environ.pop("CONDUCTOR_DOC_LINT_HEARTBEAT_H", None)

    def tearDown(self):
        if self._prev is not None:
            os.environ["CONDUCTOR_DOC_LINT_HEARTBEAT_H"] = self._prev
        else:
            os.environ.pop("CONDUCTOR_DOC_LINT_HEARTBEAT_H", None)

    def test_missing_file_is_due(self):
        # A fresh install (no throttle file yet) surfaces the nudge immediately.
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(_due(Path(d)))

    def test_recent_run_is_not_due(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            (data_dir / DOC_LINT_HEARTBEAT_FILE).write_text(
                json.dumps({"last_run_iso": recent})
            )
            self.assertFalse(_due(data_dir))

    def test_stale_run_is_due(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            stale = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
            (data_dir / DOC_LINT_HEARTBEAT_FILE).write_text(
                json.dumps({"last_run_iso": stale})
            )
            self.assertTrue(_due(data_dir))

    def test_corrupt_file_is_due(self):
        # A hand-corrupted ledger surfaces the nudge rather than silently suppressing.
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            (data_dir / DOC_LINT_HEARTBEAT_FILE).write_text("NOT JSON{")
            self.assertTrue(_due(data_dir))

    def test_disabled_threshold_is_never_due(self):
        os.environ["CONDUCTOR_DOC_LINT_HEARTBEAT_H"] = "0"
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(_due(Path(d)))  # no file, but disabled

    def test_mark_run_makes_not_due(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            self.assertTrue(_due(data_dir))
            _mark_run(data_dir)
            self.assertFalse(_due(data_dir))


class MessageTests(TestCase):
    def setUp(self):
        self._prev = os.environ.pop("CONDUCTOR_DOC_LINT_HEARTBEAT_H", None)

    def tearDown(self):
        if self._prev is not None:
            os.environ["CONDUCTOR_DOC_LINT_HEARTBEAT_H"] = self._prev
        else:
            os.environ.pop("CONDUCTOR_DOC_LINT_HEARTBEAT_H", None)

    def test_nudges_doc_linter_dispatch(self):
        msg = _message()
        self.assertIn("conductor:doc-linter", msg)
        self.assertIn("PROJECT_DIR=", msg)

    def test_points_at_wiki_doctor_for_the_repair_loop(self):
        # The heartbeat is a one-shot advisory; the loop-until-dry repair is a
        # separate, heavier flow. The nudge must distinguish them.
        self.assertIn("/conductor:wiki-doctor lint", _message())

    def test_is_advisory_non_blocking(self):
        lower = _message().lower()
        self.assertIn("non-blocking", lower)

    def test_names_the_threshold(self):
        msg = _message()
        self.assertIn(str(DEFAULT_DOC_LINT_HEARTBEAT_H), msg)


class MainHeartbeatGateTests(TestCase):
    def _payload(self):
        return {
            "session_id": "sess-e2e",
            "cwd": str(tempfile.gettempdir()),
            "tool_calls": [_finalize_call()],
        }

    def test_due_and_finalize_surfaces_nudge(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            out = _run_hook(data_dir, self._payload())  # no throttle file ⇒ due
            ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("Doc-gardening heartbeat", ctx)
        self.assertIn("conductor:doc-linter", ctx)

    def test_marks_run_so_second_call_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            _run_hook(data_dir, self._payload())  # nudge + mark
            out = _run_hook(data_dir, self._payload())  # now suppressed
            self.assertNotIn("hookSpecificOutput", out)
            # throttle file written under the data dir, not a track .conductor/
            self.assertTrue((data_dir / DOC_LINT_HEARTBEAT_FILE).exists())

    def test_non_finalize_batch_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            payload = {
                "session_id": "sess-e2e",
                "cwd": str(tempfile.gettempdir()),
                "tool_calls": [{
                    "tool_name": "Bash",
                    "tool_input": {"command": "track-state dispatch-next"},
                }],
            }
            out = _run_hook(data_dir, payload)
            self.assertNotIn("hookSpecificOutput", out)
            self.assertFalse((data_dir / DOC_LINT_HEARTBEAT_FILE).exists())

    def test_disabled_env_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            out = _run_hook(data_dir, self._payload(), env_h=0)
            self.assertNotIn("hookSpecificOutput", out)
            self.assertFalse((data_dir / DOC_LINT_HEARTBEAT_FILE).exists())


if __name__ == "__main__":
    main()
