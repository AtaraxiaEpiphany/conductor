"""Regression: Rail B-min step skills must survive a bare ``track_id`` argument.

The three step skills (implement-step / parallel-step / post-loop-step) all run
``track-state check "$ARGUMENTS"`` to get ``td`` (an absolute path) AND
``track_id`` (the bare id), then pass one of them to the next command
(``step`` / ``wave-step`` / ``recover`` / ``start`` / ...). A small-window
teleoperator sometimes hands the bare ``track_id`` to that next command, which
then crashed inside ``conductor_dir().mkdir(exist_ok=True)`` with a confusing
``FileNotFoundError: <track_id>/.conductor`` — the literal id was treated as a
relative path whose parent doesn't exist. "but actually the file exists" was
the reported symptom: the track dir was fine, the *argument* was wrong.

The fix (``_resolve_track_dir_or_halt`` wired into ``cli.main`` for the spine
commands) accepts BOTH a real path (fast path) and a bare id (resolved through
the same registry machinery ``check`` uses). These tests exercise it at the
real CLI argv layer — the cmd functions still take a path, so the existing
unit tests are untouched.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.cli import main
from scripts.track_state.core import save


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _state(track_id, **overrides):
    state = {
        "track_id": track_id,
        "type": "feature",
        "status": "in_progress",
        "description": track_id,
        "current_phase_index": 1,
        "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "pending"}]}],
    }
    state.update(overrides)
    return state


class _Project:
    """A temp project root: <root>/conductor/tracks.md + one registered track."""

    def __init__(self, track_id, with_registry=True, status="in_progress"):
        self.root = tempfile.mkdtemp()
        self.track_id = track_id
        # Place the track dir at the canonical conductor/tracks/<id>/ layout.
        self.td = Path(self.root, "conductor", "tracks", track_id)
        self.td.mkdir(parents=True)
        st = _state(track_id, status=status)
        if status == "new":
            st["current_phase_index"] = 0
            st["current_task_index"] = 0
        save(str(self.td), st)
        Path(self.td, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
        # Real git repo so step/start finalize paths work.
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "-C", str(self.td), "init", "-q"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.td), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.td), "commit", "-q", "-m", "init"],
                       check=True, capture_output=True, env=env)
        if with_registry:
            marker = {"new": " ", "in_progress": "~"}.get(status, " ")
            Path(self.root, "conductor", "tracks.md").write_text(
                f"# Tracks\n\n- [{marker}] {track_id} (conductor/tracks/{track_id}/)\n")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def _run(argv, cwd):
    """Invoke cli.main() with a patched argv/cwd; return (parsed_stdout, exit_code).

    Exit code is 0 when main() returns normally, or ``SystemExit.code`` otherwise.
    """
    old_argv, old_cwd, old_out, old_err = sys.argv, os.getcwd(), sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    sys.argv = argv
    os.chdir(cwd)
    code = 0
    try:
        main()
    except SystemExit as e:
        code = e.code if e.code is not None else 0
    finally:
        raw = sys.stdout.getvalue()
        sys.stdout, sys.stderr = old_out, old_err
        sys.argv = old_argv
        os.chdir(old_cwd)
    try:
        parsed = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        parsed = {"_raw": raw}
    return parsed, code


class BareIdResolutionTests(TestCase):
    def test_recover_bare_track_id_resolves(self):
        """The headline bug: ``recover <track_id>`` must not crash with
        ``FileNotFoundError: <track_id>/.conductor`` — it resolves the id to the
        real dir and runs there (proven by .conductor/ being created under it)."""
        p = _Project("auth_20260708")
        self.addCleanup(p.cleanup)
        parsed, code = _run(["track-state", "recover", p.track_id], p.root)
        self.assertNotEqual(code, 1, f"recover crashed: {parsed}")
        # .conductor/ must have been created under the REAL track dir, not a
        # bogus ./auth_20260708/ sibling of the project root.
        self.assertTrue((p.td / ".conductor").is_dir(),
                        "recover did not resolve to the real track dir")
        self.assertNotEqual(parsed.get("status"), "error")

    def test_step_bare_track_id_resolves_and_dispatches(self):
        """``step <track_id>`` (the loop command) resolves the bare id and emits
        a normal dispatch action instead of the mkdir crash."""
        p = _Project("auth_20260708")
        self.addCleanup(p.cleanup)
        parsed, code = _run(["track-state", "step", p.track_id], p.root)
        self.assertNotEqual(code, 1, f"step crashed: {parsed}")
        self.assertEqual(parsed.get("action"), "dispatch")
        self.assertEqual(parsed.get("name"), "Task A")
        # dispatch locks the task under the real dir.
        self.assertTrue((p.td / ".conductor").is_dir())

    def test_start_bare_track_id_resolves(self):
        """``start`` is the other implement-step §1.0 command reached with <td>."""
        p = _Project("auth_20260708", status="new")
        self.addCleanup(p.cleanup)
        parsed, code = _run(["track-state", "start", p.track_id], p.root)
        self.assertNotEqual(code, 1, f"start crashed: {parsed}")

    def test_real_path_fast_path_still_works(self):
        """Passing the actual ``td`` (the intended contract) must be a no-op —
        resolution never trips for an existing path."""
        p = _Project("auth_20260708")
        self.addCleanup(p.cleanup)
        parsed, code = _run(["track-state", "recover", str(p.td)], p.root)
        self.assertNotEqual(code, 1, f"real-path recover crashed: {parsed}")

    def test_shortname_prefix_resolves(self):
        """A shortname (track_id minus _YYYYMMDD) resolves too — Tier 2 of
        _resolve_core, same as ``check``."""
        p = _Project("auth_20260708")
        self.addCleanup(p.cleanup)
        parsed, code = _run(["track-state", "recover", "auth"], p.root)
        self.assertNotEqual(code, 1, f"shortname recover crashed: {parsed}")
        self.assertTrue((p.td / ".conductor").is_dir())


class UnresolvableArgTests(TestCase):
    """Defense-in-depth: an arg that can't be resolved yields a clean {error}
    + exit 1, never the raw ``mkdir`` traceback."""

    def test_no_match_with_registry_emits_clean_error(self):
        p = _Project("auth_20260708")
        self.addCleanup(p.cleanup)
        parsed, code = _run(["track-state", "recover", "ghost_99999999"], p.root)
        self.assertEqual(code, 1)
        self.assertIn("error", parsed)
        self.assertEqual(parsed.get("command"), "recover")
        # The bogus id must NOT have been created as a sibling dir.
        self.assertFalse((Path(p.root) / "ghost_99999999" / ".conductor").exists())

    def test_no_registry_emits_clean_error(self):
        p = _Project("auth_20260708", with_registry=False)
        self.addCleanup(p.cleanup)
        parsed, code = _run(["track-state", "step", "auth_20260708"], p.root)
        self.assertEqual(code, 1)
        self.assertIn("error", parsed)
        self.assertEqual(parsed.get("reason"), "no_registry")


if __name__ == "__main__":
    main()
