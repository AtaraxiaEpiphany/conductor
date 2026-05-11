#!/usr/bin/env python3
"""SubagentStart hook: inject execution reminders into subagent context.

Reads hook input from stdin, outputs JSON with additionalContext.
"""

import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output


AGENT_REMINDERS = {
    "task-executor": (
        "[Conductor] You are a task-executor. Follow TDD: Red -> Green -> Refactor. "
        "Validate every tool call. Report in ---TASK RESULT--- format."
    ),
    "code-reviewer": (
        "[Conductor] You are a code-reviewer. You are READ-ONLY for application code. "
        "Report in ---REVIEW RESULT--- format."
    ),
    "explorer": (
        "[Conductor] You are an explorer. You are READ-ONLY. "
        "Produce exploration.md as file-bridge. Report in ---TASK RESULT--- format."
    ),
    "phase-checker": (
        "[Conductor] You are a phase-checker. Execute full checkpoint protocol. "
        "Report in ---CHECKPOINT RESULT--- format."
    ),
    "doc-syncer": (
        "[Conductor] You are a doc-syncer. Only targeted updates with user confirmation. "
        "Report in ---DOC SYNC RESULT--- format."
    ),
    "skip-analyst": (
        "[Conductor] You are a skip-analyst. You are READ-ONLY. "
        "Be conservative — when in doubt, recommend pause_and_escalate."
    ),
    "spec-planner": (
        "[Conductor] You are a spec-planner. Write spec.md and plan.md. "
        "Return compact summary in ---SPEC PLAN RESULT--- format."
    ),
    "spec-reviewer": (
        "[Conductor] You are a spec-reviewer. Present summaries, handle revisions, "
        "keep full files out of orchestrator context. Report in ---REVIEW RESULT--- format."
    ),
    "project-analyzer": (
        "[Conductor] You are a project-analyzer. You are READ-ONLY. "
        "Return analysis in ---ANALYSIS RESULT--- format."
    ),
}


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    agent_type = input_data.get("agent_type", "")

    # Get reminder for this agent type
    reminder = AGENT_REMINDERS.get(agent_type, "")

    if reminder:
        write_simple_output(additional_context=reminder)
    else:
        write_simple_output()


if __name__ == "__main__":
    main()