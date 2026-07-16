#!/usr/bin/env python3
"""SessionEnd hook: cleanup, consistency validation, and metrics logging.

Runs on session termination: clear, resume, logout, prompt_input_exit, bypass_permissions_disabled, other.
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.env import get_data_dir
from lib.hook_io import read_hook_input, write_hook_output
from lib.json_utils import load_json_safe
from lib.logging import init_logging, log_entry
from lib.path_utils import find_tracks_registry, extract_track_dirs


def has_active_tracks(cwd: Path, log_dir: Path = None) -> bool:
    """Check if there are active tracks in the conductor directory.

    On any error (malformed tracks registry or state file), conservatively
    returns True. The caller deletes ``session-handoff.md`` (the cross-session
    spine) when this returns False, so a parse failure must NOT cause that
    deletion — losing the handoff is worse than keeping a possibly-stale one.

    Args:
        cwd: Current working directory
        log_dir: Optional logs dir for a recovery warning on the error path

    Returns:
        True if there are active tracks (or if activity could not be determined)
    """
    tracks_file = find_tracks_registry(cwd)
    if not tracks_file:
        return False

    try:
        dirs = extract_track_dirs(tracks_file)

        for d in dirs:
            state_file = cwd / d / "track-state.json"
            state = load_json_safe(state_file)
            if state:
                status = state.get("status", "")
                if status not in ("completed", "archived", "cancelled"):
                    return True
        return False
    except Exception as e:
        # Don't let a malformed registry/state delete the handoff spine.
        # Treat as active (keep the handoff) and log for manual inspection.
        if log_dir is not None:
            try:
                log_entry(log_dir / "cleanup.log",
                          f"has_active_tracks error (keeping handoff): {e}")
            except Exception:
                pass
        return True


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

    # Initialize directories — single resolver (project-scoped by default,
    # plugin-anchored fail-safe). Matches session-start.py and init_logging.
    data_dir = get_data_dir()

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
        if not has_active_tracks(cwd, log_dir):
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

    # SessionEnd has no decision control and does not support additionalContext.
    # Only side effects (cleanup, logging) are meaningful here.
    write_hook_output(hook_event_name="SessionEnd")


if __name__ == "__main__":
    main()