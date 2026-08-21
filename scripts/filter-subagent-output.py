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
from typing import Any, Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.constants import RECOVERY_SUCCESS_PATTERNS
from lib.result_probe import fresh_result_exists
from lib.recovery import (
    RECOVERY_MARKER, RESULT_BLOCK_PATTERN,
    parse_result_block,
)

# The result-block GRAMMAR (open + close) is shared with on-subagent-stop via
# lib.recovery so the two hooks cannot disagree on what a result block looks
# like. WHO has a contract is roster-driven: the agent-roster registry's
# ``result_file_agents()`` (rows with ``recovery: "result-file"``) — the same
# set on-subagent-stop gates its fresh-result recovery on, so a roster row
# changes both hooks at once.
#
# Agents whose followup is `dispatch-finalize` (the result-file rows)
# synthesize a missing result from result.json / locked task state — for these a
# missing result block is recoverable. Other agents have no dispatch-finalize
# step — a missing block means lost status the orchestrator must inspect
# manually.


def _result_file_agents():
    """The roster's result-file agent set, or ``()`` when unimportable.

    Function-level import (hooks run with ``scripts/`` on ``sys.path``);
    ``()`` fail-opens to the generic no-recovery messaging, never a crash.
    """
    try:
        from track_state import agent_roster
        return agent_roster.result_file_agents()
    except Exception:
        return ()

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

# Structured-verdict statuses that need NO routing nudge: the passing set plus the
# benign ``skipped`` (code-free phase / no spec) and ``warn`` (advisory — the
# checker proceeds). Anything else (FAILED / error / an unrecognized non-passing
# status) gets a loop-back nudge so the orchestrator branches on it.
_NO_ROUTING_STATUSES = frozenset({
    "PASSED", "PASSED.", "OK", "SUCCESS", "DONE", "SKIPPED", "WARN",
})


def extract_result_blocks(response: str) -> Optional[str]:
    """Extract result blocks from subagent response."""
    matches = re.findall(RESULT_BLOCK_PATTERN, response, re.DOTALL)
    if matches:
        return '\n\n'.join(m.strip() for m in matches)
    return None


def _extract_agent_text(tool_response) -> str:
    """Pull the subagent's textual output out of an Agent ``tool_response``.

    The Agent PostToolUse payload is a dict shaped like::

        {"agentId": "...", "agentType": "...",
         "content": [{"text": "...the agent's final message..."}, ...]}

    There is **no** top-level ``result`` key (that was the prior bug —
    ``response.get("result")`` always returned None and the hook fell through
    to ``json.dumps(tool_response)``, garbling every downstream
    result-block / failure / recovery scan). Text lives in the ``content``
    blocks' ``text`` field. Falls back to ``result`` (alternate shape) or a
    JSON dump so the hook never silently emits an empty string.
    """
    if not isinstance(tool_response, dict):
        return str(tool_response) if tool_response else ""

    content = tool_response.get("content")
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict)]
        if any(p.strip() for p in parts):
            return "\n".join(parts)

    if "result" in tool_response:
        r = tool_response["result"]
        return r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)

    return json.dumps(tool_response, ensure_ascii=False)


def _agent_result_object(tool_response: Any, trimmed_text: str) -> dict:
    """Wrap trimmed text as a schema-valid Agent ``updatedToolOutput`` object.

    Claude Code validates ``updatedToolOutput`` against the Agent tool's output
    shape and REJECTS a bare string — Zod reports ``invalid_type: expected
    object, received string`` and discards the replacement, so the verbose
    original reaches the caller's context (confirmed 1:1 against live
    PostToolUse fires in session debug logs; the prior bare-string emission was
    silently non-functional). The runtime's own ``tool_response`` is already a
    valid instance of the Agent output shape (it produced it), so echoing it
    and swapping only ``content`` for the trimmed block preserves ``agentId`` /
    ``status`` / ``usage`` / telemetry while satisfying the schema — whatever
    the exact (status-discriminated union) shape, every non-content field came
    from a known-valid instance.
    """
    base = dict(tool_response) if isinstance(tool_response, dict) else {}
    base["content"] = [{"type": "text", "text": trimmed_text}]
    return base


def detect_recovery_context(response: str) -> Optional[str]:
    """Check for recovery success after a prior failure."""
    if RECOVERY_MARKER not in response:
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

    # Keep the raw tool_response (a valid Agent-output instance) so the
    # replacement object below can echo its agentId/status/usage fields.
    raw_response = input_data.get("tool_response", "")
    if isinstance(raw_response, dict):
        response = _extract_agent_text(raw_response)
    elif isinstance(raw_response, str):
        response = raw_response
    else:
        response = str(raw_response) if raw_response else ""
    if not response:
        write_hook_output()
        return

    # Resolve agent type from the Agent tool input (PostToolUse payload).
    # Values may be bare ("phase-checker") or namespaced ("conductor:phase-checker",
    # "code-simplifier:code-simplifier") — normalize to the bare name.
    tool_input = input_data.get("tool_input") or {}
    raw_type = tool_input.get("subagent_type", "") if isinstance(tool_input, dict) else ""
    agent_type = raw_type.split(":")[-1]
    uses_finalize = agent_type in _result_file_agents()

    # --- Responsibility 1: Extract structured result blocks ---
    result = extract_result_blocks(response)

    if result:
        updated_output = result
    elif uses_finalize:
        updated_output = NO_RESULT_MESSAGE
    else:
        updated_output = NO_RESULT_MESSAGE_GENERIC

    # --- Responsibility 2: recovery advisory context ---
    # Recovery detection is gated on the deterministic "[Conductor Recovery]"
    # marker that on-subagent-stop injects as its block reason — NOT free-form
    # prose. Prose failure-mining (FAILURE_PATTERNS over agent text) was removed
    # to match on-subagent-stop's policy: it was a false-positive source (see the
    # on-subagent-stop.py docstring on why prose detection was dropped), and
    # failure status already travels deterministically via the result block above
    # and result.json. Mining agent prose for "failure"/"error" is unreliable.
    extra_context = detect_recovery_context(response)

    # Structured verdict (Phase 2 control-flow backbone): when the result block
    # carries a fenced ```json verdict object, surface its status deterministically
    # so the orchestrator's loop-back edge branches on ``status`` instead of
    # regex-mining ``STATUS:`` prose. Fired only for a status that needs a ROUTING
    # DECISION (FAILED / error / an unrecognized non-passing status) — ``skipped``
    # (a code-free phase or no spec/ACs) and ``warn`` (advisory — the checker
    # proceeds) are benign and need no loop-back nudge; the earlier form fired for
    # any non-passing status and prompted a premature re-dispatch/halt of the
    # verifier. Additive: APPENDED to a concurrently-relevant recovery advisory
    # (not assigned over it), so the [Conductor Recovery] routing context is not
    # silently dropped. Absent/missing JSON falls through to the advisories below.
    verdict = parse_result_block(response)
    if verdict:
        status = str(verdict.get("status", "")).upper()
        if status and status not in _NO_ROUTING_STATUSES:
            reason = verdict.get("failure_reason") or verdict.get("reason") or ""
            nudge = (
                f"[Conductor] Structured verdict: status={status}"
                + (f" — {reason}" if reason else "")
                + ". Branch on this status for routing (re-dispatch / halt / advance)."
            )
            extra_context = f"{extra_context}\n{nudge}" if extra_context else nudge

    # If no structured result block AND no recovery advisory, check for result file.
    # The result.json freshness probe only applies to dispatch-finalize agents
    # (task-executor/explorer write result.json); other agents never do, so a
    # missing block for them is simply a lost-status warning.
    if result is None and extra_context is None:
        if uses_finalize:
            cwd = input_data.get("cwd") or str(Path.cwd())
            extra_context = NO_RESULT_OK if fresh_result_exists(cwd) else NO_RESULT_WARN
        else:
            extra_context = NO_RESULT_WARN_GENERIC

    write_hook_output(
        updated_tool_output=_agent_result_object(raw_response, updated_output),
        additional_context=extra_context,
    )


if __name__ == "__main__":
    main()
