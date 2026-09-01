r"""Behavioral tests for on-subagent-start.py's spawn-time inflight stamp.

The marker means "spawned", not "prepared" (the 2026-09-01 dispatch-deadlock
incident): SubagentStart is the single production writer of the inflight
marker, gated on the dispatched agent being a roster single-writer (executor
class) and no active wave. These tests pin that gate at the hook boundary —
run the real hook as a subprocess against a fabricated locked track and
assert the marker's presence/absence, its start_sha, and its gen bump.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))
_HOOK = _scripts / "on-subagent-start.py"

# Import the lib directly for marker reads (lightweight, no track_state import).
from lib import dispatch_inflight as inflight  # noqa: E402


def _git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    return d


def _commit_start(d):
    path = os.path.join(d, ".conductor", "plan.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# plan\n")
    subprocess.run(["git", "add", "--", ".conductor/plan.md"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m",
                    "chore(conductor): Start task 'T1' [P1.T1]"], cwd=d, check=True)


def _short_head(d):
    return subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"], cwd=d,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _write_locked_track(d, tid="demo_20260901"):
    track_dir = os.path.join(d, "conductor", "tracks", tid)
    os.makedirs(track_dir, exist_ok=True)
    state = {
        "track_id": tid,
        "current_phase_index": 1,
        "current_task_index": 1,
        "current_subtask_index": None,
        "phases": [{"name": "P1", "tasks": [
            {"name": "T1", "status": "in_progress", "commit_sha": None}]}],
    }
    with open(os.path.join(track_dir, "track-state.json"), "w") as f:
        json.dump(state, f)
    return track_dir


def _write_wave_ledger(track_dir, statuses):
    """Fabricate a wave ledger with members of the given statuses."""
    ledger = {"wave": [
        {"phase": 1, "task": i + 1, "status": s} for i, s in enumerate(statuses)
    ]}
    p = Path(track_dir) / ".conductor" / "parallel.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger))


class SpawnStampTests(TestCase):
    def setUp(self):
        self.repo = _git_repo()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.repo, True))
        _commit_start(self.repo)
        self.start_sha = _short_head(self.repo)
        self.track_dir = _write_locked_track(self.repo)
        # Isolate env: no inherited plugin/project resolution, no real data dir.
        self.env = {
            k: v for k, v in os.environ.items()
            if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")
        }
        self.data = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.data, True))
        self.env["CLAUDE_PLUGIN_DATA"] = self.data

    def _run(self, agent_type):
        payload = {"agent_type": agent_type, "cwd": self.repo,
                   "session_id": "stamp-test"}
        proc = subprocess.run(
            [sys.executable, str(_HOOK)],
            input=json.dumps(payload), capture_output=True, text=True,
            env=self.env, cwd=self.repo,
        )
        return proc.returncode, proc.stdout

    def _marker(self):
        return inflight.read(self.track_dir, 1, 1, None)

    def test_stamps_for_single_writer_agent(self):
        # task-executor (executor class → single-writer) spawning with a locked
        # task stamps the marker: start_sha = live HEAD (the Start commit),
        # gen = 1 (fresh generation).
        rc, _ = self._run("task-executor")
        self.assertEqual(rc, 0)
        marker = self._marker()
        self.assertIsNotNone(marker, "spawn must stamp the inflight marker")
        self.assertEqual(marker["start_sha"], self.start_sha)
        self.assertEqual(marker["gen"], 1)

    def test_stamps_namespaced_dispatch_form(self):
        # `conductor:task-executor` (installed-plugin dispatch form) must stamp
        # — canonical_name resolves the roster key before the gate.
        rc, _ = self._run("conductor:task-executor")
        self.assertEqual(rc, 0)
        self.assertIsNotNone(self._marker(),
                             "namespaced single-writer spawn must stamp")

    def test_second_spawn_bumps_gen(self):
        # A re-dispatch's spawn stamps a FRESH generation (the telemetry join:
        # same gen = one dispatch spawned twice, higher gen = fresh spawn).
        self._run("task-executor")
        self._run("task-executor")
        marker = self._marker()
        self.assertEqual(marker["gen"], 2)

    def test_no_stamp_for_read_only_verifier(self):
        # phase-checker is read-only → not single-writer-critical → no stamp
        # (its dispatch is never denied by the guard).
        rc, _ = self._run("phase-checker")
        self.assertEqual(rc, 0)
        self.assertIsNone(self._marker(),
                          "read-only verifier spawn must NOT stamp")

    def test_no_stamp_for_unrostered_agent(self):
        # Unrostered → fail-open floor, no conductor scaffold, no stamp.
        rc, _ = self._run("mystery-agent")
        self.assertEqual(rc, 0)
        self.assertIsNone(self._marker(), "unrostered spawn must NOT stamp")

    def test_no_stamp_when_wave_active(self):
        # An active wave (any in_flight member) owns concurrency via the wave
        # F1 guards + per-member wave-agent markers — a serial-spine stamp here
        # would poison the guard for the drain path.
        _write_wave_ledger(self.track_dir, ["in_flight"])
        rc, _ = self._run("task-executor")
        self.assertEqual(rc, 0)
        self.assertIsNone(self._marker(), "active wave must suppress the stamp")

    def test_stamps_when_wave_drained(self):
        # A fully drained ledger is inert (no in_flight members) — the serial
        # spine owns the track again and spawns stamp normally.
        _write_wave_ledger(self.track_dir, ["completed", "completed"])
        rc, _ = self._run("task-executor")
        self.assertEqual(rc, 0)
        self.assertIsNotNone(self._marker(),
                             "drained wave must not suppress the stamp")

    def test_failopen_no_git_repo(self):
        # No git at all: the stamp still fires (fail-open direction — a marker
        # with start_sha null reads as NOT in-flight to the hook predicate,
        # so the safe direction) and the hook exits clean.
        plain = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(plain, True))
        track_dir = _write_locked_track(plain)
        payload = {"agent_type": "task-executor", "cwd": plain,
                   "session_id": "stamp-test"}
        proc = subprocess.run(
            [sys.executable, str(_HOOK)],
            input=json.dumps(payload), capture_output=True, text=True,
            env=self.env, cwd=plain,
        )
        self.assertEqual(proc.returncode, 0)
        marker = inflight.read(track_dir, 1, 1, None)
        self.assertIsNotNone(marker)
        self.assertIsNone(marker["start_sha"],
                          "no-git stamp must record start_sha=null (reads as "
                          "not-in-flight — the safe direction)")

    def test_no_locked_task_stamps_nothing(self):
        # resolve_locked_task finds no in_progress cursor → no stamp target,
        # hook exits clean (allow path elsewhere).
        bare = _git_repo()
        self.addCleanup(lambda: __import__("shutil").rmtree(bare, True))
        _commit_start(bare)
        payload = {"agent_type": "task-executor", "cwd": bare,
                   "session_id": "stamp-test"}
        proc = subprocess.run(
            [sys.executable, str(_HOOK)],
            input=json.dumps(payload), capture_output=True, text=True,
            env=self.env, cwd=bare,
        )
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    main()
