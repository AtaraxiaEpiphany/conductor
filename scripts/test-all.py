#!/usr/bin/env python3
"""Test all Python scripts to ensure they can be imported and run basic checks."""

import json
import subprocess
import sys
from pathlib import Path

# Test directory
scripts_dir = Path(__file__).parent

# Test cases for each script
TEST_CASES = {
    "filter-subagent-output.py": {
        "input": '{"tool_name":"Bash","tool_response":"no result block here"}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "git-notes-query.py": {
        "args": ["--help"],
        "type": "cli"
    },
    "lint-track-state.py": {
        "args": ["/tmp"],
        "type": "cli",
        "ignore_exit": True  # May exit with 1 if no tracks.md
    },
    "on-batch-complete.py": {
        "input": '{"tool_calls":[],"session_id":"test"}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "on-compact.py": {
        "input": '{}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "on-phase-checkpoint-stop.py": {
        "input": '{"session_id":"test"}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "on-review-stop.py": {
        "input": '{"session_id":"test"}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "on-subagent-start.py": {
        "input": '{"agent_type":"task-executor"}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "on-subagent-stop.py": {
        "input": '{"agent_type":"task-executor","session_id":"test","last_assistant_message":"done"}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "on-test-run.py": {
        "input": '{"tool_name":"Bash","tool_input":{"command":"echo test"},"tool_response":{"stdout":"","stderr":""}}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "pre-command-check.py": {
        "input": '{"tool_name":"Bash","tool_input":{"command":"echo test"},"cwd":"/tmp"}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "session-end.py": {
        "input": '{"session_id":"test","cwd":"/tmp","reason":"logout"}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "session-start.py": {
        "input": '{"source":"startup","cwd":"/tmp"}',
        "expected_keys": ["hookSpecificOutput"]
    },
    "state-consistency-check.py": {
        "input": '{"cwd":"/tmp"}',
        "expected_keys": ["hookSpecificOutput"]
    },
}


def test_script(script_name: str, test_case: dict) -> tuple[bool, str]:
    """Test a single script.

    Args:
        script_name: Name of the script file
        test_case: Test case configuration

    Returns:
        Tuple of (passed, message)
    """
    script_path = scripts_dir / script_name

    if not script_path.exists():
        return False, f"Script not found: {script_path}"

    try:
        if test_case.get("type") == "cli":
            # Test as CLI command
            args = [str(script_path)] + test_case.get("args", [])
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=5
            )

            if test_case.get("ignore_exit"):
                return True, f"CLI test completed (exit {result.returncode})"

            if result.returncode != 0:
                return False, f"CLI test failed with exit code {result.returncode}: {result.stderr}"

            # Check for expected output
            if test_case.get("expected_output"):
                if test_case["expected_output"] in result.stdout:
                    return True, "CLI test passed"
                return False, f"Expected output not found in: {result.stdout[:100]}"

            return True, "CLI test passed"

        else:
            # Test as hook (stdin input)
            input_data = test_case.get("input", "{}")
            expected_keys = test_case.get("expected_keys", [])

            result = subprocess.run(
                [str(script_path)],
                input=input_data,
                capture_output=True,
                text=True,
                timeout=5
            )

            # Exit 0 or 2 is OK (2 is for asyncRewake)
            if result.returncode not in (0, 2):
                return False, f"Exit code {result.returncode}: {result.stderr}"

            # Parse output and check expected keys
            try:
                output = json.loads(result.stdout)
                for key in expected_keys:
                    if key not in output:
                        return False, f"Missing key in output: {key}"
                return True, "Hook test passed"
            except json.JSONDecodeError as e:
                return False, f"Invalid JSON output: {e}"

    except subprocess.TimeoutExpired:
        return False, "Script timed out"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def main():
    """Run all tests."""
    print("Testing all Python scripts...")
    print("=" * 60)

    passed = 0
    failed = 0

    for script_name, test_case in sorted(TEST_CASES.items()):
        success, message = test_script(script_name, test_case)
        status = "PASS" if success else "FAIL"
        print(f"[{status}] {script_name}: {message}")

        if success:
            passed += 1
        else:
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")

    if failed > 0:
        sys.exit(1)
    else:
        print("\nAll scripts passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
