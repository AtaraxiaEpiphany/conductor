"""Shared library for validation functions

Provides common validation and checking utilities.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


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


# --- commit-message extraction helpers -------------------------------------
# Captures the value of the FIRST `git commit -m` argument. Quoted forms
# (double/single) span newlines so multi-line messages are captured whole; a
# bare -m value is a single whitespace-delimited token. The lazy `.*?` binds
# the subject (-m) rather than a later body -m. The `(?<![\w-])-m\s*` flag
# anchor matches the -m in every shell form git accepts — `git commit -m"x"`,
# `-m 'x'`, `-mx`, and `-m x` — so the no-space shorthand can't slip past the
# V10 gate. The lookbehind stops `-m` matching inside a word/flag like
# `file-m.txt` or `--message=`.
_M_ARG_PATTERN = re.compile(
    r'git\s+commit\s+.*?(?<![\w-])-m\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))',
    re.IGNORECASE,
)

# Heredoc opener:  <<-, optional space, optional quotes, then the delimiter word.
_HEREDOC_OPENER = re.compile(r'<<-?\s*[\'"]?(\w+)[\'"]?')

# Shell constructs that make the literal message unknowable without execution:
# command substitution $(…) or `…`, and variable expansion $NAME / ${…}.
_DYNAMIC_PATTERN = re.compile(r'\$\(|`|\$\{|\$\w')

# Unquoted shell metacharacters that turn a bare ``-m`` value into a bash syntax
# error rather than a message: ``()`` is empty-function syntax, ``<>`` are
# redirections (and the ``<commit_msg>`` placeholder footprint), ``{}`` a brace
# group, ``;|&`` command separators. Any competent caller QUOTES the commit
# message, so an unquoted -m value is suspect; one carrying these chars is a
# definitively broken command (the class of bug where the orchestrator emitted
# ``git commit -m ()`` and bash died with "syntax error near unexpected token").
_BARE_M_BREAKER = re.compile(r'[()<>{};|&]')


def commit_arg_shell_broken_reason(command: str) -> Optional[str]:
    """Return a deny-reason if a ``git commit -m`` argument is shell-broken.

    Catches the orchestrator-placeholder / mis-substitution bug class: a bare
    UNQUOTED ``-m`` token (group 3 of ``_M_ARG_PATTERN``'s ``"…"|'…'|\\S+``
    alternation) that carries a shell-breaking metacharacter — e.g.
    ``git commit -m ()`` (empty-function syntax error) or
    ``git commit -m <commit_msg>`` (unfilled placeholder / redirection).

    Quoted values (groups 1/2) are shell-safe by construction and never flagged
    here; dynamic substitutions (``$(…)``, ``$VAR``) are handled by the
    allow-through policy in ``_extract_commit_message``. Returns None when the
    argument is acceptable. This is a hard-deny signal (the command cannot run
    as written), distinct from V10's soft ask for non-conventional *style*.
    """
    m = _M_ARG_PATTERN.search(command)
    if not m:
        return None
    # group(1)=double-quoted, group(2)=single-quoted, group(3)=bare \S+ token.
    bare = m.group(3)
    if bare is not None and _BARE_M_BREAKER.search(bare):
        return (
            f"git commit -m argument {bare!r} is an unquoted token carrying shell "
            f"metacharacters — bash will raise a syntax error. Quote the message, "
            f'e.g. git commit -m "type(scope): description".'
        )
    return None


def _extract_heredoc_body(text: str) -> Optional[str]:
    """Return the body of a shell heredoc embedded in ``text``, else None.

    Matches ``<<'EOF'`` / ``<<EOF`` / ``<<-EOF`` style openers and returns the
    stripped lines up to the closing delimiter line (``EOF`` or the compact
    ``EOF)`` that closes a ``$(cat <<'EOF' … EOF)`` substitution).
    """
    opener = _HEREDOC_OPENER.search(text)
    if not opener:
        return None
    close = re.compile(rf'^{re.escape(opener.group(1))}\s*\)?\s*$')
    body: List[str] = []
    for line in text[opener.end():].splitlines():
        if close.match(line.strip()):
            return "\n".join(body).strip() or None
        body.append(line)
    return None


def _extract_commit_message(command: str) -> Optional[str]:
    """Extract the literal commit message from a ``git commit -m …`` command.

    When the value is supplied via a shell heredoc (e.g. ``-m "$(cat <<'EOF'
    … EOF)"``) the heredoc body is returned rather than the surrounding shell
    syntax. Returns None when there is no ``-m`` argument, or when the value
    relies on command/variable substitution and cannot be read statically — in
    that case the caller should allow the commit through without blocking,
    since blocking would be a false positive on shell syntax we cannot expand.
    """
    match = _M_ARG_PATTERN.search(command)
    if not match:
        return None
    value = match.group(1) or match.group(2) or match.group(3)
    if value is None:
        return None

    heredoc_body = _extract_heredoc_body(value)
    if heredoc_body is not None:
        value = heredoc_body

    # Can't validate shell we'd have to execute to read.
    if _DYNAMIC_PATTERN.search(value):
        return None
    return value


def _build_commit_suggestion(subject: str) -> str:
    """Build a conventional-format suggestion from a non-conforming subject."""
    from .constants import VALID_COMMIT_TYPES

    suggested = subject.strip()
    if not suggested:
        return "type(scope): description"

    # Infer a conductor-scoped chore from common orchestration action verbs.
    if re.match(
        r'(?:Start|Complete|Fail|Skip|Update|Sync|Archive|Finalize)\s+',
        suggested, re.IGNORECASE,
    ):
        desc = suggested[0].lower() + suggested[1:]
        return f"chore(conductor): {desc}"

    # Subject already has a valid type but a missing/malformed scope — repair
    # just the scope instead of rewriting the whole line. (VALID_COMMIT_TYPES
    # is already a capturing group, so group(1)=type, group(2)=rest.)
    type_match = re.match(
        rf'^{VALID_COMMIT_TYPES}\b[^\S(]*(.*)', suggested, re.IGNORECASE,
    )
    if type_match:
        ctype = type_match.group(1).lower()
        rest = type_match.group(2).lstrip(': ').strip()
        return f"{ctype}(scope): {rest}" if rest else f"{ctype}(scope): description"

    # Subject has the ``word(scope): description`` shape but ``word`` is not a
    # valid commit type — e.g. ``conductor(checkpoint): …``, where "conductor"
    # is the plugin name misused as a type. The user already chose a real scope,
    # so swap in a valid type and keep both their scope and description. Without
    # this, the generic fallback below produced a double-prefixed
    # ``fix(scope): conductor(checkpoint): …`` and discarded their scope.
    scoped = re.match(r'^(\w+)\(([^)]+)\):\s*(.+)$', suggested, re.IGNORECASE)
    if scoped:
        return f"chore({scoped.group(2)}): {scoped.group(3)}"

    snippet = suggested[:80]
    return f"fix(scope): {snippet}"


def validate_commit_message(command: str) -> tuple[bool, Optional[str]]:
    """Validate the first ``git commit -m`` message is conventional-commits.

    The subject line must match ``type(scope): description`` where type is one
    of feat, fix, docs, style, refactor, test, chore.

    Heredoc-built messages (``-m "$(cat <<'EOF' … EOF)"``) are parsed for their
    literal body; messages assembled via command/variable substitution that
    cannot be read statically are allowed through without blocking.

    Args:
        command: Full bash command string

    Returns:
        (is_valid, suggested_fix). ``suggested_fix`` is None when the message
        is valid, or when it can't be determined statically.
    """
    from .constants import COMMIT_MSG_PATTERN

    message = _extract_commit_message(command)
    if message is None:
        return True, None

    stripped = message.strip()
    subject = stripped.splitlines()[0] if stripped else ""
    if re.match(COMMIT_MSG_PATTERN, subject):
        return True, None

    return False, _build_commit_suggestion(subject)