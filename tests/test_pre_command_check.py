"""Tests for pre-command-check.py PreToolUse hook enforcement policy.

Direct track-state.json modification is DENIED (hard block); dangerous git,
non-conventional commits, and lock violations stay advisory (ask); innocuous
commands pass. Critically verifies the deny fires *before* the lock-violation
ask even when an in_progress task is present — the ordering bug that would
otherwise let `rm track-state.json` slip through as a mere "ask".
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pre-command-check.py"


def run_hook(command, cwd):
    """Invoke the PreToolUse hook with a Bash command; return parsed stdout JSON."""
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
    }
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"hook exited {proc.returncode}: {proc.stderr}"
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def decision(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


class TestPreCommandCheck(TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        # A registered track with an in_progress task (exercises lock-violation path).
        (self.d / "track-a").mkdir()
        (self.d / "track-a" / "track-state.json").write_text(json.dumps({
            "track_id": "t", "phases": [{"name": "P1", "tasks": [
                {"name": "T1", "status": "in_progress"}]}],
        }))
        (self.d / "conductor").mkdir()
        (self.d / "conductor" / "tracks.md").write_text("# Tracks\n\n- [Track A](track-a)\n")

    def _expect(self, command, want):
        out = run_hook(command, self.d)
        got = decision(out)
        self.assertEqual(got, want, f"{command!r}: expected {want}, got {got} ({out})")

    # --- direct track-state.json modification → DENY ---
    def test_rm_track_state_json_denied_despite_in_progress(self):
        # Reorder guard: lock-violation (ask) must NOT preempt the deny, even
        # though the track has an in_progress task and the command contains 'rm'.
        self._expect("rm track-a/track-state.json", "deny")

    def test_sed_track_state_denied(self):
        self._expect("sed -i 's/a/b/' track-a/track-state.json", "deny")

    def test_git_rm_track_state_json_denied(self):
        self._expect("git rm track-a/track-state.json", "deny")

    def test_deny_includes_reason_and_alternative(self):
        out = run_hook("rm track-a/track-state.json", self.d)
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        reason = spec.get("permissionDecisionReason", "")
        ctx = spec.get("additionalContext", "")
        # Reason points the user/agent at the sanctioned alternatives.
        self.assertIn("track-state", reason.lower())
        self.assertTrue("validate --fix" in ctx or "/conductor:revert" in ctx,
                        f"deny context should name a sanctioned alternative: {ctx!r}")

    # --- other checks stay advisory (ask) ---
    def test_dangerous_git_is_ask(self):
        self._expect("git rebase main", "ask")

    def test_non_conventional_commit_is_ask(self):
        self._expect('git commit -m "not conventional"', "ask")

    # --- legit commands pass through (allow) ---
    def test_track_state_cli_command_allowed(self):
        # Legit CLI usage must not trip the deny (no ".json" tampering pattern).
        self._expect("track-state complete my-track 1 1", "allow")

    def test_git_status_allowed(self):
        self._expect("git status", "allow")
