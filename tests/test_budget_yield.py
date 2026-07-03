r"""Tests for the deterministic context-budget yield gate (#3).

``on-batch-complete.py`` counts per-session ``track-state dispatch-finalize``
cycles (the per-cycle accounting seat named by decision-loop-heartbeat.md) into
``{data_dir}/budget-yield.json`` and, once the count crosses
``CONDUCTOR_BUDGET_YIELD_N`` (default 8), injects a yield instruction telling
the orchestrator to finish the in-flight task to a terminal state and emit the
§5 checkpoint string. This replaces the orchestrator's fuzzy "~6+ dispatches"
self-assessment — a weak model cannot reliably budget itself, so the hook does
it deterministically.

Pure helpers are exercised directly via importlib; ``main()`` is driven via
subprocess (stdin JSON -> stdout JSON) to pin the end-to-end gate wiring,
mirroring the test_on_stop_conductor.py harness.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "on_batch_complete", _scripts / "on-batch-complete.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_detect_dispatch_finalize = _mod._detect_dispatch_finalize
bump_budget_counter = _mod.bump_budget_counter
budget_yield_message = _mod.budget_yield_message
DEFAULT_BUDGET_YIELD_N = _mod.DEFAULT_BUDGET_YIELD_N

_HOOK = _scripts / "on-batch-complete.py"


def _finalize_call() -> dict:
    """A Bash tool_call that runs ``track-state dispatch-finalize``."""
    return {
        "tool_name": "Bash",
        "tool_input": {"command": 'track-state dispatch-finalize "result body"'},
    }


def _run_hook(data_dir: Path, payload: dict, env_n: int = None) -> dict:
    """Run the hook with given stdin JSON + CLAUDE_PLUGIN_DATA; return stdout JSON."""
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    if env_n is not None:
        env["CONDUCTOR_BUDGET_YIELD_N"] = str(env_n)
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


# --------------------------------------------------------------------------- #
# _detect_dispatch_finalize
# --------------------------------------------------------------------------- #
class DetectDispatchFinalizeTests(TestCase):
    def test_matches_finalize_command(self):
        self.assertTrue(_detect_dispatch_finalize([_finalize_call()]))

    def test_ignores_other_dispatch_subcommand(self):
        # dispatch-next / dispatch-wave are NOT the per-cycle accounting seat.
        tc = {"tool_name": "Bash",
              "tool_input": {"command": "track-state dispatch-next"}}
        self.assertFalse(_detect_dispatch_finalize([tc]))

    def test_requires_track_state_prefix(self):
        # "dispatch-finalize" alone (no track-state) must not fire — guards a
        # future unrelated CLI reusing the word.
        tc = {"tool_name": "Bash",
              "tool_input": {"command": "some-tool dispatch-finalize"}}
        self.assertFalse(_detect_dispatch_finalize([tc]))

    def test_ignores_non_bash(self):
        tc = {"tool_name": "Agent",
              "tool_input": {"command": "track-state dispatch-finalize"}}
        self.assertFalse(_detect_dispatch_finalize([tc]))

    def test_empty_batch(self):
        self.assertFalse(_detect_dispatch_finalize([]))


# --------------------------------------------------------------------------- #
# bump_budget_counter
# --------------------------------------------------------------------------- #
class BumpBudgetCounterTests(TestCase):
    def test_increments_and_persists_under_data_dir(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            n1 = bump_budget_counter(data_dir, "sess1", [_finalize_call()])
            n2 = bump_budget_counter(data_dir, "sess1", [_finalize_call()])
            # Reads must stay inside the `with` — the tempdir is unlinked on exit.
            counter = json.loads((data_dir / "budget-yield.json").read_text())
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 2)
        self.assertEqual(counter, {"sess1": 2})

    def test_no_session_returns_zero_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            n = bump_budget_counter(data_dir, "", [_finalize_call()])
            self.assertEqual(n, 0)
            self.assertFalse((data_dir / "budget-yield.json").exists())

    def test_non_finalize_batch_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            tc = {"tool_name": "Bash",
                  "tool_input": {"command": "track-state dispatch-next"}}
            n = bump_budget_counter(data_dir, "sess1", [tc])
            self.assertEqual(n, 0)
            self.assertFalse((data_dir / "budget-yield.json").exists())

    def test_sessions_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            bump_budget_counter(data_dir, "sess1", [_finalize_call()])
            bump_budget_counter(data_dir, "sess1", [_finalize_call()])
            n2 = bump_budget_counter(data_dir, "sess2", [_finalize_call()])
            counter = json.loads((data_dir / "budget-yield.json").read_text())
        self.assertEqual(n2, 1)
        self.assertEqual(counter, {"sess1": 2, "sess2": 1})

    def test_survives_corrupt_counter_file(self):
        # A hand-corrupted ledger must not crash the bump; it restarts at 1.
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            (data_dir / "budget-yield.json").write_text("NOT JSON{")
            n = bump_budget_counter(data_dir, "sess1", [_finalize_call()])
        self.assertEqual(n, 1)


# --------------------------------------------------------------------------- #
# budget_yield_message
# --------------------------------------------------------------------------- #
class BudgetYieldMessageTests(TestCase):
    def setUp(self):
        # Isolate threshold from the developer's env.
        self._prev = os.environ.pop("CONDUCTOR_BUDGET_YIELD_N", None)

    def tearDown(self):
        if self._prev is not None:
            os.environ["CONDUCTOR_BUDGET_YIELD_N"] = self._prev
        else:
            os.environ.pop("CONDUCTOR_BUDGET_YIELD_N", None)

    def test_below_default_threshold_is_none(self):
        self.assertIsNone(budget_yield_message(DEFAULT_BUDGET_YIELD_N - 1))

    def test_at_default_threshold_yields(self):
        msg = budget_yield_message(DEFAULT_BUDGET_YIELD_N)
        self.assertIsNotNone(msg)
        self.assertIn(str(DEFAULT_BUDGET_YIELD_N), msg)

    def test_zero_is_none(self):
        self.assertIsNone(budget_yield_message(0))

    def test_env_threshold_overrides(self):
        os.environ["CONDUCTOR_BUDGET_YIELD_N"] = "3"
        self.assertIsNone(budget_yield_message(2))
        msg = budget_yield_message(3)
        self.assertIsNotNone(msg)
        self.assertIn("limit 3", msg)
        self.assertIn("3 dispatch-finalize cycles", msg)

    def test_names_the_section_5_checkpoint_string(self):
        # commit 3's hook tells the model to emit "the §5 checkpoint string".
        # The injected message must reference it so the model knows what to emit.
        os.environ["CONDUCTOR_BUDGET_YIELD_N"] = "1"
        msg = budget_yield_message(1)
        self.assertIn("§5 checkpoint string", msg)
        self.assertIn("Conductor checkpoint at P{phase}.T{task}", msg)
        self.assertIn("track-state recover", msg)


# --------------------------------------------------------------------------- #
# main() — end-to-end gate wiring via subprocess
# --------------------------------------------------------------------------- #
class MainYieldGateTests(TestCase):
    def _payload(self):
        return {
            "session_id": "sess-e2e",
            "cwd": str(tempfile.gettempdir()),
            "tool_calls": [_finalize_call()],
        }

    def test_yield_absent_below_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            # 1 of 2 → below threshold, no additionalContext.
            out = _run_hook(data_dir, self._payload(), env_n=2)
            self.assertNotIn("hookSpecificOutput", out)

    def test_yield_surfaces_at_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            data_dir = Path(d)
            _run_hook(data_dir, self._payload(), env_n=2)  # count=1
            out = _run_hook(data_dir, self._payload(), env_n=2)  # count=2 → yield
            ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("Context-budget threshold reached", ctx)
        self.assertIn("§5 checkpoint string", ctx)

    def test_non_finalize_batch_does_not_bump(self):
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
            # 3 non-finalize batches with threshold 2 → never yields.
            for _ in range(3):
                out = _run_hook(data_dir, payload, env_n=2)
            self.assertNotIn("hookSpecificOutput", out)
            self.assertFalse((data_dir / "budget-yield.json").exists())


if __name__ == "__main__":
    main()
