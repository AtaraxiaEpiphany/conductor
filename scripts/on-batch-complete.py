#!/usr/bin/env python3
"""PostToolBatch hook: batch-level validation after parallel tool calls resolve.

Checks for state consistency issues across multiple operations.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.logging import init_logging, log_entry


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


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    session_id = input_data.get("session_id", "")

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

    # Default output
    write_simple_output()


if __name__ == "__main__":
    main()