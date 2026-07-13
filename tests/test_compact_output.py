"""Contract tests: dispatch-loop commands emit compact envelopes by default.

Locks the filtered-JSON compaction so future re-bloat fails CI. "Compact" is
pure subtraction — the 5 dispatch-loop commands emit only the field set the
orchestrator consumes (skills/implement/SKILL.md) by default; --full
(compact=False) restores the complete envelope.

Assertions name concrete dropped/kept keys per command rather than re-reading
COMPACT_FIELDS, so the test still catches re-bloat if the allowlist dict is
edited to match it.
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save
from scripts.track_state.dispatch import (
    cmd_next, cmd_recover, cmd_dispatch_next,
    cmd_dispatch_prepare, cmd_dispatch_finalize, cmd_post_loop_step,
)


def _out_captured(fn, *args, **kwargs):
    """Capture stdout (must be a single JSON object). Returns parsed dict."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_state(**overrides):
    state = {
        "track_id": "test",
        "type": "feature",
        "status": "in_progress",
        "description": "test track",
        "current_phase_index": 1,
        "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{
            "name": "Phase 1",
            "status": "pending",
            "tasks": [{"name": "Task A", "status": "pending"}],
        }],
    }
    state.update(overrides)
    return state


def _make_track_dir(state):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    save(d, state)
    return d


def _make_git_track_dir():
    """git repo + track-state.json (Task A in_progress) + plan.md."""
    d = tempfile.mkdtemp()
    for args in (["git", "init", d],
                 ["git", "-C", d, "config", "user.email", "t@t.com"],
                 ["git", "-C", d, "config", "user.name", "T"]):
        subprocess.run(args, capture_output=True, check=True)
    Path(d, "README.md").write_text("# t")
    subprocess.run(["git", "-C", d, "add", "README.md"], capture_output=True, check=True)
    subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, check=True)
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    state = _make_state()
    state["phases"][0]["tasks"][0]["status"] = "in_progress"
    save(d, state)
    return d


def _write_success_result(d, commit_sha=""):
    cond = Path(d, ".conductor")
    cond.mkdir(exist_ok=True)
    (cond / "result.json").write_text(json.dumps({
        "status": "SUCCESS",
        "commit_sha": commit_sha,
        "summary": "Done",
        "phase": 1,
        "task": 1,
        "subtask": None,
        "task_name": "Task A",
    }))


def _make_git_parent_stuck_dir():
    """git repo + a parent task whose subtasks are all terminal with ≥1 failed
    (and no other dispatchable work) so dispatch-next returns ``parent-stuck``."""
    d = tempfile.mkdtemp()
    for args in (["git", "init", d],
                 ["git", "-C", d, "config", "user.email", "t@t.com"],
                 ["git", "-C", d, "config", "user.name", "T"]):
        subprocess.run(args, capture_output=True, check=True)
    Path(d, "README.md").write_text("# t")
    subprocess.run(["git", "-C", d, "add", "README.md"], capture_output=True, check=True)
    subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, check=True)
    Path(d, "plan.md").write_text(
        "# Plan\n\n## Phase 1: Build\n- [ ] Build feature\n  - [x] S1\n  - [!] S2\n")
    state = {
        "track_id": "test",
        "type": "feature",
        "status": "in_progress",
        "description": "test track",
        "current_phase_index": 1,
        "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{
            "name": "Phase 1",
            "status": "pending",
            "tasks": [{
                "name": "Build feature",
                "status": "in_progress",
                "subtasks": [
                    {"name": "S1", "status": "completed", "commit_sha": "abc1234"},
                    {"name": "S2", "status": "failed"},
                ],
            }],
        }],
    }
    save(d, state)
    return d


class TestCompactDefault(TestCase):
    """Default output (compact=True) emits only the consumed field set."""

    def setUp(self):
        self.d = _make_track_dir(_make_state())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_next_drops_type_and_tags(self):
        result = _out_captured(cmd_next, self.d)
        self.assertEqual(result["phase"], 1)
        self.assertEqual(result["name"], "Task A")
        self.assertIn("execution_mode", result)
        self.assertNotIn("type", result)
        self.assertNotIn("tags", result)

    def test_recover_drops_type_tags_last_failure_summary(self):
        state = load(self.d)
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        save(self.d, state)
        result = _out_captured(cmd_recover, self.d)
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["name"], "Task A")
        self.assertIn("retry_count", result)
        self.assertIn("max_retries", result)
        self.assertIn("execution_mode", result)
        self.assertNotIn("type", result)
        self.assertNotIn("tags", result)
        self.assertNotIn("last_failure_summary", result)

    def test_dispatch_next_drops_type_tags(self):
        result = _out_captured(cmd_dispatch_next, self.d)
        self.assertEqual(result["action"], "dispatch_executor")
        self.assertIn("execution_mode", result)
        self.assertNotIn("type", result)
        self.assertNotIn("tags", result)

    def test_dispatch_prepare_drops_next_echo_and_sync_count(self):
        result = _out_captured(cmd_dispatch_prepare, self.d)
        self.assertEqual(result["action"], "execute")
        # The "Start task" commit is now made internally by dispatch-prepare
        # (mirroring dispatch-finalize), so commit_msg no longer ships in the
        # envelope — the orchestrator never constructs a `git commit` for it.
        self.assertNotIn("commit_msg", result)
        self.assertIn("retry_count", result)
        self.assertIn("max_retries", result)
        # The three biggest wins: the redundant next echo, sync_count, and the
        # free-text last_failure_summary. `tags` is also dropped: it is consumed
        # only server-side by _classify_task and never emitted onward to the
        # orchestrator (the executor prompt carries no TAGS= field).
        self.assertNotIn("next", result)
        self.assertNotIn("sync_count", result)
        self.assertNotIn("last_failure_summary", result)
        self.assertNotIn("tags", result)


class TestFullRestoresEnvelope(TestCase):
    """compact=False (the --full flag) restores the complete envelope."""

    def setUp(self):
        self.d = _make_track_dir(_make_state())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_next_full_keeps_type_and_tags(self):
        result = _out_captured(cmd_next, self.d, compact=False)
        self.assertIn("type", result)
        self.assertIn("tags", result)

    def test_dispatch_prepare_full_keeps_next_and_sync_count(self):
        result = _out_captured(cmd_dispatch_prepare, self.d, compact=False)
        self.assertIn("next", result)
        self.assertIn("sync_count", result)
        self.assertIn("last_failure_summary", result)


class TestDispatchFinalizeCompact(TestCase):
    """dispatch-finalize SUCCESS: compact drops parent_completed + sync_count."""

    def test_success_compact_drops_parent_completed_and_sync_count(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["status"], "success")
        self.assertIn("sha", result)
        self.assertIn("committed", result)
        self.assertIn("deviations", result)
        self.assertNotIn("parent_completed", result)
        self.assertNotIn("sync_count", result)

    def test_success_full_keeps_parent_completed_and_sync_count(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d)
        result = _out_captured(cmd_dispatch_finalize, d, compact=False)
        self.assertIn("parent_completed", result)
        self.assertIn("sync_count", result)

    def test_success_compact_carries_indices(self):
        """Gap #8: the finalize SUCCESS envelope carries phase/task/subtask so the
        orchestrator can place the result without re-reading state. The indices are
        in the dispatch-finalize allowlist, so they survive compaction."""
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["phase"], 1)
        self.assertEqual(result["task"], 1)
        self.assertIn("subtask", result)
        # Indices are ints, matching the next/dispatch-prepare/dispatch-next
        # envelopes (contract alignment — not the string form p/t carry internally).
        self.assertIsInstance(result["phase"], int)
        self.assertIsInstance(result["task"], int)

    def test_success_compact_carries_code_sha(self):
        """dispatch-finalize exposes ``code_sha`` (the agent's code commit) alongside
        ``sha`` (the conductor chore commit, = final_sha). The §3.6b code-reviewer
        and §3.6c refactorer seams bind ``REVISION_RANGE={code_sha}~1..{code_sha}``
        from this field — binding ``{sha}`` instead made both no-ops (the chore
        diff is state files, not code). ``code_sha`` is in the dispatch-finalize
        allowlist, so it survives compaction."""
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, commit_sha="0123456789abcdef0123456789abcdef01234567")
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["status"], "success")
        self.assertIn("code_sha", result)               # survives compaction
        self.assertEqual(result["code_sha"], "0123456")  # 7-char normalized
        # `sha` is the conductor chore commit; `code_sha` is its parent (the
        # agent's code). They differ — the split is the whole point.
        self.assertIn("sha", result)
        self.assertNotEqual(result["sha"], result["code_sha"])


class TestParentStuckCompact(TestCase):
    """Gap #8: the parent_stuck emit no longer carries ``failed=True`` — it was
    redundant with ``action="parent_stuck"`` and silently stripped by compact
    anyway (not in the dispatch-next allowlist)."""

    def test_parent_stuck_drops_failed_in_compact_and_full(self):
        d = _make_git_parent_stuck_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        compact_result = _out_captured(cmd_dispatch_next, d)
        self.assertEqual(compact_result["action"], "parent_stuck")
        self.assertNotIn("failed", compact_result)
        # Useful fields still present.
        self.assertIn("phase", compact_result)
        self.assertIn("task", compact_result)
        self.assertIn("name", compact_result)

        # And the redundant field is gone from the full envelope too.
        d2 = _make_git_parent_stuck_dir()
        self.addCleanup(shutil.rmtree, d2, ignore_errors=True)
        full_result = _out_captured(cmd_dispatch_next, d2, compact=False)
        self.assertEqual(full_result["action"], "parent_stuck")
        self.assertNotIn("failed", full_result)


class TestPostLoopStepCompact(TestCase):
    """``post-loop-step`` emits a compact leaf: only the allowlisted keys survive
    (the spine's internal gate signals — sidecar, deferred list, finalize result —
    must NOT reach the orchestrator)."""

    def _finalized_dir(self):
        # Finalized (status completed + score), not doc-synced → dispatch corpus-writer.
        state = {
            "track_id": "plsc", "type": "feature", "status": "completed",
            "description": "compact test", "quality_score": 90,
            "current_phase_index": 0, "current_task_index": 0,
            "updated_at": _recent_iso(),
            "phases": [{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed", "commit_sha": "aaa0001"},
            ]}],
        }
        d = tempfile.mkdtemp()
        Path(d, "plan.md").write_text("# Plan\n\n## Phase 1\n- [x] Task A\n")
        save(d, state)
        env = {**__import__("os").environ,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "-C", d, "init", "-q"], check=True, capture_output=True)
        subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"],
                       check=True, capture_output=True, env=env)
        return d

    def test_dispatch_leaf_compact(self):
        d = self._finalized_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        result = _out_captured(cmd_post_loop_step, d)
        self.assertEqual(result["action"], "dispatch")
        self.assertEqual(result["agent"], "corpus-writer")
        # Allowlisted keys present.
        self.assertIn("prompt", result)
        self.assertIn("track_dir", result)
        # Internal gate signals the spine computed but the orchestrator does not
        # need — stripped by the compact allowlist.
        self.assertNotIn("sidecar", result)
        self.assertNotIn("deferred", result)
        self.assertNotIn("finalized", result)
        self.assertNotIn("doc_synced", result)

    def test_advisory_leaf_post_on_survives_compact(self):
        # The §6.0 advisory leaf carries post_on="always" — the teleoperator's
        # `post` rule reads it, so it must survive compaction (be in the allowlist).
        # Drive past Phase 1+2 doc-sync (both commits) to land on the advisory gate.
        import os
        env = {**os.environ,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        d = self._finalized_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        tid = os.path.basename(d)
        for msg in (f"docs(conductor): Synchronize docs for track [{tid}]",
                    f"docs(conductor): Wiki sync for track [{tid}]"):
            subprocess.run(["git", "-C", d, "commit", "-q", "--allow-empty",
                            "-m", msg], check=True, capture_output=True, env=env)
        result = _out_captured(cmd_post_loop_step, d)
        self.assertEqual(result["action"], "dispatch_advisory")
        self.assertEqual(result["agent"], "wiki-differ")
        self.assertEqual(result["post_on"], "always")
        self.assertIn("post", result)



class TestCliFullFlag(TestCase):
    """--full replaced --compact as the bool flag controlling compaction."""

    def test_full_is_bool_flag_compact_is_gone(self):
        from scripts.track_state.cli import _BOOL_FLAGS
        self.assertIn("--full", _BOOL_FLAGS)
        self.assertNotIn("--compact", _BOOL_FLAGS)


class TestErrorEnvelopeBypassesAllowlist(TestCase):
    """Error envelopes must survive compaction — diagnostics never strip to {}.

    The orchestrator HALTs on `error`/`status:"error"`, so stripping such an
    envelope to an empty object would silently swallow the diagnostic. Both
    error shapes bypass the compact allowlist and emit the full object.
    """

    def test_error_key_bypasses_compact(self):
        from scripts.track_state.helpers import emit
        result = _out_captured(
            emit, {"error": "boom", "phase": 1, "extra": "x"}, "dispatch-prepare")
        self.assertEqual(result["error"], "boom")
        self.assertEqual(result["extra"], "x")

    def test_status_error_bypasses_compact(self):
        from scripts.track_state.helpers import emit
        result = _out_captured(
            emit, {"status": "error", "message": "nope", "detail": "d"}, "next")
        self.assertEqual(result["status"], "error")
        self.assertIn("message", result)
        self.assertIn("detail", result)


if __name__ == "__main__":
    main()
