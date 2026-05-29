#!/usr/bin/env python3
"""Stop hook for code-reviewer: log review event."""

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
    session_id = input_data.get("session_id", "")

    # Initialize logging
    log_file = init_logging("on-review-stop")

    # Log review event
    log_entry(log_file, f"session={session_id} event=code_review_complete")

    write_simple_output()


if __name__ == "__main__":
    main()