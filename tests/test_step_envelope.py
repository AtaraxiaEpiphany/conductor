"""Phase 1 invariant: Rail A and Rail B build the SAME subagent prompt.

The dispatch prompt is built by ONE pure function, ``build_dispatch_prompt``.
Rail B (``step``/``wave`` JSON's pre-assembled ``prompt`` field) calls it
directly; Rail A (``dispatch-next`` + ``skills/implement/SKILL.md`` §3.2/§3.3/
§3.4) now carries the same pre-assembled ``prompt`` field so the skill pastes it
verbatim instead of re-deriving the ``KEY=value`` lines in prose.

These tests pin the two invariants that make the unification safe:

  1. The legacy ``_step_assemble_*`` wrappers delegate to ``build_dispatch_prompt``
     byte-for-byte (no behavior drift at existing call sites / tests).
  2. For the same locked task state, ``cmd_dispatch_next`` (Rail A) emits the
     same ``agent``/``prompt`` envelope as ``cmd_step`` (Rail B) — both rails
     resolve to the single source.

If either breaks, a weak orchestrator could once again fumble field
interpolation on one rail but not the other, and replay/resume would diverge.
"""
import io
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save, load
from scripts.track_state.dispatch import (
    build_dispatch_prompt,
    _step_assemble_prompt,
    _step_assemble_verifier_prompt,
    _step_assemble_phase_checker_prompt,
    cmd_step, cmd_dispatch_next, cmd_dispatch_finalize)


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _git_track_dir(state, plan_content=None):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text(
        plan_content or "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    save(d, state)
    env = {
        **__import__("os").environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "-C", d, "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"],
                   check=True, capture_output=True, env=env)
    return d


def _capture(fn, *args, **kwargs):
    """Capture a command's stdout JSON, mirroring the test_step helper."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


class WrapperEquivalenceTests(TestCase):
    """Invariant 1: the legacy _step_assemble_* wrappers delegate to
    build_dispatch_prompt byte-for-byte."""

    def test_executor_wrapper_equals_builder(self):
        pre = dict(phase=2, task=3, subtask=1, name="Do thing",
                   tags=["Feature"])
        self.assertEqual(
            _step_assemble_prompt("/td", pre, attempt=4),
            build_dispatch_prompt("dispatch_executor", "/td", pre=pre, attempt=4))

    def test_executor_wrapper_explore_classified(self):
        # An explore-tagged task resolves to the explorer agent via the builder.
        pre = dict(phase=1, task=1, subtask=None, name="[Explore] Map it",
                   tags=["Explore"])
        self.assertEqual(
            _step_assemble_prompt("/td", pre, attempt=1),
            build_dispatch_prompt("dispatch_executor", "/td", pre=pre, attempt=1))

    def test_verifier_wrapper_equals_builder(self):
        state = dict(track_id="abc")
        for agent in ("ac-tracer", "build-runner", "test-runner"):
            self.assertEqual(
                _step_assemble_verifier_prompt("/td", state, 2, agent),
                build_dispatch_prompt("dispatch_batch", "/td", state=state,
                                     phase=2, agent=agent)[1])

    def test_phase_checker_wrapper_equals_builder(self):
        state = dict(track_id="abc", execution_mode="continuous")
        marker = dict(ac_verdict="FAILED", ac_gate="must link AC",
                      ac_n_ungrounded=3, l1_status="failed",
                      l1_command="pytest -q")
        self.assertEqual(
            _step_assemble_phase_checker_prompt("/td", state, 2, marker),
            build_dispatch_prompt("dispatch_phase_checker", "/td", state=state,
                                 phase=2, marker=marker)[1])

    def test_builder_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            build_dispatch_prompt("dispatch_nope", "/td", pre={})

    def test_builder_requires_pre_for_executor(self):
        with self.assertRaises(ValueError):
            build_dispatch_prompt("dispatch_executor", "/td", attempt=1)

    def test_builder_requires_marker_for_phase_checker(self):
        with self.assertRaises(ValueError):
            build_dispatch_prompt("dispatch_phase_checker", "/td",
                                 state={}, phase=1)


class RailARailBParityTests(TestCase):
    """Invariant 2: dispatch-next (Rail A) and step (Rail B) emit the same
    agent/prompt envelope for the same locked task state."""

    def _state(self, **overrides):
        state = {
            "track_id": "parity",
            "type": "feature",
            "status": "in_progress",
            "description": "parity test",
            "current_phase_index": 1,
            "current_task_index": 1,
            "updated_at": _recent_iso(),
            "phases": [
                {"name": "Phase 1", "status": "pending", "tasks": [
                    {"name": "Task A", "status": "pending"},
                ]},
            ],
        }
        state.update(overrides)
        return state

    def test_executor_envelope_matches_across_rails(self):
        td = _git_track_dir(self._state())
        try:
            step_out = _capture(cmd_step, td)
            # `step` makes the Start commit; roll it back so dispatch-next sees
            # the same fresh-dispatch state (attempt=1) rather than an in_progress
            # task already locked by step.
            subprocess.run(["git", "-C", td, "reset", "--hard", "HEAD~1", "-q"],
                           check=True, capture_output=True)
            # Re-mark the task pending (step set it in_progress).
            state = load(td)
            state["phases"][0]["tasks"][0]["status"] = "pending"
            state["current_phase_index"] = 1
            state["current_task_index"] = 1
            save(td, state)
            subprocess.run(["git", "-C", td, "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", td, "commit", "-q", "-m", "reset",
                 "--allow-empty"], check=True, capture_output=True)
            dn_out = _capture(cmd_dispatch_next, td)

            self.assertEqual(step_out.get("action"), "dispatch")
            self.assertEqual(dn_out.get("action"), "dispatch_executor")
            self.assertEqual(step_out["agent"], dn_out["agent"])
            self.assertEqual(step_out["prompt"], dn_out["prompt"])
            self.assertEqual(step_out.get("attempt"), dn_out.get("attempt"))
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    def test_dispatch_next_executor_carries_prompt(self):
        # Fresh dispatch: dispatch-next must attach a pre-assembled prompt.
        td = _git_track_dir(self._state())
        try:
            out = _capture(cmd_dispatch_next, td)
            self.assertEqual(out["action"], "dispatch_executor")
            self.assertEqual(out["agent"], "task-executor")
            self.assertIn("prompt", out)
            self.assertIn("TRACK_DIR=", out["prompt"])
            self.assertIn("ATTEMPT=1", out["prompt"])
            self.assertIn("MAX_RETRIES=", out["prompt"])
            # And the same prompt is reproducible from the builder directly.
            pre = dict(phase=out["phase"], task=out["task"],
                       subtask=out.get("subtask"), name=out["name"],
                       tags=out.get("tags", []))
            _, prompt = build_dispatch_prompt(
                "dispatch_executor", td, pre=pre, attempt=out["attempt"])
            self.assertEqual(prompt, out["prompt"])
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    def test_dispatch_next_explorer_carries_explorer_agent(self):
        td = _git_track_dir(self._state(
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "[Explore] Map the codebase", "status": "pending"},
            ]}],
            current_task_index=1))
        try:
            out = _capture(cmd_dispatch_next, td)
            self.assertEqual(out["action"], "dispatch_explorer")
            self.assertEqual(out["agent"], "explorer")
            self.assertIn("prompt", out)
            # Explorer prompt has no ATTEMPT/MAX_RETRIES lines.
            self.assertNotIn("ATTEMPT=", out["prompt"])
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    def test_dispatch_next_phase_checker_carries_verifier_wave(self):
        # A phase needing a checkpoint must carry the pre-assembled verifier
        # fan-out (Rail A/B parity with step's dispatch_batch).
        plan = ("# Plan\n\n## Phase 1: Build\n"
                "- [x] Task A <!-- checkpoint -->\n")
        td = _git_track_dir(self._state(
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed"},
            ]}],
            current_phase_index=1, current_task_index=1), plan_content=plan)
        try:
            out = _capture(cmd_dispatch_next, td)
            self.assertEqual(out["action"], "dispatch_phase_checker")
            self.assertIn("wave", out)
            agents = {m["agent"] for m in out["wave"]}
            self.assertEqual(agents, {"ac-tracer", "build-runner", "test-runner"})
            # Each member prompt is reproducible from the builder.
            state = load(td)
            for member in out["wave"]:
                _, prompt = build_dispatch_prompt(
                    "dispatch_batch", td, state=state, phase=out["phase"],
                    agent=member["agent"])
                self.assertEqual(prompt, member["prompt"])
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    main()
