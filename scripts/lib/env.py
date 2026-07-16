"""Shared library for environment variable handling

Provides unified environment variable retrieval and validation functionality.
"""

import os
import sys
from pathlib import Path
from typing import Optional


def get_plugin_root() -> Path:
    """Get plugin root directory

    Resolution priority (ground-truth first):
      1. ``__file__``-based root — env.py lives at ``<plugin>/scripts/lib/env.py``,
         so its parent.parent.parent is ALWAYS the true plugin root and is always
         available (we are executing from inside the plugin tree).
      2. ``$CLAUDE_PLUGIN_ROOT`` — trusted ONLY when it resolves to the same path
         as the ``__file__`` root. A stale/wrong env var (a different plugin's
         install, a subshell, a wrapper dir) used to win unconditionally and make
         scripts like scaffold-strategy.py HALT on a ``templates/`` path it
         computed wrong. Never let a hint override ground truth without checking.

    Returns:
        Plugin root directory path
    """
    # Ground truth: env.py is at <plugin>/scripts/lib/env.py.
    file_root = Path(__file__).resolve().parent.parent.parent

    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        env_root_resolved = Path(env_root).resolve()
        if env_root_resolved == file_root:
            return env_root_resolved
        # Mismatch → the env var is stale or points elsewhere. Prefer the
        # file-derived root (always correct) and surface the discrepancy so a
        # genuinely broken install is still diagnosable rather than a silent
        # HALT deep inside a downstream script.
        print(
            f"WARNING: CLAUDE_PLUGIN_ROOT={env_root!r} does not match the "
            f"plugin location derived from this file ({file_root}); using "
            f"{file_root}. Fix the env var if this plugin was relocated.",
            file=sys.stderr,
        )
    return file_root


def get_data_dir() -> Path:
    """Get the runtime data directory for project-scoped telemetry.

    Resolution priority (explicit override first, project next, plugin last):

      1. ``$CLAUDE_PLUGIN_DATA`` — explicit override (tests, sandboxes, custom
         layouts). Returned verbatim.
      2. ``$CLAUDE_PROJECT_DIR/.conductor`` — the project's runtime dir.
         Conductor's logs/failures/recovery events are *project-scoped* (they
         describe a specific project's tracks), so they belong beside the
         project's ``conductor/`` tree — where you look when debugging — not
         under the shared plugin dir (which collides across projects).
         ``CLAUDE_PROJECT_DIR`` is set by Claude Code for every project hook,
         so this is the common path with zero config. The ``/.conductor/``
         root-anchored gitignore rule (setup §2.5) already covers it.
      3. ``<plugin>/.data`` — fail-safe. Hooks that fire outside any project
         (e.g. session-start before a track exists, or a non-project cwd) still
         need a writable home; the plugin dir always exists.

    Returns:
        Data directory path (not yet created; callers mkdir as needed).
    """
    explicit = os.environ.get("CLAUDE_PLUGIN_DATA")
    if explicit:
        return Path(explicit)
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return Path(project_dir) / ".conductor"
    # Fail-safe: plugin-anchored (always writable, always exists).
    return get_plugin_root() / ".data"


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