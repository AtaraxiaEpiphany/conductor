"""Tests for scripts/on-subagent-stop.py (SubagentStop recovery hook).

Runs the hook as a subprocess (as Claude Code invokes it) with controlled
stdin JSON and asserts on the `decision` field. Logs are redirected to a temp
CLAUDE_PLUGIN_DATA so the repo is not polluted.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "on-subagent-stop.py"


def run_hook(agent_type: str, message: str, tmp_path: Path) -> dict:
    """Run on-subagent-stop.py with a SubagentStop payload; return stdout JSON."""
    payload = json.dumps({
        "hook_event_name": "SubagentStop",
        "agent_type": agent_type,
        "session_id": "test-session",
        "last_assistant_message": message,
    })
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path)  # keep logs out of the repo
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode in (0, 2), f"unexpected exit {result.returncode}: {result.stderr}"
    return json.loads(result.stdout)


def is_blocked(output: dict) -> bool:
    return output.get("decision") == "block"


# --- The bug fix: a deliberate FAILURE result block must be allowed to stop so
# dispatch-finalize can apply MAX_RETRIES, not force-looped to maxTurns. ---
def test_failure_result_block_is_allowed(tmp_path):
    msg = (
        "---TASK RESULT---\n"
        "STATUS: FAILURE\n"
        "SUMMARY: upstream dependency missing, cannot proceed\n"
        "SUGGESTED_NEXT: unblock the upstream task first\n"
        "---END RESULT---"
    )
    assert not is_blocked(run_hook("task-executor", msg, tmp_path))


# A recovered failure (Traceback present) inside a SUCCESS result block must not
# be spuriously re-blocked.
def test_success_result_block_with_traceback_allowed(tmp_path):
    msg = (
        "Ran tests, hit a Traceback on the first pass, fixed it, all green now.\n"
        "---TASK RESULT---\n"
        "STATUS: SUCCESS\n"
        "COMMIT_SHA: abc1234\n"
        "---END RESULT---"
    )
    assert not is_blocked(run_hook("task-executor", msg, tmp_path))


# Unstructured crash (no result block) with a failure pattern -> block + recover.
def test_unstructured_traceback_blocks(tmp_path):
    msg = "Traceback (most recent call last):\n  File 'x.py', line 1\nImportError\n"
    assert is_blocked(run_hook("task-executor", msg, tmp_path))


# task-executor that stopped doing normal work with no result block -> block.
def test_task_executor_turn_exhaustion_blocks(tmp_path):
    assert is_blocked(run_hook("task-executor", "done", tmp_path))


# explorer (non-task-executor) with no result and no failure -> allowed.
def test_explorer_clean_stop_allowed(tmp_path):
    assert not is_blocked(run_hook("explorer", "investigation complete", tmp_path))


# explorer crash with no result block -> blocked.
def test_explorer_failure_blocks(tmp_path):
    assert is_blocked(run_hook("explorer", "Command failed: exit 1", tmp_path))
