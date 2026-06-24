#!/usr/bin/env python3
"""lint-track-state — Boundary enforcement linter for Conductor tracks.

Verifies Execution Firewall rules F1, F4, and state consistency.
Can be run as CI check or pre-commit hook.
Exit 0 on pass, 1 on failure.
"""

import json
import sys
from pathlib import Path
from typing import Optional

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.json_utils import load_json_safe, load_json
from lib.env import get_track_state_json, get_plan_md_path
from lib.constants import TERMINAL_STATUSES
from lib.validation import check_state_file_age, validate_json_structure
from lib.path_utils import find_track_root, find_tracks_registry, extract_track_dirs
from lib.git_utils import docs_synced_for_track


def check_f1_rule(state_file: Path) -> tuple[bool, Optional[str]]:
    """Check F1 rule: only ONE in_progress task allowed

    Args:
        state_file: Path to track-state.json

    Returns:
        Tuple of (is_valid, error_message)
    """
    state = load_json_safe(state_file)
    if not state:
        return False, "Cannot read track-state.json"

    # Check valid state structure
    valid, error = validate_json_structure(state, ["track_id", "status", "phases"])
    if not valid:
        return False, f"Invalid state structure: {error}"

    # F1/V8 contract: at most ONE parent task in_progress, and at most ONE
    # child subtask in_progress — and that child must belong to the active
    # parent. Counting parents and children into a single list and thresholding
    # on ">2" is wrong: it admits two flat parents (2 = not > 2) and two
    # children, both of which are V8 violations this backstop exists to catch.
    active_parents = []   # "P{pi}.T{ti}"
    active_children = []  # "P{pi}.T{ti}.S{si}"
    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            if task.get("status") == "in_progress":
                active_parents.append(f"P{pi}.T{ti}")
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if sub.get("status") == "in_progress":
                    active_children.append(f"P{pi}.T{ti}.S{si}")

    violations = []
    if len(active_parents) > 1:
        violations.append(
            f"{len(active_parents)} parent tasks in_progress "
            f"({', '.join(active_parents)})"
        )
    if len(active_children) > 1:
        violations.append(
            f"{len(active_children)} subtasks in_progress "
            f"({', '.join(active_children)})"
        )
    # A child in_progress must belong to the (single) in_progress parent —
    # defends against hand-edited/corrupt state where a child is active under
    # a non-active parent (two units of work across different tasks).
    if active_children:
        active_parent_set = set(active_parents)
        for child in active_children:
            if child.rsplit(".S", 1)[0] not in active_parent_set:
                violations.append(
                    f"subtask {child} in_progress without its parent in_progress"
                )

    if violations:
        return False, (
            f"VIOLATION: F1 state lock — {'; '.join(violations)}. "
            f"Allowed: max ONE parent task [~] + ONE of its child subtasks [~]."
        )

    return True, None


def check_f4_rule(state_file: Path) -> tuple[bool, Optional[str]]:
    """Check F4 rule: SHA must exist for terminal tasks

    Args:
        state_file: Path to track-state.json

    Returns:
        Tuple of (is_valid, error_message)
    """
    state = load_json_safe(state_file)
    if not state:
        return False, "Cannot read track-state.json"

    # Check valid state structure
    valid, error = validate_json_structure(state, ["track_id", "status", "phases"])
    if not valid:
        return False, f"Invalid state structure: {error}"

    # Terminal statuses sourced from the shared lib.constants layer (same set
    # track_state uses). "failed" excluded: _do_fail never sets commit_sha, so a
    # SHA check for failed tasks would always be a false positive.
    terminal_statuses = TERMINAL_STATUSES
    missing_shas = []

    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            if task.get("status") in terminal_statuses:
                if not task.get("commit_sha"):
                    missing_shas.append(f'P{pi}.T{ti}: {task.get("name", "?")}')

            # Check subtasks
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if sub.get("status") in terminal_statuses:
                    if not sub.get("commit_sha"):
                        missing_shas.append(f'P{pi}.T{ti}.S{si}: {sub.get("name", "?")}')

    if missing_shas:
        return False, f"VIOLATION: Missing commit SHAs for terminal tasks: {'; '.join(missing_shas)}"

    return True, None


def check_state_consistency(state_file: Path) -> tuple[bool, Optional[str]]:
    """Check state consistency using track-state validate

    Args:
        state_file: Path to track-state.json

    Returns:
        Tuple of (is_valid, error_message)
    """
    track_dir = state_file.parent
    track_state_path = str(Path(__file__).parent / "track-state")

    import subprocess
    result = subprocess.run(
        [track_state_path, "validate", str(track_dir)],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return False, "validate command failed"

    try:
        validate_result = json.loads(result.stdout)
        if not validate_result.get("valid", False):
            errors = validate_result.get("errors", [])
            if errors:
                return False, "; ".join(errors)
    except json.JSONDecodeError:
        return False, "Invalid JSON response from validate"

    return True, None


def check_misplaced_docs(track_dir: Path) -> list:
    """Flag non-meta .md files at the top of a track dir.

    tracks/<track>/ is reserved for Spec/Plan/Meta (per the project's CLAUDE.md).
    A stray .md there (e.g. an exploration dump, an analysis doc) means a producer
    wrote to the wrong channel — durable findings belong in conductor/design/ or
    conductor/resource/, scratch in .conductor/. Does NOT descend into .conductor/
    (sanctioned working memory) or subdirs. Returns the stray filenames.
    """
    track_meta_docs = {"spec.md", "plan.md", "handoff.md", "index.md", "issues.md"}
    stray = []
    if not track_dir.is_dir():
        return stray
    for p in track_dir.iterdir():
        if p.is_file() and p.suffix == ".md" and p.name not in track_meta_docs:
            stray.append(p.name)
    return stray


def check_docsync_before_archive(track_dir: Path) -> tuple[bool, Optional[str]]:
    """Backstop: an archived track must carry a doc-sync commit.

    Delegates the git-log probe to lib.git_utils.docs_synced_for_track — the same
    single source cmd_archive's gate uses — so the lint backstop cannot drift from
    the gate when the doc-sync commit format changes. Catches tracks flipped to
    'archived' outside the cmd_archive gate (e.g. hand-edited track-state.json).

    Returns (ok, warning_message). ok=False → WARN (not error). No-op for
    non-archived tracks.
    """
    state = load_json_safe(track_dir / "track-state.json")
    if not state or state.get("status") != "archived":
        return True, None
    if docs_synced_for_track(track_dir):
        return True, None
    track_id = track_dir.name
    return False, (f"archived without a doc-sync commit "
                   f"(no docs(conductor): ...[{track_id}] found) — durable findings "
                   f"may not be synced to the wiki corpus")


def check_stale_state(state_file: Path) -> tuple[bool, Optional[str]]:
    """Check for stale state with in_progress tasks (>24h)

    Args:
        state_file: Path to track-state.json

    Returns:
        Tuple of (is_fresh, warning_message)
    """
    is_fresh, message = check_state_file_age(state_file, 24)
    if not is_fresh and message:
        # Only warn if there are in_progress tasks
        state = load_json_safe(state_file)
        if state:
            has_active = False
            for phase in state.get("phases", []):
                for task in phase.get("tasks", []):
                    if task.get("status") == "in_progress":
                        has_active = True
                        break
                if has_active:
                    break

            if has_active:
                return False, message

    return True, None


def main():
    """Main linter function"""
    # Get current working directory
    cwd = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    tracks_file = find_tracks_registry(cwd)

    # Check if tracks.md exists
    if not tracks_file:
        print("No conductor/tracks.md found. Nothing to lint.")
        sys.exit(0)

    # Extract track directories
    track_dirs = extract_track_dirs(tracks_file)

    errors = 0
    warnings = 0

    for track_dir in track_dirs:
        full_dir = cwd / track_dir
        state_file = full_dir / "track-state.json"

        # Skip if no track-state.json
        if not state_file.exists():
            continue

        # Get track ID
        state = load_json_safe(state_file)
        track_id = state.get("track_id", "unknown") if state else "unknown"

        print(f"\nChecking track: {track_id}")

        # F1: Global State Lock
        valid, error = check_f1_rule(state_file)
        if not valid:
            print(f"[F1 ERROR] Track '{track_id}': {error}")
            print("  Fix: Run track-state recover to reset stale locks.")
            errors += 1
        else:
            print(f"[F1 PASS] Track '{track_id}' has ≤1 in_progress parent + ≤1 child subtask")

        # F4: SHA Must Exist
        valid, error = check_f4_rule(state_file)
        if not valid:
            print(f"[F4 ERROR] Track '{track_id}': {error}")
            print("  Fix: Run git log to find SHAs, then track-state complete <dir> <p> <t> --sha <sha>")
            print("        or: track-state complete <dir> --phase <p> --task <t> --sha <sha>")
            errors += 1
        else:
            print(f"[F4 PASS] Track '{track_id}' has required SHAs for terminal tasks")

        # State consistency
        valid, error = check_state_consistency(state_file)
        if not valid:
            print(f"[STATE ERROR] Track '{track_id}': {error}")
            print("  Fix: Run track-state validate <dir> --fix to auto-repair, or check track-state.json manually.")
            errors += 1
        else:
            print(f"[STATE PASS] Track '{track_id}' is consistent")

        # Stale state warnings
        valid, warning = check_stale_state(state_file)
        if not valid:
            print(f"[WARNING] Track '{track_id}': {warning}")
            print("  Hint: Run /conductor:implement to recover, or /conductor:revert to roll back.")
            warnings += 1
        else:
            print(f"[AGE PASS] Track '{track_id}' state is fresh")

        # Misplaced docs (contract: tracks/<track>/ is Spec/Plan/Meta only)
        stray = check_misplaced_docs(full_dir)
        if stray:
            print(f"[DOC WARN] Track '{track_id}': non-meta .md in track dir: {', '.join(stray)}")
            print("  Fix: durable findings → conductor/design/ or conductor/resource/; "
                  "scratch → .conductor/. tracks/<track>/ holds Spec/Plan/Meta only.")
            warnings += 1
        else:
            print(f"[DOC PASS] Track '{track_id}' has only meta docs in track dir")

        # Doc-sync backstop: an archived track must have run doc-sync
        # (cmd_archive enforces this on the programmatic path; this catches
        # hand-edited state that bypassed the gate).
        ds_ok, ds_msg = check_docsync_before_archive(full_dir)
        if not ds_ok:
            print(f"[DOCSYNC WARN] Track '{track_id}': {ds_msg}")
            print("  Hint: re-run the post-loop DOC SYNC phase, or "
                  "'track-state archive --force' if intentional.")
            warnings += 1

    # Summary
    print(f"\nLint complete: {errors} errors, {warnings} warnings.")

    if errors > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()