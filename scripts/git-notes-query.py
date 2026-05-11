#!/usr/bin/env python3
"""git-notes-query: Query and display Conductor git notes audit data.

Usage:
  git-notes-query --sha <commit-hash>          Show notes for specific commit
  git-notes-query --track <track-id>           Show all notes for a track
  git-notes-query --session <session-id>       Show all notes for a session
  git-notes-query --coverage-trend             Show test coverage trend
  git-notes-query --files                      Show all changed files
  git-notes-query --deviations                 Show spec deviations
"""

import json
import subprocess
import sys
from typing import Optional


def get_git_notes_ref_list() -> list[str]:
    """Get list of git notes references

    Returns:
        List of note references (commit SHAs)
    """
    result = subprocess.run(
        ["git", "notes", "list"],
        capture_output=True,
        text=True,
        check=False
    )

    if result.returncode != 0:
        return []

    refs = []
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            refs.append(parts[1])

    return refs


def get_git_note(commit_ref: str) -> Optional[dict]:
    """Get git note for a specific commit

    Args:
        commit_ref: Commit reference (SHA)

    Returns:
        Note data or None
    """
    result = subprocess.run(
        ["git", "notes", "show", commit_ref],
        capture_output=True,
        text=True,
        check=False
    )

    if result.returncode != 0:
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def get_all_conductor_notes() -> list[dict]:
    """Get all notes that contain conductor data

    Returns:
        List of conductor notes
    """
    notes = []
    refs = get_git_notes_ref_list()

    for ref in refs:
        note = get_git_note(ref)
        if note and "conductor" in note:
            note["commit_ref"] = ref
            notes.append(note)

    return notes


def query_by_sha(sha: str) -> None:
    """Query notes for specific commit SHA

    Args:
        sha: Commit SHA
    """
    note = get_git_note(sha)
    if note:
        print(json.dumps(note, indent=2, ensure_ascii=False))
    else:
        print(f"No notes found for {sha}")


def query_by_track(track_id: str) -> None:
    """Query notes by track ID

    Args:
        track_id: Track ID
    """
    notes = get_all_conductor_notes()
    filtered = [n for n in notes if n.get("conductor", {}).get("track_id") == track_id]

    for note in filtered:
        print(json.dumps(note, indent=2, ensure_ascii=False))


def query_by_session(session_id: str) -> None:
    """Query notes by session ID

    Args:
        session_id: Session ID
    """
    notes = get_all_conductor_notes()
    filtered = [n for n in notes if n.get("conductor", {}).get("session_id") == session_id]

    for note in filtered:
        print(json.dumps(note, indent=2, ensure_ascii=False))


def show_coverage_trend() -> None:
    """Show test coverage trend"""
    notes = get_all_conductor_notes()

    print("# Test Coverage Trend")
    print("Timestamp\tTC Count")

    for note in sorted(notes, key=lambda n: n.get("conductor", {}).get("timestamp", "")):
        conductor = note.get("conductor", {})
        requirements = note.get("requirements", {})
        timestamp = conductor.get("timestamp", "")
        tc_count = len(requirements.get("tc_implemented", []))

        print(f"{timestamp}\t{tc_count}")


def show_files() -> None:
    """Show all changed files by track"""
    notes = get_all_conductor_notes()

    print("# All Changed Files by Track")

    for note in notes:
        conductor = note.get("conductor", {})
        implementation = note.get("implementation", {})

        track_id = conductor.get("track_id", "")
        files_added = ",".join(implementation.get("files_added", []))
        files_modified = ",".join(implementation.get("files_modified", []))

        print(f"{track_id}\t{files_added}\t{files_modified}")


def show_deviations() -> None:
    """Show specification deviations"""
    notes = get_all_conductor_notes()

    print("# Specification Deviations")

    for note in notes:
        requirements = note.get("requirements", {})
        conductor = note.get("conductor", {})
        task = note.get("task", {})
        implementation = note.get("implementation", {})

        deviation = requirements.get("spec_deviation", "")
        if deviation and deviation != "NONE":
            phase = task.get("phase", "?")
            task_num = task.get("task", "?")
            summary = implementation.get("summary", "")

            print(f"{conductor.get('track_id', '')} {phase}.{task_num}: {deviation} - {summary}")


def show_summary() -> None:
    """Show summary of all notes"""
    notes = get_all_conductor_notes()

    print("# Conductor Audit Summary")
    print("")

    for note in notes:
        conductor = note.get("conductor", {})
        task = note.get("task", {})
        implementation = note.get("implementation", {})

        track_id = conductor.get("track_id", "")
        phase = task.get("phase", "?")
        task_num = task.get("task", "?")
        summary = implementation.get("summary", "")

        print(f"[{track_id}] Phase {phase}.Task {task_num}: {summary}")


def print_usage() -> None:
    """Print usage information"""
    print("Usage: git-notes-query [OPTIONS]")
    print("")
    print("Options:")
    print("  --sha SHA              Show notes for specific commit")
    print("  --track ID             Show all notes for a track")
    print("  --session ID           Show all notes for a session")
    print("  --coverage-trend       Show test coverage trend")
    print("  --files                Show all changed files")
    print("  --deviations           Show spec deviations")
    print("  --help                 Show this help message")


def main() -> None:
    """Main function"""
    query_type = ""
    arg_value = ""

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--sha" and i + 1 < len(args):
            query_type = "sha"
            arg_value = args[i + 1]
            i += 2
        elif arg == "--track" and i + 1 < len(args):
            query_type = "track"
            arg_value = args[i + 1]
            i += 2
        elif arg == "--session" and i + 1 < len(args):
            query_type = "session"
            arg_value = args[i + 1]
            i += 2
        elif arg == "--coverage-trend":
            query_type = "coverage_trend"
            i += 1
        elif arg == "--files":
            query_type = "files"
            i += 1
        elif arg == "--deviations":
            query_type = "deviations"
            i += 1
        elif arg == "--help":
            print_usage()
            sys.exit(0)
        else:
            print(f"Unknown option: {arg}")
            sys.exit(1)

    # Execute query based on type
    if query_type == "sha":
        if not arg_value:
            print("Error: --sha requires a commit hash")
            sys.exit(1)
        query_by_sha(arg_value)
    elif query_type == "track":
        if not arg_value:
            print("Error: --track requires a track ID")
            sys.exit(1)
        query_by_track(arg_value)
    elif query_type == "session":
        if not arg_value:
            print("Error: --session requires a session ID")
            sys.exit(1)
        query_by_session(arg_value)
    elif query_type == "coverage_trend":
        show_coverage_trend()
    elif query_type == "files":
        show_files()
    elif query_type == "deviations":
        show_deviations()
    else:
        # Default: show summary
        show_summary()


if __name__ == "__main__":
    main()