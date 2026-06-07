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
from typing import Dict, Optional

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
    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            if task.get("status") == "in_progress":
                name = task.get("name", "")
                stale.append(f'Phase {pi} Task {ti}: {name}')
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if sub.get("status") == "in_progress":
                    name = sub.get("name", "")
                    stale.append(f'Phase {pi} Task {ti}.{si}: {name}')

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

    pi = state.get("current_phase_index", 0)
    ti = state.get("current_task_index", 0)
    mode = state.get("execution_mode", "interactive")

    if pi < 1 or ti < 1:
        return f'- Track {track_id}: status={status}, position=N/A, mode={mode}'
    return f'- Track {track_id}: status={status}, position=P{pi}.T{ti}, mode={mode}'


def write_session_handoff(data_dir: Path, handoff_data: str, gc_summary: str = "") -> None:
    """Write session handoff file"""
    handoff_file = data_dir / "session-handoff.md"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if handoff_data:
        handoff_content = f"Last session ended: {timestamp}\n"
        handoff_content += "Active tracks:\n"
        handoff_content += handoff_data
        if gc_summary:
            handoff_content += f"\n{gc_summary}\n"
        handoff_content += "Run /conductor:implement to continue, or /conductor:status for overview."

        handoff_file.write_text(handoff_content, encoding="utf-8")
    elif handoff_file.exists():
        handoff_file.unlink()


def collect_gc_metrics(cwd: Path, track_dirs: list[str]) -> Dict:
    """Collect lightweight GC metrics across all tracks.

    Returns dict with: total_tracks, archived_tracks, stale_tasks,
    orphaned_result_files, gc_warnings.
    """
    metrics = {
        "total_tracks": len(track_dirs),
        "archived_tracks": 0,
        "stale_tasks": 0,
        "orphaned_results": 0,
        "gc_warnings": [],
    }

    for track_dir in track_dirs:
        full_dir = cwd / track_dir
        state_file = full_dir / "track-state.json"
        cond_dir = full_dir / ".conductor"

        state = load_json_safe(state_file)
        if not state:
            continue

        # Count archived tracks
        if state.get("status") == "archived":
            metrics["archived_tracks"] += 1
            continue

        # Single pass: count stale in_progress tasks and detect active tasks
        has_active = False
        for phase in state.get("phases", []):
            for task in phase.get("tasks", []):
                if task.get("status") == "in_progress":
                    metrics["stale_tasks"] += 1
                    has_active = True
                for sub in task.get("subtasks", []):
                    if sub.get("status") == "in_progress":
                        metrics["stale_tasks"] += 1
                        has_active = True

        # Count orphaned result.json files (result exists but no active tasks)
        result_file = cond_dir / "result.json"
        if result_file.exists() and not has_active:
            metrics["orphaned_results"] += 1

    # Build gc_warnings
    if metrics["orphaned_results"] > 0:
        metrics["gc_warnings"].append(
            f"[Conductor] {metrics['orphaned_results']} orphaned result.json file(s) detected. "
            "Run track-state gc to clean."
        )
    if metrics["stale_tasks"] > 0:
        metrics["gc_warnings"].append(
            f"[Conductor] {metrics['stale_tasks']} stale in_progress task(s) detected. "
            "Consider running track-state validate --fix."
        )

    return metrics


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
    track_dirs = []
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

    # Collect GC metrics
    gc_summary = ""
    if track_dirs:
        gc_metrics = collect_gc_metrics(cwd, track_dirs)
        active = gc_metrics["total_tracks"] - gc_metrics["archived_tracks"]
        gc_summary = (
            f"Tracks: {active} active, {gc_metrics['archived_tracks']} archived, "
            f"{gc_metrics['total_tracks']} total."
        )
        if gc_metrics["stale_tasks"] > 0 or gc_metrics["orphaned_results"] > 0:
            gc_summary += (
                f" GC: {gc_metrics['orphaned_results']} orphaned result(s), "
                f"{gc_metrics['stale_tasks']} stale task(s)."
            )
        issues.extend(gc_metrics["gc_warnings"])

    # Write session handoff file
    write_session_handoff(data_dir, handoff_data, gc_summary)

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
