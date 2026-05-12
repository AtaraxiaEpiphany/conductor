#!/usr/bin/env python3
"""SubagentStop hook: detect failures and keep subagent running for recovery.

For critical subagents (task-executor, explorer, phase-checker):
  Synchronous hook — returns decision: "block" with a reason that is delivered
  to the subagent as its next instruction, giving it a chance to self-recover.

For non-critical subagents:
  Async hook — fire-and-forget logging only.

Per the hook protocol:
  "SubagentStop hooks use the same decision control format as Stop hooks.
   They do not support additionalContext. Returning decision: 'block' with a
   reason keeps the subagent running and delivers reason to the subagent as its
   next instruction."
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.logging import init_logging, log_entry


# Strong failure indicators
STRONG_FAILURE_PATTERNS = [
    r"Traceback \(most recent call last\):",
    r"Error:\s+",
    r"Permission denied",
    r"File not found",
    r"Command failed",
    r"BUILD FAILED",
    r"test.*failed",
    r"AssertionError",
]

# Safe contexts where error keywords don't indicate actual failure
SAFE_CONTEXT_PATTERNS = [
    r"error handling",
    r"error message",
    r"errors?:?\s*none",
    r"error\s*code",
    r"catch\s+error",
]


def detect_failure(message: str) -> tuple[bool, Optional[str]]:
    """Detect failure patterns in message with reduced false positives.

    Returns:
        Tuple of (has_failure, detected_pattern)
    """
    if not message:
        return False, None

    message_lower = message.lower()

    for pattern in SAFE_CONTEXT_PATTERNS:
        if re.search(pattern, message_lower, re.IGNORECASE):
            return False, None

    for pattern in STRONG_FAILURE_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            return True, pattern

    return False, None


def main():
    """Main hook function"""
    input_data = read_hook_input()
    agent_type = input_data.get("agent_type", "")
    session_id = input_data.get("session_id", "")
    last_message = input_data.get("last_assistant_message", "")[:500]

    # Initialize logging
    log_file = init_logging("on-subagent-stop")
    log_entry(log_file, f"session={session_id} agent={agent_type} event=subagent_stop")

    # Detect failure patterns
    has_failure, pattern = detect_failure(last_message)

    if has_failure:
        log_entry(
            Path(log_file).parent / "subagent-failures.log",
            f"session={session_id} agent={agent_type} failure_detected pattern={pattern}"
        )

        # decision: "block" + reason keeps the subagent running.
        # The reason is delivered to the subagent as its next instruction.
        reason = (
            f"[Conductor Recovery] Failure detected (pattern: {pattern}). "
            "Review the error above, correct the issue, and retry. "
            "If the issue is unresolvable, report FAILURE in your result block."
        )
        write_hook_output(
            hook_event_name="SubagentStop",
            decision="block",
            reason=reason,
        )

    # No failure detected — allow the subagent to stop normally
    write_hook_output(hook_event_name="SubagentStop")


if __name__ == "__main__":
    main()
