"""Tests for the F2 TDD commit gate in pre-command-check.py.

A feat/fix commit that stages source code without a test file is the real
"implementation before test" signal in conductor's single-commit-per-task model
(task-executor commits test+impl together at Step 8). The gate denies
at commit time — a tighter loop than the F3 coverage gate at dispatch-finalize,
which only fires at completion. Exempt by commit TYPE, so docs/chore/style/
refactor/test and chore(conductor) bookkeeping never trip it.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import shutil
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
_commit_type_from_command = _mod._commit_type_from_command
_is_test_file = _mod._is_test_file
_is_source_file = _mod._is_source_file


class UnitTests(TestCase):
    # --- commit-type extraction ---
    def test_type_feat(self):
        self.assertEqual(_commit_type_from_command('git commit -m "feat(api): x"'), "feat")

    def test_type_chore_conductor(self):
        self.assertEqual(_commit_type_from_command('git commit -m "chore(conductor): bookkeeping"'), "chore")

    def test_type_no_space_m(self):
        self.assertEqual(_commit_type_from_command('git commit -m"fix(x): y"'), "fix")

    def test_type_non_conventional_is_none(self):
        self.assertIsNone(_commit_type_from_command('git commit -m "just some message"'))

    def test_type_no_m_flag(self):
        self.assertIsNone(_commit_type_from_command("git commit -F file.txt"))

    # --- test-file detection ---
    def test_test_file_patterns(self):
        # Unambiguous conventions the gate catches. (Java's `*Test.java` suffix
        # is deliberately NOT matched — it can't be told apart from e.g.
        # `Protest.java` by case; F3 coverage backstops that case at completion.)
        for p in ("src/foo.test.ts", "tests/test_x.py", "test_foo.py",
                  "spec/foo_spec.rb", "__tests__/x.js"):
            self.assertTrue(_is_test_file(p), p)

    def test_non_test_files(self):
        for p in ("src/foo.ts", "lib/bar.py", "README.md", "package.json"):
            self.assertFalse(_is_test_file(p), p)

    # --- source-file detection ---
    def test_source_extensions(self):
        self.assertTrue(_is_source_file("a.py"))
        self.assertTrue(_is_source_file("a.tsx"))
        self.assertFalse(_is_source_file("a.md"))
        self.assertFalse(_is_source_file("a.json"))


def _git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    return d


def _stage(d, files):
    for path, content in files.items():
        full = os.path.join(d, path)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(path) else None
        with open(full, "w") as f:
            f.write(content)
    subprocess.run(["git", "add", "."], cwd=d, check=True)


def _run_hook(cwd, command):
    payload = {"tool_name": "Bash", "cwd": cwd, "tool_input": {"command": command}}
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


class F2GateIntegrationTests(TestCase):
    def tearDown(self):
        # tmpdirs are under /tmp; shutil cleans them
        pass

    def _expect_deny(self, rc, out):
        self.assertEqual(rc, 0)
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        self.assertIn("F2", spec.get("permissionDecisionReason", ""))

    def _expect_allow(self, rc, out):
        self.assertEqual(rc, 0)
        spec = out.get("hookSpecificOutput", {})
        self.assertNotIn("permissionDecision", spec)

    def test_feat_source_plus_test_allows(self):
        d = _git_repo()
        try:
            _stage(d, {"src/foo.ts": "x", "src/foo.test.ts": "y"})
            rc, out = _run_hook(d, 'git commit -m "feat(api): add foo"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_feat_source_only_denies(self):
        d = _git_repo()
        try:
            _stage(d, {"src/foo.ts": "x"})
            rc, out = _run_hook(d, 'git commit -m "feat(api): add foo"')
            self._expect_deny(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_fix_source_only_denies(self):
        d = _git_repo()
        try:
            _stage(d, {"src/foo.py": "x"})
            rc, out = _run_hook(d, 'git commit -m "fix(api): handle null"')
            self._expect_deny(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_docs_source_exempt_by_type(self):
        d = _git_repo()
        try:
            _stage(d, {"src/foo.ts": "x"})
            rc, out = _run_hook(d, 'git commit -m "docs(api): describe foo"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_conductor_bookkeeping_exempt(self):
        d = _git_repo()
        try:
            _stage(d, {"src/foo.ts": "x"})
            rc, out = _run_hook(d, 'git commit -m "chore(conductor): Complete task [abc1234]"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_refactor_source_exempt(self):
        d = _git_repo()
        try:
            _stage(d, {"src/foo.ts": "x"})
            rc, out = _run_hook(d, 'git commit -m "refactor(api): rename"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_feat_only_docs_allows(self):
        d = _git_repo()
        try:
            _stage(d, {"README.md": "x"})
            rc, out = _run_hook(d, 'git commit -m "feat(api): docs only"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_feat_nothing_staged_allows(self):
        # Don't block blindly when there's nothing staged (git unavailable / -a case).
        d = _git_repo()
        try:
            rc, out = _run_hook(d, 'git commit -m "feat(api): empty"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_feat_test_only_allows(self):
        # A RED commit (test only) satisfies F2.
        d = _git_repo()
        try:
            _stage(d, {"tests/test_foo.py": "x"})
            rc, out = _run_hook(d, 'git commit -m "feat(api): red test"')
            self._expect_allow(rc, out)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class F2ShapeCompositionTests(TestCase):
    """Stage 2b: the F2 commit hook composes ``"tdd" in gates_for(shape)`` at the
    moment of denial. A migration-shape track (gates=[checkpoint]) drops the tdd
    gate, so a feat/source-only commit is ALLOWED; a default-shape track DENIES it
    exactly as today (default-identical). Runs the real hook as a subprocess, so
    the lazy ``track_state.workflow_shapes`` import + overlay resolution are
    exercised end-to-end."""

    def tearDown(self):
        os.environ.pop("CLAUDE_PROJECT_DIR", None)

    def _seed_migration_track(self, repo, shape):
        # A conductor track under the repo whose state carries the shape, plus a
        # project overlay registering a migration shape that drops tdd/coverage.
        tracks_dir = os.path.join(repo, "conductor", "tracks", "t1")
        os.makedirs(tracks_dir, exist_ok=True)
        with open(os.path.join(tracks_dir, "track-state.json"), "w") as f:
            json.dump({"track_id": "t1", "workflow_shape": shape,
                       "phases": [{"phase": 1, "tasks": [
                           {"task": 1, "name": "migrate", "status": "in_progress"}]}]},
                      f)
        wf_dir = os.path.join(repo, "conductor", "workflow")
        os.makedirs(wf_dir, exist_ok=True)
        with open(os.path.join(wf_dir, "workflow-shapes.json"), "w") as f:
            json.dump({"shapes": {"migration": {
                "nodes": ["spec-planner", "task-executor", "phase-checker"],
                "verifiers": ["ac-tracer", "test-runner"],
                "gates": ["checkpoint"], "verify_policy": "checkpoint",
                "stop_condition": "all_nodes_done"}}}, f)
        os.environ["CLAUDE_PROJECT_DIR"] = repo  # inherited by the hook subprocess

    def test_migration_shape_allows_feat_without_test(self):
        d = _git_repo()
        try:
            self._seed_migration_track(d, "migration")
            _stage(d, {"src/foo.ts": "x"})
            rc, out = _run_hook(d, 'git commit -m "feat(api): migrate foo"')
            # tdd gate dropped by the migration shape => not denied.
            self._assert_allowed(out)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_default_shape_still_denies_feat_without_test(self):
        # Default-identical: default gates include tdd => feat/source-only DENIED.
        d = _git_repo()
        try:
            self._seed_migration_track(d, "default")
            _stage(d, {"src/foo.ts": "x"})
            rc, out = _run_hook(d, 'git commit -m "feat(api): add foo"')
            spec = out.get("hookSpecificOutput", {})
            self.assertEqual(spec.get("permissionDecision"), "deny")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def _assert_allowed(self, out):
        spec = out.get("hookSpecificOutput", {})
        self.assertNotIn("permissionDecision", spec)


if __name__ == "__main__":
    main()
