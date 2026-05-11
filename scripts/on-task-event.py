#!/usr/bin/env python3
"""TaskCreated / TaskCompleted hook: log task lifecycle events."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.logging import init_logging, log_entry


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    hook_event = input_data.get("hook_event_name", "")
    session_id = input_data.get("session_id", "")

    # Initialize logging
    log_file = init_logging("on-task-event")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Log lifecycle event
    log_entry(log_file, f"session={session_id} event={hook_event}")

    write_simple_output()


if __name__ == "__main__":
    main()