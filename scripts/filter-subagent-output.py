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

# Pattern matches all conductor result block types.
# Open tags: uppercase words (e.g. TASK RESULT, DOC SYNC RESULT, SKIP ANALYSIS).
# Close tags: END + uppercase words (e.g. END RESULT, END SPEC PLAN RESULT).
RESULT_PATTERN = r'---[A-Z][A-Z ]+---.*?---END [A-Z ]+---'

NO_RESULT_MESSAGE = (
    "[Conductor] Subagent completed without structured result block. "
    "Proceed with dispatch-finalize — it handles missing results."
)

NO_RESULT_WARN = (
    "[Conductor] No ---RESULT--- block detected in subagent output. "
    "ALWAYS proceed with dispatch-finalize — it will synthesize a result, "
    "write handoff records, and handle the failure path automatically."
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
        # output in ---RESULT--- delimiters. Only match fresh result.json files
        # (modified within the last 60 seconds) to avoid false positives from
        # stale files left by crashed sessions in other tracks.
        import time
        cwd = input_data.get("cwd") or str(Path.cwd())
        result_found = False
        freshness_threshold = time.time() - 60  # 60 seconds
        try:
            base = Path(cwd)
            # Check direct .conductor/result.json first (most common path)
            direct = base / ".conductor" / "result.json"
            if direct.exists() and direct.stat().st_mtime >= freshness_threshold:
                result_found = True
            # Then check under conductor/tracks/*/ — only fresh files
            if not result_found:
                for p in base.glob("conductor/tracks/*/.conductor/result.json"):
                    if p.exists() and p.stat().st_mtime >= freshness_threshold:
                        result_found = True
                        break
        except (TypeError, ValueError, OSError):
            pass
        extra_context = NO_RESULT_OK if result_found else NO_RESULT_WARN

    write_hook_output(
        updated_tool_output=updated_output,
        additional_context=extra_context,
    )


if __name__ == "__main__":
    main()
