"""End-to-end test for the V10 commit-message gate in pre-command-check.py.

Regression: the PreToolUse gate used ``-m\\s`` (whitespace required after -m),
so the most common shell shorthand — ``git commit -m"msg"`` (no space) — never
matched and skipped validation entirely. The fix widens the gate anchor to the
same set the extractor handles. This runs the real hook via subprocess and
asserts the permission decision, closing the loop at the hook level (not just
the validator unit).
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional
from unittest import TestCase, main

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pre-command-check.py"


def _run(command: str) -> dict:
    """Run the PreToolUse hook with a Bash command; return parsed stdout JSON."""
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": "/tmp",  # no conductor track → state-lock/tamper checks no-op
    })
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=payload, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"hook failed: {result.stderr}"
    return json.loads(result.stdout)


class PreCommandCheckV10GateTests(TestCase):
    def _decision(self, out: dict) -> Optional[str]:
        return out.get("hookSpecificOutput", {}).get("permissionDecision")

    def test_no_space_bad_message_is_flagged(self):
        # THE BYPASS: -m"…" with no space used to skip validation.
        out = _run('git commit -m"random wip junk"')
        self.assertEqual("ask", self._decision(out))

    def test_no_space_good_message_is_allowed(self):
        out = _run('git commit -m"feat(auth): add login"')
        self.assertIsNone(self._decision(out))

    def test_spaced_bad_message_still_flagged(self):
        # Regression guard for the original whitespace path.
        out = _run('git commit -m "bad subject line"')
        self.assertEqual("ask", self._decision(out))

    def test_non_commit_command_allowed(self):
        out = _run("echo hello")
        self.assertIsNone(self._decision(out))

    def test_word_with_dash_m_not_treated_as_flag(self):
        # `-m` inside a filename must not trigger the gate.
        out = _run("git commit file-m.txt")
        self.assertIsNone(self._decision(out))


if __name__ == "__main__":
    main()
