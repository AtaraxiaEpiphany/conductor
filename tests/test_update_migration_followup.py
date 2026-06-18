"""Tests for the #4 follow-up: migrating the remaining entangled RMW sites to
the atomic update() primitive.

Covers _store_evidence, cmd_complete's evidence write, and the validate fix
paths (cmd_validate --fix + ensure_healthy). The key regression guard: because
_store_evidence now reloads under LOCK_EX (instead of mutating the caller's
state object), it must RETURN the evidence-bearing state so a subsequent
_do_sync_plan absorb+save doesn't persist a state missing the evidence.
"""
import json
import shutil
import subprocess
import tempfile
import multiprocessing as mp
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load
from scripts.track_state.helpers import _store_evidence
from scripts.track_state.sync import _do_sync_plan
from scripts.track_state.cmd_complete import cmd_complete
from scripts.track_state.validate import cmd_validate, ensure_healthy, _auto_fix


def _capture(fn, *args, **kwargs):
    import io, sys
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _completed_state():
    return {
        "track_id": "t", "type": "feature", "status": "in_progress",
        "description": "test track",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": "2026-06-18T00:00:00+00:00",
        "phases": [{"name": "Phase 1", "status": "in_progress",
                    "tasks": [{"name": "Task A", "status": "completed",
                               "commit_sha": "abc1234"}]}],
    }


def _git_track(state=None, plan="# Plan\n\n## Phase 1: Build\n- [x] Task A\n"):
    """Temp dir that IS a git repo, with track-state.json + plan.md."""
    d = tempfile.mkdtemp()
    for a in (["git", "init", d],
              ["git", "-C", d, "config", "user.email", "t@t.com"],
              ["git", "-C", d, "config", "user.name", "T"]):
        subprocess.run(a, capture_output=True, check=True)
    Path(d, "README.md").write_text("# t")
    subprocess.run(["git", "-C", d, "add", "README.md"], capture_output=True, check=True)
    subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, check=True)
    Path(d, "plan.md").write_text(plan)
    from scripts.track_state.core import save
    save(d, state or _completed_state())
    return d


# --------------------------------------------------------------------------- #
# _store_evidence
# --------------------------------------------------------------------------- #
class TestStoreEvidence(TestCase):
    def test_writes_evidence_and_returns_state(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        from scripts.track_state.core import save
        save(d, _completed_state())
        r = {"coverage_pct": 90, "tc_coverage": "TC1", "spec_deviation_detail": ["d1", "d2"]}
        state = _store_evidence(d, 1, 1, None, r)
        # Returned state carries the evidence.
        ev = state["phases"][0]["tasks"][0]["evidence"]
        self.assertEqual(ev["coverage_pct"], 90)
        self.assertEqual(ev["deviations"], 2)
        # And it's persisted to disk.
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["evidence"]["coverage_pct"], 90)

    def test_evidence_survives_subsequent_sync_absorb(self):
        # Regression guard: _store_evidence reloads under LOCK_EX and must return
        # the evidence-bearing state, else a following _do_sync_plan absorb+save
        # would persist a state missing the evidence.
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        from scripts.track_state.core import save
        save(d, _completed_state())
        # plan.md has a subtask state doesn't -> _do_sync_plan will absorb + save.
        Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [x] Task A\n  - [ ] New sub\n")
        r = {"coverage_pct": 88, "tc_coverage": "", "spec_deviation_detail": []}
        state = _store_evidence(d, 1, 1, None, r)
        _do_sync_plan(d, state)  # absorbs "New sub" and saves state
        after = load(d)
        # Evidence on Task A must survive the sync save.
        self.assertEqual(after["phases"][0]["tasks"][0]["evidence"]["coverage_pct"], 88)
        # And the subtask was absorbed.
        self.assertTrue(after["phases"][0]["tasks"][0].get("subtasks"))

    def test_no_lost_update_under_concurrent_writers(self):
        # _store_evidence runs under LOCK_EX via update(); a concurrent writer
        # bumping a different field on the same task must not be lost.
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        from scripts.track_state.core import save, update
        save(d, _completed_state())

        def bump_counter(_d):
            def mut(s):
                s["phases"][0]["tasks"][0]["retry_count"] = \
                    s["phases"][0]["tasks"][0].get("retry_count", 0) + 1
                return s
            update(_d, mut)

        procs = [mp.Process(target=bump_counter, args=(d,)) for _ in range(6)]
        for p in procs: p.start()
        # Interleave the evidence write with the concurrent bumps.
        _store_evidence(d, 1, 1, None, {"coverage_pct": 77, "spec_deviation_detail": []})
        for p in procs: p.join()

        st = load(d)
        self.assertEqual(st["phases"][0]["tasks"][0]["evidence"]["coverage_pct"], 77)
        self.assertEqual(st["phases"][0]["tasks"][0].get("retry_count"), 6)


# --------------------------------------------------------------------------- #
# cmd_complete evidence write
# --------------------------------------------------------------------------- #
class TestCmdCompleteEvidence(TestCase):
    def test_coverage_evidence_persists(self):
        d = _git_track(); self.addCleanup(shutil.rmtree, d)
        out = _capture(cmd_complete, d, 1, 1, None, "abc1234", coverage=92)
        self.assertTrue(out.get("ok"))
        ev = load(d)["phases"][0]["tasks"][0]["evidence"]
        self.assertEqual(ev["coverage_pct"], 92)

    def test_default_evidence_initialized_without_flags(self):
        d = _git_track(); self.addCleanup(shutil.rmtree, d)
        out = _capture(cmd_complete, d, 1, 1, None, "abc1234")
        self.assertTrue(out.get("ok"))
        ev = load(d)["phases"][0]["tasks"][0].get("evidence")
        self.assertIsNotNone(ev)  # default evidence initialized
        self.assertIsNone(ev["coverage_pct"])


# --------------------------------------------------------------------------- #
# validate fix paths persist atomically
# --------------------------------------------------------------------------- #
class TestValidateFixAtomic(TestCase):
    def test_cmd_validate_fix_persists(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        from scripts.track_state.core import save
        # Stale in_progress task -> _auto_fix will reset it; --fix must persist.
        st = _completed_state()
        st["phases"][0]["tasks"][0]["status"] = "in_progress"
        st["updated_at"] = "2020-01-01T00:00:00+00:00"  # >24h old -> stale
        Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
        save(d, st)
        out = _capture(cmd_validate, d, fix=True)
        self.assertTrue(out.get("fixed"))
        self.assertTrue(out.get("fixes"))
        # Persisted: stale task reset to pending.
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["status"], "pending")

    def test_ensure_healthy_applies_fixes(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        from scripts.track_state.core import save
        st = _completed_state()
        st["phases"][0]["tasks"][0]["status"] = "in_progress"
        st["updated_at"] = "2020-01-01T00:00:00+00:00"
        Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
        save(d, st)
        state, fixes, errors = ensure_healthy(d)
        self.assertTrue(fixes)
        self.assertEqual(load(d)["phases"][0]["tasks"][0]["status"], "pending")

    def test_auto_fix_idempotent_on_reapply(self):
        # The validate --fix migration re-applies _auto_fix inside update();
        # confirm a second application (on persisted-then-reloaded state)
        # doesn't duplicate or re-break.
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        from scripts.track_state.core import save
        Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [x] Task A\n")
        save(d, _completed_state())
        st1 = load(d); _auto_fix(st1, track_dir=d, errors=[]); save(d, st1)
        st2 = load(d); f2 = _auto_fix(st2, track_dir=d, errors=[])
        # Second pass on already-fixed state yields no new fixes.
        self.assertEqual(len(f2), 0)


if __name__ == "__main__":
    main()
