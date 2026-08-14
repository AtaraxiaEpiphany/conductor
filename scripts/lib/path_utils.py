"""Shared library for path operations

Provides unified path manipulation and file system utilities.
"""

import os
import re
from pathlib import Path
from typing import Optional, List


# --- registry parsing (mirrors track_state.misc._iter_registry_entries) -------
# Kept self-contained here (rather than importing the canonical parser) because
# lib is lower-level than track_state (track_state.core already imports
# lib.atomic_io) and because this extractor serves a DIFFERENT contract: callers
# need project-root-relative path STRINGS (``project_root / d``), whereas
# ``_iter_registry_entries`` returns resolved ABSOLUTE dirs + entry dicts. The
# checkbox regex is GREEDY (``.*\(``) for the same reason as the misc one: the
# link is the trailing parenthetical, so a description containing parens must
# not be captured as the link. If you change the registry format, update BOTH
# this and ``track_state.misc``.
_RE_CHECKBOX_LINK = re.compile(r"^\s*-\s+\[[ x~!>#\-d@]\]\s+.*\(([^)]*)\)\s*$")
_RE_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")          # section [text](link)
_RE_TABLE_STATUS = re.compile(
    r"\b(new|in_progress|completed|archived|blocked|cancelled|deferred|skipped|failed)\b")
_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
# Universal track_id token — twin of ``track_state.misc._RE_TRACK_ID_TOKEN``.
# ``derive-name`` always emits ``<slug>_<YYYYMMDD>``, so every real track_id
# matches; used as a backstop for registry lines the format-specific branches
# drop (plain bullet, bold id, checkbox-without-link, inline mention). Keep the
# two in lockstep — the pre-command rm/mv guard and consistency checks depend on
# this enumerator seeing the SAME entries ``_iter_registry_entries`` does.
_RE_TRACK_ID_TOKEN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_]*_\d{8})")
# Status-word gate for table rows mirrors track_state.misc so a non-track
# markdown table is (mostly) not mistaken for track entries. False positives are
# harmless here: a derived dir with no track-state.json is skipped by callers.


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


def find_tracks_registry(cwd: Optional[Path] = None) -> Optional[Path]:
    """Find tracks registry file (conductor/tracks.md)

    Args:
        cwd: Current working directory (default: current directory)

    Returns:
        Tracks registry path or None if not found
    """
    if cwd is None:
        cwd = Path.cwd()

    # Standard path
    registry = cwd / "conductor" / "tracks.md"
    if registry.exists():
        return registry

    return None


def extract_track_dirs(tracks_file: Path) -> List[str]:
    """Extract track directory paths from tracks.md, one per registry entry.

    Returns paths **relative to the project root** (the dir that contains
    ``conductor/``), so every caller resolves a track the same way:
    ``project_root / d / "track-state.json"``. ``find_tracks_registry`` only
    matches ``<cwd>/conductor/tracks.md``, so the cwd callers pass IS the
    project root, and ``conductor/tracks/<id>`` is the correct relative form.

    Handles all three registry formats (mirroring
    ``track_state.misc._iter_registry_entries``):

    - **checkbox** ``- [marker] desc (link/)`` — the link is the trailing
      parenthetical (greedy, so a description with parens doesn't mis-parse;
      this is the default ``new-track`` output and the format the OLD extractor
      silently missed entirely).
    - **section** ``- **Path:** [text](link/)`` — the markdown link.
    - **table** ``| id | type | status | desc |`` — carries no path, so the dir
      is derived as ``conductor/tracks/<id>`` from the id cell.

    Links are written either project-root-relative (``conductor/tracks/<id>/``)
    or conductor-root-relative (``tracks/<id>/``); both normalize to
    project-root-relative. Absolute paths, Windows drive paths, and URLs are
    dropped (a track outside the project tree can't be reached by
    ``project_root / d``). Order preserved; de-duplicated.

    Args:
        tracks_file: Path to tracks.md file

    Returns:
        List of project-root-relative track directory paths (e.g.
        ``"conductor/tracks/auth_20260706"``).
    """
    if not tracks_file.exists():
        return []

    content = tracks_file.read_text(encoding="utf-8")
    raw = []  # link paths / derived ids, each to be normalized
    for line in content.splitlines():
        stripped = line.lstrip()
        # Checkbox: the trailing (...) is the link.
        m = _RE_CHECKBOX_LINK.match(line)
        if m:
            raw.append(m.group(1))
            continue
        # Table row: | id | type | status | desc | — no path; derive from id.
        if stripped.startswith("|"):
            tid = _table_row_track_id(line)
            if tid:
                raw.append(f"tracks/{tid}")  # conductor-root-relative; normalized below
            continue
        # Section / inline markdown link: [text](link).
        raw.extend(_RE_MARKDOWN_LINK.findall(line))
        # Universal fallback: a dated track_id token on an otherwise-unmatched
        # line (plain bullet ``- auth_20260706``, bold id, checkbox-without-link,
        # inline mention). derive-name always stamps _YYYYMMDD, so this is a
        # high-signal backstop. Mirrors track_state.misc._iter_registry_entries
        # — keep in lockstep. Re-emits are deduped below.
        raw.extend(f"tracks/{tid}" for tid in _RE_TRACK_ID_TOKEN.findall(line))

    dirs = []
    seen = set()
    for p in raw:
        d = _to_project_relative(p)
        if d and d not in seen:
            seen.add(d)
            dirs.append(d)
    return dirs


def _table_row_track_id(line: str) -> Optional[str]:
    """First-cell id from a track TABLE row, or None for non-track rows.

    Skips the separator (``| --- |``) and header (``| id | track | ...``) and
    gates on a status word somewhere in the row — the same heuristic
    ``_iter_registry_entries`` uses, so a generic markdown table isn't mistaken
    for track entries.
    """
    if not _RE_TABLE_STATUS.search(line):
        return None
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if not cells:
        return None
    tid = cells[0]
    if tid.lower() in ("id", "track id", "track"):
        return None  # header
    if re.fullmatch(r"-+", tid):
        return None  # separator row
    return tid or None


def _to_project_relative(link: str) -> Optional[str]:
    """Normalize a registry link/id to a project-root-relative track dir.

    ``conductor/tracks/<id>`` is returned as-is; a conductor-root-relative
    ``tracks/<id>`` is prefixed with ``conductor/``. Absolute / drive / URL
    paths return None (unreachable via ``project_root / d``).

    Absoluteness is checked on the whitespace-trimmed link BEFORE slash-
    stripping, so a leading ``/`` (the absolute-path signal) is not erased by
    the trailing-slash cleanup.
    """
    p = (link or "").strip()
    if not p:
        return None
    low = p.lower()
    if (low.startswith(("http://", "https://"))
            or p.startswith(("/", "\\")) or _WIN_DRIVE.match(p)):
        return None
    norm = p.replace("\\", "/").strip("/")
    if not norm:
        return None
    low = norm.lower()
    if low == "conductor" or low.startswith("conductor/"):
        return norm
    return "conductor/" + norm


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