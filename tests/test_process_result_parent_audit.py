"""Tests for cmd_process_result parent-completion audit trail (Tier-2 #5).

When the last subtask's SUCCESS auto-completes its parent, process-result must
give the parent the same audit trail dispatch-next's parent-complete path gets:
a conductor commit + git note + evidence. Previously the parent's status/SHA
were set in state but left no commit/note on this legacy CLI path.
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save
from scripts.track_state.result import cmd_process_result
from test_track_state import _out_captured


def _git_track():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", d], capture_output=True, check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t.t"], capture_output=True, check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], capture_output=True, check=True)
    Path(d, "README.md").write_text("# test")
    subprocess.run(["git", "-C", d, "add", "README.md"], capture_output=True, check=True)
    subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, check=True)
    return d


def _parent_state():
    """Phase 1 → parent 'Build' with 2 subtasks; sub1 completed, sub2 in_progress."""
    return {
        "track_id": "t1",
        "execution_mode": "continuous",
        "current_phase_index": 1,
        "current_task_index": 1,
        "current_subtask_index": 2,
        "phases": [{
            "name": "Phase 1",
            "status": "in_progress",
            "tasks": [{
                "name": "Build feature",
                "status": "in_progress",
                "commit_sha": "",
                "subtasks": [
                    {"name": "sub one", "status": "completed", "commit_sha": "111aaaa",
                     "retry_count": 0, "coverage_pct": 90},
                    {"name": "sub two", "status": "in_progress", "commit_sha": "",
                     "retry_count": 0},
                ],
            }],
        }],
    }


class ProcessResultParentAuditTests(TestCase):
    def test_last_subtask_completes_parent_with_commit_and_note(self):
        d = _git_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # plan.md with a parent + 2 subtasks
        Path(d, "plan.md").write_text(
            "# Plan\n\n## Phase 1: Build\n"
            "- [~] Build feature\n"
            "  - [x] sub one [111aaaa]\n"
            "  - [~] sub two\n"
        )
        save(d, _parent_state())

        # result.json for sub two (SUCCESS) — this auto-completes the parent
        cond = Path(d, ".conductor")
        cond.mkdir(exist_ok=True)
        (cond / "result.json").write_text(json.dumps({
            "status": "SUCCESS",
            "commit_sha": "222bbbb",
            "summary": "done sub two",
            "phase": 1,
            "task": 1,
            "subtask": 2,
            "task_name": "sub two",
            "coverage_pct": 88,
        }))

        out, _ = _out_captured(cmd_process_result, d)
        self.assertEqual(out.get("status"), "success")
        self.assertTrue(out.get("parent_completed"))

        # Parent is completed in state with a SHA.
        state = load(d)
        parent = state["phases"][0]["tasks"][0]
        self.assertEqual(parent["status"], "completed")
        parent_sha = parent.get("commit_sha", "")
        self.assertTrue(parent_sha, "parent should have a commit_sha after completion")
        self.assertIn("evidence", parent, "parent should get minimal evidence")

        # A conductor commit for the parent completion exists in git history.
        log = subprocess.run(
            ["git", "-C", d, "log", "--format=%s"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("chore(conductor): Complete parent 'Build feature'", log)

        # A git note is attached to the parent's SHA (audit trail).
        note = subprocess.run(
            ["git", "-C", d, "notes", "show", parent_sha],
            capture_output=True, text=True,
        )
        self.assertEqual(note.returncode, 0, "git note should exist for parent SHA")
        self.assertIn("Conductor", note.stdout)

    def test_non_final_subtask_does_not_create_parent_commit(self):
        """Completing sub1 (sub2 still pending) must not trigger parent completion."""
        d = _git_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "plan.md").write_text(
            "# Plan\n\n## Phase 1: Build\n"
            "- [~] Build feature\n"
            "  - [~] sub one\n"
            "  - [ ] sub two\n"
        )
        st = _parent_state()
        st["phases"][0]["tasks"][0]["subtasks"][0]["status"] = "in_progress"
        st["phases"][0]["tasks"][0]["subtasks"][0]["commit_sha"] = ""
        st["phases"][0]["tasks"][0]["subtasks"][1]["status"] = "pending"
        st["current_subtask_index"] = 1
        save(d, st)

        cond = Path(d, ".conductor")
        cond.mkdir(exist_ok=True)
        (cond / "result.json").write_text(json.dumps({
            "status": "SUCCESS",
            "commit_sha": "111aaaa",
            "summary": "done sub one",
            "phase": 1, "task": 1, "subtask": 1, "task_name": "sub one",
            "coverage_pct": 90,
        }))

        out, _ = _out_captured(cmd_process_result, d)
        self.assertEqual(out.get("status"), "success")
        self.assertFalse(out.get("parent_completed"))

        # No parent-completion conductor commit should have been created.
        log = subprocess.run(
            ["git", "-C", d, "log", "--format=%s"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertNotIn("Complete parent", log)


if __name__ == "__main__":
    main()
