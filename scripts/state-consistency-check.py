#!/usr/bin/env python3
"""Stop hook for conductor:implement skill:

1. Verifies track-state.json and plan.md markers are consistent.
2. Writes session handoff file for next session recovery.
Returns additionalContext with warning if inconsistencies found.
Non-blocking -- only warns, does not halt.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.json_utils import load_json_safe
from lib.env import get_data_dir
from lib.path_utils import find_tracks_registry, extract_track_dirs


def find_stale_in_progress_tasks(state_file: Path) -> list[str]:
    """Find stale in_progress tasks in state file"""
    state = load_json_safe(state_file)
    if not state:
        return []

    stale = []
    for pi, phase in enumerate(state.get("phases", [])):
        for ti, task in enumerate(phase.get("tasks", [])):
            if task.get("status") == "in_progress":
                name = task.get("name", "")
                stale.append(f'Phase {pi+1} Task {ti+1}: {name}')
            for si, sub in enumerate(task.get("subtasks", [])):
                if sub.get("status") == "in_progress":
                    name = sub.get("name", "")
                    stale.append(f'Phase {pi+1} Task {ti+1}.{si+1}: {name}')

    return stale


def get_track_handoff_info(state_file: Path) -> Optional[str]:
    """Get handoff information for active track"""
    state = load_json_safe(state_file)
    if not state:
        return None

    status = state.get("status", "unknown")
    track_id = state.get("track_id", "unknown")

    if status in ("completed", "archived", "cancelled"):
        return None

    pi = state.get("current_phase_index", -1)
    ti = state.get("current_task_index", -1)
    mode = state.get("execution_mode", "interactive")

    if pi < 0 or ti < 0:
        return f'- Track {track_id}: status={status}, position=N/A, mode={mode}'
    return f'- Track {track_id}: status={status}, position=P{pi + 1}.T{ti + 1}, mode={mode}'


def write_session_handoff(data_dir: Path, handoff_data: str) -> None:
    """Write session handoff file"""
    handoff_file = data_dir / "session-handoff.md"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if handoff_data:
        handoff_content = f"Last session ended: {timestamp}\n"
        handoff_content += "Active tracks:\n"
        handoff_content += handoff_data
        handoff_content += "Run /conductor:implement to continue, or /conductor:status for overview."

        handoff_file.write_text(handoff_content, encoding="utf-8")
    elif handoff_file.exists():
        handoff_file.unlink()


def main():
    """Main hook function"""
    input_data = read_hook_input()
    cwd_str = input_data.get("cwd", "")
    cwd = Path(cwd_str) if cwd_str else Path.cwd()

    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    issues = []
    handoff_data = ""

    conductor_directory = cwd / "conductor"
    tracks_file = find_tracks_registry(cwd)
    if tracks_file:
        track_dirs = extract_track_dirs(tracks_file)

        for track_dir in track_dirs:
            full_dir = cwd / track_dir
            state_file = full_dir / "track-state.json"
            plan_file = full_dir / "plan.md"

            if state_file.exists() and plan_file.exists():
                stale_locks = find_stale_in_progress_tasks(state_file)
                if stale_locks:
                    stale_str = "; ".join(stale_locks)
                    issues.append(
                        f"[Conductor] Stale in_progress tasks found in {track_dir}: {stale_str}. "
                    )

                track_info = get_track_handoff_info(state_file)
                if track_info:
                        handoff_data += track_info + "\n"

    # Write session handoff file
    write_session_handoff(data_dir, handoff_data)

    # Output result — auto-detect event name (Stop or SubagentStop after auto-convert)
    if issues:
        msg = "".join(issues)
        msg += "Consider running /conductor:implement to recover state, or /conductor:status to inspect."
        write_hook_output(
            additional_context=msg,
            system_message=msg,
        )
    else:
        write_hook_output()


if __name__ == "__main__":
    main()
