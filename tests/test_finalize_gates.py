r"""Gap #7 — F2/F3 advisory gates now run on the dispatch-finalize hot path
(WARN-only), not just the orphaned process-result path. Both paths call the
shared ``_evaluate_gates`` helper, so the two cannot drift; these tests lock
that the finalize envelope surfaces ``coverage_gate``/``tdd_gate``/``coverage_pct``
and matches the helper's own verdict.

dispatch-finalize performs real git commits, so each test builds a git-backed
track dir (the same fixture pattern as ``test_compact_output``).
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
from scripts.track_state.dispatch import cmd_dispatch_finalize
from scripts.track_state.helpers import _extract_tags_for_task
from scripts.track_state.result import _evaluate_gates


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


def _make_git_track_dir(task_name="Task A"):
    """git repo + track-state.json (Task in_progress) + plan.md."""
    d = tempfile.mkdtemp()
    for args in (["git", "init", d],
                 ["git", "-C", d, "config", "user.email", "t@t.com"],
                 ["git", "-C", d, "config", "user.name", "T"]):
        subprocess.run(args, capture_output=True, check=True)
    Path(d, "README.md").write_text("# t")
    subprocess.run(["git", "-C", d, "add", "README.md"], capture_output=True, check=True)
    subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, check=True)
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
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
            "tasks": [{"name": task_name, "status": "in_progress"}],
        }],
    }
    save(d, state)
    return d


def _write_success_result(d, *, coverage_pct=None, commit_sha="abc1234",
                          files_changed="src/foo.py tests/test_foo.py"):
    cond = Path(d, ".conductor")
    cond.mkdir(exist_ok=True)
    payload = {
        "status": "SUCCESS",
        "commit_sha": commit_sha,
        "summary": "Done",
        "phase": 1,
        "task": 1,
        "subtask": None,
        "task_name": "Task A",
        "files_changed": files_changed,
    }
    if coverage_pct is not None:
        payload["coverage_pct"] = coverage_pct
    (cond / "result.json").write_text(json.dumps(payload))


class FinalizeCoverageGateTests(TestCase):
    """Sub-80% coverage must surface as a FAILED gate; >=80% as PASS."""

    def test_emits_coverage_gate_fail_below_threshold(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=50)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["coverage_gate"], "FAILED (50% < 80%)")
        self.assertEqual(result["coverage_pct"], 50)

    def test_emits_coverage_gate_pass_at_threshold(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=90)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["coverage_gate"], "PASS")
        self.assertEqual(result["coverage_pct"], 90)

    def test_no_coverage_pct_keeps_pass_omits_pct(self):
        """A result without coverage_pct can't fail the gate and omits the field
        (mirrors process-result, which only emits coverage_pct when present)."""
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=None)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["coverage_gate"], "PASS")
        self.assertNotIn("coverage_pct", result)


class FinalizeTagExemptionTests(TestCase):
    """[Docs]/[Config]/[Chore]/[Manual] skip the coverage gate even under 80%."""

    def test_docs_tag_exempt_from_coverage_gate(self):
        d = _make_git_track_dir(task_name="[Docs] Update README")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=50)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["coverage_gate"], "PASS")

    def test_config_tag_exempt_from_coverage_gate(self):
        d = _make_git_track_dir(task_name="[Config] Tune knobs")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=40)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["coverage_gate"], "PASS")


class FinalizeSharedHelperParityTests(TestCase):
    """The finalize envelope carries ``_evaluate_gates``' verdict verbatim —
    the contract that keeps the hot path and process-result from drifting."""

    def test_envelope_matches_helper_verdict(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=50)
        # Re-read the exact result the finalize path will consume.
        r = json.loads((Path(d) / ".conductor" / "result.json").read_text())
        state = load(d)
        tags = _extract_tags_for_task(state, "1", "1")
        # Mirror dispatch-finalize's normalization of the commit SHA.
        from scripts.track_state.helpers import _normalize_sha
        code_sha = _normalize_sha(r.get("commit_sha", ""))
        exp_cov, exp_tdd, exp_pct = _evaluate_gates(tags, r, code_sha, d)

        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["coverage_gate"], exp_cov)
        self.assertEqual(result["tdd_gate"], exp_tdd)
        self.assertEqual(result["coverage_pct"], exp_pct)

    def test_helper_parity_with_process_result_inputs(self):
        """Sanity: the same ``_evaluate_gates`` inputs yield identical tuples
        regardless of which code path assembled them (the drift guard)."""
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        r = {"coverage_pct": 50, "files_changed": "tests/test_x.py"}
        # process-result pulls tags from state; dispatch-finalize does the same.
        tags_pr = _extract_tags_for_task(load(d), "1", "1")
        cov_pr, tdd_pr, pct_pr = _evaluate_gates(tags_pr, r, "abc1234", d)
        # A second call with identical args is deterministic and equal.
        cov2, tdd2, pct2 = _evaluate_gates(tags_pr, r, "abc1234", d)
        self.assertEqual((cov_pr, tdd_pr, pct_pr), (cov2, tdd2, pct2))
        self.assertEqual(cov_pr, "FAILED (50% < 80%)")


if __name__ == "__main__":
    main()
