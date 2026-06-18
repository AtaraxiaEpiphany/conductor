"""Shared library for validation functions

Provides common validation and checking utilities.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# Dangerous git operations that should be blocked during active tracks.
# NOTE: "git branch -D" is matched case-sensitively — see is_dangerous_git_operation.
DANGEROUS_GIT_OPS = {
    "git reset --hard",
    "git rebase",
    "git clean",
    "git filter-branch",
    "git checkout --force",
    "git branch -D",
}


# Dangerous command patterns to scan for
DANGEROUS_PATTERNS = [
    r"rm\s+-rf",
    r"curl.*\|\s*sh",
    r"eval\s+\$",
    r"rm\s+-rf\s+/",
]


def is_dangerous_git_operation(command: str) -> bool:
    """Check if command is a dangerous git operation.

    Matching is case-insensitive for subcommands, with one exception:
    ``git branch -D`` (force-delete) is matched against the original command
    casing. Lowercasing the whole command collapses ``-D`` into ``-d``, which
    both hid force-delete from detection entirely and would falsely flag the
    safe ``git branch -d`` (deletes merged branches only).

    Args:
        command: Command string to check

    Returns:
        True if command is dangerous
    """
    command_lower = command.lower()
    for dangerous_op in DANGEROUS_GIT_OPS:
        haystack = command if dangerous_op.endswith(" -D") else command_lower
        if dangerous_op in haystack:
            return True
    return False


def contains_dangerous_pattern(content: str) -> bool:
    """Check if content contains dangerous command patterns

    Args:
        content: Content to check

    Returns:
        True if dangerous pattern found
    """
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return True
    return False


def validate_json_structure(
    data: Dict,
    required_fields: List[str],
    optional_fields: Optional[List[str]] = None
) -> tuple[bool, Optional[str]]:
    """Validate JSON structure has required fields

    Args:
        data: Data dictionary to validate
        required_fields: List of required field names
        optional_fields: List of optional field names

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check required fields
    missing_fields = []
    for field in required_fields:
        if field not in data:
            missing_fields.append(field)

    if missing_fields:
        return False, f"Missing required fields: {', '.join(missing_fields)}"

    # Check for unexpected fields if optional_fields provided
    if optional_fields is not None:
        allowed_fields = set(required_fields + optional_fields)
        unexpected_fields = set(data.keys()) - allowed_fields
        if unexpected_fields:
            return False, f"Unexpected fields: {', '.join(unexpected_fields)}"

    return True, None


def validate_track_state(state: Dict) -> tuple[bool, Optional[str]]:
    """Validate track state structure

    Args:
        state: Track state dictionary

    Returns:
        Tuple of (is_valid, error_message)
    """
    required_fields = ["track_id", "type", "status", "description", "current_phase_index",
                       "current_task_index", "updated_at", "phases"]
    return validate_json_structure(state, required_fields)


def validate_plan_markers(plan_content: str, task_id: str) -> tuple[bool, Optional[str]]:
    """Validate plan has proper task markers

    Args:
        plan_content: Plan markdown content
        task_id: Expected current task ID

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check for current task marker
    current_pattern = rf"<!-- CURRENT-TASK:\s*{re.escape(task_id)}\s*-->"
    if not re.search(current_pattern, plan_content):
        return False, f"Plan missing current task marker for {task_id}"

    # Check for completed pattern
    if "<!-- COMPLETED-TASK" not in plan_content:
        return False, "Plan missing completed task markers"

    return True, None


def check_state_file_age(state_file: Path, max_hours: int = 24) -> tuple[bool, Optional[str]]:
    """Check if state file is too old (stale lock)

    Args:
        state_file: State file path
        max_hours: Maximum allowed age in hours

    Returns:
        Tuple of (is_fresh, warning_message)
    """
    if not state_file.exists():
        return True, None

    from .path_utils import get_file_age_hours

    age_hours = get_file_age_hours(state_file)
    if age_hours is None:
        return True, None

    if age_hours > max_hours:
        return False, f"State file is {age_hours:.1f} hours old (max {max_hours}h)"

    return True, None


def validate_no_duplicate_tasks(tasks: List[Dict]) -> tuple[bool, Optional[str]]:
    """Check for duplicate task IDs

    Args:
        tasks: List of task dictionaries

    Returns:
        Tuple of (is_valid, error_message)
    """
    seen_ids = set()
    duplicates = []

    for task in tasks:
        task_id = task.get("id")
        if task_id:
            if task_id in seen_ids:
                duplicates.append(task_id)
            seen_ids.add(task_id)

    if duplicates:
        return False, f"Duplicate task IDs: {', '.join(duplicates)}"

    return True, None


def validate_git_commit_sha(sha: str) -> bool:
    """Validate git commit SHA format

    Args:
        sha: Commit SHA string

    Returns:
        True if valid format
    """
    # Accept both short (7-8 chars) and full (40 chars) SHA
    return bool(re.match(r'^[0-9a-f]{7,40}$', sha.lower()))


def sanitize_filename(filename: str) -> str:
    """Sanitize filename by removing dangerous characters

    Args:
        filename: Original filename

    Returns:
        Sanitized filename
    """
    # Remove or replace dangerous characters
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)
    # Remove leading/trailing dots and spaces
    sanitized = sanitized.strip('. ')
    # Ensure not empty
    return sanitized or "untitled"


def validate_path_safe(path_str: str) -> tuple[bool, Optional[str]]:
    """Validate path string doesn't contain directory traversal

    Args:
        path_str: Path string to validate

    Returns:
        Tuple of (is_safe, error_message)
    """
    if ".." in path_str:
        return False, "Path contains directory traversal (..)"

    if path_str.startswith("/") and not path_str.startswith("//"):
        # Absolute path might be okay depending on context
        pass

    return True, None


def validate_commit_message(command: str) -> tuple[bool, Optional[str]]:
    """Validate git commit -m message follows conventional commit format.

    Extracts the -m argument from a git commit command and checks it
    matches: type(scope): description

    Standard types: feat, fix, docs, style, refactor, test, chore

    Args:
        command: Full bash command string

    Returns:
        Tuple of (is_valid, suggested_fix). suggested_fix is None when valid.
    """
    from .constants import COMMIT_MSG_PATTERN, VALID_COMMIT_TYPES

    # Extract the -m message from the command.
    # Handles: git commit -m "message", git commit -m 'message', git commit -m message
    message = None

    match = re.search(
        r'git\s+commit\s+.*-m\s+(?:"([^"]*)"|\'([^\']*)\'|(\S+))',
        command, re.IGNORECASE
    )
    if match:
        message = match.group(1) or match.group(2) or match.group(3)

    if message is None:
        # No -m flag found — might be git commit (opens editor) or git commit -F file
        return True, None

    if re.match(COMMIT_MSG_PATTERN, message.strip()):
        return True, None

    # Build a suggested fix by trying to infer the type from context
    # Common patterns agents produce that need correction
    suggested = message.strip()

    # Try to extract a scope from common conductor patterns
    conductor_match = re.match(
        r'(?:Start|Complete|Fail|Skip|Update|Sync|Archive|Finalize)\s+(.+)',
        suggested, re.IGNORECASE
    )
    if conductor_match:
        action = "chore"
        scope = "conductor"
        desc = suggested[0].lower() + suggested[1:]
        suggested = f"{action}({scope}): {desc}"
    else:
        # Generic: wrap in chore(scope) — agent can adjust
        suggested = f"fix(scope): {suggested}"

    return False, suggested