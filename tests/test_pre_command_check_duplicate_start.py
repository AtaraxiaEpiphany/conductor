"""Tests for the duplicate-Start-commit backstop gate in pre-command-check.py.

`track-state start` owns the Start-track commit and is idempotent (a re-run is a
no-op), so a skill body that still emits a prose ``git commit -m
"chore(conductor): Start …"`` on top of an existing Start commit re-introduces
the user-reported double-start bug. ``_check_duplicate_start_gate`` mechanizes
the guard at commit time: a Start-prefix commit is denied when HEAD is already a
Start commit.

The trigger is the ``chore(conductor): Start `` message PREFIX — PreToolUse
hooks can't see which agent is running, so the message IS the identity signal
(mirrors the refactor-scope gate). FAIL-OPEN: a non-start commit, a HEAD that
isn't a start commit, an unreadable message, or any git error → allow.
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

_spec = importlib.util.spec_from_file_location(
    "pre_command_check", _scripts / "pre-command-check.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_HOOK = _scripts / "pre-command-check.py"


def _git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    return d


def _commit(d, msg, files):
    for path, content in files.items():
        with open(os.path.join(d, path), "w") as f:
            f.write(content)
        subprocess.run(["git", "add", "--", path], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=d, check=True)


def _stage(d, files):
    for path, content in files.items():
        with open(os.path.join(d, path), "w") as f:
            f.write(content)
        subprocess.run(["git", "add", "--", path], cwd=d, check=True)


def _run_hook(cwd, command):
    payload = {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command}}
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


class DuplicateStartGateTests(TestCase):
    def setUp(self):
        self.d = _git_repo()

    def _commit_cmd(self, msg):
        # message is quoted so _extract_commit_message reads it statically
        return f'git commit -m "{msg}"'

    def _expect_allow(self, rc, out):
        spec = out.get("hookSpecificOutput", {})
        self.assertNotIn("permissionDecision", spec,
                         f"unexpected deny: {out}")

    def _expect_deny(self, rc, out):
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        self.assertIn("duplicate start-commit", spec.get("additionalContext", ""))

    def test_second_start_commit_on_start_head_denies(self):
        # HEAD is already a Start commit → a second Start commit is the bug.
        _commit(self.d, "chore(conductor): Start track 'abc'",
                {"track-state.json": "{}\n"})
        _stage(self.d, {"plan.md": "x\n"})  # something staged so the commit is real
        rc, out = _run_hook(self.d, self._commit_cmd("chore(conductor): Start track 'abc'"))
        self._expect_deny(rc, out)

    def test_first_start_commit_on_non_start_head_allows(self):
        # HEAD is a normal commit (init), so this Start commit is the FIRST one.
        _commit(self.d, "chore: init", {"README.md": "init\n"})
        _stage(self.d, {"track-state.json": "{}\n"})
        rc, out = _run_hook(self.d, self._commit_cmd("chore(conductor): Start track 'abc'"))
        self._expect_allow(rc, out)

    def test_non_start_commit_allows_even_when_head_is_start(self):
        # A normal bookkeeping commit on top of a Start commit is fine.
        _commit(self.d, "chore(conductor): Start track 'abc'",
                {"track-state.json": "{}\n"})
        _stage(self.d, {"plan.md": "x\n"})
        rc, out = _run_hook(self.d, self._commit_cmd("chore(conductor): Sync plan"))
        self._expect_allow(rc, out)

    def test_start_task_commit_is_not_confused_with_start_track(self):
        # The per-task Start commit (Site A, from dispatch-prepare) is also
        # `chore(conductor): Start ` prefixed → it IS a start commit and a
        # duplicate of IT would also be blocked. Confirm the gate treats it
        # consistently (denies a second consecutive Start-task commit).
        _commit(self.d, "chore(conductor): Start task 'Foo' [P1.T1]",
                {"track-state.json": "{}\n"})
        _stage(self.d, {"plan.md": "x\n"})
        rc, out = _run_hook(self.d,
                            self._commit_cmd("chore(conductor): Start task 'Foo' [P1.T1]"))
        self._expect_deny(rc, out)

    def test_fail_open_when_head_not_start(self):
        # Covered structurally by first_start test, but pin explicitly: a Start
        # commit message when HEAD is a feat commit must allow.
        _commit(self.d, "feat(x): thing", {"README.md": "x\n"})
        _stage(self.d, {"track-state.json": "{}\n"})
        rc, out = _run_hook(self.d, self._commit_cmd("chore(conductor): Start track 'y'"))
        self._expect_allow(rc, out)


# --- unit-level: the helper's fail-open contract ---------------------------
class HeadIsStartCommitUnitTests(TestCase):
    def test_true_for_start_track(self):
        d = _git_repo()
        _commit(d, "chore(conductor): Start track 'z'", {"a": "1"})
        self.assertIs(_mod._head_is_start_commit(d), True)

    def test_false_for_normal_commit(self):
        d = _git_repo()
        _commit(d, "feat: x", {"a": "1"})
        self.assertIs(_mod._head_is_start_commit(d), False)

    def test_none_when_not_a_git_repo(self):
        # fail-open: no git here → None, never a hard block
        import tempfile
        d = tempfile.mkdtemp()
        self.assertIs(_mod._head_is_start_commit(d), None)


if __name__ == "__main__":
    main()
