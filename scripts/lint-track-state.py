#!/usr/bin/env python3
"""lint-track-state — Boundary enforcement linter for Conductor tracks.

Verifies Execution Firewall rules F1, F4, and state consistency.
Can be run as CI check or pre-commit hook.
Exit 0 on pass, 1 on failure.
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.json_utils import load_json_safe, load_json
from lib.env import get_track_state_json, get_plan_md_path
from lib.validation import check_state_file_age, validate_json_structure
from lib.path_utils import find_track_root, find_tracks_registry, extract_track_dirs


def check_f1_rule(state_file: Path) -> tuple[bool, Optional[str]]:
    """Check F1 rule: only ONE in_progress task allowed

    Args:
        state_file: Path to track-state.json

    Returns:
        Tuple of (is_valid, error_message)
    """
    state = load_json_safe(state_file)
    if not state:
        return False, "Cannot read track-state.json"

    # Check valid state structure
    valid, error = validate_json_structure(state, ["track_id", "status", "phases"])
    if not valid:
        return False, f"Invalid state structure: {error}"

    active_tasks = []
    for pi, phase in enumerate(state.get("phases", [])):
        for ti, task in enumerate(phase.get("tasks", [])):
            if task.get("status") == "in_progress":
                active_tasks.append(f"P{pi}.T{ti}")
            # Check subtasks
            for si, sub in enumerate(task.get("subtasks", [])):
                if sub.get("status") == "in_progress":
                    active_tasks.append(f"P{pi}.T{ti}.S{si}")

    if len(active_tasks) > 2:
        return False, (
            f"VIOLATION: {len(active_tasks)} in_progress tasks "
            f"(max 2 allowed: 1 parent task + 1 subtask). "
            f"Tasks: {', '.join(active_tasks)}"
        )

    return True, None


def check_f4_rule(state_file: Path) -> tuple[bool, Optional[str]]:
    """Check F4 rule: SHA must exist for terminal tasks

    Args:
        state_file: Path to track-state.json

    Returns:
        Tuple of (is_valid, error_message)
    """
    state = load_json_safe(state_file)
    if not state:
        return False, "Cannot read track-state.json"

    # Check valid state structure
    valid, error = validate_json_structure(state, ["track_id", "status", "phases"])
    if not valid:
        return False, f"Invalid state structure: {error}"

    # Canonical source: scripts/track-state TERMINAL_FOR_PARENT.
    # "failed" excluded: _do_fail never sets commit_sha, so SHA check for
    # failed tasks would always be a false positive.
    terminal_statuses = {"completed", "skipped", "deferred", "blocked", "cancelled"}
    missing_shas = []

    for pi, phase in enumerate(state.get("phases", [])):
        for ti, task in enumerate(phase.get("tasks", [])):
            if task.get("status") in terminal_statuses:
                if not task.get("commit_sha"):
                    missing_shas.append(f'P{pi}.T{ti}: {task.get("name", "?")}')

            # Check subtasks
            for si, sub in enumerate(task.get("subtasks", [])):
                if sub.get("status") in terminal_statuses:
                    if not sub.get("commit_sha"):
                        missing_shas.append(f'P{pi}.T{ti}.S{si}: {sub.get("name", "?")}')

    if missing_shas:
        return False, f"VIOLATION: Missing commit SHAs for terminal tasks: {'; '.join(missing_shas)}"

    return True, None


def check_state_consistency(state_file: Path) -> tuple[bool, Optional[str]]:
    """Check state consistency using track-state validate

    Args:
        state_file: Path to track-state.json

    Returns:
        Tuple of (is_valid, error_message)
    """
    track_dir = state_file.parent
    track_state_path = str(Path(__file__).parent / "track-state")

    import subprocess
    result = subprocess.run(
        [track_state_path, "validate", str(track_dir)],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return False, "validate command failed"

    try:
        validate_result = json.loads(result.stdout)
        if not validate_result.get("valid", False):
            errors = validate_result.get("errors", [])
            if errors:
                return False, "; ".join(errors)
    except json.JSONDecodeError:
        return False, "Invalid JSON response from validate"

    return True, None


def check_stale_state(state_file: Path) -> tuple[bool, Optional[str]]:
    """Check for stale state with in_progress tasks (>24h)

    Args:
        state_file: Path to track-state.json

    Returns:
        Tuple of (is_fresh, warning_message)
    """
    is_fresh, message = check_state_file_age(state_file, 24)
    if not is_fresh and message:
        # Only warn if there are in_progress tasks
        state = load_json_safe(state_file)
        if state:
            has_active = False
            for phase in state.get("phases", []):
                for task in phase.get("tasks", []):
                    if task.get("status") == "in_progress":
                        has_active = True
                        break
                if has_active:
                    break

            if has_active:
                return False, message

    return True, None


def main():
    """Main linter function"""
    # Get current working directory
    cwd = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    tracks_file = find_tracks_registry(cwd)

    # Check if tracks.md exists
    if not tracks_file:
        print("No conductor/tracks.md found. Nothing to lint.")
        sys.exit(0)

    # Extract track directories
    track_dirs = extract_track_dirs(tracks_file)

    errors = 0
    warnings = 0

    for track_dir in track_dirs:
        full_dir = cwd / track_dir
        state_file = full_dir / "track-state.json"

        # Skip if no track-state.json
        if not state_file.exists():
            continue

        # Get track ID
        state = load_json_safe(state_file)
        track_id = state.get("track_id", "unknown") if state else "unknown"

        print(f"\nChecking track: {track_id}")

        # F1: Global State Lock
        valid, error = check_f1_rule(state_file)
        if not valid:
            print(f"[F1 ERROR] Track '{track_id}': {error}")
            print("  Fix: Run track-state recover to reset stale locks.")
            errors += 1
        else:
            print(f"[F1 PASS] Track '{track_id}' has ≤2 in_progress tasks")

        # F4: SHA Must Exist
        valid, error = check_f4_rule(state_file)
        if not valid:
            print(f"[F4 ERROR] Track '{track_id}': {error}")
            print("  Fix: Run git log to find SHAs, then track-state complete <dir> <p> <t> --sha <sha>")
            print("        or: track-state complete <dir> --phase <p> --task <t> --sha <sha>")
            errors += 1
        else:
            print(f"[F4 PASS] Track '{track_id}' has required SHAs for terminal tasks")

        # State consistency
        valid, error = check_state_consistency(state_file)
        if not valid:
            print(f"[STATE ERROR] Track '{track_id}': {error}")
            print("  Fix: Run track-state validate <dir> --fix to auto-repair, or check track-state.json manually.")
            errors += 1
        else:
            print(f"[STATE PASS] Track '{track_id}' is consistent")

        # Stale state warnings
        valid, warning = check_stale_state(state_file)
        if not valid:
            print(f"[WARNING] Track '{track_id}': {warning}")
            print("  Hint: Run /conductor:implement to recover, or /conductor:revert to roll back.")
            warnings += 1
        else:
            print(f"[AGE PASS] Track '{track_id}' state is fresh")

    # Summary
    print(f"\nLint complete: {errors} errors, {warnings} warnings.")

    if errors > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()