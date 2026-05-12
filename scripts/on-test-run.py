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


# Precise test runner patterns — anchored to avoid matching arbitrary
# strings that happen to contain "test" (e.g. grep, cat, echo commands).
TEST_RUNNER_PATTERNS = [
    r'\bpytest\b',
    r'\bjest\b',
    r'\bvitest\b',
    r'\bgo\s+test\b',
    r'\bcargo\s+test\b',
    r'\bdotnet\s+test\b',
    r'\bnpm\s+test\b',
    r'\byarn\s+test\b',
    r'\bpnpm\s+test\b',
    r'\bbun\s+test\b',
    r'\bmvn\s+test\b',
    r'\bgradle\s+.*test\b',
    r'\brspec\b',
    r'\bminitest\b',
    r'\bnose2?\b',
    r'\bcoverage\s+(run|report)\b',
]


def is_test_command(command: str) -> bool:
    """Check if command is a test command using precise pattern matching."""
    for pattern in TEST_RUNNER_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
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