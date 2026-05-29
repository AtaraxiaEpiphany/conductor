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
from lib.constants import FAILURE_PATTERNS


# Safe contexts where error keywords don't indicate actual failure.
# Must be narrow to avoid masking real errors — only match phrases that
# clearly describe error handling/absence, not the errors themselves.
SAFE_CONTEXT_PATTERNS = [
    r"no\s+errors?\s*(?:were\s+)?(?:found|detected|encountered|occurred|reported)",
    r"errors?:?\s*none",
    r"error\s+was\s+handled",
    r"successfully\s+handled\s+the\s+error",
    r"catch\s+error",
]


def detect_failure(message: str) -> tuple[bool, Optional[str]]:
    """Detect failure patterns in message with reduced false positives.

    Returns:
        Tuple of (has_failure, detected_pattern)
    """
    if not message:
        return False, None

    # Check for failure patterns first — real failures must never be masked.
    failure_hit = None
    for pattern in FAILURE_PATTERNS:
        if re.search(pattern, message, re.IGNORECASE):
            failure_hit = pattern
            break

    if failure_hit is None:
        return False, None

    # A failure pattern matched — but check if the ENTIRE message is a safe
    # context (e.g. "no errors found"). Only suppress if every line containing
    # the failure keyword is also matched by a safe context pattern.
    message_lower = message.lower()
    for safe in SAFE_CONTEXT_PATTERNS:
        if re.search(safe, message_lower, re.IGNORECASE):
            # Safe context present alongside failure — check overlap.
            # If the failure keyword appears ONLY within safe phrases, suppress.
            for line in message.split("\n"):
                line_lower = line.strip().lower()
                if re.search(failure_hit, line_lower, re.IGNORECASE):
                    # This line has the failure keyword — is it in a safe context?
                    is_safe = False
                    for sp in SAFE_CONTEXT_PATTERNS:
                        if re.search(sp, line_lower, re.IGNORECASE):
                            is_safe = True
                            break
                    if not is_safe:
                        return True, failure_hit
            # All lines containing the failure keyword are in safe contexts
            return False, None

    return True, failure_hit


def main():
    """Main hook function"""
    input_data = read_hook_input()
    agent_type = input_data.get("agent_type", "")
    session_id = input_data.get("session_id", "")
    last_message = input_data.get("last_assistant_message", "")[:2000]

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
            decision="block",
            reason=reason,
        )
        return  # write_hook_output calls sys.exit — but be explicit for clarity

    # No failure detected — allow the subagent to stop normally
    write_hook_output()


if __name__ == "__main__":
    main()
