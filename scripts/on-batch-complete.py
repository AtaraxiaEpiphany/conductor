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


def detect_project_type(cwd: Path) -> Optional[str]:
    """Detect project type based on files present.

    Args:
        cwd: Current working directory

    Returns:
        Project type string or None
    """
    if (cwd / "pyproject.toml").exists() or (cwd / "setup.py").exists():
        return "python"
    if (cwd / "package.json").exists():
        return "node"
    if (cwd / "go.mod").exists():
        return "go"
    return None


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
            timeout=30
        )

        if result.returncode != 0:
            # Coverage tool not configured or failed
            return None

        output = result.stdout + result.stderr

        # Parse coverage percentage based on tool type
        if project_type == "python":
            # Coverage.py output: "TOTAL                             100      100    100.00%"
            for line in output.split('\n'):
                if line.strip().startswith("TOTAL"):
                    parts = line.split()
                    if len(parts) >= 4:
                        try:
                            return float(parts[-1].rstrip('%'))
                        except (ValueError, IndexError):
                            continue
        elif project_type == "node":
            # Jest output: "All files | 85.5 | ..."
            for line in output.split('\n'):
                if "All files" in line or "% Statements" in line:
                    match = re.search(r'(\d+\.?\d*)\s*%?', line)
                    if match:
                        try:
                            return float(match.group(1))
                        except ValueError:
                            pass
        elif project_type == "go":
            # go test -cover output: "coverage: 87.5% of statements"
            match = re.search(r'coverage:\s*(\d+\.?\d*)%', output)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    pass

    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
        return None
    except Exception:
        return None

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


def verify_coverage_gate(cwd: Path) -> Optional[str]:
    """Run server-side coverage verification (F3 gate).

    Args:
        cwd: Working directory

    Returns:
        Warning message if coverage gate fails, None otherwise
    """
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

    # Issue-based context injection
    if issues:
        context_msg = get_context_message(issues)
        if context_msg:
            write_simple_output(additional_context=context_msg)
            return

    # Server-side coverage verification (F3 gate)
    # Only runs after git commit to prevent agent self-report bypass
    if should_verify_coverage(tool_calls):
        coverage_msg = verify_coverage_gate(cwd)
        if coverage_msg:
            write_simple_output(additional_context=coverage_msg)
            return

    # Phase checkpoint verification (V6 gate)
    # Only runs after track-state complete/skip operations
    if should_verify_checkpoint(tool_calls):
        checkpoint_msg = verify_phase_checkpoint(cwd)
        if checkpoint_msg:
            write_simple_output(additional_context=checkpoint_msg)
            return

    # Default output
    write_simple_output()


if __name__ == "__main__":
    main()
