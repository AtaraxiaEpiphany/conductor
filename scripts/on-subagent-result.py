#!/usr/bin/env python3
"""PostToolUse hook on Agent tool: inject recovery context into the parent session.

Per the hook protocol:
  "To inject context into the parent session after a subagent returns,
   use a PostToolUse hook on the Agent tool instead."

This hook complements the SubagentStop hook:
  - SubagentStop with decision: "block" + reason → keeps subagent running
  - This PostToolUse hook → injects context into the PARENT session after
    the subagent eventually returns (either recovered or failed again)

Detects failure indicators in subagent output and adds additionalContext
so the orchestrator can react appropriately.
"""

import json
import re
import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output

FAILURE_INDICATORS = [
    r"status.*FAILURE",
    r"BUILD FAILED",
    r"Traceback \(most recent call last\):",
    r"Command failed",
    r"test.*failed",
]

RECOVERY_SUCCESS_INDICATORS = [
    r"status.*SUCCESS",
    r"All tests passed",
    r"Coverage:",
]


def analyze_subagent_response(response: str) -> str | None:
    """Analyze subagent response for failure/success signals.

    Returns additionalContext for the parent session, or None.
    """
    if not response:
        return None

    # Check for failure
    for pattern in FAILURE_INDICATORS:
        if re.search(pattern, response, re.IGNORECASE):
            return (
                "[Conductor] Subagent reported failure. "
                "If retries remain, the orchestrator will re-dispatch. "
                "If max retries reached, the skip-analyst will evaluate."
            )

    # Check for recovery success after previous failure
    for pattern in RECOVERY_SUCCESS_INDICATORS:
        if re.search(pattern, response, re.IGNORECASE):
            # Check if this was preceded by a recovery hint
            if "[Conductor Recovery]" in response:
                return (
                    "[Conductor] Subagent recovered from failure and completed successfully."
                )

    return None


def main():
    """Main hook function"""
    input_data = read_hook_input()
    tool_name = input_data.get("tool_name", "")

    # Only process Agent tool calls
    if tool_name != "Agent":
        write_hook_output(hook_event_name="PostToolUse")
        return

    response = input_data.get("tool_response", "")
    if not response:
        write_hook_output(hook_event_name="PostToolUse")
        return

    context = analyze_subagent_response(response)
    if context:
        write_hook_output(
            hook_event_name="PostToolUse",
            additional_context=context,
        )
    else:
        write_hook_output(hook_event_name="PostToolUse")


if __name__ == "__main__":
    main()
