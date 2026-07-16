"""Tests for the PreToolUse:Agent dispatch-dedupe hook (on-dispatch-dedupe.py).

The hook enforces the single-writer invariant for a locked task: if a
task-executor/explorer dispatch is already in flight (marker present, HEAD still
the Start commit, no result.json), a second ``Agent`` dispatch for that same
task is ``permissionDecision: "deny"`` before it spawns. Otherwise allow.

Property-level (pin the invariant, not the implementation): the hook reads the
same marker + HEAD/result predicate the spine uses, so these tests drive that
predicate directly and assert allow/deny — they do not assert on internals.
Fail-open is asserted too: corrupt state must allow, never raise.
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

_HOOK = _scripts / "on-dispatch-dedupe.py"

# Import the lib directly for marker setup (lightweight, no track_state import).
from lib import dispatch_inflight as inflight  # noqa: E402


# --- shared fixtures ----------------------------------------------------------
def _git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    return d


def _commit(d, msg, body="# plan\n"):
    # Commit a conductor-managed file so HEAD advances. `body` lets a second
    # commit in the same repo change content (else "nothing added").
    path = os.path.join(d, ".conductor", "plan.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
    subprocess.run(["git", "add", "--", ".conductor/plan.md"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=d, check=True)


def _short_head(d):
    return subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"], cwd=d,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _write_locked_track(d, tid="demo_20260716", phase=1, task=1, subtask=None):
    """Write a track-state.json with the cursor on an in_progress task."""
    track_dir = os.path.join(d, "conductor", "tracks", tid)
    os.makedirs(track_dir, exist_ok=True)
    state = {
        "track_id": tid,
        "current_phase_index": phase,
        "current_task_index": task,
        "current_subtask_index": subtask,
        "phases": [{"name": "P1", "tasks": [
            {"name": "T1", "status": "in_progress", "commit_sha": None}]}],
    }
    with open(os.path.join(track_dir, "track-state.json"), "w") as f:
        json.dump(state, f)
    return track_dir


def _stamp_marker(track_dir, phase, task, subtask, start_sha):
    inflight.write(track_dir, phase, task, subtask, start_sha, "2026-07-16T00:00:00+00:00")


def _run_hook(cwd, subagent_type="task-executor"):
    payload = {
        "tool_name": "Agent", "cwd": cwd,
        "tool_input": {"subagent_type": subagent_type, "prompt": "x"},
    }
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


# --- hook tests ---------------------------------------------------------------
class DispatchDedupeHookTests(TestCase):
    def setUp(self):
        self.repo = _git_repo()
        _commit(self.repo, "chore(conductor): Start task 'T1' [P1.T1]")
        self.start_sha = _short_head(self.repo)
        self.track_dir = _write_locked_track(self.repo)

    def test_non_agent_tool_is_allowed(self):
        rc, out = _run_hook(self.repo)
        # Override payload to a Bash tool — must allow unconditionally.
        payload = {"tool_name": "Bash", "cwd": self.repo,
                   "tool_input": {"command": "ls"}}
        proc = subprocess.run(
            [sys.executable, str(_HOOK)],
            input=json.dumps(payload), capture_output=True, text=True,
        )
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(decision, "allow")

    def test_non_write_agent_is_allowed(self):
        # phase-checker is read-only → not single-writer-critical → allow.
        rc, out = _run_hook(self.repo, subagent_type="phase-checker")
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(decision, "allow")

    def test_no_marker_is_allowed(self):
        # Fresh state — no prior dispatch recorded → allow.
        rc, out = _run_hook(self.repo)
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(decision, "allow")

    def test_inflight_dispatch_is_denied(self):
        # Marker present, HEAD still the Start commit, no result.json → in flight.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        rc, out = _run_hook(self.repo)
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        # Reason must name the task and prescribe the TERMINATING recovery
        # (`dispatch-finalize`), NOT `step`. In this exact state `step`
        # re-emits `dispatch` and would loop the model back here, so the
        # directive must be the finalize command. We assert the prescribed
        # action (the `Run \`...<cmd>...\`` clause), not mere substring
        # presence — the reason legitimately *warns against* `step` too.
        reason = spec.get("permissionDecisionReason", "")
        self.assertIn("P1T1", reason)
        self.assertIn("dispatch-finalize", reason)
        self.assertIn('Run `track-state dispatch-finalize', reason)
        # And it must explicitly warn off the looping path.
        self.assertIn("Do NOT re-run", reason)

    def test_denies_explorer_too(self):
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        rc, out = _run_hook(self.repo, subagent_type="explorer")
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_allows_after_head_advances_and_clears_marker(self):
        # Marker present but HEAD moved past the Start commit → not in flight.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        _commit(self.repo, "feat: real work landed", body="# plan v2\n")  # advance HEAD
        rc, out = _run_hook(self.repo)
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "allow")
        # Stale marker must be cleared.
        self.assertFalse(inflight.read(self.track_dir, 1, 1, None))

    def test_allows_when_result_json_present(self):
        # HEAD still the Start commit, but a result.json landed → the dispatch
        # returned a verdict → not in flight → allow.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        result_path = os.path.join(self.track_dir, ".conductor", "result.json")
        os.makedirs(os.path.dirname(result_path), exist_ok=True)
        with open(result_path, "w") as f:
            json.dump({"status": "success"}, f)
        rc, out = _run_hook(self.repo)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_corrupt_marker_is_allowed_failopen(self):
        # Bad-JSON marker → tolerant reader returns None → allow, no raise.
        marker = inflight.marker_path(self.track_dir, 1, 1, None)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{ not valid json")
        rc, out = _run_hook(self.repo)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_no_locked_task_is_allowed(self):
        # A repo with no in_progress cursor → resolve() returns None → allow.
        repo = _git_repo()
        _commit(repo, "init")
        rc, out = _run_hook(repo)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")


# --- marker-lib unit tests ----------------------------------------------------
class DispatchInflightLibTests(TestCase):
    def test_write_read_roundtrip(self):
        d = tempfile.mkdtemp()
        inflight.write(d, 1, 2, 3, "abc1234", "2026-07-16T00:00:00+00:00")
        m = inflight.read(d, 1, 2, 3)
        self.assertIsNotNone(m)
        self.assertEqual(m["phase"], 1)
        self.assertEqual(m["task"], 2)
        self.assertEqual(m["subtask"], 3)
        self.assertEqual(m["start_sha"], "abc1234")

    def test_read_missing_returns_none(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(inflight.read(d, 1, 1, None))

    def test_read_corrupt_returns_none(self):
        d = tempfile.mkdtemp()
        p = inflight.marker_path(d, 1, 1, None)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("garbage")
        self.assertIsNone(inflight.read(d, 1, 1, None))

    def test_clear_removes_marker(self):
        d = tempfile.mkdtemp()
        inflight.write(d, 1, 1, None, "abc1234", "t")
        self.assertIsNotNone(inflight.read(d, 1, 1, None))
        inflight.clear(d, 1, 1, None)
        self.assertIsNone(inflight.read(d, 1, 1, None))

    def test_clear_missing_is_noop(self):
        d = tempfile.mkdtemp()
        inflight.clear(d, 1, 1, None)  # must not raise

    def test_clear_all_removes_every_marker(self):
        d = tempfile.mkdtemp()
        inflight.write(d, 1, 1, None, "aaaaaaa", "t")
        inflight.write(d, 2, 3, 4, "bbbbbbb", "t")
        inflight.clear_all(d)
        self.assertIsNone(inflight.read(d, 1, 1, None))
        self.assertIsNone(inflight.read(d, 2, 3, 4))

    def test_subtask_none_omits_suffix(self):
        d = tempfile.mkdtemp()
        p = inflight.marker_path(d, 1, 1, None)
        self.assertNotIn("-None", p.name)
        self.assertEqual(p.name, ".dispatch-inflight-1-1.json")


if __name__ == "__main__":
    main()
