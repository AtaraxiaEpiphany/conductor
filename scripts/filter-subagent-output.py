#!/usr/bin/env python3
"""filter-subagent-output - PostToolUse hook on Agent tool.

Extracts only ---RESULT--- delimited blocks from subagent output,
discarding narrative/thinking text to reduce main session context pressure.
"""

import re
import sys
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output


# Pattern matches all conductor result block types
RESULT_PATTERNS = [
    r'---(?:TASK RESULT|CHECKPOINT RESULT|SKIP ANALYSIS|DOC SYNC RESULT|REVIEW RESULT|SPEC PLAN RESULT)---.*?---(?:END RESULT|END ANALYSIS|END REVIEW RESULT|END SPEC PLAN RESULT)---',
]

NO_RESULT_MESSAGE = (
    "[Conductor] Subagent completed. No structured result block found. "
    "Check .conductor/ for artifacts."
)

NO_RESULT_CONTEXT = (
    "[Conductor] Subagent output filtered: no ---RESULT--- block detected in response."
)


def extract_result_blocks(response: str) -> Optional[str]:
    """Extract result blocks from subagent response

    Args:
        response: Subagent response text

    Returns:
        Filtered result blocks or None if not found
    """
    for pattern in RESULT_PATTERNS:
        matches = re.findall(pattern, response, re.DOTALL)
        if matches:
            filtered = '\n\n'.join(m.strip() for m in matches)
            return filtered
    return None


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    tool_name = input_data.get("tool_name", "")

    # Only process Agent tool calls
    if tool_name != "Agent":
        write_hook_output(hook_event_name="PostToolUse")
        return

    # Extract result blocks from tool_response
    response = input_data.get("tool_response", "")
    if not response:
        write_hook_output(hook_event_name="PostToolUse")
        return

    result = extract_result_blocks(response)

    if result:
        write_hook_output(
            hook_event_name="PostToolUse",
            updated_tool_output=result
        )
    else:
        # No result block found - provide compact summary
        write_hook_output(
            hook_event_name="PostToolUse",
            updated_tool_output=NO_RESULT_MESSAGE,
            additional_context=NO_RESULT_CONTEXT
        )


if __name__ == "__main__":
    main()