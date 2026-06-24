#!/usr/bin/env python3
"""SubagentStop hook: keep a subagent running when it stopped without a result.

A subagent signals completion through ONE deterministic channel:

- **result-file agents** (task-executor, explorer) write a fresh
  ``.conductor/result.json`` via ``track-state write-result``. A missing fresh
  file means the agent exhausted turns or crashed before its result step.
- **stdout-block agents** (phase-checker) emit a ``---END RESULT---`` close tag
  (no result file). A missing close tag means it stopped mid-protocol.

In either case the hook returns ``decision: "block"`` with a `reason` that is
delivered to the subagent as its next instruction, giving it one recovery turn.

Failures are NOT detected from prose. A result-file agent that wrote a FAILURE
result.json has signalled correctly — the orchestrator's retry/skip path reads
the file. Prose failure-detection was removed: it was the source of the
``[:2000]`` head-truncation bug (the close tag sits at the END of a turn) and
the ``SAFE_CONTEXT`` false-positive suppression (regex-mining the word "error"
in prose). result.json is deterministic; prose is not.

Per the hook protocol:
  "SubagentStop hooks use the same decision control format as Stop hooks.
   They do not support additionalContext. Returning decision: 'block' with a
   reason keeps the subagent running and delivers reason to the subagent as its
   next instruction."
"""

import re
import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.logging import init_logging, log_entry
from lib.result_probe import fresh_result_exists
from lib.recovery import (
    RECOVERY_MARKER, RESULT_FILE_AGENT_TYPES, RESULT_END_TAG,
)


# Per-agent recovery instructions for result-file agents. The GATE (which
# agents count as result-file agents) is the shared RESULT_FILE_AGENT_TYPES from
# lib.recovery — these strings are just the hook UI appended after the shared
# RECOVERY_MARKER lead. The guard keeps the two in sync, so adding an agent type
# in the shared set without an instruction here (or vice versa) fails loudly at
# import instead of raising a cryptic KeyError when that agent crashes.
_RESULT_FILE_INSTRUCTIONS = {
    "task-executor": (
        "IMMEDIATELY call track-state write-result (Section 6.0) and print "
        "the ---TASK RESULT--- block. Report FAILURE if you cannot complete."
    ),
    "explorer": (
        "IMMEDIATELY call track-state write-result (Section 5.1) and print "
        "the ---TASK RESULT--- block. Report FAILURE if you cannot complete."
    ),
}
assert set(_RESULT_FILE_INSTRUCTIONS) == RESULT_FILE_AGENT_TYPES, (
    "RESULT_FILE_AGENT_TYPES (lib.recovery) and _RESULT_FILE_INSTRUCTIONS keys "
    "drifted — a result-file agent lacks a recovery instruction or vice versa."
)

# stdout-block agents (no result file) → gated on the presence of a close tag.
# Instruction appended after "[Conductor Recovery] You stopped without producing
# a result block."
STDOUT_BLOCK_AGENTS = {
    "phase-checker": (
        "IMMEDIATELY print the ---CHECKPOINT RESULT--- block (Section 8.0). "
        "Report STATUS: FAILED with a one-line FAILURE_REASON if the checkpoint "
        "protocol did not complete; do NOT create a checkpoint commit on this "
        "recovery turn."
    ),
}

# Matches any conductor result-block close tag, e.g. ---END RESULT---,
# ---END CHECKPOINT RESULT---, ---END TASK RESULT---. The grammar is shared with
# filter-subagent-output's block extractor via RESULT_END_TAG (lib.recovery) so
# the two hooks agree on what a result block looks like. Emitted at the END of a
# turn, so the scan must cover the full message — never a head-truncated prefix
# (see _has_result_block).
_RESULT_END_PATTERN = re.compile(RESULT_END_TAG)


def _has_result_block(message: str) -> bool:
    """True if `message` contains a conductor result-block close tag.

    Result blocks are emitted at the END of a subagent's turn. Scanning only the
    first N chars (the prior ``[:2000]`` head truncation) missed the close tag
    on any normal-length turn (>2KB of explanation/diffs before the block), so a
    successful agent that DID print its block was falsely flagged as "stopped
    without producing a result block" and force-blocked for a recovery turn.
    Search the full message instead — a single turn is bounded and the regex is
    cheap; correctness beats the micro-optimization that broke it.
    """
    if not message:
        return False
    return bool(_RESULT_END_PATTERN.search(message))


def _block_recovery(agent_type: str, lead: str, instruction: str,
                    log_file, session_id: str, tag: str) -> None:
    """Log the recovery event and return decision:block + reason."""
    log_entry(Path(log_file).parent / "subagent-failures.log",
              f"session={session_id} agent={agent_type} {tag}")
    write_hook_output(decision="block", reason=f"{lead} {instruction}")


def main():
    """Main hook function"""
    input_data = read_hook_input()
    agent_type = input_data.get("agent_type", "")
    session_id = input_data.get("session_id", "")
    cwd = input_data.get("cwd") or str(Path.cwd())
    last_message = input_data.get("last_assistant_message", "")

    log_file = init_logging("on-subagent-stop")
    log_entry(log_file, f"session={session_id} agent={agent_type} event=subagent_stop")

    # result-file agents: a fresh result.json is the single completion signal.
    # A written FAILURE result.json is a valid signal — do NOT block (the
    # orchestrator's retry/skip path reads it). Only a missing file means the
    # agent never reached its result step.
    if agent_type in RESULT_FILE_AGENT_TYPES:
        if not fresh_result_exists(cwd):
            _block_recovery(
                agent_type,
                f"{RECOVERY_MARKER} You stopped without writing a result.",
                _RESULT_FILE_INSTRUCTIONS[agent_type],
                log_file, session_id, "no_result_file_detected",
            )
            return
        write_hook_output()  # result.json present → allow normal stop
        return

    # stdout-block agents: must emit their close tag.
    if agent_type in STDOUT_BLOCK_AGENTS:
        if not _has_result_block(last_message):
            _block_recovery(
                agent_type,
                f"{RECOVERY_MARKER} You stopped without producing a result block.",
                STDOUT_BLOCK_AGENTS[agent_type],
                log_file, session_id, "no_result_block_detected",
            )
            return
        write_hook_output()
        return

    # All other agents (registered async in hooks.json): no recovery contract.
    # Allow the stop; failure/recovery for these is advisory-only.
    write_hook_output()


if __name__ == "__main__":
    main()
