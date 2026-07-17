"""Shared library for git operations

Provides git utility functions for notes, commits, and status checks.
"""

import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any


def run_git_command(
    args: List[str],
    cwd: Optional[Path] = None,
    capture_output: bool = True
) -> subprocess.CompletedProcess:
    """Run git command

    Args:
        args: Git command arguments
        cwd: Working directory
        capture_output: Whether to capture output

    Returns:
        Completed process
    """
    cmd = ["git"] + args
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture_output,
        text=True,
        check=False
    )


def get_git_status(cwd: Optional[Path] = None) -> Dict[str, Any]:
    """Get git status

    Args:
        cwd: Working directory

    Returns:
        Git status dictionary
    """
    result = run_git_command(["status", "--porcelain"], cwd=cwd)

    if result.returncode != 0:
        return {"error": result.stderr}

    files = []
    for line in result.stdout.splitlines():
        if line.strip():
            status = line[:2]
            path = line[3:]
            files.append({
                "status": status,
                "path": path
            })

    return {"files": files, "clean": len(files) == 0}


# Conductor-managed namespaces — never counted as "stranded implementation"
# work. ``conductor/`` covers the per-track tree (``conductor/tracks/<id>/``)
# alongside the repo-root ``.conductor/`` runtime dir. Shared by the
# write-result clean-tree hook and the finalize telemetry so the two cannot
# drift on what counts as implementation work.
_CONDUCTOR_MANAGED_PREFIXES = (
    "track-state", "plan.md", ".conductor/", "conductor/", "issues.md",
    "handoff.md",
)


def implementation_uncommitted_files(track_dir):
    """Repo-relative implementation files that are uncommitted (unstaged,
    staged, or untracked), excluding conductor-managed artifacts.

    Uses ``--untracked-files=all`` so a brand-new file under a fresh directory
    expands to its real path (default porcelain collapses it to just the dir,
    e.g. ``?? src/``), which would break both classification and any file list
    shown to the agent. Returns a sorted list; empty list on any git error
    (fail-open — callers must never raise on a dirty-tree probe).
    """
    result = run_git_command(
        ["status", "--porcelain", "--untracked-files=all"], cwd=track_dir)
    if result.returncode != 0:
        return []
    files = set()
    for line in result.stdout.splitlines():
        if len(line) <= 3:
            continue
        path = line[3:]
        # Rename/copy: "<orig> -> <dest>" — keep the destination (what now exists).
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        path = path.strip().strip('"')
        if not path:
            continue
        if any(path.startswith(p) or path == p.rstrip("/")
               for p in _CONDUCTOR_MANAGED_PREFIXES):
            continue
        files.add(path)
    return sorted(files)


def get_current_branch(cwd: Optional[Path] = None) -> Optional[str]:
    """Get current git branch

    Args:
        cwd: Working directory

    Returns:
        Current branch name or None
    """
    result = run_git_command(["branch", "--show-current"], cwd=cwd)

    if result.returncode == 0:
        return result.stdout.strip()

    return None


def get_git_notes(commit: str, cwd: Optional[Path] = None) -> Optional[str]:
    """Get git notes for a commit

    Args:
        commit: Commit SHA
        cwd: Working directory

    Returns:
        Notes content or None
    """
    result = run_git_command(["notes", "show", commit], cwd=cwd)

    if result.returncode == 0:
        return result.stdout

    return None


def write_git_note(
    commit: str,
    content: str,
    cwd: Optional[Path] = None,
    note_ref: str = "refs/notes/conductor"
) -> bool:
    """Write git note to a commit

    Args:
        commit: Commit SHA
        content: Note content
        cwd: Working directory
        note_ref: Note reference

    Returns:
        True if successful
    """
    result = run_git_command(
        ["notes", "--ref", note_ref, "add", "-f", "-m", content, commit],
        cwd=cwd
    )

    return result.returncode == 0


def has_git_changes(cwd: Optional[Path] = None) -> bool:
    """Check if git has uncommitted changes

    Args:
        cwd: Working directory

    Returns:
        True if there are changes
    """
    status = get_git_status(cwd)
    return not status.get("clean", True)


def get_git_log(
    since: Optional[str] = None,
    until: Optional[str] = None,
    format: str = "%H|%an|%ad|%s",
    date_format: str = "iso",
    cwd: Optional[Path] = None,
    max_count: Optional[int] = None
) -> List[Dict[str, str]]:
    """Get git log entries

    Args:
        since: Since date/commit
        until: Until date/commit
        format: Format string
        date_format: Date format
        cwd: Working directory
        max_count: Maximum number of commits

    Returns:
        List of log entries
    """
    args = ["log"]

    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if max_count:
        args.extend(["--max-count", str(max_count)])

    args.extend(["--format", format])
    args.extend(["--date", date_format])

    result = run_git_command(args, cwd=cwd)

    if result.returncode != 0:
        return []

    entries = []
    for line in result.stdout.splitlines():
        if line.strip():
            parts = line.split("|", 3)
            if len(parts) >= 4:
                entry = {
                    "sha": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "subject": parts[3]
                }
                entries.append(entry)

    return entries


def get_commits_by_message(
    pattern: str,
    cwd: Optional[Path] = None
) -> List[Dict[str, str]]:
    """Get commits with message matching pattern

    Args:
        pattern: Pattern to search for in commit messages
        cwd: Working directory

    Returns:
        List of matching commits
    """
    args = ["log", "--grep", pattern, "--format=%H|%an|%ad|%s", "--date=iso"]
    result = run_git_command(args, cwd=cwd)

    if result.returncode != 0:
        return []

    entries = []
    for line in result.stdout.splitlines():
        if line.strip():
            parts = line.split("|", 3)
            if len(parts) >= 4:
                entry = {
                    "sha": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "subject": parts[3]
                }
                entries.append(entry)

    return entries


def get_file_commits(
    file_path: str,
    cwd: Optional[Path] = None
) -> List[Dict[str, str]]:
    """Get commit history for a specific file

    Args:
        file_path: File path
        cwd: Working directory

    Returns:
        List of commits that modified the file
    """
    args = ["log", "--", file_path, "--format=%H|%an|%ad|%s", "--date=iso"]
    result = run_git_command(args, cwd=cwd)

    if result.returncode != 0:
        return []

    entries = []
    for line in result.stdout.splitlines():
        if line.strip():
            parts = line.split("|", 3)
            if len(parts) >= 4:
                entry = {
                    "sha": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "subject": parts[3]
                }
                entries.append(entry)

    return entries


def docs_synced_for_track(track_dir) -> bool:
    """Return True if a doc-sync commit exists for this track.

    Evidence that the post-loop DOC SYNC phase ran: the doc-sync agents commit
    ``docs(conductor): ... [{TRACK_ID}]``. The single source for "is this track
    synced" — consumed by cmd_archive's archive gate (via the
    ``track_state.git_ops`` re-export) AND by lint-track-state's
    ``check_docsync_before_archive`` backstop, so the gate and the lint backstop
    cannot drift apart when the doc-sync commit format changes.

    Self-contained subprocess call (only stdlib); returns False on any git
    failure or non-repo dir so callers degrade to "not synced".
    """
    track_id = Path(track_dir).name
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--format=%s", "--grep",
             "docs(conductor):", "-50"],
            capture_output=True, text=True, cwd=str(track_dir), timeout=10
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        needle = f"[{track_id}]"
        return any(needle in line for line in result.stdout.splitlines())
    except Exception:
        return False


def wiki_phase2_committed_for_track(track_dir) -> bool:
    """Return True if the wiki-synthesizer (doc-sync Phase 2) commit exists.

    Phase 1 (corpus-writer) and Phase 2 (wiki-synthesizer) both make a
    ``docs(conductor): ... [{TRACK_ID}]`` commit, so :func:`docs_synced_for_track`
    can't tell them apart. This discriminator greps for Phase 2's distinct
    ``Wiki sync`` subject, so the post-loop spine can resume at Phase 2 when
    Phase 1 ran but Phase 2 was interrupted out (the two-tier doc-sync gate).
    """
    track_id = Path(track_dir).name
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--format=%s", "--grep",
             "docs(conductor):", "-50"],
            capture_output=True, text=True, cwd=str(track_dir), timeout=10
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        needle = f"[{track_id}]"
        return any("Wiki sync" in line and needle in line
                   for line in result.stdout.splitlines())
    except Exception:
        return False