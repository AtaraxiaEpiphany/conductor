"""Tests for the PreToolUse:Bash write-result clean-tree hook
(on-write-result-clean-tree.py).

The hook closes the "lots of files not committed" leak: task-executor can
report ``write-result --status success`` while implementation files sit
uncommitted, because the conductor finalize commit stages ONLY conductor-managed
files (``track-state.json``/``plan.md``/``.conductor/``) by design
— it will never sweep up the agent's code. So the only place implementation
files get committed is Step 8, and nothing caught a SUCCESS claim with a dirty
tree. This hook makes that lie deterministic: deny ``--status success`` while
implementation files are uncommitted, with a deny reason that is itself the Step
8 cure.

Property-level (pin the invariant, not the implementation): the hook filters on
agent_type == task-executor + the visible ``--status success`` flag, resolves the
locked task, and tests the working tree via ``git status --porcelain`` minus
conductor-managed paths. These tests drive that predicate directly and assert
allow/deny; fail-open is asserted too.
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

_HOOK = _scripts / "on-write-result-clean-tree.py"


# --- shared fixtures ----------------------------------------------------------
def _git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    return d


def _commit_plan(d, msg):
    """Commit a conductor-managed file so HEAD exists."""
    path = os.path.join(d, ".conductor", "plan.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# plan\n")
    subprocess.run(["git", "add", "--", ".conductor/plan.md"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=d, check=True)


def _write_locked_track(d, tid="demo_20260717"):
    """Track-state.json with the cursor on an in_progress task (resolve() target)."""
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


def _run_hook(cwd, command, agent_type="task-executor", tool_name="Bash"):
    """Pipe a PreToolUse payload on stdin; return (rc, parsed-output)."""
    payload = {
        "tool_name": tool_name, "cwd": cwd, "agent_type": agent_type,
        "tool_input": {"command": command},
    }
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


def _decision(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


_WR_SUCCESS = 'track-state write-result "{td}" --status success --commit-sha abc1234'


class WriteResultCleanTreeHookTests(TestCase):
    def setUp(self):
        self.repo = _git_repo()
        _commit_plan(self.repo, "chore(conductor): Start task 'T1' [P1.T1]")
        self.track_dir = _write_locked_track(self.repo)

    def test_non_bash_tool_is_allowed(self):
        rc, out = _run_hook(self.repo, _WR_SUCCESS.format(td=self.track_dir),
                            tool_name="Write")
        self.assertEqual(_decision(out), "allow")

    def test_non_task_executor_agent_is_allowed(self):
        # A non-task-executor agent reporting success is out of scope → allow.
        rc, out = _run_hook(self.repo, _WR_SUCCESS.format(td=self.track_dir),
                            agent_type="phase-checker")
        self.assertEqual(_decision(out), "allow")

    def test_non_write_result_command_is_allowed(self):
        rc, out = _run_hook(self.repo, "git status")
        self.assertEqual(_decision(out), "allow")

    def test_failure_status_is_allowed(self):
        # FAILURE is the honest escape; a dirty tree is expected and fine.
        impl = os.path.join(self.repo, "src", "app.py")
        os.makedirs(os.path.dirname(impl), exist_ok=True)
        Path(impl).write_text("x = 1\n")
        cmd = (f'track-state write-result "{self.track_dir}" '
               f'--status failure --summary "incomplete"')
        rc, out = _run_hook(self.repo, cmd)
        self.assertEqual(_decision(out), "allow")

    def test_success_clean_tree_is_allowed(self):
        rc, out = _run_hook(self.repo, _WR_SUCCESS.format(td=self.track_dir))
        self.assertEqual(_decision(out), "allow")

    def test_success_with_only_conductor_files_is_allowed(self):
        # A dirty tree consisting solely of conductor-managed artifacts is
        # finalize's job — not stranded implementation work → allow.
        Path(os.path.join(self.repo, "plan.md")).write_text("# changed\n")
        rc, out = _run_hook(self.repo, _WR_SUCCESS.format(td=self.track_dir))
        self.assertEqual(_decision(out), "allow")

    def test_success_with_untracked_impl_file_is_denied(self):
        # Untracked implementation file → the canonical leak → deny.
        impl = os.path.join(self.repo, "src", "app.py")
        os.makedirs(os.path.dirname(impl), exist_ok=True)
        Path(impl).write_text("x = 1\n")
        rc, out = _run_hook(self.repo, _WR_SUCCESS.format(td=self.track_dir))
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        reason = spec.get("permissionDecisionReason", "")
        # The deny reason IS the cure: the Step 8 idiom + the FAILURE escape.
        self.assertIn("git add -A", reason)
        self.assertIn("Step 8", reason)
        self.assertIn("--status failure", reason)
        self.assertIn("src/app.py", reason)
        # systemMessage surfaces the deny for log visibility.
        self.assertIn("systemMessage", out)

    def test_success_with_modified_tracked_impl_file_is_denied(self):
        # Tracked implementation file modified, not committed → deny too.
        impl = os.path.join(self.repo, "lib", "mod.py")
        os.makedirs(os.path.dirname(impl), exist_ok=True)
        Path(impl).write_text("v = 0\n")
        subprocess.run(["git", "add", "--", "lib/mod.py"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "feat: add mod"], cwd=self.repo,
                       check=True)
        Path(impl).write_text("v = 1\n")  # now dirty (modified, tracked)
        rc, out = _run_hook(self.repo, _WR_SUCCESS.format(td=self.track_dir))
        self.assertEqual(_decision(out), "deny")

    def test_status_eq_success_form_is_denied(self):
        # --status=success (equals form) must also match.
        impl = os.path.join(self.repo, "src", "a.py")
        os.makedirs(os.path.dirname(impl), exist_ok=True)
        Path(impl).write_text("y = 2\n")
        cmd = (f'track-state write-result "{self.track_dir}" '
               f'--status=success --commit-sha abc1234')
        rc, out = _run_hook(self.repo, cmd)
        self.assertEqual(_decision(out), "deny")

    def test_data_escape_hatch_is_allowed(self):
        # --data supplies status inside JSON we can't inspect → fail-open.
        impl = os.path.join(self.repo, "src", "b.py")
        os.makedirs(os.path.dirname(impl), exist_ok=True)
        Path(impl).write_text("z = 3\n")
        cmd = (f'track-state write-result "{self.track_dir}" '
               f"--data '{{\"status\":\"success\"}}'")
        rc, out = _run_hook(self.repo, cmd)
        self.assertEqual(_decision(out), "allow")

    def test_no_locked_task_is_allowed(self):
        # Repo with no in_progress cursor → resolve() returns None → allow.
        repo = _git_repo()
        _commit_plan(repo, "init")
        rc, out = _run_hook(repo, _WR_SUCCESS.format(td="/no/such/track"))
        self.assertEqual(rc, 0)
        self.assertEqual(_decision(out), "allow")


# --- invariant regression: finalize still stages only conductor-managed files --
# Pins the load-bearing invariant this fix deliberately does NOT touch: _git_commit
# must never sweep implementation files into the conductor commit. Extends the
# spirit of test_envelope_commit_staging.py.
class FinalizeStagingInvariantTests(TestCase):
    def setUp(self):
        self.repo = _git_repo()
        _commit_plan(self.repo, "init")
        sys.path.insert(0, str(Path(self.repo)))
        # _git_commit lives in track_state.git_ops; import lazily after path setup.
        from track_state.git_ops import _git_commit
        self._git_commit = _git_commit

    def test_git_commit_excludes_implementation_files(self):
        # An implementation file + a conductor file both dirty in the tree.
        impl = os.path.join(self.repo, "src", "app.py")
        os.makedirs(os.path.dirname(impl), exist_ok=True)
        Path(impl).write_text("x = 1\n")
        # track-state.json is a conductor-managed file that _git_commit stages
        # (issues.md was removed from the staging list when its legacy writer
        # was deleted). Use a conductor-managed file to prove the staging set
        # is still narrow-but-real.
        ts = os.path.join(self.repo, "track-state.json")
        Path(ts).write_text('{"status":"in_progress"}\n')

        committed = self._git_commit(self.repo, "chore(conductor): test")
        self.assertTrue(committed)

        # The conductor file must be in the commit; the implementation file must NOT.
        show = subprocess.run(
            ["git", "show", "--name-only", "--format=", "HEAD"],
            cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("track-state.json", show)
        self.assertNotIn("src/app.py", show)

        # And the implementation file remains uncommitted in the working tree.
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.repo,
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("src/app.py", status)


if __name__ == "__main__":
    main()
