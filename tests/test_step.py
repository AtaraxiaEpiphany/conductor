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
from scripts.track_state.dispatch import (
    cmd_step, _phase_cp_write_marker, _phase_cp_read_marker, _phase_cp_marker_path,
    _skip_analysis_write_marker, _skip_analysis_marker_path)
from scripts.track_state.dispatch import cmd_dispatch_finalize


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


def _finalize(track_dir):
    """Capture cmd_dispatch_finalize stdout as a dict."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        cmd_dispatch_finalize(track_dir)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _result(track_dir, body):
    cond = Path(track_dir, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / "result.json").write_text(json.dumps(body))


def _start_commit_count(track_dir):
    """Count consecutive `chore(conductor): Start task` commits from HEAD.

    Mirrors git_ops._is_start_commit's prefix. Used to assert the re-dispatch /
    reaper path does NOT emit a duplicate Start-task commit on re-entry.
    """
    out = subprocess.run(
        ["git", "-C", track_dir, "log", "--format=%s"],
        capture_output=True, text=True, check=True)
    return sum(1 for line in out.stdout.splitlines()
               if line.startswith("chore(conductor): Start task "))


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


def _phase_complete_track():
    """A track whose Phase 1 is fully terminal with no checkpoint marker —
    the state that surfaces ``dispatch_batch`` on the next ``step`` (site B:
    ``_emit_quiescent_leaf``). Shared by the dispatch_batch prompt-field tests."""
    state = _make_state(
        current_phase_index=0, current_task_index=0,
        phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "completed", "commit_sha": "abc1234"}]}])
    plan = "# Plan\n\n## Phase 1: Build\n- [x] Task A [abc1234]\n"
    return _git_track_dir(state, plan_content=plan)


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

    def test_uncommitted_impl_work_finalizes_not_redispatch(self):
        # Regression: a task-executor that DID work (left uncommitted impl files)
        # but returned no result.json and made no commit used to be treated as
        # "interrupted before any work" → re-dispatched forever without
        # incrementing retry_count and with a wrong attempt number. The working
        # tree is the discriminator: dirty tree (impl files beyond the
        # conductor-managed set) means the agent ran → finalize so
        # _synthesize_result_from_state can produce FAILURE-with-handoff (and
        # bump retry_count), not loop.
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        first = _step(d)  # locks Task A + Start commit
        self.assertEqual(first["action"], "dispatch")
        # Simulate the agent: wrote impl files but returned no result block and
        # committed nothing. HEAD is still the Start commit, tree is dirty.
        Path(d, "impl_a.py").write_text("x=1")
        o = _step(d)  # dirty tree → finalize (synthesize FAILURE-with-handoff)
        # Finalize ran the FAILURE path. retry_count is now set (was absent
        # before) — first failure records retry_count=1 (1-based: counts failed
        # attempts) — and last_failure_summary is populated (only _do_fail sets
        # it). Pre-fix this re-dispatched silently with retry_count never
        # written, looping forever at attempt 1.
        tgt = load(d)["phases"][0]["tasks"][0]
        self.assertIn("retry_count", tgt,
                      "dirty tree must finalize (write retry_count), not "
                      "silently re-dispatch (the pre-fix loop never did)")
        self.assertTrue(tgt.get("last_failure_summary"),
                        "finalize FAILURE must populate last_failure_summary")
        # The decisive regression check: a SECOND dirty-tree no-result step must
        # ESCALATE retry_count (1→2) and attempt (2→3). Pre-fix, both stayed flat
        # forever — the orchestrator re-dispatched without incrementing, exactly
        # the reported bug.
        Path(d, "impl_b.py").write_text("y=1")
        o2 = _step(d)
        tgt2 = load(d)["phases"][0]["tasks"][0]
        self.assertEqual(tgt2["retry_count"], 2)
        self.assertEqual(o2["attempt"], 3)

    def test_step_reentry_after_clear_preserves_retry_count(self):
        # The user's reported bug: task-executor returned no result, session was
        # CLEARED, user re-ran implement-step >30 min later. The stale-lock
        # reaper (ensure_healthy → _fix_stale_lock) used to zero retry_count via
        # _reset_task, flipping the task to pending so is_resume=False → a fresh
        # Start-task commit + retry budget restart. Now the reaper must PRESERVE
        # retry_count/commit_sha: the task re-dispatches, but with full history,
        # and no second Start-task commit.
        import time as _time
        from scripts.track_state.constants import LOCKED_AT_FIELD
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        first = _step(d)  # locks Task A + Start commit
        self.assertEqual(first["action"], "dispatch")
        start_count = _start_commit_count(d)
        self.assertEqual(start_count, 1)

        # Simulate the cleared session: the task is still in_progress with a
        # prior-attempt history, but locked_at has aged past STALE_LOCK_SECONDS.
        st = load(d)
        tgt = st["phases"][0]["tasks"][0]
        tgt["retry_count"] = 1
        tgt["last_failure_summary"] = "prior attempt failed"
        tgt[LOCKED_AT_FIELD] = _time.time() - 3600  # 1h old, past 30min threshold
        save(d, st)

        o = _step(d)  # reaper runs, then re-dispatch branch
        self.assertEqual(o["action"], "dispatch")
        # retry_count survived BOTH the reap AND the re-lock (the core assertion:
        # _lock_inplace preserves it, and the reaper no longer zeroes it).
        after = load(d)["phases"][0]["tasks"][0]
        self.assertEqual(after.get("retry_count"), 1)
        self.assertEqual(after.get("last_failure_summary"), "prior attempt failed")
        # No second Start-task commit (is_resume path skipped it; the reaper did
        # not flip the task to pending in a way that re-triggers a fresh start).
        self.assertEqual(_start_commit_count(d), 1)

    def test_stale_lock_with_impl_work_advances_not_retries(self):
        # Regression: a task-executor that completed + committed, but >30 min
        # elapsed since dispatch-prepare stamped locked_at, used to be REAPED to
        # pending by _fix_stale_lock BEFORE the finalize branch could mark it
        # completed. The plan checkbox went [~]→[ ] and the done work was
        # retried. The reaper must be finalize-aware: when the agent produced an
        # impl commit (HEAD past the Start commit), leave the task in_progress so
        # the finalize branch synthesizes SUCCESS and marks it completed.
        import time as _time
        from scripts.track_state.constants import LOCKED_AT_FIELD
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        first = _step(d)  # locks Task A + Start commit
        self.assertEqual(first["action"], "dispatch")

        # The agent completes: real impl commit + result.json.
        _commit(d, "feat: Task A done", files={"a.py": "x"})
        _result(d, {"status": "SUCCESS", "phase": 1, "task": 1, "subtask": None,
                    "task_name": "Task A", "commit_sha": "abc1234"})

        # Now >30 min pass (paused/long session). Age the lock past the threshold.
        st = load(d)
        st["phases"][0]["tasks"][0][LOCKED_AT_FIELD] = _time.time() - 3600
        save(d, st)

        o = _step(d)  # reaper runs BUT skips the task (impl work exists) → finalize
        # Task A is completed (finalize synthesized SUCCESS from the impl commit),
        # NOT reaped to pending + retried. This is the core Bug-2 fix.
        after = load(d)["phases"][0]["tasks"][0]
        self.assertEqual(after["status"], "completed",
                         "a task with committed impl work must finalize to "
                         "completed even after the stale-lock threshold, not be "
                         "reaped to pending and retried")

    def test_interrupted_before_work_emits_redispatch_telemetry(self):
        # Same interrupted state as above, but asserts the silent re-dispatch is
        # made observable: a ``re-dispatch`` line lands in dispatch-lifecycle.log
        # carrying the phase/task + the inflight gen, so an interrupted→re-dispatch
        # loop becomes grep-able (the evidence that would justify routing to FAILURE).
        import os as _os
        log_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, log_dir, ignore_errors=True)
        # Point the data dir at a temp root so we read the EXACT log this run wrote.
        # get_data_dir() re-resolves CLAUDE_PLUGIN_DATA each call (no memo).
        prior = _os.environ.get("CLAUDE_PLUGIN_DATA")
        _os.environ["CLAUDE_PLUGIN_DATA"] = log_dir
        try:
            state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "pending"}]}])
            d = _git_track_dir(state)
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
            _step(d)            # locks Task A + Start commit
            # The dispatched agent spawned — SubagentStart stamped the marker
            # (gen 1). Then the session died: no result.json, HEAD unmoved.
            from scripts.lib import dispatch_inflight as inflight
            inflight.stamp(d, 1, 1, None)
            o = _step(d)        # interrupted → re-dispatch
            self.assertEqual(o["action"], "dispatch")
            log_path = Path(log_dir) / "logs" / "dispatch-lifecycle.log"
            self.assertTrue(log_path.exists(), "lifecycle log must be written")
            line = log_path.read_text()
            self.assertIn("event=re-dispatch", line)
            self.assertIn("phase=1", line)
            self.assertIn("task=1", line)
            # The interrupted spawn stamped gen 1; the re-dispatch line records
            # that prior generation (successive interruptions = 1, 2, 3 …).
            self.assertIn("gen=1", line)
        finally:
            if prior is None:
                _os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            else:
                _os.environ["CLAUDE_PLUGIN_DATA"] = prior

    def test_spawn_stamp_marker_matches_head(self):
        # The inflight marker is stamped at SPAWN by the SubagentStart hook
        # (lib.dispatch_inflight.stamp — the "spawned, not prepared" semantics
        # that fixed the 2026-09-01 dispatch deadlock). prepare_dispatch no
        # longer writes it. This pins the stamp's contract: after `step`
        # emits `dispatch` and the spawn fires, the marker carries the
        # phase/task and the Start-commit SHA so the PreToolUse:Agent dedupe
        # hook's HEAD == start_sha in-flight test holds. The hook's deny is
        # tested in test_dispatch_dedupe.py.
        from scripts.lib import dispatch_inflight as inflight
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        first = _step(d)  # locks Task A + Start commit
        self.assertEqual(first["action"], "dispatch")
        # Prepared but not spawned: NO marker (a prepare-time stamp made the
        # guard deny the first dispatch itself — the deadlock this pins shut).
        self.assertIsNone(inflight.read(d, 1, 1, None),
                          "prepare must NOT stamp the inflight marker")
        inflight.stamp(d, 1, 1, None)  # the SubagentStart spawn stamp
        marker = inflight.read(d, 1, 1, None)
        self.assertIsNotNone(marker, "spawn must stamp the inflight marker")
        self.assertEqual(marker["phase"], 1)
        self.assertEqual(marker["task"], 1)
        # start_sha must equal the live HEAD (the Start commit) so the hook's
        # HEAD == start_sha in-flight test holds.
        head = subprocess.run(["git", "-C", d, "rev-parse", "--short=7", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(marker["start_sha"], head)

    def test_finalize_clears_inflight_marker(self):
        # After a dispatch returns SUCCESS and step finalizes, the inflight
        # marker must be cleared so the hook stops guarding the (now-done) task.
        from scripts.lib import dispatch_inflight as inflight
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"},
            {"name": "Task B", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _step(d)  # dispatch Task A → Start commit
        inflight.stamp(d, 1, 1, None)  # the SubagentStart spawn stamp
        self.assertIsNotNone(inflight.read(d, 1, 1, None))
        # Agent returns success: a real impl commit + result.json.
        _commit(d, "feat: Task A done", files={"a.py": "x"})
        _result(d, {"status": "success", "summary": "done"})
        out = _step(d)  # finalize Task A
        self.assertEqual(out["action"], "dispatch")  # advances to Task B
        self.assertIsNone(inflight.read(d, 1, 1, None),
                          "finalize must clear Task A's inflight marker")

    def test_dispatch_finalize_breaks_inflight_loop(self):
        # Regression for the dispatch-dedupe flailing loop. When a dispatch is
        # in flight (in_progress + HEAD == start_sha + no result.json),
        # `track-state step` re-emits `dispatch` (the no-retry-burn contract),
        # the PreToolUse:Agent hook denies the spawn, and its deny reason now
        # prescribes `dispatch-finalize` as the terminating recovery. This test
        # pins that finalize actually breaks the stuck state: it clears the
        # inflight marker (so the hook releases the guard) and the next
        # dispatch stamps a FRESH start_sha — a new spawn is then allowed,
        # instead of the orchestrator being wedged against a stale marker that
        # never advanced.
        from scripts.lib import dispatch_inflight as inflight
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"}]}])
        d = _git_track_dir(state)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)

        first = _step(d)  # locks Task A + Start commit
        self.assertEqual(first["action"], "dispatch")
        inflight.stamp(d, 1, 1, None)  # the SubagentStart spawn stamp
        stuck_marker = inflight.read(d, 1, 1, None)
        self.assertIsNotNone(stuck_marker, "spawn stamps the inflight marker")
        stuck_sha = stuck_marker["start_sha"]

        # Simulate "agent in flight, never returned": no result.json, no impl
        # commit. `step` re-emits dispatch (no-retry-burn) — the exact state
        # the deny hook guards. HEAD is still the stuck start_sha.
        second = _step(d)
        self.assertEqual(second["action"], "dispatch")
        self.assertEqual(inflight.read(d, 1, 1, None)["start_sha"], stuck_sha,
                         "marker still points at the never-advanced start_sha")

        # The terminating recovery the deny reason prescribes. The emitted
        # finalize result normalizes the verdict to lowercase ("failure").
        fin = _finalize(d)
        self.assertEqual(fin.get("status"), "failure",
                         "dispatch-finalize must synthesize a failure from a "
                         "task that produced no result.json / no impl commit")

        # The guard is released: the stuck marker is cleared. (The task resets
        # to pending for a no-retry-burn re-dispatch — correct; what matters
        # is the OLD marker with its never-advanced start_sha is gone.)
        self.assertIsNone(inflight.read(d, 1, 1, None),
                          "finalize must clear the inflight marker so the "
                          "dedupe hook stops guarding the stuck start_sha")

        # The next dispatch spawns fresh — the new spawn stamps a marker with a
        # new start_sha (the reset's Start commit), so a subsequent spawn is
        # allowed — not denied against the old stuck SHA. This is the loop
        # terminating.
        third = _step(d)
        self.assertEqual(third["action"], "dispatch")
        inflight.stamp(d, 1, 1, None)  # the re-dispatch's SubagentStart stamp
        fresh_marker = inflight.read(d, 1, 1, None)
        self.assertIsNotNone(fresh_marker, "a fresh spawn re-stamps the marker")
        self.assertNotEqual(
            fresh_marker["start_sha"], stuck_sha,
            "loop not terminated: the new dispatch reused the stuck "
            "start_sha — a subsequent spawn would be denied against it")


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

    def test_failed_exhausted_continuous_emits_dispatch_skip_analyst(self):
        # WM2-3: continuous failed+exhausted now emits dispatch_skip_analyst (the
        # spine owns the §3.6 skip-analyst→refute→route handshake), not the old
        # non-spine skip_analyze leaf.
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
        self.assertEqual(o["action"], "dispatch_skip_analyst")
        self.assertEqual(o["agent"], "skip-analyst")
        self.assertEqual(o["name"], "Task A")
        self.assertIn("TASK_INDEX=1", o["prompt"])
        self.assertIn("TASK_NAME=Task A", o["prompt"])


class StepTerminalTests(TestCase):
    def test_phase_complete_without_checkpoint_emits_dispatch_batch(self):
        # Serial spine: a phase whose tasks are all terminal but has no checkpoint
        # emits dispatch_batch — the pre-assembled ac-tracer + build-runner +
        # test-runner fan-out (COMPACT_FIELDS["step"] now keeps "wave") — retiring
        # the phase_checkpoint non-spine hand-off for the verifier prompts.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _step(d)
        self.assertEqual(o["action"], "dispatch_batch")
        self.assertEqual(o["phase"], 1)
        self.assertEqual(o["execution_mode"], "interactive")
        members = {m["agent"]: m for m in o["wave"]}
        self.assertEqual(set(members), {"ac-tracer", "build-runner", "test-runner"})

    def test_dispatch_batch_ac_tracer_prompt_omits_phase_index(self):
        # ac-tracer §2.0 ASSIGNMENT takes only TRACK_DIR + TRACK_ID (no PHASE_INDEX).
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        ac = next(m for m in _step(d)["wave"] if m["agent"] == "ac-tracer")
        self.assertIn("TRACK_DIR=", ac["prompt"])
        self.assertIn("TRACK_ID=step", ac["prompt"])
        self.assertNotIn("PHASE_INDEX=", ac["prompt"])

    def test_dispatch_batch_test_runner_prompt_includes_phase_index(self):
        # test-runner §2.0 ASSIGNMENT adds PHASE_INDEX, dropped straight from the
        # phase value (reporting-only — mirrors skills/implement/SKILL.md §3.2).
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        tr = next(m for m in _step(d)["wave"] if m["agent"] == "test-runner")
        self.assertIn("TRACK_DIR=", tr["prompt"])
        self.assertIn("TRACK_ID=step", tr["prompt"])
        self.assertIn("PHASE_INDEX=1", tr["prompt"])

    def test_dispatch_batch_build_runner_prompt_includes_phase_index(self):
        # build-runner is fanned out for EVERY code phase (the cheapest-first L0
        # tier) and, like test-runner, takes PHASE_INDEX so it resolves the build
        # command scoped to the right phase. Track 1 — the new compile floor.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        wave = _step(d)["wave"]
        br = next(m for m in wave if m["agent"] == "build-runner")
        self.assertIn("TRACK_DIR=", br["prompt"])
        self.assertIn("TRACK_ID=step", br["prompt"])
        self.assertIn("PHASE_INDEX=1", br["prompt"])

    def test_finalizing_last_task_in_phase_emits_dispatch_batch(self):
        # Site A (_step_route_after_finalize): finalizing the last in_progress
        # task sets phase_checkpoint_pending on the outcome → dispatch_batch.
        # (The quiescent tests above exercise site B — _emit_quiescent_leaf.)
        state = _make_state(phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "in_progress"},
            {"name": "Task B", "status": "completed", "commit_sha": "bbb2222"}]}])
        plan = "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n- [x] Task B [bbb2222]\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _commit(d, "impl A", {"impl_a.py": "x=1"})
        _result(d, {"status": "SUCCESS", "phase": 1, "task": 1, "subtask": None,
                    "task_name": "Task A", "commit_sha": "abc1234"})
        o = _step(d)
        self.assertEqual(o["action"], "dispatch_batch")
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


def _cp_marker(track_dir, **fields):
    """Write a phase-checkpoint marker (defaults to a valid synth_pending P1)."""
    base = {"phase": 1, "stage": "synth_pending", "ac_verdict": "passed",
            "ac_gate": None, "ac_n_ungrounded": None,
            "l1_status": "passed", "l1_command": "pytest -q"}
    base.update(fields)
    _phase_cp_write_marker(track_dir, base)


class StepCheckpointMarkerTests(TestCase):
    """The synth_pending marker discriminates 'verifiers fanned, awaiting the
    synthesizer' from 'nothing fanned yet' — the open design point of WM2-2.
    `_any_phase_needs_checkpoint` can't tell them apart (it only sees 'checkpoint
    absent'); the marker routing in `_emit_quiescent_leaf` does."""

    def test_synth_pending_marker_emits_dispatch_phase_checker(self):
        # Verifiers fanned, verdicts on disk → spine dispatches the synthesizer
        # (phase-checker) with a pre-assembled prompt, NOT a re-fan.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _cp_marker(d)
        o = _step(d)
        self.assertEqual(o["action"], "dispatch_phase_checker")
        self.assertEqual(o["agent"], "phase-checker")
        self.assertEqual(o["phase"], 1)
        self.assertEqual(o["execution_mode"], "interactive")

    def test_dispatch_phase_checker_prompt_assembles_verdict_fields(self):
        # The prompt is the exact §3.2 Step-3 field set, assembled from the marker.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _cp_marker(d, ac_verdict="passed", l1_status="failed",
                   l1_command="pytest -q tests/")
        o = _step(d)
        self.assertIn("TRACK_ID=step", o["prompt"])
        self.assertIn("PHASE_INDEX=1", o["prompt"])
        self.assertIn("EXECUTION_MODE=interactive", o["prompt"])
        self.assertIn("AC_TRACE_VERDICT=passed", o["prompt"])
        self.assertIn("L1_VERIFY_STATUS=failed", o["prompt"])
        self.assertIn("L1_VERIFY_COMMAND=pytest -q tests/", o["prompt"])
        # passed verdict → no GATE / N_UNGROUNDED lines emitted.
        self.assertNotIn("AC_TRACE_GATE=", o["prompt"])
        self.assertNotIn("AC_TRACE_N_UNGROUNDED=", o["prompt"])

    def test_failed_ac_verdict_includes_gate(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _cp_marker(d, ac_verdict="FAILED", ac_gate="AC1: missing grounding")
        o = _step(d)
        self.assertIn("AC_TRACE_VERDICT=FAILED", o["prompt"])
        self.assertIn("AC_TRACE_GATE=AC1: missing grounding", o["prompt"])

    def test_warn_ac_verdict_includes_n_ungrounded(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _cp_marker(d, ac_verdict="warn", ac_n_ungrounded=2)
        o = _step(d)
        self.assertIn("AC_TRACE_VERDICT=warn", o["prompt"])
        self.assertIn("AC_TRACE_N_UNGROUNDED=2", o["prompt"])

    def test_stale_marker_phase_mismatch_clears_and_fans(self):
        # A marker for a DIFFERENT phase than the one needing a checkpoint is
        # stale (e.g. left by a crash) → cleared, verifiers re-fan.
        d = _phase_complete_track()  # phase 1 needs the checkpoint
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _cp_marker(d, phase=2)  # wrong phase
        o = _step(d)
        self.assertEqual(o["action"], "dispatch_batch")
        self.assertFalse(_phase_cp_marker_path(d).exists(),
                         "stale marker must be cleared so it can't block re-fan")

    def test_unknown_stage_clears_and_fans(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _cp_marker(d, stage="garbage")
        o = _step(d)
        self.assertEqual(o["action"], "dispatch_batch")
        self.assertFalse(_phase_cp_marker_path(d).exists())


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


def _failed_exhausted_track(checkpoint=False):
    """A continuous track whose Phase 1 Task A is failed+exhausted (retry_count=3)
    — the state that surfaces the skip_analyze handshake. ``checkpoint`` stamps
    Phase 1 so a post-skip advance reaches ``done`` (not ``dispatch_batch``)."""
    state = _make_state(
        execution_mode="continuous",
        current_phase_index=0, current_task_index=0,
        phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "failed", "retry_count": 3,
             "commit_sha": "abc1234"}]}])
    cp = " [checkpoint: abc1234]" if checkpoint else ""
    plan = f"# Plan\n\n## Phase 1: Build{cp}\n- [!] Task A [abc1234]\n"
    return _git_track_dir(state, plan_content=plan)


def _sa_marker(track_dir, **fields):
    """Write a skip-analysis marker (defaults to a valid analyzed P1T1)."""
    base = {"phase": 1, "task": 1, "subtask": None, "name": "Task A",
            "stage": "analyzed", "recommendation": "skip",
            "reasoning": "r", "impact": "i", "can_skip": True,
            "refute_status": None, "refute_reasoning": None}
    base.update(fields)
    _skip_analysis_write_marker(track_dir, base)


class SkipAnalyzeRoutingTests(TestCase):
    """The skip-analysis marker routes the §3.6 handshake in the spine (WM2-3):
    analyzed→(dispatch_refuter | halt); refuted→(skip+advance | halt)."""

    def test_analyzed_skip_emits_dispatch_refuter(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _sa_marker(d, recommendation="skip", reasoning="no downstream deps")
        o = _step(d)
        self.assertEqual(o["action"], "dispatch_refuter")
        self.assertEqual(o["agent"], "refuter")
        self.assertIn("DOMAIN=skip", o["prompt"])
        self.assertIn("no downstream deps", o["prompt"])  # reasoning embedded in CLAIM
        self.assertIn("P1T1", o["prompt"])

    def test_analyzed_pause_emits_halt_and_clears_marker(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _sa_marker(d, recommendation="pause_and_escalate",
                   reasoning="downstream impact", impact="breaks phase 2")
        o = _step(d)
        self.assertEqual(o["action"], "halt")
        self.assertEqual(o["reason"], "pause_and_escalate")
        self.assertEqual(o["impact"], "breaks phase 2")
        self.assertFalse(_skip_analysis_marker_path(d).exists(),
                         "halt must clear the marker so re-invoke re-analyzes")

    def test_analyzed_retry_emits_halt(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _sa_marker(d, recommendation="retry_with_modification",
                   reasoning="add a null guard")
        o = _step(d)
        # B.6: retry_with_modification no longer halts — it hands off to
        # failure-analyst for a real diagnosis (which may reactivate the task
        # for a modified retry). Skip marker is cleared so the failure-analysis
        # marker takes over.
        self.assertEqual(o["action"], "dispatch_failure_analyst")
        self.assertEqual(o["agent"], "failure-analyst")
        self.assertFalse(_skip_analysis_marker_path(d).exists())

    def test_refuted_refuted_executes_skip_and_advances(self):
        d = _failed_exhausted_track(checkpoint=True)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _sa_marker(d, stage="refuted", recommendation="skip",
                   refute_status="REFUTED", refute_reasoning="safe to skip")
        o = _step(d)
        # Skip executed in-spine → phase terminal + checkpoint present → done.
        self.assertEqual(o["action"], "done")
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["status"], "skipped")
        self.assertFalse(_skip_analysis_marker_path(d).exists())

    def test_refuted_failure_executes_skip(self):
        # A crashed refute defers to skip-analyst (§3.6): skip still stands.
        d = _failed_exhausted_track(checkpoint=True)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _sa_marker(d, stage="refuted", recommendation="skip", refute_status="FAILURE")
        o = _step(d)
        self.assertEqual(o["action"], "done")
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["status"], "skipped")

    def test_refuted_sustained_emits_halt(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _sa_marker(d, stage="refuted", recommendation="skip",
                   refute_status="SUSTAINED", refute_reasoning="AC1 not actually met")
        o = _step(d)
        self.assertEqual(o["action"], "halt")
        self.assertEqual(o["reason"], "pause_and_escalate")
        self.assertEqual(o["evidence"], "AC1 not actually met")
        self.assertFalse(_skip_analysis_marker_path(d).exists())


if __name__ == "__main__":
    main()
