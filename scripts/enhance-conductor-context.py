#!/usr/bin/env python3
"""InstructionsLoaded hook: log when conductor instruction files are loaded.

InstructionsLoaded does not support additionalContext — it fires for
observability only. This hook logs the load event for audit purposes.
"""

import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.logging import init_logging, log_entry


def main():
    """Main hook function"""
    input_data = read_hook_input()
    file_path = input_data.get("file_path", "")
    load_reason = input_data.get("load_reason", "")
    session_id = input_data.get("session_id", "")

    # Log conductor file loads for audit
    if "conductor" in file_path or "conductor-core" in file_path:
        log_file = init_logging("enhance-conductor-context")
        log_entry(log_file, f"session={session_id} file={file_path} reason={load_reason}")

    write_hook_output()


if __name__ == "__main__":
    main()
