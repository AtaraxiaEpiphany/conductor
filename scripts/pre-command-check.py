#!/usr/bin/env python3
"""PreToolUse hook: real-time state protection for git and track-state operations.

Validates commands before execution, blocks suspicious operations.
Exit 2 = block with reason. Exit 0 = allow.
"""

import json
import re
import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import (
    read_hook_input,
    write_hook_output,
    get_tool_name,
    get_cwd
)
from lib.json_utils import load_json_safe
from lib.validation import is_dangerous_git_operation, contains_dangerous_pattern


def has_in_progress_task(state_file: Path) -> bool:
    """Check if state file has in_progress tasks

    Args:
        state_file: Path to track-state.json

    Returns:
        True if has in_progress tasks
    """
    state = load_json_safe(state_file)
    if not state:
        return False

    for phase in state.get("phases", []):
        for task in phase.get("tasks", []):
            if task.get("status") == "in_progress":
                return True
            # Check subtasks
            for sub in task.get("subtasks", []):
                if sub.get("status") == "in_progress":
                    return True

    return False


def find_track_state_violations(cwd: Path, command: str) -> list[str]:
    """Find tracks with state lock violations

    Args:
        cwd: Current working directory
        command: Command to check

    Returns:
        List of violation messages
    """
    tracks_file = cwd / "conductor" / "tracks.md"
    if not tracks_file.exists():
        return []

    # Extract track directories from tracks.md
    content = tracks_file.read_text(encoding="utf-8")
    dirs = re.findall(r'\[.*?\]\(([^)]+)\)', content)

    violations = []
    cmd_lower = command.lower()

    for d in dirs:
        state_file = cwd / d / "track-state.json"
        if not state_file.exists():
            continue

        state = load_json_safe(state_file)
        if not state:
            continue

        # Check for in_progress tasks
        has_in_progress = has_in_progress_task(state_file)

        if has_in_progress:
            # Check for dangerous operations during in_progress
            if 'rm ' in cmd_lower or 'delete' in cmd_lower:
                violations.append(f'{d}: in_progress task + deletion command')
            elif ' mv ' in cmd_lower or 'move' in cmd_lower:
                violations.append(f'{d}: in_progress task + move operation')

    return violations


def is_direct_track_state_modification(command: str) -> bool:
    """Check if command directly modifies track-state.json

    Args:
        command: Command to check

    Returns:
        True if directly modifies track-state.json
    """
    patterns = [
        r'(rm|mv|git\s+rm).*track-state\.json',
        r'sed.*track-state',
        r'python.*track-state.*write'
    ]
    for pattern in patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    tool_name = input_data.get("tool_name", "")
    cwd_str = input_data.get("cwd", "")
    cwd = Path(cwd_str) if cwd_str else Path.cwd()

    # Only check Bash tool
    if tool_name != "Bash":
        write_hook_output(hook_event_name="PreToolUse")
        return

    # Extract command from tool input
    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")

    # Check for dangerous git operations
    if is_dangerous_git_operation(command):
        # Extract the operation name
        match = re.search(r'git\s+(reset|rebase|clean|filter-branch)', command, re.IGNORECASE)
        operation = match.group(1) if match else "history-modifying"

        additional_context = (
            f'[Conductor] DANGER: Git {operation} command detected. '
            f'This may break state consistency. Run /conductor:revert if needed.'
        )
        permission_reason = (
            'Git history-modifying operations can break conductor state tracking. '
            'Confirm with /conductor:revert workflow instead.'
        )

        write_hook_output(
            hook_event_name="PreToolUse",
            additional_context=additional_context,
            permissionDecision="ask",
            reason=permission_reason
        )
        # Should not reach here due to sys.exit in write_hook_output

    # Check for track-state lock violations
    if 'track-state' in command.lower():
        violations = find_track_state_violations(cwd, command)
        if violations:
            violations_str = '; '.join(violations)
            additional_context = (
                f'[Conductor] State lock violation detected: {violations_str}. '
                f'Complete or revert the in_progress task before modifying track files.'
            )
            permission_reason = (
                'Track has in_progress task. '
                'Complete or revert first to maintain state consistency.'
            )

            write_hook_output(
                hook_event_name="PreToolUse",
                additional_context=additional_context,
                permissionDecision="ask",
                reason=permission_reason
            )
            # Should not reach here

    # Check for direct modifications to track-state.json
    if is_direct_track_state_modification(command):
        additional_context = (
            '[Conductor] Direct track-state.json modification detected. '
            'Use track-state CLI commands instead to maintain consistency.'
        )
        permission_reason = (
            'Direct modification of track-state.json bypasses state machine. '
            'Use /conductor:revert or track-state CLI.'
        )

        write_hook_output(
            hook_event_name="PreToolUse",
            additional_context=additional_context,
            permissionDecision="ask",
            reason=permission_reason
        )
        # Should not reach here

    # Allow all other commands
    write_hook_output(hook_event_name="PreToolUse")


if __name__ == "__main__":
    main()