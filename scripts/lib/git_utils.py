"""Shared library for git operations

Provides git utility functions for notes, commits, and status checks.
"""

import subprocess
from pathlib import Path
from typing import Optional, List


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


# Conductor-managed namespaces — never counted as "stranded implementation"
# work. ``conductor/`` covers the per-track tree (``conductor/tracks/<id>/``)
# alongside the repo-root ``.conductor/`` runtime dir. Shared by the
# write-result clean-tree hook and the finalize telemetry so the two cannot
# drift on what counts as implementation work.
_CONDUCTOR_MANAGED_PREFIXES = (
    "track-state", "plan.md", ".conductor/", "conductor/",
    "handoff.md",
)


def head_commit_files(track_dir):
    """Repo-relative paths changed by the most recent commit (HEAD), or ``[]``.

    Used by the clean-tree guard's artifact check: task-executor commits in
    Step 8 *before* calling ``write-result --status success``, so by the time
    the hook fires the index is clean and the offending ``node_modules`` /
    build output is already in HEAD. Inspecting HEAD's file list is the only
    way to catch it post-commit. Returns ``[]`` on any git error (fail-open).
    """
    result = run_git_command(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        cwd=track_dir)
    if result.returncode != 0:
        return []
    return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]


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