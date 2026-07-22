"""Tests for the PreToolUse:Read orchestrator-read-guard hook
(on-orchestrator-read-guard.py).

The hook enforces the thin-router invariant: while a task is in flight (marker
present, HEAD still the Start commit, no result.json), the orchestrator may NOT
Read the track's ``spec.md``/``plan.md`` — those are the subagent's context,
not the orchestrator's, and reading them to "compensate" for a missing result
block is the thin-router violation. Other reads, and all reads when no task is
in flight, are allowed.

Property-level (pin the invariant, not the implementation): the hook reads the
same marker + HEAD/result predicate the spine and on-dispatch-dedupe use, so
these tests drive that predicate directly and assert allow/deny. Fail-open is
asserted too: corrupt state must allow, never raise.
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

_HOOK = _scripts / "on-orchestrator-read-guard.py"

from lib import dispatch_inflight as inflight  # noqa: E402


# --- shared fixtures (mirror tests/test_dispatch_dedupe.py) -------------------
def _git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    return d


def _commit(d, msg, body="# plan\n"):
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
    inflight.write(track_dir, phase, task, subtask, start_sha, "2026-07-22T00:00:00+00:00")


def _run_hook(cwd, file_path):
    """Run the guard against a Read of ``file_path`` from the orchestrator
    (no agent_type — the orchestrator-side PreToolUse case)."""
    payload = {
        "tool_name": "Read", "cwd": cwd,
        "tool_input": {"file_path": file_path},
    }
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


def _run_hook_payload(payload):
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


# --- hook tests ---------------------------------------------------------------
class OrchestratorReadGuardTests(TestCase):
    def setUp(self):
        self.repo = _git_repo()
        _commit(self.repo, "chore(conductor): Start task 'T1' [P1.T1]")
        self.start_sha = _short_head(self.repo)
        self.track_dir = _write_locked_track(self.repo)
        self.spec_path = os.path.join(self.track_dir, "spec.md")
        self.plan_path = os.path.join(self.track_dir, "plan.md")

    def test_non_read_tool_is_allowed(self):
        rc, out = _run_hook_payload(
            {"tool_name": "Bash", "cwd": self.repo,
             "tool_input": {"command": "ls"}})
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_subagent_read_is_allowed(self):
        # PreToolUse fires inside subagents too; an agent_type means this Read is
        # the subagent self-loading its context — must be allowed regardless.
        rc, out = _run_hook_payload(
            {"tool_name": "Read", "cwd": self.repo, "agent_type": "task-executor",
             "tool_input": {"file_path": self.spec_path}})
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_no_locked_task_is_allowed(self):
        # Repo with no in_progress cursor → resolve() None → allow.
        repo = _git_repo()
        _commit(repo, "init")
        rc, out = _run_hook(repo, os.path.join(repo, "conductor", "tracks", "x", "spec.md"))
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_no_marker_is_allowed(self):
        # Locked task but no inflight marker → allow (nothing in flight).
        rc, out = _run_hook(self.repo, self.spec_path)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_unrelated_read_is_allowed(self):
        # Locked task + in-flight marker, but reading an unrelated file → allow.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        rc, out = _run_hook(self.repo, os.path.join(self.repo, "scripts", "foo.py"))
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_spec_md_read_denied_when_in_flight(self):
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        rc, out = _run_hook(self.repo, self.spec_path)
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        reason = spec.get("permissionDecisionReason", "")
        # Must name the task and prescribe the terminating recovery, not self-read.
        self.assertIn("P1T1", reason)
        self.assertIn("dispatch-finalize", reason)

    def test_plan_md_read_denied_when_in_flight(self):
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        rc, out = _run_hook(self.repo, self.plan_path)
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        self.assertIn("plan.md", spec.get("permissionDecisionReason", ""))

    def test_allows_after_head_advances(self):
        # Marker present but HEAD moved past Start → task finalized → allow.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        _commit(self.repo, "feat: real work landed", body="# plan v2\n")
        rc, out = _run_hook(self.repo, self.spec_path)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_allows_when_result_json_present(self):
        # HEAD still Start, but result.json landed → dispatch returned → allow.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        result_path = os.path.join(self.track_dir, ".conductor", "result.json")
        os.makedirs(os.path.dirname(result_path), exist_ok=True)
        with open(result_path, "w") as f:
            json.dump({"status": "success"}, f)
        rc, out = _run_hook(self.repo, self.spec_path)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_corrupt_marker_is_allowed_failopen(self):
        marker = inflight.marker_path(self.track_dir, 1, 1, None)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{ not valid json")
        rc, out = _run_hook(self.repo, self.spec_path)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_no_start_sha_is_allowed_failopen(self):
        # Marker present but start_sha empty/missing → in_flight False → allow.
        inflight.write(self.track_dir, 1, 1, None, "", "2026-07-22T00:00:00+00:00")
        rc, out = _run_hook(self.repo, self.spec_path)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_relative_path_spec_md_denied(self):
        # A spec.md reached by a relative/cwd-anchored path must still trip the
        # guard (basename + track-dir suffix match), not just absolute paths.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        rel = os.path.relpath(self.spec_path, self.repo)
        rc, out = _run_hook(self.repo, rel)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")


if __name__ == "__main__":
    main()
