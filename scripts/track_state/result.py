"""Process task results, enforce quality gates."""
import json
import os
import sys
from pathlib import Path

from .core import load, save
from .helpers import (
    out, conductor_dir, _store_evidence, _extract_tags_for_task,
    _tag_exempt_from_coverage, _tag_exempt_from_tdd, flag, _last_subtask_sha,
)
from .mutations import _do_complete, _do_fail
from .sync import _do_sync_plan
from .git_ops import _write_git_note, _git_commit_ensured, _git_head_sha, _ensure_note
from .handoff import (
    _append_execution_record, _append_deviation_legacy,
    _append_failure_legacy,
)


def _verify_tdd_gate(track_dir, sha, result_data):
    """Best-effort TDD verification: check that test files exist in the commit."""
    if not sha or sha == "N/A":
        return "UNKNOWN"

    # Check files_changed in result for test file patterns
    files = result_data.get("files_changed", "")
    if not files:
        return "UNKNOWN"

    test_patterns = ("test/", "tests/", "spec/", "_test.", "_spec.", ".test.", ".spec.", "Test", "Spec")
    has_test = any(p in files for p in test_patterns)

    return "PASS" if has_test else "NO_TESTS_FOUND"


def cmd_write_result(track_dir):
    """Atomically write result.json from stdin or --data flag.

    Usage: track-state write-result <track-dir> [--data '<json>']

    Reads JSON from --data flag, or from stdin if --data is not provided.
    Writes to .conductor/result.json using atomic replace (temp file + os.replace).
    """
    cdir = conductor_dir(track_dir)
    result_path = cdir / "result.json"

    raw = flag(sys.argv[3:], "--data")
    if raw:
        data = raw
    else:
        data = sys.stdin.read()

    try:
        result = json.loads(data)
    except json.JSONDecodeError as e:
        out(dict(error=f"Invalid JSON: {e}"))
        sys.exit(1)

    # Validate required fields
    status = result.get("status", "").upper()
    if status not in ("SUCCESS", "FAILURE"):
        out(dict(error=f"Missing or invalid 'status': must be SUCCESS or FAILURE"))
        sys.exit(1)

    # Atomic write
    import tempfile
    tmp = tempfile.NamedTemporaryFile(
        mode="w", dir=str(cdir), prefix=".result.tmp.", delete=False
    )
    try:
        json.dump(result, tmp, indent=2, ensure_ascii=False)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
    finally:
        tmp.close()

    os.replace(tmp_name, str(result_path))
    out(dict(ok=True, path=str(result_path)))


def cmd_process_result(track_dir):
    """Read .conductor/result.json, update state, sync plan, manage handoff.
    Writes git notes audit trail. Enforces F2/F3 quality gates.
    Deletes result file after processing."""
    result_path = conductor_dir(track_dir) / "result.json"

    if not result_path.exists():
        out(dict(error="No result file at .conductor/result.json"))
        return

    with open(result_path) as f:
        r = json.load(f)

    status = r.get("status", "").upper()
    p = str(r.get("phase", ""))
    t = str(r.get("task", ""))
    s = r.get("subtask")
    if s is not None:
        s = str(s)
    task_name = r.get("task_name", "unknown")

    # Load state once for use in handoff functions
    state = load(track_dir)

    # Get task tags for gate exemption check
    tags = _extract_tags_for_task(state, p, t)

    if status == "SUCCESS":
        sha = r.get("commit_sha", "")

        # F3 Coverage Gate — warn if below threshold (not enforced for [Docs]/[Config]/[Chore])
        cov_pct = r.get("coverage_pct")
        coverage_gate = "PASS"
        if cov_pct is not None and not _tag_exempt_from_coverage(tags):
            if cov_pct < 80:
                coverage_gate = f"FAILED ({cov_pct}% < 80%)"

        # F2 TDD Gate — verify test files present in commit (best-effort)
        tdd_gate = "PASS"
        if not _tag_exempt_from_tdd(tags):
            tdd_gate = _verify_tdd_gate(track_dir, sha, r)

        try:
            parent_completed = _do_complete(track_dir, p, t, s, sha)
        except (ValueError, IndexError) as e:
            out(dict(error=str(e), status="error"))
            return
        # Reload state after _do_complete modified the file
        state = load(track_dir)

        _store_evidence(state, track_dir, p, t, s, r)

        _do_sync_plan(track_dir, state)

        # Write to handoff
        _append_execution_record(track_dir, p, t, s, r, state)

        # Handle spec deviations (legacy: also to issues.md for compatibility)
        deviations = r.get("spec_deviation_detail", [])
        for dev in deviations:
            _append_deviation_legacy(track_dir, task_name, dev)

        # Write git notes audit trail
        _write_git_note(track_dir, r, state)

        # If completing this subtask auto-completed its parent, give the parent
        # the same audit trail dispatch-next's parent-complete path gets
        # (conductor commit + git note + evidence). Without this, the parent's
        # completion left no commit/note on this legacy CLI path. Mirrors
        # dispatch.py cmd_dispatch_next's parent-complete handling.
        if parent_completed:
            try:
                state = load(track_dir)
                parent = state["phases"][int(p) - 1]["tasks"][int(t) - 1]
                parent_name = parent.get("name", "unknown")
                parent_sha = _last_subtask_sha(parent) or sha
                _git_commit_ensured(
                    track_dir,
                    f"chore(conductor): Complete parent '{parent_name}' [{parent_sha}]",
                )
                final_sha = _git_head_sha(track_dir) or parent_sha
                if final_sha != parent_sha:
                    state = load(track_dir)
                    state["phases"][int(p) - 1]["tasks"][int(t) - 1]["commit_sha"] = final_sha
                    save(track_dir, state)
                    _do_sync_plan(track_dir, state)
                state = load(track_dir)
                parent_tgt = state["phases"][int(p) - 1]["tasks"][int(t) - 1]
                _ensure_note(track_dir, state, int(p), int(t), None, parent_tgt)
                if "evidence" not in parent_tgt:
                    parent_tgt["evidence"] = {
                        "coverage_pct": None, "tc_coverage": "", "deviations": 0,
                    }
                    save(track_dir, state)
            except (ValueError, IndexError, KeyError):
                # Best-effort: the subtask itself already completed + committed.
                pass

        # Clean up
        result_path.unlink(missing_ok=True)

        result = dict(
            status="success",
            sha=sha,
            parent_completed=parent_completed,
            deviations=len(deviations),
            coverage_gate=coverage_gate,
            tdd_gate=tdd_gate,
        )
        if cov_pct is not None:
            result["coverage_pct"] = cov_pct
        out(result)

    elif status == "FAILURE":
        summary = r.get("summary", "")
        retry_count = _do_fail(track_dir, p, t, s, summary)
        # Reload state after _do_fail modified the file
        state = load(track_dir)
        _do_sync_plan(track_dir, state)

        # Write to handoff
        _append_execution_record(track_dir, p, t, s, r, state)

        # Legacy: also to issues.md for backward compatibility
        _append_failure_legacy(track_dir, r)

        # Clean up
        result_path.unlink(missing_ok=True)

        out(dict(
            status="failure",
            retry_count=retry_count,
            summary=summary,
        ))

    else:
        out(dict(error=f"Unknown status: {status}"))
