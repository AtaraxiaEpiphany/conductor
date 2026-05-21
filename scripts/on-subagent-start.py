#!/usr/bin/env python3
"""SubagentStart hook: inject execution reminders into subagent context.

Reads hook input from stdin, outputs JSON with additionalContext.
"""

import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output


# Minimal result-format reminders. Agent markdown files already define full role behavior.
# Only the delimiter format is reinforced here — filter-subagent-output.py depends on it.
AGENT_REMINDERS = {
    "task-executor": "[Conductor] Result format: ---TASK RESULT--- ... ---END RESULT---",
    "code-reviewer": "[Conductor] Result format: ---REVIEW RESULT--- ... ---END REVIEW RESULT---",
    "explorer": "[Conductor] Result format: ---TASK RESULT--- ... ---END RESULT---",
    "phase-checker": "[Conductor] Result format: ---CHECKPOINT RESULT--- ... ---END RESULT---",
    "doc-syncer": "[Conductor] Result format: ---DOC SYNC RESULT--- ... ---END RESULT---",
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

    if reminder:
        write_simple_output(additional_context=reminder)
    else:
        write_simple_output()


if __name__ == "__main__":
    main()