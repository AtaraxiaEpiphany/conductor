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
        self.assertEqual("deny", self._decision(out))

    def test_no_space_good_message_is_allowed(self):
        out = _run('git commit -m"feat(auth): add login"')
        self.assertIsNone(self._decision(out))

    def test_spaced_bad_message_still_flagged(self):
        # Regression guard for the original whitespace path.
        out = _run('git commit -m "bad subject line"')
        self.assertEqual("deny", self._decision(out))

    def test_non_commit_command_allowed(self):
        out = _run("echo hello")
        self.assertIsNone(self._decision(out))

    def test_word_with_dash_m_not_treated_as_flag(self):
        # `-m` inside a filename must not trigger the gate.
        out = _run("git commit file-m.txt")
        self.assertIsNone(self._decision(out))

    def test_shell_broken_empty_parens_is_denied(self):
        # The orchestrator-placeholder bug: `git commit -m ()` is a bash syntax
        # error ("syntax error near unexpected token `('"). V10 would only ask;
        # the hard-deny blocks it outright and tells the model to quote the msg.
        out = _run("git commit -m ()")
        self.assertEqual("deny", self._decision(out))

    def test_shell_broken_placeholder_is_denied(self):
        # Unfilled `<commit_msg>` placeholder, unquoted → redirection / broken.
        out = _run("git commit -m <commit_msg>")
        self.assertEqual("deny", self._decision(out))

    def test_unquoted_paren_message_is_denied_not_just_asked(self):
        # Unquoted `feat(auth): …` carries parens → shell-broken. Deny (must be
        # quoted), not the V10 soft-ask.
        out = _run('git commit -m feat(auth): login')
        self.assertEqual("deny", self._decision(out))

    def test_quoted_message_not_denied_as_broken(self):
        # Quoted parens are shell-safe — must reach V10 (valid → allowed).
        out = _run('git commit -m "feat(auth): add login"')
        self.assertIsNone(self._decision(out))


if __name__ == "__main__":
    main()
