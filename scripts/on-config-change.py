#!/usr/bin/env python3
"""ConfigChange hook: validate and log hook configuration changes.

Monitors hooks.json modifications for safety.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.env import get_data_dir
from lib.logging import init_logging, log_entry


DANGEROUS_PATTERNS = [
    'rm -rf',
    'curl.*\\|.*sh',
    'eval $',
    '; rm ',
    '> /etc/',
    'mv /usr/',
]


def validate_hook_config(file_path: Path) -> Optional[list[str]]:
    """Validate hook configuration file for dangerous patterns

    Args:
        file_path: Path to the configuration file

    Returns:
        List of dangerous patterns found, or None if validation failed
    """
    if not file_path.exists():
        return None

    try:
        content = file_path.read_text(encoding="utf-8")
        content_lower = content.lower()

        issues = []
        for pattern in DANGEROUS_PATTERNS:
            if pattern.lower() in content_lower:
                issues.append(pattern)

        return issues if issues else []
    except Exception:
        return None


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    source = input_data.get("source", "")
    file_path_str = input_data.get("file_path", "")
    session_id = input_data.get("session_id", "")

    # Initialize logging
    log_file = init_logging("on-config-change")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Log config change
    message = f"session={session_id} source={source} file={file_path_str} event=config_change"
    log_entry(log_file, message)

    # Validate hooks.json and settings changes
    file_path = Path(file_path_str)
    if "hooks" in file_path_str or "settings" in file_path_str:
        issues = validate_hook_config(file_path)

        if issues is not None and issues:
            dangerous_str = ",".join(issues)
            context = (
                f"[Conductor] WARNING: Hook configuration contains suspicious patterns: {dangerous_str}. "
                "Review before trusting."
            )
            write_simple_output(additional_context=context)
            return

    write_simple_output()


if __name__ == "__main__":
    main()