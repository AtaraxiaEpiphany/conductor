#!/usr/bin/env python3
"""filter-subagent-output - PostToolUse hook on Agent tool.

Two responsibilities merged into a single hook to avoid duplicate processing:
1. Extract structured ---RESULT--- blocks from subagent output (context pressure reduction)
2. Inject failure/recovery context into the parent session (recovery advisory)

Previously split across filter-subagent-output.py and on-subagent-result.py.
"""

import json
import re
import sys
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.constants import FAILURE_PATTERNS, RECOVERY_SUCCESS_PATTERNS

# Pattern matches all conductor result block types
RESULT_PATTERN = (
    r'---(?:TASK RESULT|CHECKPOINT RESULT|SKIP ANALYSIS|DOC SYNC RESULT|'
    r'REVIEW RESULT|SPEC PLAN RESULT|ANALYSIS RESULT)---.*?'
    r'---(?:END RESULT|END ANALYSIS|END REVIEW RESULT|END SPEC PLAN RESULT|END ANALYSIS RESULT)---'
)

NO_RESULT_MESSAGE = (
    "[Conductor] Subagent completed. No structured result block found. "
    "Check .conductor/ for artifacts."
)

NO_RESULT_WARN = (
    "[Conductor] Subagent output filtered: no ---RESULT--- block detected in response."
)

NO_RESULT_OK = (
    "[Conductor] Subagent output filtered: no ---RESULT--- block in output, "
    "but result.json was written — processing will continue via dispatch-finalize."
)


def extract_result_blocks(response: str) -> Optional[str]:
    """Extract result blocks from subagent response."""
    matches = re.findall(RESULT_PATTERN, response, re.DOTALL)
    if matches:
        return '\n\n'.join(m.strip() for m in matches)
    return None


def detect_failure_context(response: str) -> Optional[str]:
    """Check for failure indicators and return advisory context.

    Returns None if no failure detected (normal path).
    """
    for pattern in FAILURE_PATTERNS:
        if re.search(pattern, response, re.IGNORECASE):
            return (
                "[Conductor] Subagent reported failure. "
                "If retries remain, the orchestrator will re-dispatch. "
                "If max retries reached, the skip-analyst will evaluate."
            )
    return None


def detect_recovery_context(response: str) -> Optional[str]:
    """Check for recovery success after a prior failure."""
    if "[Conductor Recovery]" not in response:
        return None

    for pattern in RECOVERY_SUCCESS_PATTERNS:
        if re.search(pattern, response, re.IGNORECASE):
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
        write_hook_output()
        return

    response = input_data.get("tool_response", "")
    if isinstance(response, dict):
        response = response.get("result") or json.dumps(response, ensure_ascii=False)
    elif not isinstance(response, str):
        response = str(response) if response else ""
    if not response:
        write_hook_output()
        return

    # --- Responsibility 1: Extract structured result blocks ---
    result = extract_result_blocks(response)

    if result:
        updated_output = result
    else:
        updated_output = NO_RESULT_MESSAGE

    # --- Responsibility 2: Failure/recovery advisory context ---
    # Check recovery first (higher priority — confirms a resolved failure)
    extra_context = detect_recovery_context(response)
    if extra_context is None:
        extra_context = detect_failure_context(response)

    # If no structured result block AND no failure/recovery, check for result file
    if result is None and extra_context is None:
        # The subagent may have written results via track-state write-result
        # (to conductor/tracks/<name>/.conductor/result.json) without wrapping
        # output in ---RESULT--- delimiters. Search for any recent result.json.
        cwd = input_data.get("cwd") or str(Path.cwd())
        result_found = False
        try:
            # Search conductor/tracks/*/**.conductor/result.json under cwd
            base = Path(cwd)
            for p in base.glob("conductor/tracks/*/.conductor/result.json"):
                if p.exists():
                    result_found = True
                    break
            # Also check .conductor/result.json directly (non-tracks layout)
            if not result_found:
                direct = base / ".conductor" / "result.json"
                if direct.exists():
                    result_found = True
        except (TypeError, ValueError):
            pass
        extra_context = NO_RESULT_OK if result_found else NO_RESULT_WARN

    write_hook_output(
        updated_tool_output=updated_output,
        additional_context=extra_context,
    )


if __name__ == "__main__":
    main()
