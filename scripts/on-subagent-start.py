#!/usr/bin/env python3
"""SubagentStart hook: inject the safety floor + result-format reminder.

Reads hook input from stdin, outputs JSON with additionalContext. For every known
Conductor subagent the output is: the universal safety floor
(``runtime/subagent-firewall.md``) followed by the agent's result-format reminder.
Unknown agent types get no context (the SubagentStart matcher gates which agents
fire this hook at all).
"""

import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output


# The universal safety floor injected ahead of every agent's reminder. Single
# source of truth for the cross-agent safety baseline; curated to hold only what
# every subagent must respect (it deliberately omits orchestrator-only rules like
# F1's lock mechanics and the V5/V9 rules that contradict task-executor's workflow).
FLOOR_FILE = Path(__file__).parent.parent / "runtime" / "subagent-firewall.md"


def _load_safety_floor() -> str:
    """Load the universal subagent safety floor.

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
    "doc-syncer": "[Conductor] Result format: ---DOC SYNC RESULT--- ... ---END RESULT---",
    "doc-linter": "[Conductor] Result format: ---DOC LINT RESULT--- ... ---END RESULT---",
    "skip-analyst": "[Conductor] Result format: ---SKIP ANALYSIS--- ... ---END ANALYSIS---",
    "spec-planner": "[Conductor] Result format: ---SPEC PLAN RESULT--- ... ---END SPEC PLAN RESULT---",
    "spec-reviewer": "[Conductor] Result format: ---REVIEW RESULT--- ... ---END REVIEW RESULT---",
    "project-analyzer": "[Conductor] Result format: ---ANALYSIS RESULT--- ... ---END ANALYSIS RESULT---",
}


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    agent_type = input_data.get("agent_type", "")

    # Get reminder for this agent type
    reminder = AGENT_REMINDERS.get(agent_type, "")

    if not reminder:
        # Unknown agent type — emit no context (the matcher gates this in practice).
        write_simple_output()
        return

    # Prepend the universal safety floor to the agent's result-format reminder.
    floor = _load_safety_floor()
    context = f"{floor}\n\n{reminder}" if floor else reminder
    write_simple_output(additional_context=context)


if __name__ == "__main__":
    main()