"""Process task results, enforce quality gates."""
import json
import sys
from pathlib import Path

from lib.atomic_io import atomic_write_json
from .core import load
from .spec_integrity import _ac_integrity_gates, _TC_ID, _measured_tcs
from .plan_parse import parse_plan
from .helpers import (
    out, conductor_dir, _store_evidence, _extract_tags_for_task,
    _tag_exempt_from_coverage, _tag_exempt_from_tdd, flag, flags_all,
    _last_subtask_sha,
)
from .mutations import _do_complete, _do_fail
from .sync import _do_sync_plan
from .git_ops import _write_git_note, _git_commit_ensured, _finalize_parent
from .handoff import _append_execution_record


def _verify_tdd_gate(track_dir, sha, result_data):
    """Best-effort TDD verification: check that test files exist in the commit.

    Returns ``PASS`` or a non-PASS verdict that carries its own remediation
    (what to do) so the agent can self-correct in one turn — the same
    "verdict + fix in one message" contract the blocking hooks already use.
    """
    if not sha or sha == "N/A":
        return ("UNKNOWN — no commit SHA recorded; pass --commit-sha to "
                "write-result so the TDD check can run.")

    # Check files_changed in result for test file patterns
    files = result_data.get("files_changed", "")
    if not files:
        return ("UNKNOWN — no files_changed recorded; pass --files-changed "
                "to write-result so the TDD check can run.")

    test_patterns = ("test/", "tests/", "spec/", "_test.", "_spec.", ".test.", ".spec.", "Test", "Spec")
    has_test = any(p in files for p in test_patterns)

    if has_test:
        return "PASS"
    return ("NO_TESTS_FOUND — add a test file matching test*/, tests*/, "
            "spec*/, or *_test.*/*_spec.*/*.test.*/*.spec.* naming to the "
            "commit (Step 3 Red), or tag the task [Explore] if TDD-exempt.")


def _declared_tcs_for_task(track_dir, phase, task):
    """TC IDs this task's ``<!-- TC-n.m -->`` plan annotation declares.

    Resolved structurally via parse_plan — the guaranteed half of the
    self-extraction chain. Subtasks inherit their parent's annotation, so refs
    live on the parent task. Returns [] when plan.md, the phase, or the task is
    absent, or the task carries no TC comment.
    """
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        return []
    try:
        pi, ti = int(phase) - 1, int(task) - 1
    except (TypeError, ValueError):
        return []
    phases = parse_plan(plan_path).get("phases", [])
    if not (0 <= pi < len(phases)):
        return []
    tasks = phases[pi].get("tasks", [])
    if not (0 <= ti < len(tasks)):
        return []
    return tasks[ti].get("tc_refs", [])


def _grounding_refinement(track_dir, claimed, tags):
    """Third link of the self-extraction chain: are the claimed TCs GROUNDED in
    real ``def test_TC_{n}_{m}`` functions?

    Only called after the declared↔claimed link resolved to PASS — you can't
    ground TCs you extracted wrong, so grounding is a *refinement* of PASS, not
    a separate verdict (returns ``PASS`` plus a parenthetical). Plain ``PASS``
    (no annotation) when: ``tags`` unknown (caller passed None — e.g. legacy
    2-arg unit calls), the task is test-exempt, or the naming convention is
    unadopted track-wide (empty measured set — that's a track-level signal
    carried once by ``ac_verification_measured_rate``, not a per-task nag).
    Otherwise ``PASS (grounded)`` / ``PASS (PARTIAL grounding: …)`` /
    ``PASS (UNGROUND: …)``. Advisory only — a grounded TC has a named test, not
    a proof that test is semantically correct.
    """
    if tags is None or _tag_exempt_from_tdd(tags):
        return "PASS"
    measured = _measured_tcs(track_dir)
    if not measured:
        return "PASS"  # convention unadopted — rate carries the signal, not the gate
    ungrounded = sorted(claimed - measured)
    if not ungrounded:
        return "PASS (grounded)"
    if claimed & measured:
        return (f"PASS (PARTIAL grounding: {', '.join(ungrounded)} — add a "
                f"test_TC_{{n}}_{{m}}_* function for each, per plan-format-"
                f"contract.md §Test↔TC Naming Link)")
    return (f"PASS (UNGROUND: claimed {', '.join(sorted(claimed))} have no "
            f"matching test_TC_* functions — add test_TC_{{n}}_{{m}}_* tests "
            f"per plan-format-contract.md §Test↔TC Naming Link)")


def _tc_consistency_gate(track_dir, result_data, tags=None):
    """Advisory per-task gate over the self-extraction chain:
    declared (``<!-- TC-n.m -->``) → claimed (``tc_coverage``) → grounded
    (real ``def test_TC_{n}_{m}`` functions).

    The first two links (declared↔claimed) yield the verdict prefix —
    PASS / WRONG_AC / PARTIAL / UNKNOWN / N/A — verifying the agent extracted
    the right ACs post-hoc rather than by pre-injection (closes the "agent
    extracted the wrong AC/TC" gap without touching the minimal-dispatch
    contract). WARN-only sibling to ``_evaluate_gates``; verdict + remediation
    in one string.

    The third link (claimed↔grounded) is folded in as a refinement of PASS only
    (see ``_grounding_refinement``): ``PASS (grounded)`` / ``PASS (PARTIAL
    grounding: …)`` / ``PASS (UNGROUND: …)``, or plain ``PASS`` when the naming
    convention is unadopted track-wide or the task is test-exempt. Pass
    ``tags`` from the finalize paths (computed via ``_extract_tags_for_task``)
    to enable grounding; ``tags=None`` (default) skips it, preserving the
    legacy 2-arg contract. Subtasks claim a subset of the parent's declared
    TCs, so an intermediate subtask may report PARTIAL grounding that the final
    subtask resolves — advisory only, never blocks.

    Structural only: a PASS (grounded) means a named test exists, not that it
    is semantically correct.
    """
    declared = _declared_tcs_for_task(track_dir, result_data.get("phase"),
                                      result_data.get("task"))
    if not declared:
        return "N/A"
    raw = (result_data.get("tc_coverage") or "").strip()
    if not raw:
        return ("UNKNOWN — no tc_coverage recorded; pass --tc-coverage to "
                "write-result so the declared-vs-claimed check can run.")
    claimed = set(_TC_ID.findall(raw))
    if not claimed:
        return ("UNKNOWN — tc_coverage present but no TC-n.m IDs parsed; pass "
                "--tc-coverage with the covered TC IDs.")
    declared_set = set(declared)
    overlap = claimed & declared_set
    if not overlap:
        return (f"WRONG_AC — claimed TCs {', '.join(sorted(claimed))} intersect "
                f"none of declared {', '.join(sorted(declared_set))}; re-read "
                f"your task's `<!-- ... -->` annotation and spec.md §Acceptance "
                f"Criteria — you implemented the wrong AC.")
    missing = sorted(declared_set - claimed)
    if missing:
        return (f"PARTIAL — claimed {', '.join(sorted(overlap))} but declared "
                f"also has {', '.join(missing)}; add a test for the missing TC "
                f"(Step 3 Red) or report it SPEC_DEVIATION (§6.1).")
    return _grounding_refinement(track_dir, claimed, tags)


def _evaluate_gates(tags, result_data, sha, track_dir=None):
    """Advisory F2/F3 gate evaluation — the single source shared by both the
    legacy ``process-result`` path and the ``dispatch-finalize`` hot path so the
    two cannot drift (the hot path previously skipped gates entirely, letting
    sub-80% coverage complete silently).

    Returns ``(coverage_gate, tdd_gate, cov_pct)``. WARN-only: emits status
    strings, never fails the task. The real teeth stay at the commit-time F2
    ``ask`` gate (pre-command-check) and the F3 server-side coverage probe
    (on-batch-complete); this surfaces the signal in the finalize envelope so the
    orchestrator/plan can see it. ``[Docs]``/``[Config]``/``[Chore]``/``[Manual]``
    tasks are exempt from coverage; ``[Explore]`` is additionally TDD-exempt.
    """
    cov_pct = result_data.get("coverage_pct")
    coverage_gate = "PASS"
    if cov_pct is not None and not _tag_exempt_from_coverage(tags):
        if cov_pct < 80:
            # Verdict prefix stays stable ("FAILED (...)") for prefix/substring
            # matching; the appended clause is the remediation that lets the
            # agent self-correct in one turn.
            coverage_gate = (
                f"FAILED ({cov_pct}% < 80%) — add tests for uncovered lines "
                f"to reach ≥80%, or tag the task [Docs]/[Config]/[Chore]/"
                f"[Manual] if it is coverage-exempt."
            )
    tdd_gate = "PASS"
    if not _tag_exempt_from_tdd(tags):
        tdd_gate = _verify_tdd_gate(track_dir, sha, result_data)
    return coverage_gate, tdd_gate, cov_pct


def _advisory_gates(track_dir, result_data, tags, sha):
    """All advisory gate strings + ``cov_pct`` for a finalize envelope.

    Single source for the F2/F3 (``_evaluate_gates``), AC-integrity, EARS, and
    TC-consistency gates shared by both finalize paths (``cmd_process_result``
    here and ``_finalize_task`` in dispatch.py) so the envelope fields cannot
    drift between them. The AC-integrity snapshot is computed ONCE via
    ``_ac_integrity_gates`` and both ``ac_integrity_gate`` and ``ears_gate`` are
    derived from it (halving the spec/plan parse + measured-TC scan the two
    single-gate helpers would each repeat). WARN-only — never raises; a gate
    status never blocks completion.

    Returns ``(coverage_gate, tdd_gate, ac_integrity_gate, ears_gate,
    tc_consistency_gate, cov_pct)``.
    """
    coverage_gate, tdd_gate, cov_pct = _evaluate_gates(tags, result_data, sha, track_dir)
    ac_integrity_gate, ears_gate = _ac_integrity_gates(track_dir)
    tc_consistency_gate = _tc_consistency_gate(track_dir, result_data, tags)
    return (coverage_gate, tdd_gate, ac_integrity_gate, ears_gate,
            tc_consistency_gate, cov_pct)


# (cli flag, result key, coerce) for write-result field mode. A nested
# failure_detail sub-field uses a (section, key) tuple so flat flags populate a
# nested object without the agent hand-writing JSON.
_RESULT_FIELD_FLAGS = [
    ("--status", "status", str),
    ("--commit-sha", "commit_sha", str),
    ("--summary", "summary", str),
    ("--files-changed", "files_changed", str),
    ("--tc-coverage", "tc_coverage", str),
    ("--coverage-pct", "coverage_pct", int),
    ("--coverage-tool", "coverage_tool", str),
    ("--spec-deviation", "spec_deviation", str),
    ("--phase", "phase", int),
    ("--task", "task", int),
    ("--subtask", "subtask", int),
    ("--task-name", "task_name", str),
    ("--attempt", "attempt", int),
    ("--max-retries", "max_retries", int),
    ("--context-footprint", "context_footprint", str),
    ("--failure-done", ("failure_detail", "what_was_done"), str),
    ("--failure-reason", ("failure_detail", "failure_reason"), str),
    ("--failure-suggested", ("failure_detail", "suggested_next_step"), str),
]


def _result_from_flags(args):
    """Assemble a result dict from typed --flags so the agent never hand-writes JSON.

    Returns ``(result, used)`` — ``used`` is True if any field flag or
    ``--deviation`` was supplied (False → caller falls back to ``--data``/stdin).
    Integer fields are coerced; a non-integer value exits non-zero with a clear
    message — the type-validation the raw-JSON path lacked (a stray ``"94%"``
    used to land in result.json and blow up later in process-result).
    """
    result = {}
    failure_detail = {}
    for fflag, target, coerce in _RESULT_FIELD_FLAGS:
        val = flag(args, fflag)
        if val is None:
            continue
        try:
            coerced = coerce(val)
        except ValueError:
            out(dict(error=f"{fflag} expects {coerce.__name__}, got {val!r}"))
            sys.exit(1)
        if isinstance(target, tuple):
            failure_detail[target[1]] = coerced
        else:
            result[target] = coerced
    if failure_detail:
        result["failure_detail"] = failure_detail

    # Repeatable --deviation '<json object>' → spec_deviation_detail[].
    for d in flags_all(args, "--deviation"):
        try:
            result.setdefault("spec_deviation_detail", []).append(json.loads(d))
        except json.JSONDecodeError as e:
            out(dict(error=f"--deviation must be a JSON object: {e}"))
            sys.exit(1)

    return result, bool(result)


def cmd_write_result(track_dir, args=None):
    """Atomically write result.json from typed flags, --data, or stdin.

    Usage:
      track-state write-result <track-dir> --status success --commit-sha <sha> ...
      track-state write-result <track-dir> --data '<json>'   # raw-JSON escape hatch
      track-state write-result <track-dir>                   # JSON piped on stdin

    Field-flag mode assembles + type-validates the result so the agent never
    hand-writes JSON (the root cause of malformed/missing result.json). ``--data``
    wins if present (explicit raw JSON); otherwise field flags; otherwise stdin.
    ``args`` defaults to ``sys.argv[3:]`` but is injectable for tests.
    """
    cdir = conductor_dir(track_dir)
    result_path = cdir / "result.json"
    if args is None:
        args = sys.argv[3:]

    raw = flag(args, "--data")
    if raw is not None:
        # Explicit raw-JSON escape hatch — takes precedence over field flags.
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            out(dict(error=f"Invalid JSON: {e}"))
            sys.exit(1)
    else:
        result, used_fields = _result_from_flags(args)
        if not used_fields:
            # No field flags and no --data → read JSON from stdin.
            try:
                result = json.loads(sys.stdin.read())
            except json.JSONDecodeError as e:
                out(dict(error=f"Invalid JSON: {e}"))
                sys.exit(1)

    # Validate the one required field (shared by all three input modes).
    status = str(result.get("status", "")).upper()
    if status not in ("SUCCESS", "FAILURE"):
        out(dict(error="Missing or invalid 'status': must be SUCCESS or FAILURE "
                       "(--status success|failure, or 'status' in --data/stdin)."))
        sys.exit(1)
    result["status"] = status

    # Atomic write (temp + fsync + os.replace — shared with track-state core).
    atomic_write_json(result_path, result)
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

        try:
            parent_completed, state = _do_complete(track_dir, p, t, s, sha)
        except (ValueError, IndexError) as e:
            out(dict(error=str(e), status="error"))
            return

        _store_evidence(state, track_dir, p, t, s, r)

        _do_sync_plan(track_dir, state)

        # Write to handoff
        _append_execution_record(track_dir, p, t, s, r, state)

        # Spec deviations are recorded in handoff.md (_append_execution_record
        # above); the legacy issues.md mirror was removed.
        deviations = r.get("spec_deviation_detail", [])

        # Write git notes audit trail
        _write_git_note(track_dir, r, state)

        # If completing this subtask auto-completed its parent, give the parent
        # the same audit trail dispatch-next's parent-complete path gets
        # (conductor commit + git note + evidence). The post-commit sequence is
        # shared via _finalize_parent (same helper dispatch.py uses).
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
                _finalize_parent(track_dir, int(p), int(t), parent_sha)
            except (ValueError, IndexError, KeyError):
                # Best-effort: the subtask itself already completed + committed.
                pass

        # Clean up
        result_path.unlink(missing_ok=True)

        # F2/F3 + AC-integrity + EARS + TC-consistency advisory gates — WARN-only,
        # shared with dispatch-finalize via _advisory_gates so the two paths can't
        # drift. Computed AFTER _do_complete so a gate status never blocks
        # completion; the AC-integrity snapshot is computed once here.
        (coverage_gate, tdd_gate, ac_integrity_gate, ears_gate,
         tc_consistency_gate, cov_pct) = _advisory_gates(track_dir, r, tags, sha)
        result = dict(
            status="success",
            sha=sha,
            parent_completed=parent_completed,
            deviations=len(deviations),
            coverage_gate=coverage_gate,
            tdd_gate=tdd_gate,
            ac_integrity_gate=ac_integrity_gate,
            ears_gate=ears_gate,
            tc_consistency_gate=tc_consistency_gate,
        )
        if cov_pct is not None:
            result["coverage_pct"] = cov_pct
        out(result)

    elif status == "FAILURE":
        summary = r.get("summary", "")
        retry_count, state = _do_fail(track_dir, p, t, s, summary)
        _do_sync_plan(track_dir, state)

        # Write to handoff
        _append_execution_record(track_dir, p, t, s, r, state)

        # Clean up
        result_path.unlink(missing_ok=True)

        out(dict(
            status="failure",
            retry_count=retry_count,
            summary=summary,
        ))

    else:
        out(dict(error=f"Unknown status: {status}"))
