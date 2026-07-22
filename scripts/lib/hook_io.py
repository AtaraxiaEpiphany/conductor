"""Shared library for hook JSON input/output handling

Provides unified interface for processing Claude Code hook JSON input/output,
following the Claude Code hook protocol.

JSON output format per the official docs:
  - Top-level: continue, stopReason, suppressOutput, systemMessage
  - Top-level decision: decision ("block") + reason
  - hookSpecificOutput: nested object requiring hookEventName, containing
    event-specific fields like additionalContext, permissionDecision,
    updatedToolOutput, etc.
"""

import json
import os
import sys
from typing import Any, Dict, Optional


# Cache for hook input to avoid multiple reads
_cached_hook_input: Optional[Dict[str, Any]] = None


def read_hook_input() -> Dict[str, Any]:
    """Read hook input JSON from stdin (cached)

    Also promotes the payload's ``cwd`` into ``$CLAUDE_PROJECT_DIR`` (once, via
    ``lib.env.infer_project_dir_from_payload``) so the rest of this process
    resolves the *project* for logs/telemetry — not the shared plugin dir. This
    is the single chokepoint where every hook reads its payload, so the ~10
    ``get_data_dir`` call sites need no changes. Best-effort; never raises.

    Returns:
        Parsed JSON data
    """
    global _cached_hook_input
    if _cached_hook_input is None:
        _cached_hook_input = json.load(sys.stdin)
        # Promote payload cwd → CLAUDE_PROJECT_DIR so logs land project-scoped.
        try:
            from lib.env import infer_project_dir_from_payload
            infer_project_dir_from_payload(_cached_hook_input)
        except Exception:
            pass
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
    hook_event_name: Optional[str] = None,
    *,
    permission_decision: Optional[str] = None,
    permission_decision_reason: Optional[str] = None,
    updated_input: Optional[Dict] = None,
    updated_tool_output: Any = None,
    retry: Optional[bool] = None,
) -> None:
    """Write hook output, following Claude Code hook protocol.

    Builds the correct JSON structure with hookSpecificOutput for event-specific
    fields.

    Args:
        additional_context: Context string injected into Claude's context window
        decision: Top-level decision ("block" to block the action)
        reason: Reason for decision (required when decision is "block")
        system_message: Warning message shown to the user
        suppress_output: Whether to suppress output from debug log
        stop_reason: Message shown to the user when continue is false
        hook_event_name: Hook event name (auto-detected if not provided)
        permission_decision: PreToolUse: "allow", "deny", "ask", or "defer"
        permission_decision_reason: Reason for permission decision
        updated_input: PreToolUse: modified tool input parameters
        updated_tool_output: PostToolUse: replacement tool output
        retry: PermissionDenied: whether the model may retry
    """
    output: Dict[str, Any] = {}

    # Resolve hook event name
    if hook_event_name is None:
        event_name = get_hook_event_name() or os.environ.get("HOOK_EVENT_NAME", "")
    else:
        event_name = hook_event_name

    # Build hookSpecificOutput if any event-specific fields are present
    has_specific_fields = any([
        additional_context,
        permission_decision is not None,
        permission_decision_reason is not None,
        updated_input is not None,
        updated_tool_output is not None,
        retry is not None,
    ])

    if has_specific_fields:
        specific = {"hookEventName": event_name}

        if additional_context:
            specific["additionalContext"] = additional_context
        if permission_decision is not None:
            specific["permissionDecision"] = permission_decision
        if permission_decision_reason is not None:
            specific["permissionDecisionReason"] = permission_decision_reason
        if updated_input is not None:
            specific["updatedInput"] = updated_input
        if updated_tool_output is not None:
            specific["updatedToolOutput"] = updated_tool_output
        if retry is not None:
            specific["retry"] = retry

        output["hookSpecificOutput"] = specific

    # Top-level decision control
    if decision:
        output["decision"] = decision
        if reason:
            output["reason"] = reason

    # Top-level universal fields
    if system_message:
        output["systemMessage"] = system_message
    if suppress_output:
        output["suppressOutput"] = suppress_output
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
    write_hook_output(additional_context=additional_context)


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
