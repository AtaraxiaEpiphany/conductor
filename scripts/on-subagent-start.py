#!/usr/bin/env python3
"""SubagentStart hook: inject the safety floor + result-format reminder.

Reads hook input from stdin, outputs JSON with additionalContext. For every known
Conductor subagent the output is: the universal safety floor
(``runtime/subagent-firewall.md``), then the agent's result-format reminder.
Unknown agent types get no context (the SubagentStart matcher gates which agents
fire this hook at all).

For retry-context agents (task-executor), a third piece is appended when the
locked task has a prior failed attempt: the most recent ``### Attempt ❌`` record
from its handoff. This is the deterministic, can't-be-skipped counterpart to the
agent's own Layer 3.R load — even if the orchestrator under-reports retry status,
the prior failure reason / suggested next step reaches the retry agent here.
"""

import functools
import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.locked_task import resolve as resolve_locked_task


# The universal safety floor injected ahead of every agent's reminder. Single
# source of truth for the cross-agent safety baseline; curated to hold only what
# every subagent must respect (it deliberately omits orchestrator-only rules like
# F1's lock mechanics and the V5/V9 rules that contradict task-executor's workflow).
FLOOR_FILE = Path(__file__).parent.parent / "runtime" / "subagent-firewall.md"


@functools.lru_cache(maxsize=1)
def _load_safety_floor() -> str:
    """Load the universal subagent safety floor.

    Cached for the process: ``subagent-firewall.md`` is a static curatorial doc
    that only changes across plugin upgrades, so it is read once and reused for
    every SubagentStart fire in the session (SubagentStart fires once per
    subagent dispatch — the per-call disk read it replaced was new hot-path I/O).

    Returns '' if the file is missing/unreadable, after warning on stderr so the
    degradation is visible rather than silent (mirrors session-start.py's handling
    of an unreadable core-contract.md). A missing floor must not also drop the
    result-format reminder, so callers fall back to reminder-only on ''.
    """
    try:
        return FLOOR_FILE.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(
            f"[conductor on-subagent-start] WARNING: runtime/subagent-firewall.md "
            f"unreadable ({e}); injecting result-format reminder only.",
            file=sys.stderr,
        )
        return ""


# Result-format reminders. Agent markdown files already define full role behavior;
# only the delimiter format is reinforced here — filter-subagent-output.py depends
# on it being present in the subagent's emitted output.
AGENT_REMINDERS = {
    "task-executor": "[Conductor] Result format: ---TASK RESULT--- ... ---END RESULT---",
    "code-reviewer": "[Conductor] Result format: ---REVIEW RESULT--- ... ---END REVIEW RESULT---",
    "explorer": "[Conductor] Result format: ---TASK RESULT--- ... ---END RESULT---",
    "phase-checker": "[Conductor] Result format: ---CHECKPOINT RESULT--- ... ---END RESULT---",
    "corpus-writer": "[Conductor] Result format: ---DOC SYNC RESULT--- ... ---END RESULT---",
    "wiki-synthesizer": "[Conductor] Result format: ---DOC SYNC RESULT--- ... ---END RESULT---",
    "doc-linter": "[Conductor] Result format: ---DOC LINT RESULT--- ... ---END RESULT---",
    "skip-analyst": "[Conductor] Result format: ---SKIP ANALYSIS--- ... ---END ANALYSIS---",
    "spec-planner": "[Conductor] Result format: ---SPEC PLAN RESULT--- ... ---END SPEC PLAN RESULT---",
    "spec-reviewer": "[Conductor] Result format: ---REVIEW RESULT--- ... ---END REVIEW RESULT---",
    "project-analyzer": "[Conductor] Result format: ---ANALYSIS RESULT--- ... ---END ANALYSIS RESULT---",
    "wiki-differ": "[Conductor] Result format: ---WIKI DIFF RESULT--- ... ---END RESULT---",
    "wiki-researcher": "[Conductor] Result format: ---WIKI RESEARCH RESULT--- ... ---END RESULT---",
    "refuter": "[Conductor] Result format: ---REFUTATION RESULT--- ... ---END RESULT---",
}


# Agents whose re-dispatch carries prior-failure context. task-executor is THE
# retry agent (attempt 2+); explorer and the stdout-block agents are dispatched
# fresh, so they are excluded — injecting a stale failure record into a non-retry
# dispatch would mislead. Add here if another agent gains retry semantics.
_RETRY_AGENTS = {"task-executor"}

_RETRY_LEAD = (
    "[Conductor Retry] A prior attempt at this task failed — its handoff record "
    "is below. Do NOT repeat the same approach; heed Failure Reason and "
    "Suggested Next Step. (Full history: track-state get-handoff, Layer 3.R.)"
)


def _latest_failure_attempt(content):
    """Verbatim text of the most recent ``### Attempt ... ❌`` block, or None.

    A block runs from its ``### Attempt`` heading to the next ``## `` / ``### ``
    heading. Only the LATEST block qualifies, and only if it is a FAILURE (the
    heading carries ❌): a trailing ✅ means the task ultimately completed and
    would not be re-dispatched, so surfacing an older failure would mislead.
    """
    blocks = []
    cur = None
    for line in content.split("\n"):
        if line.startswith("### Attempt "):
            if cur is not None:
                blocks.append(cur)
            cur = [line]
        elif cur is not None:
            if line.startswith("## ") or line.startswith("### "):
                blocks.append(cur)
                cur = None
            else:
                cur.append(line)
    if cur is not None:
        blocks.append(cur)
    if not blocks:
        return None
    last = blocks[-1]
    if "❌" not in last[0]:
        return None
    return "\n".join(last).strip()


def _retry_context(cwd, agent_type):
    """Prior-failure context to inject for a retrying agent, or None.

    Resolves the locked in_progress task, reads its handoff (scoped to the
    locked subtask when applicable), and returns the most recent failure record.
    None when: the agent is not a retry-context agent, no task is locked, no
    handoff exists, or the latest attempt was not a failure.

    Fail-safe: any error → None. This probe is advisory and must never break the
    floor/reminder injection that is the hook's primary contract — a retry nudge
    that risks the safety floor is worse than none.
    """
    if agent_type not in _RETRY_AGENTS:
        return None
    try:
        locked = resolve_locked_task(cwd)
        if locked is None:
            return None
        track_dir, p, t, s = locked
        # Lazy import: track_state is heavier than lib.* and only needed on the
        # retry path — mirrors on-subagent-stop's lazy track_state.mutations import.
        from track_state.handoff import get_handoff_content
        content = get_handoff_content(track_dir, p, t, s)
        if not content:
            return None
        block = _latest_failure_attempt(content)
        if not block:
            return None
        return f"{_RETRY_LEAD}\n\n{block}"
    except Exception:
        return None


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    agent_type = input_data.get("agent_type", "")
    cwd = input_data.get("cwd") or str(Path.cwd())

    # Get reminder for this agent type
    reminder = AGENT_REMINDERS.get(agent_type, "")

    if not reminder:
        # Unknown agent type — emit no context (the matcher gates this in practice).
        write_simple_output()
        return

    # Safety floor first, then the result-format reminder, then any retry-context
    # nudge (advisory; None for fresh tasks and non-retry agents). Order matters:
    # the floor must lead and the reminder must precede any appended retry block.
    floor = _load_safety_floor()
    retry = _retry_context(cwd, agent_type)
    parts = [p for p in (floor, reminder, retry) if p]
    write_simple_output(additional_context="\n\n".join(parts))


if __name__ == "__main__":
    main()