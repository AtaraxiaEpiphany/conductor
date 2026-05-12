"""Shared library for logging functionality

Provides unified log directory initialization and log writing functionality.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def init_logging(script_name: str, log_dir_name: str = "logs") -> Path:
    """Initialize log directory, return log file path

    Args:
        script_name: Script name (for log file name)
        log_dir_name: Log directory name (default "logs")

    Returns:
        Log file path
    """
    # Get data directory
    data_dir = os.environ.get(
        "CLAUDE_PLUGIN_DATA",
        os.environ.get("CLAUDE_PLUGIN_ROOT", "") + "/.data"
    )

    # Create log directory structure
    log_dir = Path(data_dir) / log_dir_name
    log_dir.mkdir(parents=True, exist_ok=True)

    # Return log file path
    log_file = log_dir / f"{script_name}.log"
    return log_file


def log_entry(
    log_file: Path,
    message: str,
    level: str = "INFO",
    timestamp: Optional[datetime] = None
) -> None:
    """Write log entry with timestamp

    Args:
        log_file: Log file path
        message: Log message
        level: Log level (INFO, WARNING, ERROR)
        timestamp: Custom timestamp (default current time)
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    # Format log entry
    timestamp_str = timestamp.isoformat()
    log_entry = f"{timestamp_str} [{level}] {message}\n"

    # Write to file
    try:
        log_file.write_text(log_entry, encoding="utf-8")
    except Exception:
        # Fallback to stderr if write fails
        print(f"LOG_ERROR: {log_entry.strip()}", file=sys.stderr)


def log_script_start(script_name: str) -> Path:
    """Log script start execution

    Args:
        script_name: Script name

    Returns:
        Log file path
    """
    log_file = init_logging(script_name)
    log_entry(log_file, f"Script {script_name} started")
    return log_file


def log_script_end(log_file: Path, script_name: str) -> None:
    """Log script completion

    Args:
        log_file: Log file path
        script_name: Script name
    """
    log_entry(log_file, f"Script {script_name} completed")


def log_hook_input(log_file: Path, hook_event_name: str, input_data: dict) -> None:
    """Log hook input

    Args:
        log_file: Log file path
        hook_event_name: Hook event name
        input_data: Input data (dict)
    """
    # Simplify input data to avoid logging large content
    simplified_input = {k: str(v)[:200] if isinstance(v, str) else str(v)
                       for k, v in input_data.items()}

    log_entry(log_file, f"Hook {hook_event_name} input: {json.dumps(simplified_input)}")


# Convenience functions for common usage
def log_info(script_name: str, message: str) -> None:
    """Log INFO level message

    Args:
        script_name: Script name
        message: Message
    """
    log_file = init_logging(script_name)
    log_entry(log_file, message, "INFO")


def log_error(script_name: str, message: str) -> None:
    """Log ERROR level message

    Args:
        script_name: Script name
        message: Message
    """
    log_file = init_logging(script_name)
    log_entry(log_file, message, "ERROR")


def log_warning(script_name: str, message: str) -> None:
    """Log WARNING level message

    Args:
        script_name: Script name
        message: Message
    """
    log_file = init_logging(script_name)
    log_entry(log_file, message, "WARNING")