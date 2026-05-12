#!/usr/bin/env python3
"""CwdChanged hook: update context when working directory changes.

Validates conductor state availability in new directory.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.env import get_data_dir
from lib.logging import init_logging, log_entry


def count_tracks(tracks_file: Path) -> int:
    """Count the number of tracks in tracks.md

    Args:
        tracks_file: Path to tracks.md

    Returns:
        Number of tracks found
    """
    if not tracks_file.exists():
        return 0

    try:
        content = tracks_file.read_text(encoding="utf-8")
        # Count lines starting with "- "
        return sum(1 for line in content.split('\n') if line.strip().startswith("- "))
    except Exception:
        return 0


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    old_cwd = input_data.get("old_cwd", "")
    new_cwd_str = input_data.get("new_cwd", "")

    # Initialize logging
    log_file = init_logging("on-cwd-change")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Log cwd change
    message = f"old={old_cwd} new={new_cwd_str} event=cwd_changed"
    log_entry(log_file, message)

    new_cwd = Path(new_cwd_str) if new_cwd_str else Path.cwd()
    conductor_dir = new_cwd / "conductor"

    # Check if new directory has conductor setup
    if conductor_dir.exists():
        tracks_file = conductor_dir / "tracks.md"
        track_count = count_tracks(tracks_file)

        if track_count > 0:
            msg = (
                f"[Conductor] Switched to project with {track_count} track(s). "
                "Run /conductor:status for overview."
            )
            write_hook_output(system_message=msg)
            return

    write_hook_output()


if __name__ == "__main__":
    main()