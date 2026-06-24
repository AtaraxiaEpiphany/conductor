r"""Tests for on-subagent-start.py — the SubagentStart result-format reminder.

Two drift guards: (1) every agent registered in the SubagentStart matcher
(hooks.json) has a reminder, so a newly-added agent can't silently start with no
result-format hint; (2) an unknown agent gets no context.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "on_subagent_start", _scripts / "on-subagent-start.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_REMINDERS = _mod.AGENT_REMINDERS

_HOOK = _scripts / "on-subagent-start.py"
_HOOKS_JSON = Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"


def _subagent_start_agents():
    """Agent types named in any SubagentStart matcher of hooks.json."""
    data = json.loads(_HOOKS_JSON.read_text())
    for entry in data["hooks"]["SubagentStart"]:
        for agent in entry["matcher"].split("|"):
            yield agent.strip()


def _run(agent_type: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps({"agent_type": agent_type}),
        capture_output=True, text=True,
    )
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


class SubagentStartReminderTests(TestCase):
    def test_every_matched_agent_has_a_reminder(self):
        """Drift guard: an agent added to the SubagentStart matcher must get a
        reminder, else it starts with no result-format hint."""
        matched = set(_subagent_start_agents())
        missing = matched - set(_REMINDERS)
        self.assertFalse(missing, f"SubagentStart agents without a reminder: {missing}")

    def test_each_reminder_names_a_result_block(self):
        for agent, reminder in _REMINDERS.items():
            self.assertIn("Result format:", reminder)
            self.assertIn("---", reminder, f"{agent} reminder has no delimiter")

    def test_known_agent_gets_its_reminder(self):
        out = _run("task-executor")
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("TASK RESULT", ctx)

    def test_unknown_agent_gets_no_context(self):
        out = _run("mystery-agent")
        self.assertNotIn("hookSpecificOutput", out)


if __name__ == "__main__":
    main()
