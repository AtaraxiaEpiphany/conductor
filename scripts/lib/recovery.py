"""Shared vocabulary for the SubagentStop recovery flow.

"Recovery" here is the SubagentStop→PostToolUse pair: when a subagent stops
without its expected artifact, the SubagentStop hook (``on-subagent-stop.py``)
injects a ``[Conductor Recovery]`` turn keeping it alive, and the PostToolUse
filter (``filter-subagent-output.py``) detects whether that recovery turn
succeeded. This module is the single source of truth for the vocabulary both
sides share — the marker prefix, the result-file agent set, and the result-
block grammar — so the two hooks cannot drift apart (they previously each
hardcoded all three, and a change on one side silently broke the other).

Distinct from ``track_state.dispatch.cmd_recover`` (state-machine *resumption*
after an orchestrator interrupt — it shares the word "recover" but is unrelated
to this flow) and ``git_ops._recover_git_notes`` (git-notes repair).
"""
import re

# The bracketed token marking a hook-injected recovery turn. on-subagent-stop
# prefixes its block-reason with this; filter-subagent-output keys its recovery
# detection on the same prefix substring. If this changes, BOTH hooks must change
# together — hence the single constant.
RECOVERY_MARKER = "[Conductor Recovery]"

# Agents whose completion is gated on a fresh ``.conductor/result.json`` (written
# by the agent, consumed by dispatch-finalize). on-subagent-stop triggers a
# recovery turn when this file is stale/missing for these agents; the PostToolUse
# filter routes a missing result block to dispatch-finalize for the SAME set.
# Defined once here so the two hooks agree on who is a "result-file agent" —
# adding one is a single-line change (Tier 2 #21 will extend this, noting those
# agents' finalize path differs).
RESULT_FILE_AGENT_TYPES = frozenset({"task-executor", "explorer"})

# The result-block grammar (e.g. ``---TASK RESULT---`` / ``---END RESULT---``,
# ``---END CHECKPOINT RESULT---``). on-subagent-stop checks an agent emitted its
# close tag; filter-subagent-output extracts the full open+close block. Both
# derive from this one definition of what a result block looks like.
#
# The class allows DIGITS as well as letters: test-runner's open tag is
# ``---L1 VERIFY RESULT---`` and the ``1`` is not in ``[A-Z ]``. A letters-only
# class made that block invisible to extract_result_blocks — every test-runner
# result was replaced with the generic no-result warning and the phase-checker
# fan-out could not parse L1_VERIFY_STATUS. Keep digits in.
RESULT_END_TAG = r"---END [A-Z0-9 ]+---"
RESULT_BLOCK_PATTERN = rf"---[A-Z][A-Z0-9 ]+---.*?{RESULT_END_TAG}"

# Bounded recovery: how many SubagentStop recovery turns a result-file agent
# gets before the hook stops forcing them and lets dispatch-finalize synthesize
# a result (→ ``_do_fail`` retry queue). Caps a crash-looping agent at this many
# extra turns instead of burning its whole ``maxTurns`` budget before Layer-2
# synthesis engages. Counted per locked task and reset when a new task is locked
# (see ``track_state.mutations._do_lock`` / ``increment_recovery_turns``). Lives
# here alongside ``RESULT_FILE_AGENT_TYPES`` so the SubagentStop hook and the
# state-machine counter share one vocabulary.
MAX_RECOVERY_TURNS = 2
RECOVERY_TURN_FIELD = "recovery_turns"  # track-state.json key on the locked task
