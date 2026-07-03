"""Tests for ``track-state wave-step`` (Rail B-min wave spine).

``wave-step`` collapses the dispatch-wave + wave-finalize loop into one leaf
action per call. These tests pin the action enum and the three subtle behaviors
that make the wave spine safe for a small-window model:

  - the pre-assembled per-member ``prompt`` (no model-side field interpolation
    across the N-member fan-out),
  - the interrupted-member discriminator (no-retry-burn: ``n_commits == 0`` +
    no result.json + worktree exists → re-dispatch, not finalize),
  - drain-marker idempotency (seam-review applicability decided once per wave,
    keyed on ``(track_id, base_sha)`` so a new wave self-invalidates it).
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase

from scripts.track_state.core import save, load
from scripts.track_state.wave import (
    cmd_wave_step, cmd_dispatch_wave, cmd_wave_finalize,
    _drain_processed, _mark_drain_processed, _load_ledger,
)
from scripts.track_state.quality import _CONDUCTOR_GITIGNORE

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _capture(fn, *args, **kwargs):
    """Capture stdout JSON from a cmd."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _git(d, *args):
    return subprocess.run(["git", "-C", str(d), *args],
                          capture_output=True, text=True, check=True)


def _make_git_track(state, plan_body):
    """Temp git repo (track_dir == repo root) with plan.md + state + .conductor/.gitignore.

    Commits plan + .conductor/.gitignore + track-state.json at base so worktrees
    branched from HEAD carry the gitignore — the wave-agent.marker is then ignored
    by the agent's ``git add .`` and doesn't conflict across sequential squashes.
    Mirrors production, where these are committed by track init / first conductor
    commit (without this, each member's marker would land on its branch and
    conflict on the second squash-merge)."""
    d = tempfile.mkdtemp()
    _git(d, "init")
    _git(d, "config", "user.email", "test@test.com")
    _git(d, "config", "user.name", "Test")
    Path(d, "README.md").write_text("# base\n")
    Path(d, "plan.md").write_text(plan_body)
    cond = Path(d, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / ".gitignore").write_text(_CONDUCTOR_GITIGNORE)
    save(d, state)
    _git(d, "add", ".")
    _git(d, "commit", "-m", "init")
    return d


def _disjoint_plan(n=3):
    """A phase of N file-disjoint deps-declared tasks (empty deps = independent)."""
    lines = ["# Plan", "", "## Phase 1: Build"]
    for i in range(1, n + 1):
        lines.append(f"- [ ] Task {i}: t{i} <!-- deps: -->")
    return "\n".join(lines) + "\n"


def _state(n_tasks):
    return {
        "track_id": "wtest_20260101", "type": "feature", "status": "in_progress",
        "current_phase_index": 1, "current_task_index": 0,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "phases": [{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": f"Task {i}: t{i}", "status": "pending"} for i in range(1, n_tasks + 1)]}],
    }


def _wave_step(td):
    return _capture(cmd_wave_step, td)


def _dispatch_wave(td):
    return _capture(cmd_dispatch_wave, td)


def _wave_finalize(td, p, t):
    return _capture(cmd_wave_finalize, td, p, t)


def _simulate_agent(member, files=None, status="SUCCESS", summary="done"):
    """Edit files in the member's worktree, commit on its branch, write result.json
    in the worktree's .conductor (track_dir == repo_root ⇒ worktree_track_dir == worktree)."""
    wt = member["worktree"]
    for name, content in (files or {f"out_{member['task']}.txt": "x"}).items():
        Path(wt, name).write_text(content)
    _git(wt, "add", ".")
    subprocess.run(["git", "-C", wt, "commit", "-q", "-m",
                    f"work P{member['phase']}.T{member['task']}"],
                   check=True, capture_output=True, env=_GIT_ENV)
    cond = Path(wt, ".conductor")
    cond.mkdir(parents=True, exist_ok=True)
    (cond / "result.json").write_text(json.dumps({
        "status": status, "summary": summary,
        "phase": member["phase"], "task": member["task"], "subtask": None,
        "task_name": member.get("name", "?"),
    }))


class WaveStepBatchTests(TestCase):
    def test_dispatch_batch_assembles_per_member_prompts(self):
        d = _make_git_track(_state(3), _disjoint_plan(3))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _wave_step(d)
        self.assertEqual(out["action"], "dispatch_batch")
        self.assertEqual(out["phase"], 1)
        members = out["wave"]
        self.assertEqual(len(members), 3)
        m = members[0]
        # 6 slim keys + the pre-assembled prompt.
        self.assertEqual(sorted(m.keys()),
                         ["branch", "name", "phase", "prompt",
                          "task", "worktree", "worktree_track_dir"])
        self.assertIn(f'cd "{m["worktree"]}"', m["prompt"])
        self.assertIn(f"WORKTREE_DIR={m['worktree']}", m["prompt"])
        self.assertIn(f"TRACK_DIR={m['worktree_track_dir']}", m["prompt"])
        self.assertIn("PHASE=1", m["prompt"])
        self.assertIn(f"TASK={m['task']}", m["prompt"])
        self.assertIn("ATTEMPT=1", m["prompt"])
        self.assertIn("MAX_RETRIES=3", m["prompt"])
        self.assertNotIn("SUBTASK=", m["prompt"])  # wave members are flat-only
        # Ledger written + members locked in state.
        st = load(d)
        locs = {(m["phase"], m["task"]) for m in members}
        locked = {(pi + 1, ti + 1)
                  for pi, ph in enumerate(st["phases"])
                  for ti, tk in enumerate(ph["tasks"])
                  if tk["status"] == "in_progress"}
        self.assertEqual(locs, locked)

    def test_dispatch_batch_surfaces_deferred_overflow(self):
        d = _make_git_track(_state(6), _disjoint_plan(6))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _wave_step(d)
        self.assertEqual(out["action"], "dispatch_batch")
        self.assertEqual(len(out["wave"]), 4)         # DEFAULT_WAVE_SIZE cap
        self.assertEqual(len(out["deferred"]), 2)     # eligible-but-capped (no-silent-caps)


class WaveStepIntegrateTests(TestCase):
    def test_wave_integrate_one_in_flight_member(self):
        d = _make_git_track(_state(3), _disjoint_plan(3))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        batch = _dispatch_wave(d)
        for m in batch["wave"]:
            _simulate_agent(m)
        # wave-step integrates in_flight[0] (lowest phase,task) one at a time.
        out = _wave_step(d)
        self.assertEqual(out["action"], "wave_integrate")
        self.assertEqual(out["phase"], 1)
        self.assertEqual(out["task"], 1)
        self.assertEqual(out["name"], "Task 1: t1")

    def test_interrupted_member_redispatches_no_retry_burn(self):
        # Wave dispatched; agents never ran (no commits, no result.json, worktrees
        # exist). wave-step re-dispatches in_flight[0] WITHOUT finalizing — a
        # dispatch that never ran must not burn a retry. (Long-running-critical.)
        d = _make_git_track(_state(3), _disjoint_plan(3))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _dispatch_wave(d)  # no _simulate_agent — interrupted before any work
        out = _wave_step(d)
        self.assertEqual(out["action"], "dispatch_batch")
        self.assertEqual(len(out["wave"]), 1)        # single-member re-dispatch
        self.assertTrue(out["is_resume"])
        self.assertEqual(out["attempt"], 1)          # retry_count 0 → attempt 1
        # No finalize ran → still in_progress, retry_count untouched.
        tgt = load(d)["phases"][0]["tasks"][0]
        self.assertEqual(tgt["status"], "in_progress")
        self.assertEqual(tgt.get("retry_count", 0), 0)

    def test_missing_worktree_falls_through_to_finalize(self):
        # An in_flight member whose worktree was torn down (partial abort) must
        # NOT be re-dispatched — wave-step emits wave_integrate, and wave-finalize
        # synthesizes FAILURE correctly.
        d = _make_git_track(_state(3), _disjoint_plan(3))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        batch = _dispatch_wave(d)
        shutil.rmtree(batch["wave"][0]["worktree"], ignore_errors=True)
        out = _wave_step(d)
        self.assertEqual(out["action"], "wave_integrate")
        self.assertEqual(out["task"], 1)


class WaveStepDrainTests(TestCase):
    def _drain_wave(self, d, batch, statuses=None):
        """Simulate + finalize every member to terminal status; returns the last
        wave-finalize envelope (carries drained=True)."""
        statuses = statuses or ["SUCCESS"] * len(batch["wave"])
        last = None
        for m, st in zip(batch["wave"], statuses):
            if st == "SUCCESS":
                _simulate_agent(m)
            # FAILURE/None: leave the worktree without a SUCCESS result.
            last = _wave_finalize(d, m["phase"], m["task"])
        return last

    def test_drained_two_finalized_emits_seam_review(self):
        d = _make_git_track(_state(3), _disjoint_plan(3))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        batch = _dispatch_wave(d)
        self._drain_wave(d, batch)
        out = _wave_step(d)
        self.assertEqual(out["action"], "seam_review")
        self.assertEqual(out["finalized_count"], 3)
        self.assertEqual(out["revision_range"], f"{batch['base_sha']}..HEAD")
        # Idempotency: the marker was written before emitting — a second call
        # does NOT re-emit seam_review (routes to next wave / phase_checkpoint / done).
        again = _wave_step(d)
        self.assertNotEqual(again["action"], "seam_review")

    def test_drained_single_finalized_skips_seam_review(self):
        # <2 finalized → no seam review; wave-step routes onward (next wave /
        # phase_checkpoint / done), never seam_review.
        d = _make_git_track(_state(1), _disjoint_plan(1))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        batch = _dispatch_wave(d)
        self._drain_wave(d, batch)
        out = _wave_step(d)
        self.assertNotEqual(out["action"], "seam_review")

    def test_drain_marker_keyed_on_base_sha(self):
        # The drain marker matches on (track_id, base_sha): a new wave (new
        # base_sha) self-invalidates a prior wave's marker. Unit-level so the
        # cross-wave invalidation is pinned without driving two full wave cycles.
        d = _make_git_track(_state(2), _disjoint_plan(2))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        wave1_env = _dispatch_wave(d)
        self._drain_wave(d, wave1_env)
        ledger = _load_ledger(d)   # carries track_id + base_sha (the envelope doesn't)
        _mark_drain_processed(d, ledger)
        self.assertTrue(_drain_processed(d, ledger))
        # A hypothetical second wave with a different base_sha is NOT processed.
        wave2_ledger = {"track_id": ledger["track_id"], "base_sha": "deadbeef"}
        self.assertFalse(_drain_processed(d, wave2_ledger))


class WaveStepRoutingTests(TestCase):
    def test_failed_member_after_drain_emits_ask(self):
        # No active wave; a failed+exhausted task surfaces its Retry/Skip/Block
        # decision via the shared quiescent router (interactive mode).
        state = _state(1)
        state["phases"][0]["tasks"][0]["status"] = "failed"
        state["phases"][0]["tasks"][0]["retry_count"] = 3  # MAX_RETRIES
        state["execution_mode"] = "interactive"
        d = _make_git_track(state, _disjoint_plan(1))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _wave_step(d)
        self.assertEqual(out["action"], "ask")
        self.assertEqual([o["label"] for o in out["decision"]["options"]],
                         ["Retry", "Skip", "Block"])

    def test_no_wave_work_emits_serial_with_ineligible(self):
        # One serial task (no deps comment) → no wave-eligible work, but a
        # dispatchable serial task → wave-step delegates to the step spine and
        # carries the rejection reason (no-silent-X).
        plan = "# Plan\n\n## Phase 1: Build\n- [ ] Task S: serial-work\n"
        d = _make_git_track(_state_with_plan(plan, 1), plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _wave_step(d)
        self.assertEqual(out["action"], "serial")
        self.assertTrue(out["ineligible"])
        self.assertEqual(out["ineligible"][0]["reason"], "no_deps_comment")

    def test_serial_delegation_then_wave_recheck_unlocks_wave(self):
        # Task S (serial, no deps) blocks Task W (deps: P1.T1). Initially W's dep
        # is unsatisfied → wave-step emits serial. After S completes, wave-step
        # emits dispatch_batch for W (the serial task unlocked the wave).
        plan = ("# Plan\n\n## Phase 1: Build\n"
                "- [ ] Task S: serial-work\n"
                "- [ ] Task W: consumer <!-- deps: P1.T1 -->\n")

        def st(s_status="pending", w_status="pending"):
            return {"track_id": "wtest_20260101", "type": "feature",
                    "status": "in_progress", "current_phase_index": 1,
                    "current_task_index": 0, "updated_at": "2026-01-01T00:00:00+00:00",
                    "phases": [{"name": "Phase 1", "status": "pending", "tasks": [
                        {"name": "Task S: serial-work", "status": s_status},
                        {"name": "Task W: consumer", "status": w_status}]}]}

        d = _make_git_track(st(), plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertEqual(_wave_step(d)["action"], "serial")
        # Simulate S completing: rewrite state with S completed.
        save(d, st(s_status="completed"))
        out = _wave_step(d)
        self.assertEqual(out["action"], "dispatch_batch")
        self.assertEqual(len(out["wave"]), 1)
        self.assertEqual(out["wave"][0]["task"], 2)   # W = P1.T2

    def test_all_done_emits_done(self):
        # All phases terminal + checkpoint satisfied → done. The checkpoint
        # annotation lives on the phase heading; completed tasks carry a commit_sha.
        plan = ("# Plan\n\n## Phase 1: Build [checkpoint: abc1234]\n"
                "- [x] Task 1: t1 <!-- deps: -->\n"
                "- [x] Task 2: t2 <!-- deps: -->\n")
        state = _state(2)
        state["current_phase_index"] = 0
        state["current_task_index"] = 0
        for tk in state["phases"][0]["tasks"]:
            tk["status"] = "completed"
            tk["commit_sha"] = "abc1234"
        d = _make_git_track(state, plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertEqual(_wave_step(d)["action"], "done")

    def test_does_not_start_wave_while_serial_task_in_progress(self):
        # A serial task left in_progress (mid-serial-leaf) must not be bypassed
        # by a new wave — wave-step delegates back to the step spine. ``locked_at``
        # and a fresh ``updated_at`` keep ensure_healthy's two stale-lock reapers
        # (_fix_stale_lock on locked_at; _fix_stale_in_progress on updated_at age)
        # from resetting the task before the gate fires.
        from datetime import datetime, timezone
        plan = ("# Plan\n\n## Phase 1: Build\n"
                "- [ ] Task S: serial-work\n"
                "- [ ] Task W: consumer <!-- deps: -->\n")

        def st(s_status, w_status):
            s_task = {"name": "Task S: serial-work", "status": s_status}
            if s_status == "in_progress":
                s_task["locked_at"] = time.time()   # fresh — not stale
            return {"track_id": "wtest_20260101", "type": "feature",
                    "status": "in_progress", "current_phase_index": 1,
                    "current_task_index": 0,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "phases": [{"name": "Phase 1", "status": "pending", "tasks": [
                        s_task, {"name": "Task W: consumer", "status": w_status}]}]}

        d = _make_git_track(st("in_progress", "pending"), plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _wave_step(d)
        self.assertEqual(out["action"], "serial")     # NOT dispatch_batch for W


def _state_with_plan(plan, n_tasks):
    """Build a state matching a plan's task names (used by tests that craft a
    custom plan). Re-derives task names from the plan's `- [ ] NAME` lines."""
    import re
    names = re.findall(r"- \[[ x]\] (.+?)(?: <!--)", plan)
    return {"track_id": "wtest_20260101", "type": "feature", "status": "in_progress",
            "current_phase_index": 1, "current_task_index": 0,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "phases": [{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": nm, "status": "pending"} for nm in names]}]}


if __name__ == "__main__":
    import unittest
    unittest.main()
