#!/usr/bin/env python3
"""SessionEnd hook: cleanup, consistency validation, and metrics logging.

Runs on session termination: clear, resume, logout, prompt_input_exit, bypass_permissions_disabled, other.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input
from lib.json_utils import load_json_safe
from lib.logging import init_logging, log_entry


def has_active_tracks(cwd: Path) -> bool:
    """Check if there are active tracks in the conductor directory

    Args:
        cwd: Current working directory

    Returns:
        True if there are active tracks
    """
    tracks_file = cwd / "conductor" / "tracks.md"
    if not tracks_file.exists():
        return False

    try:
        content = tracks_file.read_text(encoding="utf-8")
        dirs = re.findall(r'\[.*?\]\(([^)]+)\)', content)

        for d in dirs:
            state_file = cwd / d / "track-state.json"
            state = load_json_safe(state_file)
            if state:
                status = state.get("status", "")
                if status not in ("completed", "archived", "cancelled"):
                    return True
    except Exception:
        pass

    return False


def clean_temp_files(temp_dir: Path, max_age_hours: int = 24) -> int:
    """Clean temp files older than max_age_hours

    Args:
        temp_dir: Temp directory
        max_age_hours: Maximum age in hours

    Returns:
        Number of files cleaned
    """
    if not temp_dir.exists():
        return 0

    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    cleaned = 0

    # Remove old files
    for item in temp_dir.rglob("*"):
        if item.is_file():
            try:
                mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    item.unlink()
                    cleaned += 1
            except Exception:
                continue

    # Remove empty directories
    for item in sorted(temp_dir.rglob("*"), reverse=True):
        if item.is_dir() and not any(item.iterdir()):
            try:
                item.rmdir()
            except Exception:
                continue

    return cleaned


def log_session_duration(session_id: str, start_time_file: Path, log_file: Path) -> None:
    """Log session duration

    Args:
        session_id: Session ID
        start_time_file: File containing start time
        log_file: Log file path
    """
    if not start_time_file.exists():
        return

    try:
        start_timestamp = int(start_time_file.read_text().strip())
        duration = int(time.time()) - start_timestamp

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = f"session={session_id} duration_seconds={duration}"
        log_entry(log_file, message)

        # Clean up start time file
        start_time_file.unlink()
    except Exception:
        pass


def ensure_data_structure(data_dir: Path, log_file: Path) -> None:
    """Ensure required data directories exist

    Args:
        data_dir: Data directory
        log_file: Log file path
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    required_dirs = ["logs"]
    for dir_name in required_dirs:
        dir_path = data_dir / dir_name
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            message = f"created missing directory {dir_path}"
            log_entry(log_file, message)


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    session_id = input_data.get("session_id", "")
    cwd_str = input_data.get("cwd", "")
    end_reason = input_data.get("reason", "")

    cwd = Path(cwd_str) if cwd_str else Path.cwd()

    # Initialize directories
    data_dir = Path(os.environ.get("CLAUDE_PLUGIN_DATA", ""))
    if not data_dir.is_absolute():
        plugin_root = Path(__file__).parent.parent
        data_dir = plugin_root / ".data"

    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log session end
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    message = f"session={session_id} reason={end_reason} event=session_end"
    log_entry(log_dir / "session-lifecycle.log", message, "INFO", datetime.fromisoformat(timestamp))

    # 1. Validate session-handoff.md consistency
    handoff_file = data_dir / "session-handoff.md"
    if handoff_file.exists():
        if not has_active_tracks(cwd):
            # No active tracks but handoff exists - cleanup
            handoff_file.unlink()
            message = "cleaned stale session-handoff.md (no active tracks)"
            log_entry(log_dir / "cleanup.log", message, "INFO", datetime.fromisoformat(timestamp))

    # 2. Clean orphaned temp files
    temp_dir = data_dir / "tmp"
    if temp_dir.exists():
        cleaned = clean_temp_files(temp_dir)
        if cleaned > 0:
            message = f"cleaned {cleaned} temp files"
            log_entry(log_dir / "cleanup.log", message, "INFO", datetime.fromisoformat(timestamp))

    # 3. Log session duration
    session_start_file = data_dir / "logs" / f".session-{session_id}.start"
    log_session_duration(session_id, session_start_file, log_dir / "session-metrics.log")

    # 4. Validate .data/ directory structure
    ensure_data_structure(data_dir, log_dir / "cleanup.log")

    # 5. Emit session summary
    session_metrics_file = log_dir / "session-metrics.log"
    if session_metrics_file.exists():
        try:
            recent_lines = session_metrics_file.read_text(encoding="utf-8").strip().split("\n")
            summary_count = min(5, len(recent_lines))
            if summary_count > 0:
                summary = f"[Conductor] Session ended. {summary_count} recent sessions logged."
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionEnd",
                        "additionalContext": summary
                    }
                }
                print(json.dumps(output, ensure_ascii=False))
                return
        except Exception:
            pass

    # Default output
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionEnd"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()