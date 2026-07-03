"""Tests for ``track-state step`` (Rail B-min spine).

``step`` collapses the §2.0 recover + §3.0 dispatch routing into one leaf action
per call. These tests pin the action enum and the two subtle behaviors that make
the spine safe for a small-window model:

  - the pre-assembled ``prompt`` (no model-side field interpolation), and
  - the interrupted-dispatch discriminator: an in_progress task with no result
    and a still-Start HEAD re-dispatches WITHOUT finalizing, so a dispatch that
    never ran doesn't burn a retry (core to the long-running goal).
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save, load
from scripts.track_state.dispatch import cmd_step


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_state(**overrides):
    state = {
        "track_id": "step",
        "type": "feature",
        "status": "in_progress",
        "description": "step test",
        "current_phase_index": 1,
        "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [
            {"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "pending"},
                {"name": "Task B", "status": "pending"},
            ]},
        ],
    }
    state.update(overrides)
    return state


def _git_track_dir(state, plan_content=None, with_initial_commit=True):
    """Temp track dir backed by a real git repo (finalize + _is_start_commit need it)."""
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text(plan_content or "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n- [ ] Task B\n")
    save(d, state)
    env = {
        **__import__("os").environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "-C", d, "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
    if with_initial_commit:
        subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"],
                       check=True, capture_output=True, env=env)
    return d


def _step(track_dir):
    """Capture cmd_step stdout as a dict."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        cmd_step(track_dir)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _result(track_dir, body):
    cond = Path(track_dir, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / "result.json").write_text(json.dumps(body))


def _commit(track_dir, msg, files=None):
    """Make a real impl commit so finalize records a SHA / _is_start_commit is False."""
    env = {
        **__import__("os").environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    if files:
        for name, content in files.items():
            Path(track_dir, name).write_text(content)
        subprocess.run(["git", "-C", track_dir, "add", *files], check=True, capture_output=True)
    else:
        # force a non-empty commit even with nothing staged
        subprocess.run(["git", "-C", track_dir, "commit", "-q", "--allow-empty", "-m", msg],
                       check=True, capture_output=True, env=env)
        return
    subprocess.run(["git", "-C", track_dir, "commit", "-q", "-m", msg],
                   check=True, capture_output=True, env=env)


class StepDispatchTests(TestCase):
    def test_dispatch_execute_assembles_prompt(self):
        d = _git_track_dir(_make_state())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["agent"], "task-executor")
        self.assertEqual(o["name"], "Task A")
        self.assertEqual(o["attempt"], 1)
        self.assertIn("TRACK_DIR=", o["prompt"])
        self.assertIn("PHASE=1", o["prompt"])
        self.assertIn("TASK=1", o["prompt"])
        self.assertIn("NAME=Task A", o["prompt"])
        self.assertIn("ATTEMPT=1", o["prompt"])
        self.assertIn("MAX_RETRIES=3", o["prompt"])
        # flat task → no SUBTASK line
        self.assertNotIn("SUBTASK=", o["prompt"])
        # task was locked + start-committed by prepare
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["status"], "in_progress")

    def test_dispatch_explore_uses_explorer_no_attempt(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "[Explore] Map the repo", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["agent"], "explorer")
        self.assertNotIn("ATTEMPT=", o["prompt"])

    def test_dispatch_subtask_includes_subtask_line(self):
        state = _make_state(
            current_subtask_index=1,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Parent", "status": "in_progress", "subtasks": [
                    {"name": "sub one", "status": "pending"}]}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["subtask"], 1)
        self.assertIn("SUBTASK=1", o["prompt"])


class StepFinalizeRouteTests(TestCase):
    def test_success_advances_to_next_task(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "in_progress"},
            {"name": "Task B", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _commit(d, "impl A", {"impl_a.py": "x=1"})
        _result(d, {"status": "SUCCESS", "phase": 1, "task": 1, "subtask": None,
                    "task_name": "Task A", "commit_sha": "abc1234"})
        o = _step(d)
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["name"], "Task B")
        # Task A is now completed
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["status"], "completed")

    def test_failure_under_max_retries_redispatches_with_incremented_attempt(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "in_progress", "retry_count": 0}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _result(d, {"status": "FAILURE", "phase": 1, "task": 1, "subtask": None,
                    "task_name": "Task A", "commit_sha": "",
                    "failure_detail": {"failure_reason": "tests red"}})
        o = _step(d)
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["name"], "Task A")
        self.assertEqual(o["attempt"], 2)  # retry_count 0 → fail → 1 → attempt 2

    def test_interrupted_before_work_redispatches_no_retry_burn(self):
        # in_progress, HEAD still the Start commit, no result.json → re-dispatch,
        # NOT finalize. retry_count must stay 0 and attempt stay 1.
        # Start Task A pending; the first step locks it + makes the Start commit;
        # the second step observes the interrupted (no-result, Start-HEAD) state.
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        first = _step(d)  # locks Task A + Start commit
        self.assertEqual(first["action"], "dispatch")
        o = _step(d)  # interrupted state — no result, HEAD is the Start commit
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["is_resume"], True)
        self.assertEqual(o["attempt"], 1)
        # No finalize ran → still in_progress, retry_count untouched
        tgt = load(d)["phases"][0]["tasks"][0]
        self.assertEqual(tgt["status"], "in_progress")
        self.assertEqual(tgt.get("retry_count", 0), 0)


class StepExhaustedTests(TestCase):
    def test_failed_exhausted_interactive_emits_ask(self):
        state = _make_state(
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "failed", "retry_count": 3,
                 "commit_sha": "abc1234", "last_failure_summary": "boom"}]}])
        plan = "# Plan\n\n## Phase 1: Build\n- [!] Task A [abc1234]\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "ask")
        self.assertEqual(o["name"], "Task A")
        dec = o["decision"]
        self.assertEqual([x["label"] for x in dec["options"]], ["Retry", "Skip", "Block"])
        self.assertIn("Retry", dec["commands"])
        # Block → HALT (the only non-continue outcome)
        self.assertEqual(dec["next"]["Block"], "HALT")

    def test_failed_exhausted_continuous_emits_skip_analyze(self):
        state = _make_state(
            execution_mode="continuous",
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "failed", "retry_count": 3,
                 "commit_sha": "abc1234"}]}])
        plan = "# Plan\n\n## Phase 1: Build\n- [!] Task A [abc1234]\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "skip_analyze")
        self.assertEqual(o["name"], "Task A")


class StepTerminalTests(TestCase):
    def test_phase_complete_without_checkpoint_emits_phase_checkpoint(self):
        state = _make_state(
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed", "commit_sha": "abc1234"}]}])
        # plan.md has no [checkpoint: ...] marker on Phase 1; checkbox matches state.
        plan = "# Plan\n\n## Phase 1: Build\n- [x] Task A [abc1234]\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "phase_checkpoint")
        self.assertEqual(o["phase"], 1)

    def test_all_done_with_checkpoint_emits_done(self):
        state = _make_state(
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed", "commit_sha": "abc1234"}]}])
        plan = "# Plan\n\n## Phase 1: Build [checkpoint: abc1234]\n- [x] Task A\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "done")


class StepManualTests(TestCase):
    def test_manual_interactive_emits_ask_defer_skip(self):
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "[Manual] Deploy", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "ask")
        dec = o["decision"]
        self.assertEqual([x["label"] for x in dec["options"]], ["Defer", "Skip"])
        self.assertIn("Defer", dec["commands"])
        self.assertIn("Skip", dec["commands"])

    def test_manual_continuous_auto_defers_and_advances(self):
        state = _make_state(
            execution_mode="continuous",
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "[Manual] Deploy", "status": "pending"},
                {"name": "Next task", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        # manual auto-deferred internally → advanced to the next pending task
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["name"], "Next task")
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["status"], "deferred")


class StepParentTests(TestCase):
    def test_parent_complete_auto_resolves_then_advances(self):
        state = _make_state(
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Parent", "status": "pending", "subtasks": [
                    {"name": "sub one", "status": "completed", "commit_sha": "111aaaa"},
                    {"name": "sub two", "status": "completed", "commit_sha": "222bbbb"}]},
                {"name": "After parent", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        # parent auto-completed internally → advanced to the next pending task
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["name"], "After parent")
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["status"], "completed")


if __name__ == "__main__":
    main()
