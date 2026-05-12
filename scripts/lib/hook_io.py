"""Shared library for hook JSON input/output handling

Provides unified interface for processing Claude Code hook JSON input/output,
following the Claude Code hook protocol.
"""

import json
import os
import sys
from typing import Any, Dict, Optional


# Cache for hook input to avoid multiple reads
_cached_hook_input: Optional[Dict[str, Any]] = None


def read_hook_input() -> Dict[str, Any]:
    """Read hook input JSON from stdin (cached)

    Returns:
        Parsed JSON data
    """
    global _cached_hook_input
    if _cached_hook_input is None:
        _cached_hook_input = json.load(sys.stdin)
    return _cached_hook_input


def get_hook_field(field_name: str, default: Any = None) -> Any:
    """Get a specific field from hook input

    Args:
        field_name: Field name
        default: Default value

    Returns:
        Field value or default
    """
    data = read_hook_input()
    return data.get(field_name, default)


def write_hook_output(
    additional_context: Optional[str] = None,
    decision: Optional[str] = None,
    reason: Optional[str] = None,
    system_message: Optional[str] = None,
    suppress_output: bool = False,
    stop_reason: Optional[str] = None,
    hook_event_name: Optional[str] = None
) -> None:
    """Write hook output, following Claude Code hook protocol

    Args:
        additional_context: Additional context injection
        decision: Decision ('block' to prevent execution)
        reason: Reason for decision
        system_message: System warning message
        suppress_output: Whether to suppress output
        stop_reason: Stop reason (when continue=false)
        hook_event_name: Hook event name (overrides auto-detection)
    """
    output = {"hookSpecificOutput": {}}

    # Set hook event name - try parameter first, then read from input
    if hook_event_name is None:
        event_name = get_hook_event_name() or os.environ.get("HOOK_EVENT_NAME", "")
    else:
        event_name = hook_event_name
    output["hookSpecificOutput"]["hookEventName"] = event_name

    # Additional context
    if additional_context:
        output["hookSpecificOutput"]["additionalContext"] = additional_context

    # Decision control
    if decision:
        output["decision"] = decision
        if reason:
            output["reason"] = reason

    # System message
    if system_message:
        output["systemMessage"] = system_message

    # Output control
    if suppress_output:
        output["suppressOutput"] = suppress_output

    # Stop reason
    if stop_reason:
        output["stopReason"] = stop_reason

    # Output JSON
    print(json.dumps(output, ensure_ascii=False))

    # Exit based on decision
    if decision == "block":
        sys.exit(2)
    else:
        sys.exit(0)


def write_simple_output(additional_context: Optional[str] = None) -> None:
    """Write simple hook response (additional_context only)

    Args:
        additional_context: Additional context
    """
    if additional_context:
        write_hook_output(additional_context=additional_context)
    else:
        event_name = get_hook_event_name() or os.environ.get("HOOK_EVENT_NAME", "")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name}}, ensure_ascii=False))
        sys.exit(0)


def write_decision_block(reason: str) -> None:
    """Write decision to block execution

    Args:
        reason: Reason for blocking
    """
    write_hook_output(decision="block", reason=reason)


def write_decision_allow() -> None:
    """Write decision to allow execution"""
    write_hook_output()


# Common field quick accessors
def get_hook_event_name() -> Optional[str]:
    """Get hook_event_name from input

    Returns:
        Hook event name or empty string if not found
    """
    return get_hook_field("hook_event_name", "")


def get_session_id() -> Optional[str]:
    """Get session_id"""
    return get_hook_field("session_id")


def get_cwd() -> Optional[str]:
    """Get current working directory"""
    return get_hook_field("cwd")


def get_tool_name() -> Optional[str]:
    """Get tool name"""
    return get_hook_field("tool_name")


def get_agent_id() -> Optional[str]:
    """Get agent_id (subagent hooks)"""
    return get_hook_field("agent_id")


def get_agent_type() -> Optional[str]:
    """Get agent_type (subagent hooks)"""
    return get_hook_field("agent_type")