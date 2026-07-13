"""Tests for the refactor diff-scope gate in pre-command-check.py.

The orchestrator-dispatched refactorer (agents/refactorer.md) is a behavior-
preserving patcher scoped to the task's own code diff. Its boundary was prose-
only; `_check_refactor_scope_gate` mechanizes it at commit time: a
`refactor(area):` commit (the refactorer's mandated conventional type) may only
stage files the completed task already touched. The bound is derived from
track-state.json's cursor-target commit_sha (= the agent's code commit).

The trigger is the `refactor(` commit PREFIX — PreToolUse hooks can't see which
agent is running, so the conventional type IS the identity signal (no sidecar).
The gate is FAIL-OPEN: an unresolvable track (none / >1 / cursor not completed /
empty sha) or an undeterminable bound (git error) → allow, never a false block.
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

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "pre_command_check", _scripts / "pre-command-check.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_HOOK = _scripts / "pre-command-check.py"
_cursor_completed_code_sha = _mod._cursor_completed_code_sha
_resolve_refactor_bound = _mod._resolve_refactor_bound


# --- shared fixtures ----------------------------------------------------------
def _git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    return d


def _commit(d, msg, files):
    """Write `files` into repo `d`, stage ONLY those paths, commit. Staging
    explicit paths (not `git add .`) keeps the untracked track-state.json out
    of the index so it can't pollute the refactorer's staged set."""
    for path, content in files.items():
        full = os.path.join(d, path)
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        subprocess.run(["git", "add", "--", path], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=d, check=True)


def _stage_paths(d, files):
    """Write `files` and stage ONLY those paths (no `git add .`)."""
    for path, content in files.items():
        full = os.path.join(d, path)
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        subprocess.run(["git", "add", "--", path], cwd=d, check=True)


def _short_head(d):
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=d,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _write_track_state(d, tid, sha, status="completed"):
    """Write a conductor track-state.json with the cursor on the completed task."""
    track_dir = os.path.join(d, "conductor", "tracks", tid)
    os.makedirs(track_dir, exist_ok=True)
    state = {
        "current_phase_index": 1, "current_task_index": 1,
        "current_subtask_index": None,
        "phases": [{"name": "P1", "tasks": [
            {"name": "T1", "status": status, "commit_sha": sha}]}],
    }
    with open(os.path.join(track_dir, "track-state.json"), "w") as f:
        json.dump(state, f)


def _repo_with_completed_track():
    """A repo whose completed task touched exactly {src/foo.py, tests/test_foo.py}.

    Returns (repo_dir, code_sha). The code commit has a real parent (the init
    commit) so `<code_sha>~1..<code_sha>` is a valid, non-empty diff range.
    """
    d = _git_repo()
    _commit(d, "chore: init", {"README.md": "init\n"})           # parent commit
    _commit(d, "feat(api): add foo", {                            # code_sha
        "src/foo.py": "def foo(): ...\n",
        "tests/test_foo.py": "def test_foo(): ...\n",
    })
    code_sha = _short_head(d)
    _write_track_state(d, "demo_20260713", code_sha)
    return d, code_sha


def _run_hook(cwd, command):
    payload = {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command}}
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


# --- unit tests: cursor → commit_sha resolution ------------------------------
_COMPLETED = {
    "current_phase_index": 1, "current_task_index": 1, "current_subtask_index": None,
    "phases": [{"name": "P1", "tasks": [
        {"name": "T1", "status": "completed", "commit_sha": "abc1234"}]}],
}


def _clone(state):
    return json.loads(json.dumps(state))


class CursorShaUnitTests(TestCase):
    def test_completed_returns_sha(self):
        self.assertEqual(_cursor_completed_code_sha(_COMPLETED), "abc1234")

    def test_in_progress_returns_none(self):
        s = _clone(_COMPLETED)
        s["phases"][0]["tasks"][0]["status"] = "in_progress"
        self.assertIsNone(_cursor_completed_code_sha(s))

    def test_non_terminal_returns_none(self):
        for status in ("new", "blocked", "deferred", "skipped"):
            s = _clone(_COMPLETED)
            s["phases"][0]["tasks"][0]["status"] = status
            self.assertIsNone(_cursor_completed_code_sha(s), status)

    def test_empty_sha_returns_none(self):
        s = _clone(_COMPLETED)
        s["phases"][0]["tasks"][0]["commit_sha"] = ""
        self.assertIsNone(_cursor_completed_code_sha(s))

    def test_missing_sha_returns_none(self):
        s = _clone(_COMPLETED)
        del s["phases"][0]["tasks"][0]["commit_sha"]
        self.assertIsNone(_cursor_completed_code_sha(s))

    def test_subtask_cursor_resolves(self):
        # current_subtask_index points into the task's subtasks list.
        s1 = {"name": "S1", "status": "completed", "commit_sha": "s1sha00"}
        s2 = {"name": "S2", "status": "completed", "commit_sha": "s2sha00"}
        t1 = {"name": "T1", "subtasks": [s1, s2]}
        p1 = {"name": "P1", "tasks": [t1]}
        s = {
            "current_phase_index": 1, "current_task_index": 1,
            "current_subtask_index": 2,
            "phases": [p1],
        }
        self.assertEqual(_cursor_completed_code_sha(s), "s2sha00")

    def test_zero_indices_returns_none(self):
        self.assertIsNone(_cursor_completed_code_sha(
            {"current_phase_index": 0, "current_task_index": 0, "phases": []}))

    def test_out_of_range_indices_returns_none(self):
        s = _clone(_COMPLETED)
        s["current_phase_index"] = 99  # no such phase
        self.assertIsNone(_cursor_completed_code_sha(s))


class ResolveBoundTests(TestCase):
    def _track(self, d, tid, sha, status="completed"):
        _write_track_state(d, tid, sha, status=status)

    def test_one_completed_returns_sha(self):
        d = tempfile.mkdtemp()
        try:
            self._track(d, "a_20260713", "aaaaaaa")
            self.assertEqual(_resolve_refactor_bound(Path(d)), "aaaaaaa")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_zero_tracks_returns_none(self):
        d = tempfile.mkdtemp()
        try:
            self.assertIsNone(_resolve_refactor_bound(Path(d)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_two_completed_returns_none(self):
        # Ambiguous (conductor runs one track/session, but two completed cursors
        # can't be disambiguated from disk alone) → fail-open.
        d = tempfile.mkdtemp()
        try:
            self._track(d, "a_20260713", "aaaaaaa")
            self._track(d, "b_20260713", "bbbbbbb")
            self.assertIsNone(_resolve_refactor_bound(Path(d)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_in_progress_only_returns_none(self):
        d = tempfile.mkdtemp()
        try:
            self._track(d, "a_20260713", "aaaaaaa", status="in_progress")
            self.assertIsNone(_resolve_refactor_bound(Path(d)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_malformed_state_skipped(self):
        # A corrupt track-state.json is skipped, not raised.
        d = tempfile.mkdtemp()
        try:
            self._track(d, "a_20260713", "aaaaaaa")
            bad = os.path.join(d, "conductor", "tracks", "bad_20260713")
            os.makedirs(bad, exist_ok=True)
            with open(os.path.join(bad, "track-state.json"), "w") as f:
                f.write("{ not json")
            self.assertEqual(_resolve_refactor_bound(Path(d)), "aaaaaaa")
        finally:
            shutil.rmtree(d, ignore_errors=True)


# --- integration tests: the full hook (subprocess) ---------------------------
class RefactorScopeGateIntegrationTests(TestCase):
    def _expect_deny(self, rc, out):
        self.assertEqual(rc, 0, out)
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        self.assertIn("refactor-scope", spec.get("permissionDecisionReason", ""))

    def _expect_allow(self, rc, out):
        self.assertEqual(rc, 0, out)
        spec = out.get("hookSpecificOutput", {})
        self.assertNotIn("permissionDecision", spec)

    def test_refactor_in_scope_allows(self):
        # Staging a file the completed task touched → within REVISION_RANGE.
        d, _ = _repo_with_completed_track()
        try:
            _stage_paths(d, {"src/foo.py": "def foo(): return 1\n"})
            rc, out = _run_hook(d, 'git commit -m "refactor(api): simplify foo"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_refactor_out_of_scope_denies(self):
        # Staging a file the task did NOT touch → diff-scope violation.
        d, _ = _repo_with_completed_track()
        try:
            _stage_paths(d, {"src/bar.py": "def bar(): ...\n"})
            rc, out = _run_hook(d, 'git commit -m "refactor(api): touch bar"')
            self._expect_deny(rc, out)
            spec = out["hookSpecificOutput"]
            # Remediation names the offending file + the bound range.
            self.assertIn("src/bar.py", spec.get("additionalContext", ""))
            self.assertIn("~1..", spec.get("permissionDecisionReason", ""))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_refactor_multiple_out_of_scope_denies(self):
        d, _ = _repo_with_completed_track()
        try:
            _stage_paths(d, {"src/bar.py": "x", "src/baz.py": "y"})
            rc, out = _run_hook(d, 'git commit -m "refactor(api): touch two"')
            self._expect_deny(rc, out)
            ctx = out["hookSpecificOutput"].get("additionalContext", "")
            self.assertIn("+1 more", ctx)  # the `more` suffix on >1 violation
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_non_refactor_commit_allows(self):
        # A docs/chore commit is outside the refactor gate's scope (and F2's).
        d, _ = _repo_with_completed_track()
        try:
            _stage_paths(d, {"src/bar.py": "x"})
            rc, out = _run_hook(d, 'git commit -m "docs(api): note bar"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_nothing_staged_allows(self):
        # Mirrors F2: don't block blindly when nothing is staged.
        d, _ = _repo_with_completed_track()
        try:
            rc, out = _run_hook(d, 'git commit -m "refactor(api): empty"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # --- fail-open cases (must NEVER false-block) ---
    def test_fail_open_no_track(self):
        d = _git_repo()
        try:
            _commit(d, "chore: init", {"README.md": "x"})
            _stage_paths(d, {"src/bar.py": "x"})
            rc, out = _run_hook(d, 'git commit -m "refactor(api): no track"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_fail_open_cursor_not_completed(self):
        d = _git_repo()
        try:
            _commit(d, "chore: init", {"README.md": "x"})
            _commit(d, "feat(api): add foo", {"src/foo.py": "x"})
            _write_track_state(d, "demo_20260713", _short_head(d), status="in_progress")
            _stage_paths(d, {"src/bar.py": "x"})
            rc, out = _run_hook(d, 'git commit -m "refactor(api): not done"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_fail_open_empty_commit_sha(self):
        d = _git_repo()
        try:
            _commit(d, "chore: init", {"README.md": "x"})
            _commit(d, "feat(api): add foo", {"src/foo.py": "x"})
            _write_track_state(d, "demo_20260713", "")  # no code commit recorded
            _stage_paths(d, {"src/bar.py": "x"})
            rc, out = _run_hook(d, 'git commit -m "refactor(api): no sha"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_fail_open_two_completed_tracks(self):
        d = _git_repo()
        try:
            _commit(d, "chore: init", {"README.md": "x"})
            _commit(d, "feat(api): add foo", {"src/foo.py": "x"})
            sha = _short_head(d)
            _write_track_state(d, "a_20260713", sha)
            _write_track_state(d, "b_20260713", sha)
            _stage_paths(d, {"src/bar.py": "x"})
            rc, out = _run_hook(d, 'git commit -m "refactor(api): ambiguous"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
