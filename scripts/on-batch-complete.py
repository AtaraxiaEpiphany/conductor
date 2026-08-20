#!/usr/bin/env python3
"""PostToolBatch hook: batch-level validation after parallel tool calls resolve.

Checks for state consistency issues across multiple operations.
Includes server-side coverage gate verification (F3) to prevent agent self-report bypass.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports. TERMINAL_FOR_PARENT is sourced from
# the shared lib.constants layer rather than the track_state package — this
# keeps the hook single-path and avoids importing the whole state machine (via
# track_state/__init__) at every PostToolBatch fire just to read one status set.
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.constants import TERMINAL_FOR_PARENT
from lib.hook_io import read_hook_input, write_simple_output
from lib.logging import init_logging, log_entry
from lib.json_utils import load_json_safe
from lib.path_utils import find_tracks_registry, extract_track_dirs
from lib.env import get_data_dir
from lib.atomic_io import atomic_write_json
# detect_project_type + the pure coverage parser live in lib.coverage so the
# command-digester agent (via scripts/coverage-pct.py) shares the exact same
# per-language parsing the F3 probe uses here. get_coverage_percent below stays
# in this module (it owns the subprocess run); only the parser is shared.
from lib.coverage import detect_project_type, parse_coverage_percent


# Coverage detection patterns for common tools
COVERAGE_CONFIG_FILES = [
    ".coveragerc",
    "pyproject.toml",
    "setup.cfg",
    "package.json",
    "jest.config.js",
    "vitest.config.ts",
]

# Commands to get coverage per tool type
COVERAGE_COMMANDS = {
    "python": ["coverage", "report", "--format=text"],
    "pytest": ["pytest", "--cov", "--cov-report=term-missing"],
    "node": ["npm", "test", "--", "--coverage"],
    "go": ["go", "test", "-coverprofile=/dev/stdout", "-cover"],
}


def get_coverage_percent(cwd: Path) -> Optional[float]:
    """Get coverage percentage from running coverage tool.

    Args:
        cwd: Working directory

    Returns:
        Coverage percentage or None if unavailable
    """
    project_type = detect_project_type(cwd)
    if not project_type:
        return None

    cmd = COVERAGE_COMMANDS.get(project_type)
    if not cmd:
        return None

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=20
        )

        if result.returncode != 0:
            # Coverage tool not configured or failed
            return None

        output = result.stdout + result.stderr
        # Per-language parsing is shared with the command-digester agent via
        # lib.coverage — one parser, used by both the F3 probe and the
        # implementation-loop digester, so coverage % stays deterministic and
        # can't drift between the two call sites.
        return parse_coverage_percent(output, project_type)

    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
        return None
    except Exception:
        return None


def analyze_tool_calls(tool_calls: list[dict]) -> dict:
    """Analyze batch of tool calls for patterns

    Args:
        tool_calls: List of tool call dictionaries

    Returns:
        Analysis results dictionary
    """
    git_ops = []
    track_state_ops = []
    agent_calls = []

    for tc in tool_calls:
        tool_name = tc.get("tool_name", "")
        tool_input = tc.get("tool_input", {})

        if tool_name == "Bash":
            cmd = tool_input.get("command", "")
            if cmd:
                if re.match(r'^git\s', cmd):
                    git_ops.append(cmd[:100])
                if 'track-state' in cmd:
                    track_state_ops.append(cmd[:100])

        elif tool_name == "Agent":
            agent_calls.append(tool_input.get("description", "unknown"))

    # Check for patterns that suggest state drift
    issues = []

    # Pattern: multiple git commits without track-state update
    git_commits = [c for c in git_ops if 'commit' in c]
    if len(git_commits) >= 2 and not track_state_ops:
        issues.append('multiple_git_commits_without_state_update')

    # Pattern: git operations during active subagent
    if agent_calls and git_commits:
        issues.append('git_ops_during_subagent')

    return {
        'git_ops': git_ops,
        'track_state_ops': track_state_ops,
        'agent_calls': agent_calls,
        'issues': issues,
        'total_tools': len(tool_calls)
    }


def log_batch_metrics(
    log_file: Path,
    session_id: str,
    git_count: int,
    track_state_count: int
) -> None:
    """Log batch metrics

    Args:
        log_file: Log file path
        session_id: Session ID
        git_count: Number of git operations
        track_state_count: Number of track-state operations
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    message = f"session={session_id} git_ops={git_count} track_state_ops={track_state_count}"
    log_entry(log_file, message)


def get_context_message(issues: list[str]) -> Optional[str]:
    """Get context message based on detected issues

    Args:
        issues: List of detected issues

    Returns:
        Context message or None
    """
    if 'multiple_git_commits_without_state_update' in issues:
        return (
            "[Conductor] Batch analysis: Multiple git commits detected "
            "without track-state update. Consider running track-state sync."
        )
    elif 'git_ops_during_subagent' in issues:
        return (
            "[Conductor] Batch analysis: Git operations detected during active subagent. "
            "Verify state consistency after subagent completes."
        )
    return None


# Conductor orchestration commits carry the `(conductor)` scope. Matched on
# scope+colon so an unrelated commit that merely mentions "conductor" (e.g.
# `fix(conductor-plugin): typo`, `-m "update conductor docs"`) doesn't false-fire
# the F3 coverage gate.
_CONDUCTOR_COMMIT_SCOPE = re.compile(r"\(conductor\)\s*:")


def should_verify_coverage(tool_calls: list[dict]) -> bool:
    """Determine if coverage verification should run based on tool calls.

    Only triggers for conductor-related commits (message contains conductor
    markers or stages conductor-managed files), not for arbitrary git commits.

    Args:
        tool_calls: List of tool call dictionaries

    Returns:
        True if coverage verification should run
    """
    for tc in tool_calls:
        if tc.get("tool_name") == "Bash":
            cmd = tc.get("tool_input", {}).get("command", "")
            if cmd and "git commit" in cmd.lower():
                # Only trigger for conductor-managed commits (the `(conductor):`
                # scope), not any commit that happens to mention "conductor".
                if _CONDUCTOR_COMMIT_SCOPE.search(cmd):
                    return True
                # Also trigger when a conductor state file is named in the
                # command (explicit pathspec), e.g. `git commit track-state.json -m …`.
                if "track-state.json" in cmd or "plan.md" in cmd:
                    return True
    return False


def _resolve_active_gates(cwd: Path):
    """The resolved quality-gate set of the single active track under ``cwd`` —
    fail-open to the default full set.

    Mirrors pre-command-check's ``_resolve_active_gates`` (duplicated rather than
    shared to avoid a lib<->track_state cycle — workflow_shapes is deliberately a
    leaf). Exactly one active track → that shape's gates; zero or >1 → default.
    The F3 probe composes ``"coverage" in _resolve_active_gates(cwd)`` so a track
    whose shape drops the coverage gate (e.g. migration) is not falsely flagged.
    """
    from lib.locked_task import _iter_track_states
    from track_state.workflow_shapes import resolve_shape, gates_for
    shapes = []
    for _state_path, state in _iter_track_states(cwd):
        try:
            field = (state.get("workflow_shape")
                     if isinstance(state, dict) else None)
            shapes.append(resolve_shape(field))
        except Exception:
            continue
    shape = shapes[0] if len(shapes) == 1 else "default"
    return gates_for(shape)


def verify_coverage_gate(cwd: Path) -> Optional[str]:
    """Run server-side coverage verification (F3 gate).

    Args:
        cwd: Working directory

    Returns:
        Warning message if coverage gate fails, None otherwise

    Skipped (returns None) when the active track's shape drops the coverage gate
    (e.g. a migration shape) — F3 is not owed, so the probe does not run.
    """
    if "coverage" not in _resolve_active_gates(cwd):
        return None
    coverage = get_coverage_percent(cwd)

    if coverage is None:
        # Coverage tool not available or configured - skip verification
        return None

    if coverage < 80.0:
        return (
            f"[Conductor] Coverage Gate Failed: {coverage:.1f}% < 80%. "
            f"Run coverage tool and add tests to reach 80% threshold. "
            f"This is a server-side verification and cannot be bypassed."
        )

    return None


def should_verify_checkpoint(tool_calls: list[dict]) -> bool:
    """Detect if a conductor task completion just happened.

    Triggers only on track-state complete or skip commands, which indicate
    a task transition that may need a phase checkpoint.

    Args:
        tool_calls: List of tool call dictionaries

    Returns:
        True if checkpoint verification should run
    """
    for tc in tool_calls:
        if tc.get("tool_name") == "Bash":
            cmd = tc.get("tool_input", {}).get("command", "")
            if cmd and "track-state" in cmd:
                cmd_lower = cmd.lower()
                if "complete" in cmd_lower or "skip" in cmd_lower:
                    return True
    return False


def verify_phase_checkpoint(cwd: Path) -> Optional[str]:
    """Check if recently completed phases have checkpoint commits (V6 gate).

    Scans git log for checkpoint commits and cross-references with
    track-state to find completed phases missing checkpoints.

    Args:
        cwd: Working directory

    Returns:
        Warning message if missing checkpoints detected, None otherwise
    """
    tracks_file = find_tracks_registry(cwd)
    if not tracks_file:
        return None

    track_dirs = extract_track_dirs(tracks_file)

    # Get recent git log for checkpoint commits
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-30"],
            capture_output=True, text=True, cwd=cwd, timeout=5
        )
        if result.returncode != 0:
            return None
        git_log = result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    missing_checkpoints = []

    for track_dir in track_dirs:
        state_file = cwd / track_dir / "track-state.json"
        state = load_json_safe(state_file)
        if not state:
            continue

        # Only check tracks that are in_progress (not archived/completed)
        track_status = state.get("status", "")
        if track_status in ("archived", "cancelled"):
            continue

        phases = state.get("phases", [])
        for pi, phase in enumerate(phases, 1):
            tasks = phase.get("tasks", [])
            if not tasks:
                continue

            # Check if all tasks in this phase are terminal
            all_terminal = all(t.get("status") in TERMINAL_FOR_PARENT for t in tasks)
            if not all_terminal:
                continue

            # Check if there's a checkpoint commit for this phase
            phase_name = phase.get("name", f"Phase {pi}")
            # Checkpoint commits contain "checkpoint" + the phase number or name
            # (emitted by phase-checker as `chore(conductor): Checkpoint end of …`).
            checkpoint_patterns = [
                f"phase {pi}",
                f"P{pi}",
                phase_name.lower(),
            ]
            has_checkpoint = False
            for line in git_log.split('\n'):
                line_lower = line.lower()
                if "checkpoint" in line_lower:
                    for pattern in checkpoint_patterns:
                        if pattern in line_lower:
                            has_checkpoint = True
                            break
                if has_checkpoint:
                    break

            if not has_checkpoint:
                track_id = state.get("track_id", track_dir)
                completed_count = sum(1 for t in tasks if t.get("status") == "completed")
                missing_checkpoints.append(
                    f"{track_id} Phase {pi} ({completed_count}/{len(tasks)} tasks completed)"
                )

    if missing_checkpoints:
        details = "; ".join(missing_checkpoints)
        return (
            f"[Conductor] V6 Warning: Phase checkpoint missing for: {details}. "
            f"Phase-checker should have created a checkpoint commit. "
            f"If tasks were completed without checkpoint, consider running "
            f"track-state add-checkpoint to retroactively create one."
        )

    return None


# ---------------------------------------------------------------------------
# Context-budget yield (#3). dispatch-finalize is the per-cycle accounting
# seat (decision-loop-heartbeat.md); each fire ≈ one task completed. Counting
# them per session gives a deterministic budget signal that replaces the
# orchestrator's fuzzy "~6+ dispatches, or you sense compaction" heuristic —
# a weak model cannot reliably self-assess its own budget, so the hook does it.
# The counter lives under the data dir (session-scoped, gitignored) — never
# under a track's .conductor/, which would race the orchestrator's commit and
# violate single-writer (decision-serial-execution.md).
BUDGET_YIELD_FILE = "budget-yield.json"
# Disabled by default — long-running sessions run uninterrupted (per-task
# atomic commits + ``track-state recover`` already make a mid-session
# interruption benign). Opt back INTO the deterministic pacing yield by setting
# ``CONDUCTOR_BUDGET_YIELD_N`` to this suggested small-window value (a
# small-context model exhausts its budget well before 8 cycles; 8 suits a
# strong model on a long phase).
SUGGESTED_BUDGET_YIELD_N = 4


def _detect_dispatch_finalize(tool_calls: list[dict]) -> bool:
    """True if the batch contains a ``track-state dispatch-finalize`` Bash call."""
    for tc in tool_calls:
        if tc.get("tool_name") == "Bash":
            cmd = tc.get("tool_input", {}).get("command", "")
            if cmd and "track-state" in cmd and "dispatch-finalize" in cmd:
                return True
    return False


def _budget_threshold() -> int:
    """dispatch-finalize count at which to recommend a yield; 0 = disabled.

    Disabled by default (long-running sessions run uninterrupted): when
    ``CONDUCTOR_BUDGET_YIELD_N`` is unset/invalid, returns 0. Set it to a
    positive int (``SUGGESTED_BUDGET_YIELD_N`` = 4 is the suggested window for a
    small-context model) to re-enable the deterministic pacing yield.
    """
    raw = os.environ.get("CONDUCTOR_BUDGET_YIELD_N", "")
    try:
        n = int(raw)
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


def bump_budget_counter(data_dir: Path, session_id: str,
                        tool_calls: list[dict]) -> int:
    """Increment the per-session dispatch-finalize counter; return the new count.

    Returns 0 when the batch is not a dispatch-finalize or there is no session
    id to attribute the count to — in either case the budget gate stays idle.
    Persistence is best-effort: the in-memory count still gates this turn even
    if the write fails.
    """
    if not session_id or not _detect_dispatch_finalize(tool_calls):
        return 0

    counter_path = data_dir / BUDGET_YIELD_FILE
    counts: dict = {}
    if counter_path.exists():
        loaded = load_json_safe(counter_path)
        if isinstance(loaded, dict):
            counts = loaded

    n = int(counts.get(session_id, 0)) + 1
    counts[session_id] = n
    try:
        atomic_write_json(counter_path, counts)
    except OSError:
        pass  # best-effort; the in-memory n still gates this turn

    return n


def budget_yield_message(count: int) -> Optional[str]:
    """The yield instruction when the count crosses threshold, else None.

    None when disabled (threshold <= 0, the default) — long-running sessions
    are not forced to checkpoint-and-stop.
    """
    if count <= 0:
        return None
    threshold = _budget_threshold()
    if threshold <= 0 or count < threshold:
        return None
    return (
        "[Conductor] Context-budget threshold reached: " + str(count) +
        " dispatch-finalize cycles this session (limit " + str(threshold) +
        "). Yield now — finish any in-flight task to a terminal committed "
        "state, then emit the §5 checkpoint string (`⏸️ Conductor checkpoint "
        "at P{phase}.T{task} — state committed. Re-invoke /conductor:implement "
        "to resume.`) and stop. `track-state recover` picks up here; do NOT "
        "stop mid-task."
    )


# ---------------------------------------------------------------------------
# Doc-gardening heartbeat (Pillar 3 — entropy-fighting on a cadence). The
# freshness infra (doc-linter, last_verified frontmatter, lib/frontmatter.py)
# otherwise only fires post-loop (once per completed track) or as a SessionStart
# nudge; nothing fights corpus drift on a regular heartbeat. This gate nudges a
# one-shot doc-linter dispatch at most once per N hours, riding the
# dispatch-finalize event — the sanctioned per-cycle seat
# (decision-loop-heartbeat.md) — NOT a wall clock (a cron is explicitly rejected
# there; this rides a deterministic event, so it is compliant).
#
# The hook MUST NOT dispatch the agent itself: PostToolBatch runs with a 35s
# budget vs doc-linter's 30 maxTurns. It emits one additional_context line and
# the orchestrator acts on it. Advisory and non-blocking, consistent with the
# plugin's long-standing non-blocking review posture. The throttle is
# project-global (doc freshness is global, not per-session), so a single
# last_run_iso gates every session — once nudged it stays quiet for the window
# across resumes. Coordinate with the post-loop §6.5 lint + the SessionStart
# drift nudge (both still run): at most one redundant nudge per window is the
# accepted cost of keeping this decoupled from the track sidecar's boolean-only
# `lint_done` marker.
DOC_LINT_HEARTBEAT_FILE = "doc-lint-heartbeat.json"
# 24h default: the corpus drifts on the scale of sessions/days, not minutes.
# Override via CONDUCTOR_DOC_LINT_HEARTBEAT_H; 0 (or negative) disables.
DEFAULT_DOC_LINT_HEARTBEAT_H = 24


def _doc_lint_heartbeat_threshold_h() -> int:
    """Hours between doc-lint heartbeat nudges. Env-overridable; <=0 disables."""
    raw = os.environ.get("CONDUCTOR_DOC_LINT_HEARTBEAT_H", "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DOC_LINT_HEARTBEAT_H


def _doc_lint_heartbeat_due(data_dir: Path) -> bool:
    """True if no nudge in the last threshold hours. Disabled threshold ⇒ False.

    Best-effort: a missing/corrupt throttle file ⇒ due now (a fresh install or a
    hand-corrupted ledger both surface the nudge rather than silently suppressing).
    """
    threshold = _doc_lint_heartbeat_threshold_h()
    if threshold <= 0:
        return False
    data = load_json_safe(data_dir / DOC_LINT_HEARTBEAT_FILE)
    last = data.get("last_run_iso") if isinstance(data, dict) else None
    if not last:
        return True
    try:
        then = datetime.fromisoformat(last)
    except (ValueError, TypeError):
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - then).total_seconds() / 3600.0
    return age_h >= threshold


def _doc_lint_mark_run(data_dir: Path) -> None:
    """Stamp the heartbeat clock. Best-effort: a failed write re-nudges next cycle."""
    try:
        atomic_write_json(
            data_dir / DOC_LINT_HEARTBEAT_FILE,
            {"last_run_iso": datetime.now(timezone.utc).isoformat()},
        )
    except OSError:
        pass


def doc_lint_heartbeat_message() -> str:
    """The one-line advisory nudge. The orchestrator dispatches conductor:doc-linter."""
    threshold = _doc_lint_heartbeat_threshold_h()
    return (
        "[Conductor] Doc-gardening heartbeat: no doc-lint pass in >=" + str(threshold) +
        "h. Dispatch `conductor:doc-linter` with prompt `PROJECT_DIR=<project root>` "
        "(one-shot advisory — orphans / stale claims / contradictions / missing "
        "frontmatter). For the loop-until-dry repair run `/conductor:wiki-doctor lint`. "
        "Non-blocking; ignore if a pass ran recently."
    )


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    session_id = input_data.get("session_id", "")
    cwd_str = input_data.get("cwd", "")
    cwd = Path(cwd_str) if cwd_str else Path.cwd()

    # Get tool calls
    tool_calls = input_data.get("tool_calls", [])

    # Analyze tool calls
    analysis = analyze_tool_calls(tool_calls)
    git_count = len(analysis.get("git_ops", []))
    track_state_count = len(analysis.get("track_state_ops", []))
    issues = analysis.get("issues", [])

    # Log batch metrics
    log_file = init_logging("on-batch-complete")
    log_batch_metrics(log_file, session_id, git_count, track_state_count)

    # Bump the per-session dispatch-finalize counter FIRST (always, before any
    # early-return gate) so a task that completes under a failing state gate
    # still counts toward the budget. The yield message itself is emitted as the
    # last gate below — correctness gates take precedence within a single turn.
    budget_count = bump_budget_counter(get_data_dir(), session_id, tool_calls)

    # Issue-based context injection
    if issues:
        context_msg = get_context_message(issues)
        if context_msg:
            write_simple_output(additional_context=context_msg)
            return

    # Phase checkpoint verification (V6 gate) — runs FIRST.
    # Cheap git-log scan (5s budget) and high-signal: a phase boundary crossed
    # without a checkpoint commit is a structural-integrity issue. Running it
    # before the F3 coverage probe guarantees a slow/timeout coverage run can't
    # starve it under the 35s PostToolBatch hook budget.
    if should_verify_checkpoint(tool_calls):
        checkpoint_msg = verify_phase_checkpoint(cwd)
        if checkpoint_msg:
            write_simple_output(additional_context=checkpoint_msg)
            return

    # Server-side coverage verification (F3 gate)
    # Only runs after git commit to prevent agent self-report bypass. Runs second
    # so its 20s subprocess timeout (headroom under the 35s hook budget) cannot
    # drop the cheaper checkpoint gate above.
    if should_verify_coverage(tool_calls):
        coverage_msg = verify_coverage_gate(cwd)
        if coverage_msg:
            write_simple_output(additional_context=coverage_msg)
            return

    # Context-budget yield gate (pacing, not correctness). Fires only when the
    # state gates above are clean; on a dispatch-finalize batch those gates
    # don't match (no `git commit`/`complete`/`skip` substring in the command),
    # so the yield surfaces reliably once the threshold is crossed.
    yield_msg = budget_yield_message(budget_count)
    if yield_msg:
        write_simple_output(additional_context=yield_msg)
        return

    # Doc-gardening heartbeat (Pillar 3 entropy-fight; advisory, non-blocking).
    # Rides the dispatch-finalize event — the sanctioned per-cycle seat — at most
    # once per CONDUCTOR_DOC_LINT_HEARTBEAT_H (default 24h). The hook nudges only;
    # it does NOT dispatch the agent (35s budget vs doc-linter's 30 maxTurns).
    # Last gate so it can never starve the correctness/checkpoint/coverage gates.
    if _detect_dispatch_finalize(tool_calls) and _doc_lint_heartbeat_due(get_data_dir()):
        _doc_lint_mark_run(get_data_dir())
        write_simple_output(additional_context=doc_lint_heartbeat_message())
        return

    # Default output
    write_simple_output()


if __name__ == "__main__":
    main()
