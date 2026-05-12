"""Shared library for path operations

Provides unified path manipulation and file system utilities.
"""

import os
import shutil
from pathlib import Path
from typing import Optional, List


def get_script_dir() -> Path:
    """Get directory of the calling script

    Returns:
        Directory path of the calling script
    """
    import inspect
    caller_frame = inspect.stack()[1]
    return Path(caller_frame.filename).parent


def ensure_dir(path: Path) -> None:
    """Ensure directory exists, create if not

    Args:
        path: Directory path
    """
    path.mkdir(parents=True, exist_ok=True)


def find_track_root(cwd: Optional[Path] = None) -> Optional[Path]:
    """Find track root directory by looking for track-state.json

    Args:
        cwd: Current working directory (default: current directory)

    Returns:
        Track root path or None if not found
    """
    if cwd is None:
        cwd = Path.cwd()

    # Check if current directory has track-state.json
    if (cwd / "track-state.json").exists():
        return cwd

    # Check parent directories
    for parent in cwd.parents:
        if (parent / "track-state.json").exists():
            return parent

    return None


def get_relative_path(base: Path, target: Path) -> Path:
    """Get relative path from base to target

    Args:
        base: Base path
        target: Target path

    Returns:
        Relative path
    """
    return os.path.relpath(target, base)


def clean_temp_files(directory: Path, max_age_hours: int = 24) -> List[Path]:
    """Clean temporary files older than specified hours

    Args:
        directory: Directory to clean
        max_age_hours: Maximum age in hours

    Returns:
        List of deleted file paths
    """
    from datetime import datetime, timezone, timedelta

    deleted = []
    if not directory.exists():
        return deleted

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    for item in directory.iterdir():
        if item.is_file():
            try:
                # Get file modification time
                mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    item.unlink()
                    deleted.append(item)
            except Exception:
                continue

    return deleted


def copy_with_backup(src: Path, dst: Path, backup_suffix: str = ".bak") -> bool:
    """Copy file with backup if destination exists

    Args:
        src: Source file path
        dst: Destination file path
        backup_suffix: Backup file suffix

    Returns:
        True if copy succeeded
    """
    if dst.exists():
        backup_path = dst.with_suffix(dst.suffix + backup_suffix)
        shutil.copy2(dst, backup_path)

    try:
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


def resolve_safe_path(base: Path, path_str: str) -> Optional[Path]:
    """Resolve path safely, preventing directory traversal

    Args:
        base: Base directory
        path_str: Path string to resolve

    Returns:
        Resolved path or None if unsafe
    """
    try:
        # Convert to Path and resolve
        resolved = (base / path_str).resolve()

        # Ensure it's within base directory
        try:
            resolved.relative_to(base)
            return resolved
        except ValueError:
            # Path is outside base directory
            return None
    except Exception:
        return None


def list_files_by_pattern(
    directory: Path,
    pattern: str,
    recursive: bool = False
) -> List[Path]:
    """List files matching pattern

    Args:
        directory: Directory to search
        pattern: File pattern (e.g., "*.json")
        recursive: Whether to search recursively

    Returns:
        List of matching file paths
    """
    if recursive:
        return list(directory.rglob(pattern))
    else:
        return list(directory.glob(pattern))


def get_file_age_hours(file_path: Path) -> Optional[float]:
    """Get file age in hours

    Args:
        file_path: File path

    Returns:
        Age in hours or None if file doesn't exist
    """
    if not file_path.exists():
        return None

    from datetime import datetime, timezone

    mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
    age = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
    return age


def is_within_path(parent: Path, child: Path) -> bool:
    """Check if child path is within parent path

    Args:
        parent: Parent path
        child: Child path

    Returns:
        True if child is within parent
    """
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False