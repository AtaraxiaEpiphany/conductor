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

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.logging import init_logging, log_entry


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


def should_verify_coverage(tool_calls: list[dict]) -> bool:
    """Determine if coverage verification should run based on tool calls.

    Only triggers for conductor-related commits (message contains conductor
    markers or stages conductor-managed files), not for arbitrary git commits.

    Args:
        tool_calls: List of tool call dictionaries

    Returns:
        True if coverage verification should run
    """
    conductor_markers = ["conductor", "chore(conductor)", "track-state"]
    for tc in tool_calls:
        if tc.get("tool_name") == "Bash":
            cmd = tc.get("tool_input", {}).get("command", "")
            if cmd and "git commit" in cmd.lower():
                # Only trigger for conductor-managed commits
                if any(marker in cmd.lower() for marker in conductor_markers):
                    return True
                # Also check if conductor state files are staged
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

    # Default output
    write_simple_output()


if __name__ == "__main__":
    main()
