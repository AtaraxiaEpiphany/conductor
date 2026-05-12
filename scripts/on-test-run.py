#!/usr/bin/env python3
"""PostToolUse hook for task-executor: log test results and provide TDD context.

After any test command runs, log and provide additionalContext on failure.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.logging import init_logging, log_entry


TEST_PATTERNS = [
    r'test',
    r'pytest',
    r'jest',
    r'vitest',
    r'go test',
    r'cargo test',
    r'dotnet test',
]


def is_test_command(command: str) -> bool:
    """Check if command is a test command

    Args:
        command: Command string to check

    Returns:
        True if command appears to be a test command
    """
    command_lower = command.lower()
    for pattern in TEST_PATTERNS:
        if pattern in command_lower:
            return True
    return False


def has_test_failure(stdout: str, stderr: str, interrupted: bool) -> bool:
    """Check if test run had failures

    Args:
        stdout: Standard output
        stderr: Standard error
        interrupted: Whether the command was interrupted

    Returns:
        True if failures detected
    """
    if interrupted:
        return True

    # Precise failure indicators — avoid matching "successfully", "without failure", etc.
    failure_patterns = [
        r'\bFAILED\b',
        r'\bFAILURES\b',
        r'\d+\s+failed\b',
        r'tests?\s+failed\b',
        r'assertion\s+error',
        r'test\s+run\s+failed\b',
        r'runtime\s+error',
    ]

    combined = stdout + "\n" + stderr
    combined_lower = combined.lower()
    for pattern in failure_patterns:
        if re.search(pattern, combined_lower):
            return True

    return False


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()

    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")
    tool_response = input_data.get("tool_response", {})

    stdout = tool_response.get("stdout", "")
    stderr = tool_response.get("stderr", "")
    interrupted = tool_response.get("interrupted", False)

    # Check if this was a test command
    if not is_test_command(command):
        write_simple_output()
        return

    # Initialize logging
    log_file = init_logging("on-test-run")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Log test result
    log_msg = f'{timestamp} test_command="{command}"'

    if interrupted:
        log_msg += " result=interrupted"
        has_failure = True
    elif has_test_failure(stdout, stderr, interrupted):
        log_msg += " result=failed"
        has_failure = True
    else:
        log_msg += " result=passed"
        has_failure = False

    log_entry(log_file, log_msg)

    # Provide TDD context on failure
    if has_failure:
        context = (
            "[Conductor TDD] Test command produced errors. "
            "If this is the Red phase (Step 3), failure is expected. "
            "If this is the Green phase (Step 4), fix the implementation."
        )
        write_simple_output(additional_context=context)
    else:
        write_simple_output()


if __name__ == "__main__":
    main()