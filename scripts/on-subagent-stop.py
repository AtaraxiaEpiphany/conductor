#!/usr/bin/env python3
"""SubagentStop hook: log subagent completion and inject post-processing context.

Uses asyncRewake for critical subagents to auto-recover on failure.

Improved failure detection to reduce false positives through context-aware pattern matching.
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


# Strong failure indicators - actual errors
STRONG_FAILURE_PATTERNS = [
    r"Traceback \(most recent call last\):",  # Python traceback
    r"Error:\s+",  # Explicit error messages
    r"Permission denied",  # OS-level permission errors
    r"File not found",  # File access errors
    r"Command failed",  # Command execution failures
    r"BUILD FAILED",  # Build failures
    r"test.*failed",  # Test failures
    r"AssertionError",  # Assertion failures
]

# Medium failure indicators - potential issues
MEDIUM_FAILURE_PATTERNS = [
    r"warning",  # Warnings
    r"deprecated",  # Deprecation notices
]

# Safe contexts where "error" keywords don't indicate actual failure
SAFE_CONTEXT_PATTERNS = [
    r"error handling",  # Code discussing error handling
    r"error message",  # Documentation about error messages
    r"errors?:?\s*none",  # Explicitly no errors
    r"error\s*code",  # Error codes (not errors)
    r"catch\s+error",  # Try-catch code
]

# Critical subagent types that should trigger auto-recovery on failure
CRITICAL_AGENTS = {
    "task-executor",
    "explorer",
    "phase-checker",
}


def detect_failure(message: str) -> tuple[bool, Optional[str]]:
    """Detect failure patterns in message with reduced false positives.

    Args:
        message: Message to check

    Returns:
        Tuple of (has_failure, detected_pattern)
    """
    if not message:
        return False, None

    message_lower = message.lower()

    # First, check if message is in a safe context
    for pattern in SAFE_CONTEXT_PATTERNS:
        if re.search(pattern, message_lower, re.IGNORECASE):
            return False, None

    # Check for strong failure indicators
    for pattern in STRONG_FAILURE_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return True, pattern

    # Medium patterns only trigger on specific agent types or conditions
    for pattern in MEDIUM_FAILURE_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            # Warnings alone don't trigger failure detection
            return False, None

    return False, None


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    agent_type = input_data.get("agent_type", "")
    session_id = input_data.get("session_id", "")
    last_message = input_data.get("last_assistant_message", "")[:500]

    # Initialize logging
    log_file = init_logging("on-subagent-stop")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Log lifecycle event
    log_entry(log_file, f"session={session_id} agent={agent_type} event=subagent_stop")

    # Detect failure patterns
    has_failure, pattern = detect_failure(last_message)

    if has_failure:
        failure_log_file = Path(log_file).parent / "subagent-failures.log"
        log_entry(
            failure_log_file,
            f"session={session_id} agent={agent_type} failure_detected pattern={pattern}"
        )

        # For critical subagents with asyncRewake: exit 2 to wake session on failure
        if agent_type in CRITICAL_AGENTS:
            context = (
                f"[Conductor] {agent_type} reported failure. "
                "Auto-recovery triggered. Run /conductor:implement to continue."
            )
            # Write to stderr to avoid being captured as hook output
            sys.stderr.write(
                json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "SubagentStop",
                        "additionalContext": context
                    }
                })
            )
            # Exit 2 signals asyncRewake to wake Claude for recovery
            sys.exit(2)

    write_simple_output()


if __name__ == "__main__":
    main()