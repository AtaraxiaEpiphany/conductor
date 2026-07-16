"""Tests for the modified-guidance injection into task-executor (B.5).

``on-subagent-start.py``'s retry path already builds a ``[Conductor Retry]`` block
from the latest ``### Attempt ❌`` handoff record. B.5 extends it: when the spine
wrote a failure-analyst ``modification`` to the per-task modified-guidance marker,
the hook appends a ``[Conductor Modified Retry]`` block carrying it — and CONSUMES
the marker (delete-on-read) so it applies to exactly one retry.

Driven end-to-end via the hook subprocess with a real locked task under the
``conductor/tracks/`` layout ``resolve_locked_task`` scans.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
_scripts = ROOT / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "on_subagent_start", _scripts / "on-subagent-start.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_HOOK = _scripts / "on-subagent-start.py"


def _write_modified_guidance(track_dir, phase, task, subtask, modification, root_cause=None):
    """Write the modified-guidance marker the hook reads (mirrors the spine writer)."""
    cdir = track_dir / ".conductor"
    cdir.mkdir(parents=True, exist_ok=True)
    sub = f"-{subtask}" if subtask is not None else ""
    payload = modification or ""
    if root_cause:
        payload = f"Root cause: {root_cause}\n\nModified approach:\n{payload}"
    (cdir / f".modified-guidance-{phase}-{task}{sub}.md").write_text(payload)


def _make_locked_track(retry_count=2):
    """A cwd whose ``conductor/tracks/<id>/`` holds a locked in_progress task.

    The hook's ``resolve_locked_task`` scans ``<cwd>/conductor/tracks/*/track-state.json``
    for a task whose ``current_*_index`` point at an ``in_progress`` unit.
    """
    cwd = tempfile.mkdtemp()
    track_dir = Path(cwd) / "conductor" / "tracks" / "feat_20260101"
    track_dir.mkdir(parents=True)
    state = {
        "track_id": "feat_20260101", "type": "feature", "status": "in_progress",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "phases": [{"name": "Phase 1", "status": "in_progress", "tasks": [
            {"name": "Task A", "status": "in_progress",
             "retry_count": retry_count}]}],
    }
    (track_dir / "track-state.json").write_text(json.dumps(state))
    (track_dir / "plan.md").write_text("# Plan\n")
    # git init so nothing explodes if a git op runs; not required for the probe.
    subprocess.run(["git", "-C", str(track_dir), "init", "-q"],
                   check=True, capture_output=True)
    return cwd, track_dir


def _write_handoff(track_dir, phase, task, attempt_failed=True):
    """Write a handoff file with one ### Attempt record (❌ if failed)."""
    hdir = track_dir / ".conductor" / "handoff"
    hdir.mkdir(parents=True, exist_ok=True)
    mark = "❌" if attempt_failed else "✅"
    body = (
        f"### Attempt 1/3 {mark}\n"
        f"What Was Done: partial impl\n"
        f"Failure Reason: null deref\n"
        f"Suggested Next Step: add a None guard\n"
    )
    (hdir / f"P{phase}T{task}.md").write_text(body)


def _run_hook(agent_type, cwd):
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps({"agent_type": agent_type, "cwd": cwd}),
        capture_output=True, text=True,
    )
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


class ModifiedGuidanceBlockTests(TestCase):
    """Unit tests for ``_modified_guidance_block`` (read + consume-on-read)."""

    def test_reads_and_consumes_marker(self):
        cwd, track_dir = _make_locked_track()
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        _write_modified_guidance(track_dir, 1, 1, None,
                                      "use a defaultdict instead", "null deref")
        block = _mod._modified_guidance_block(str(track_dir), 1, 1, None)
        self.assertIsNotNone(block)
        self.assertIn("[Conductor Modified Retry]", block)
        self.assertIn("defaultdict", block)
        # Consumed: a second read returns None.
        self.assertIsNone(_mod._modified_guidance_block(str(track_dir), 1, 1, None))

    def test_none_when_no_marker(self):
        cwd, _ = _make_locked_track()
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        self.assertIsNone(_mod._modified_guidance_block(cwd, 1, 1, None))


class RetryContextInjectionTests(TestCase):
    """End-to-end: the hook injects the modified-guidance block for a retrying
    task-executor, alongside the existing handoff retry nudge."""

    def test_modified_retry_block_appended_for_task_executor(self):
        cwd, track_dir = _make_locked_track(retry_count=2)
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        _write_handoff(track_dir, 1, 1, attempt_failed=True)
        _write_modified_guidance(track_dir, 1, 1, None,
                                      "add a None guard at line 42", "null deref")
        out = _run_hook("task-executor", cwd)
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        if not ctx:
            ctx = out.get("additionalContext", "")
        self.assertIn("[Conductor Modified Retry]", ctx)
        self.assertIn("None guard at line 42", ctx)
        # The plain retry nudge is still present too.
        self.assertIn("[Conductor Retry]", ctx)

    def test_no_modified_block_when_marker_absent(self):
        # A normal (non-modified) retry must NOT carry the modified block — only
        # the plain retry nudge from the handoff.
        cwd, track_dir = _make_locked_track(retry_count=1)
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        _write_handoff(track_dir, 1, 1, attempt_failed=True)
        out = _run_hook("task-executor", cwd)
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        if not ctx:
            ctx = out.get("additionalContext", "")
        self.assertNotIn("[Conductor Modified Retry]", ctx)
        self.assertIn("[Conductor Retry]", ctx)

    def test_marker_consumed_after_one_dispatch(self):
        # The marker is delete-on-read, so a second dispatch (no new marker) must
        # not see the modified block.
        cwd, track_dir = _make_locked_track(retry_count=2)
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        _write_handoff(track_dir, 1, 1, attempt_failed=True)
        _write_modified_guidance(track_dir, 1, 1, None, "approach X", "r")
        _run_hook("task-executor", cwd)  # consumes the marker
        out = _run_hook("task-executor", cwd)  # second dispatch
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        if not ctx:
            ctx = out.get("additionalContext", "")
        self.assertNotIn("[Conductor Modified Retry]", ctx)

    def test_non_retry_agent_gets_no_modified_block(self):
        # explorer is dispatched fresh (not a retry agent) → no retry context at all.
        cwd, track_dir = _make_locked_track()
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        _write_modified_guidance(track_dir, 1, 1, None, "approach X", "r")
        out = _run_hook("explorer", cwd)
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        if not ctx:
            ctx = out.get("additionalContext", "")
        self.assertNotIn("[Conductor Modified Retry]", ctx)


if __name__ == "__main__":
    main()
