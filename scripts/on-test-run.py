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


# Precise test runner patterns — anchored to command start or pipe/chain
# boundaries to avoid matching "grep pytest", "echo 'jest'", etc.
TEST_RUNNER_PATTERNS = [
    r'(?:^|[|&;]\s*)pytest\b',
    r'(?:^|[|&;]\s*)jest\b',
    r'(?:^|[|&;]\s*)vitest\b',
    r'(?:^|[|&;]\s*)go\s+test\b',
    r'(?:^|[|&;]\s*)cargo\s+test\b',
    r'(?:^|[|&;]\s*)dotnet\s+test\b',
    r'(?:^|[|&;]\s*)(?:npm|npx)\s+(?:run\s+)?test\b',
    r'(?:^|[|&;]\s*)yarn\s+test\b',
    r'(?:^|[|&;]\s*)pnpm\s+test\b',
    r'(?:^|[|&;]\s*)bun\s+test\b',
    r'(?:^|[|&;]\s*)mvn\s+test\b',
    r'(?:^|[|&;]\s*)gradle\s+.*test\b',
    r'(?:^|[|&;]\s*)rspec\b',
    r'(?:^|[|&;]\s*)minitest\b',
    r'(?:^|[|&;]\s*)nose2?\b',
    r'(?:^|[|&;]\s*)coverage\s+(run|report)\b',
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

    # Precise failure indicators — avoid matching "successfully", "without
    # failure", etc. Count-based patterns are anchored to a NON-ZERO count so
    # the "0 failed"/"0 failures"/"Failed: 0" lines that all-green Jest/Vitest/
    # dotnet summaries always print don't trip a false failure (which would
    # inject "fix the implementation" guidance into a passing Green run).
    failure_patterns = [
        # Non-zero failure count, either order:
        # "1 failed", "2 failures", "1 failing", "Failed: 1", "failures: 3".
        r'[1-9]\d*\s*(?:failed|failures?|failing)\b',
        r'(?:failed|failures?|failing)\b\s*:?\s*[1-9]\d*',
        # Unambiguous structural signals.
        r'assertion\s+error',
        r'test\s+run\s+failed\b',
        r'runtime\s+error',
    ]

    combined = stdout + "\n" + stderr
    for pattern in failure_patterns:
        if re.search(pattern, combined, re.IGNORECASE):
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