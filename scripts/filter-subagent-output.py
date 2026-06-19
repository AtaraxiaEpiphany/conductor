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
import time
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

# Agents whose followup is `dispatch-finalize`, which synthesizes a missing
# result from result.json / locked task state. For these, a missing result
# block is recoverable. Other agents (phase-checker, code-reviewer,
# skip-analyst, doc-syncer, ...) have no dispatch-finalize step — a missing
# block means lost status the orchestrator must inspect manually.
DISPATCH_FINALIZE_AGENTS = {"task-executor", "explorer"}

NO_RESULT_MESSAGE = (
    "[Conductor] Subagent completed without structured result block. "
    "Proceed with dispatch-finalize — it handles missing results."
)
NO_RESULT_MESSAGE_GENERIC = (
    "[Conductor] Subagent completed without a structured result block, and "
    "this agent type has no dispatch-finalize recovery. Inspect the track "
    "state and re-dispatch or roll back as needed."
)

NO_RESULT_WARN = (
    "[Conductor] No ---RESULT--- block detected in subagent output. "
    "ALWAYS proceed with dispatch-finalize — it will synthesize a result, "
    "write handoff records, and handle the failure path automatically."
)
NO_RESULT_WARN_GENERIC = (
    "[Conductor] No ---RESULT--- block detected in subagent output, and "
    "this agent type has no dispatch-finalize recovery. Inspect the track "
    "state directly and re-dispatch or roll back as needed."
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


def _is_fresh(path: Path, threshold: float) -> bool:
    """True if `path` exists and was modified at/after `threshold` (epoch secs)."""
    try:
        return path.stat().st_mtime >= threshold
    except OSError:
        return False


def _fresh_result_exists(cwd: str) -> bool:
    """True if a result.json was freshly written (within 3 min) under `cwd`.

    A subagent may write results via track-state write-result (to
    conductor/tracks/<name>/.conductor/result.json) without wrapping its output
    in ---RESULT--- delimiters. Only fresh files match, avoiding false positives
    from stale files left by crashed sessions in other tracks.
    """
    threshold = time.time() - 180  # 3 minutes (generous for long-running agents)
    try:
        base = Path(cwd)
        # Check direct .conductor/result.json first (most common path), then
        # under conductor/tracks/*/ — short-circuit on the first fresh hit.
        if _is_fresh(base / ".conductor" / "result.json", threshold):
            return True
        for p in base.glob("conductor/tracks/*/.conductor/result.json"):
            if _is_fresh(p, threshold):
                return True
    except (TypeError, ValueError, OSError):
        pass
    return False


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

    # Resolve agent type from the Agent tool input (PostToolUse payload).
    # Values may be bare ("phase-checker") or namespaced ("conductor:phase-checker",
    # "code-simplifier:code-simplifier") — normalize to the bare name.
    tool_input = input_data.get("tool_input") or {}
    raw_type = tool_input.get("subagent_type", "") if isinstance(tool_input, dict) else ""
    agent_type = raw_type.split(":")[-1]
    uses_finalize = agent_type in DISPATCH_FINALIZE_AGENTS

    # --- Responsibility 1: Extract structured result blocks ---
    result = extract_result_blocks(response)

    if result:
        updated_output = result
    elif uses_finalize:
        updated_output = NO_RESULT_MESSAGE
    else:
        updated_output = NO_RESULT_MESSAGE_GENERIC

    # --- Responsibility 2: Failure/recovery advisory context ---
    # Check recovery first (higher priority — confirms a resolved failure)
    extra_context = detect_recovery_context(response)
    if extra_context is None:
        extra_context = detect_failure_context(response)

    # If no structured result block AND no failure/recovery, check for result file.
    # The result.json freshness probe only applies to dispatch-finalize agents
    # (task-executor/explorer write result.json); other agents never do, so a
    # missing block for them is simply a lost-status warning.
    if result is None and extra_context is None:
        if uses_finalize:
            cwd = input_data.get("cwd") or str(Path.cwd())
            extra_context = NO_RESULT_OK if _fresh_result_exists(cwd) else NO_RESULT_WARN
        else:
            extra_context = NO_RESULT_WARN_GENERIC

    write_hook_output(
        updated_tool_output=updated_output,
        additional_context=extra_context,
    )


if __name__ == "__main__":
    main()
