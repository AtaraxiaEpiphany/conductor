"""Tests for scripts/on-batch-complete.py — PostToolBatch enforcement hook.

Covers the batch-analysis drift detector and the two server-side backstops
(F3 coverage gate, V6 phase checkpoint) that are the last line of defense
against agent self-report bypass. The hook filename is hyphenated so the module
is loaded by path via importlib; the main() flow is exercised end-to-end via
subprocess (faithful to the real stdin -> JSON -> exit contract).
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

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "scripts" / "on-batch-complete.py"

# Production hooks rely on Python auto-adding the script's directory to
# sys.path[0]; importlib loading does not, so add scripts/ explicitly.
_SCRIPTS_DIR = str(REPO / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _load_hook():
    """Load the hyphenated hook module by path (not a valid package name)."""
    spec = importlib.util.spec_from_file_location("obc_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_hook(tool_calls, cwd, data_dir=None):
    """Run the hook as a subprocess; return parsed stdout JSON."""
    payload = json.dumps({
        "hook_event_name": "PostToolBatch",
        "session_id": "test-sess",
        "cwd": str(cwd),
        "tool_calls": tool_calls,
    })
    env = dict(os.environ)
    if data_dir is not None:
        env["CLAUDE_PLUGIN_DATA"] = str(data_dir)
    proc = subprocess.run(
        ["python3", str(HOOK)], input=payload, capture_output=True,
        text=True, env=env, cwd=str(REPO),
    )
    assert proc.returncode == 0, f"hook failed: {proc.stderr}"
    return json.loads(proc.stdout)


def _bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


# --------------------------------------------------------------------------- #
# Batch analysis / drift detection
# --------------------------------------------------------------------------- #
class TestAnalyze(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook()

    def test_classifies_git_and_track_state_and_agent(self):
        calls = [
            _bash("git commit -m 'a'"),
            _bash("track-state complete t 1 1"),
            {"tool_name": "Agent", "tool_input": {"description": "impl"}},
            _bash("ls -la"),
        ]
        a = self.mod.analyze_tool_calls(calls)
        self.assertEqual(len(a["git_ops"]), 1)
        self.assertEqual(len(a["track_state_ops"]), 1)
        self.assertEqual(a["agent_calls"], ["impl"])
        self.assertEqual(a["total_tools"], 4)

    def test_issue_multiple_commits_without_state_update(self):
        calls = [_bash("git commit -m a"), _bash("git commit -m b")]
        a = self.mod.analyze_tool_calls(calls)
        self.assertIn("multiple_git_commits_without_state_update", a["issues"])

    def test_no_issue_when_track_state_present(self):
        calls = [_bash("git commit -m a"), _bash("git commit -m b"),
                 _bash("track-state sync t")]
        a = self.mod.analyze_tool_calls(calls)
        self.assertNotIn("multiple_git_commits_without_state_update", a["issues"])

    def test_issue_git_ops_during_subagent(self):
        calls = [_bash("git commit -m a"),
                 {"tool_name": "Agent", "tool_input": {"description": "x"}}]
        a = self.mod.analyze_tool_calls(calls)
        self.assertIn("git_ops_during_subagent", a["issues"])

    def test_empty_batch_no_issues(self):
        a = self.mod.analyze_tool_calls([])
        self.assertEqual(a["issues"], [])


class TestContextMessage(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook()

    def test_multiple_commits_message(self):
        msg = self.mod.get_context_message(["multiple_git_commits_without_state_update"])
        self.assertIsNotNone(msg)
        self.assertIn("Multiple git commits", msg)

    def test_git_during_subagent_message(self):
        msg = self.mod.get_context_message(["git_ops_during_subagent"])
        self.assertIsNotNone(msg)
        self.assertIn("active subagent", msg)

    def test_no_issues_returns_none(self):
        self.assertIsNone(self.mod.get_context_message([]))


# --------------------------------------------------------------------------- #
# F3 coverage gate trigger + backstop
# --------------------------------------------------------------------------- #
class TestCoverageGate(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook()

    def test_should_verify_on_conductor_commit(self):
        calls = [_bash('git commit -m "chore(conductor): sync state"')]
        self.assertTrue(self.mod.should_verify_coverage(calls))

    def test_should_verify_on_staged_state_files(self):
        calls = [_bash('git commit -m "x" track-state.json plan.md')]
        self.assertTrue(self.mod.should_verify_coverage(calls))

    def test_should_NOT_verify_on_plain_commit(self):
        # The bypass surface: a non-conductor commit message skips the gate.
        calls = [_bash('git commit -m "feat: add thing"')]
        self.assertFalse(self.mod.should_verify_coverage(calls))

    def test_should_NOT_verify_non_commit(self):
        self.assertFalse(self.mod.should_verify_coverage([_bash("git status")]))
        self.assertFalse(self.mod.should_verify_coverage([]))

    def test_verify_gate_below_threshold_warns(self):
        self.mod.get_coverage_percent = lambda cwd: 40.0
        msg = self.mod.verify_coverage_gate(Path("/tmp"))
        self.assertIsNotNone(msg)
        self.assertIn("40.0% < 80%", msg)

    def test_verify_gate_at_threshold_passes(self):
        self.mod.get_coverage_percent = lambda cwd: 80.0
        self.assertIsNone(self.mod.verify_coverage_gate(Path("/tmp")))

    def test_verify_gate_unavailable_passes(self):
        self.mod.get_coverage_percent = lambda cwd: None
        self.assertIsNone(self.mod.verify_coverage_gate(Path("/tmp")))

    def test_detect_project_type(self):
        def _fresh(marker, content=""):
            d = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, d)
            Path(d, marker).write_text(content)
            return d

        empty = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, empty)
        self.assertIsNone(self.mod.detect_project_type(Path(empty)))
        self.assertEqual(self.mod.detect_project_type(Path(_fresh("pyproject.toml"))), "python")
        self.assertEqual(self.mod.detect_project_type(Path(_fresh("package.json", "{}"))), "node")
        self.assertEqual(self.mod.detect_project_type(Path(_fresh("go.mod", "module x"))), "go")

    def test_get_coverage_percent_none_when_no_project(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        self.assertIsNone(self.mod.get_coverage_percent(Path(d)))


# --------------------------------------------------------------------------- #
# V6 phase checkpoint backstop
# --------------------------------------------------------------------------- #
class TestPhaseCheckpoint(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook()

    def test_should_verify_checkpoint_on_complete_or_skip(self):
        self.assertTrue(self.mod.should_verify_checkpoint([_bash("track-state complete t 1 1")]))
        self.assertTrue(self.mod.should_verify_checkpoint([_bash("track-state skip t 1 1")]))

    def test_should_NOT_verify_checkpoint_on_sync(self):
        self.assertFalse(self.mod.should_verify_checkpoint([_bash("track-state sync t")]))
        self.assertFalse(self.mod.should_verify_checkpoint([_bash("git status")]))

    def _make_git_repo_with_track(self, phase_terminal=True, checkpoint_commit=None):
        """Temp git repo + conductor/tracks.md + one track with a terminal phase."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        for args in (["git", "init", d],
                     ["git", "-C", d, "config", "user.email", "t@t.com"],
                     ["git", "-C", d, "config", "user.name", "T"]):
            subprocess.run(args, capture_output=True, check=True)
        Path(d, "README.md").write_text("# t")
        subprocess.run(["git", "-C", d, "add", "README.md"], capture_output=True, check=True)
        subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, check=True)

        if checkpoint_commit:
            Path(d, ".marker").write_text("")
            subprocess.run(["git", "-C", d, "add", ".marker"], capture_output=True, check=True)
            subprocess.run(["git", "-C", d, "commit", "-m", checkpoint_commit],
                            capture_output=True, check=True)

        cond = Path(d, "conductor"); cond.mkdir()
        (cond / "tracks.md").write_text("# Tracks\n- [t](track-t/)\n")
        track = Path(d, "track-t"); track.mkdir()
        status = "completed" if phase_terminal else "in_progress"
        state = {
            "track_id": "t", "status": "in_progress",
            "phases": [{"name": "Phase 1", "status": "completed",
                        "tasks": [{"name": "Task 1", "status": status,
                                   "commit_sha": "abc1234"}]}],
        }
        (track / "track-state.json").write_text(json.dumps(state))
        return Path(d)

    def test_missing_checkpoint_warns(self):
        d = self._make_git_repo_with_track(phase_terminal=True, checkpoint_commit=None)
        msg = self.mod.verify_phase_checkpoint(d)
        self.assertIsNotNone(msg)
        self.assertIn("Phase checkpoint missing", msg)

    def test_present_checkpoint_passes(self):
        d = self._make_git_repo_with_track(
            phase_terminal=True, checkpoint_commit="conductor(checkpoint): phase 1 P1")
        self.assertIsNone(self.mod.verify_phase_checkpoint(d))

    def test_no_tracks_registry_returns_none(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        self.assertIsNone(self.mod.verify_phase_checkpoint(Path(d)))


# --------------------------------------------------------------------------- #
# main() end-to-end via subprocess
# --------------------------------------------------------------------------- #
class TestMain(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data)

    def test_main_emits_drift_warning_for_concurrent_commits(self):
        out = _run_hook(
            [_bash("git commit -m a"), _bash("git commit -m b")],
            cwd=self.d, data_dir=self.data)
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("Multiple git commits", ctx)

    def test_main_clean_batch_no_context(self):
        out = _run_hook([], cwd=self.d, data_dir=self.data)
        # No additionalContext injected on a clean batch.
        self.assertNotIn("additionalContext",
                         out.get("hookSpecificOutput", {}))

    def test_main_coverage_trigger_no_tool_emits_nothing(self):
        # Conductor commit triggers the gate, but no coverage tool in empty
        # cwd -> get_coverage_percent None -> no warning.
        out = _run_hook([_bash('git commit -m "chore(conductor): x"')],
                        cwd=self.d, data_dir=self.data)
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertNotIn("Coverage Gate", ctx)


if __name__ == "__main__":
    main()
