"""Shared library for environment variable handling

Provides unified environment variable retrieval and validation functionality.
"""

import os
from pathlib import Path
from typing import Optional


def get_plugin_root() -> Path:
    """Get plugin root directory

    Returns:
        Plugin root directory path
    """
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        return Path(plugin_root)
    else:
        # Fallback: use current working directory parent or default
        cwd = Path.cwd()
        # Check if we're in a conductor-plugin directory
        if cwd.name == "scripts":
            return cwd.parent
        elif (cwd / "scripts" / "track-state").exists():
            return cwd
        else:
            # Default to parent of scripts directory
            return Path(__file__).parent.parent


def get_data_dir() -> Path:
    """Get data directory (.data)

    Returns:
        Data directory path
    """
    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
    if data_dir:
        return Path(data_dir)
    else:
        # Default .data under plugin root
        plugin_root = get_plugin_root()
        return plugin_root / ".data"


def get_logs_dir() -> Path:
    """Get logs directory

    Returns:
        Logs directory path
    """
    data_dir = get_data_dir()
    return data_dir / "logs"


def get_session_id() -> Optional[str]:
    """Get current session ID

    Returns:
        Session ID or None
    """
    return os.environ.get("CLAUDE_SESSION_ID")


def get_permission_mode() -> Optional[str]:
    """Get current permission mode

    Returns:
        Permission mode or None
    """
    return os.environ.get("CLAUDE_PERMISSION_MODE")


def get_cwd() -> Optional[str]:
    """Get current working directory

    Returns:
        Working directory path or None
    """
    return os.environ.get("CLAUDE_CWD")


def ensure_env_vars(required_vars: list[str]) -> None:
    """Ensure required environment variables exist

    Args:
        required_vars: List of required environment variable names

    Raises:
        ValueError: When required environment variables are missing
    """
    missing_vars = []
    for var in required_vars:
        if not os.environ.get(var):
            missing_vars.append(var)

    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")


def is_remote_env() -> bool:
    """Check if running in remote environment

    Returns:
        True if running in remote environment
    """
    return os.environ.get("CLAUDE_CODE_REMOTE") == "true"


def is_compact_mode() -> bool:
    """Check if running in compact mode

    Returns:
        True if running in compact mode
    """
    return os.environ.get("CLAUDE_EFFORT") == "low"


# Common path quick accessors
def get_track_state_json(track_dir: Path) -> Path:
    """Get track-state.json file path

    Args:
        track_dir: Track directory

    Returns:
        track-state.json file path
    """
    return track_dir / "track-state.json"


def get_plan_md_path(track_dir: Path) -> Path:
    """Get plan.md file path

    Args:
        track_dir: Track directory

    Returns:
        plan.md file path
    """
    return track_dir / "plan.md"