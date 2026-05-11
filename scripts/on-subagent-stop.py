#!/usr/bin/env python3
"""SubagentStop hook: log subagent completion and inject post-processing context.

Uses asyncRewake for critical subagents to auto-recover on failure.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.logging import init_logging, log_entry


FAILURE_PATTERNS = [
    "error",
    "failed",
    "exception",
    "traceback",
    "timed out",
    "API error",
]

# Critical subagent types that should trigger auto-recovery on failure
CRITICAL_AGENTS = {
    "task-executor",
    "explorer",
    "phase-checker",
}


def detect_failure(message: str) -> tuple[bool, Optional[str]]:
    """Detect failure patterns in message

    Args:
        message: Message to check

    Returns:
        Tuple of (has_failure, detected_pattern)
    """
    if not message:
        return False, None

    message_lower = message.lower()
    for pattern in FAILURE_PATTERNS:
        if pattern in message_lower:
            return True, pattern
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