#!/usr/bin/env python3
"""PreToolUse hook: real-time state protection for git and track-state operations.

Validates commands before execution, blocks suspicious operations.
Uses hookSpecificOutput.permissionDecision per the Claude Code hook protocol.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import (
    read_hook_input,
    write_hook_output,
    get_tool_name,
    get_cwd
)
from lib.json_utils import load_json_safe
from lib.validation import validate_commit_message
from lib.path_utils import find_tracks_registry, extract_track_dirs


def has_in_progress_task(state_file: Path) -> bool:
    """Check if state file has in_progress tasks"""
    state = load_json_safe(state_file)
    if not state:
        return False

    for phase in state.get("phases", []):
        for task in phase.get("tasks", []):
            if task.get("status") == "in_progress":
                return True
            for sub in task.get("subtasks", []):
                if sub.get("status") == "in_progress":
                    return True

    return False


def find_track_state_violations(cwd: Path, command: str) -> list[str]:
    """Find tracks with state lock violations"""
    tracks_file = find_tracks_registry(cwd)
    if not tracks_file:
        return []

    dirs = extract_track_dirs(tracks_file)

    violations = []
    cmd_lower = command.lower()

    for d in dirs:
        state_file = cwd / d / "track-state.json"
        if not state_file.exists():
            continue

        has_in_progress = has_in_progress_task(state_file)

        if has_in_progress:
            if 'rm ' in cmd_lower or 'delete' in cmd_lower:
                violations.append(f'{d}: in_progress task + deletion command')
            elif ' mv ' in cmd_lower or 'move' in cmd_lower:
                violations.append(f'{d}: in_progress task + move operation')

    return violations


def _iter_command_segments(command):
    """Split a shell command string into independent top-level segments.

    Segments are delimited by ``;``, ``&``, ``|``, and newlines that occur at
    the TOP level — i.e. outside single/double quotes, ``$(...)`` substitutions,
    and backticks — so a separator inside any of those does not start a new
    segment. Redirection fd operators (``2>&1``, ``<&``) are not separators.

    This is what lets the tamper check tell ``rm -f x; git diff track-state.json``
    (two segments; the rm never touches track-state) apart from
    ``rm "a;b" track-state.json`` (one segment; track-state really is removed).
    """
    segments = []
    seg = []
    quote = None           # '"' or "'" while inside a quote
    subshell = 0           # depth of $(...) substitutions
    backtick = False
    i, n = 0, len(command)
    while i < n:
        ch = command[i]

        # Inside a quote: consume verbatim until the matching close (honoring
        # backslash escapes inside double quotes).
        if quote:
            seg.append(ch)
            if ch == '\\' and quote == '"' and i + 1 < n:
                seg.append(command[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch in ('"', "'"):
            quote = ch
            seg.append(ch)
            i += 1
            continue
        if ch == '`':
            backtick = not backtick
            seg.append(ch)
            i += 1
            continue
        if ch == '$' and i + 1 < n and command[i + 1] == '(':
            subshell += 1
            seg.append('$(')
            i += 2
            continue
        if ch == ')' and subshell > 0:
            subshell -= 1
            seg.append(ch)
            i += 1
            continue
        if subshell > 0 or backtick:
            # Inside a substitution / backticks: separators stay verbatim.
            seg.append(ch)
            i += 1
            continue

        # Top-level separator?
        sep_len = 0
        if ch in (';', '\n'):
            sep_len = 1
        elif ch == '|' and i + 1 < n and command[i + 1] == ch:
            sep_len = 2  # ||
        elif ch == '|':
            sep_len = 1
        elif ch == '&' and i + 1 < n and command[i + 1] == ch:
            sep_len = 2  # &&
        elif ch == '&':
            # Lone & is a background operator — unless it's a redirect fd
            # (2>&1, <&, >&), in which case it binds to the redirection.
            tail = ''.join(seg).rstrip()
            if tail and tail[-1] in ('>', '<'):
                seg.append(ch)
                i += 1
                continue
            sep_len = 1

        if sep_len:
            text = ''.join(seg).strip()
            if text:
                segments.append(text)
            seg = []
            i += sep_len
            continue

        seg.append(ch)
        i += 1

    text = ''.join(seg).strip()
    if text:
        segments.append(text)
    return segments


# Matched WITHIN a single segment (see _iter_command_segments), so the gaps use
# a plain .* — the segmenter, not the regex, enforces "no crossing separators".
# re.DOTALL so a newline surviving inside a quoted segment can't break a match.
_TRACK_STATE_MOD_PATTERNS = (
    re.compile(r'(?:\brm\b|\bmv\b|git\s+rm\b).*track-state\.json',
               re.IGNORECASE | re.DOTALL),
    re.compile(r'\bsed\b.*track-state', re.IGNORECASE | re.DOTALL),
    re.compile(r'\bpython\w*.*track-state.*\bwrite\b', re.IGNORECASE | re.DOTALL),
)


def is_direct_track_state_modification(command: str) -> bool:
    """Check if command directly modifies track-state.json.

    Shell-aware: splits the command into top-level segments (respecting quotes,
    ``$(...)`` and backticks) and only then pattern-matches, so a destructive
    verb in one segment can't pair with a read-only track-state.json reference
    in another (``rm -f x; git diff track-state.json`` is NOT a match) while a
    quoted separator stays inside its segment (``rm "a;b" track-state.json``
    IS a match). Leading word boundaries avoid substring hits (perform / used).
    """
    if not command:
        return False
    for segment in _iter_command_segments(command):
        for pattern in _TRACK_STATE_MOD_PATTERNS:
            if pattern.search(segment):
                return True
    return False


# --- dangerous-git detection (segment-aware) --------------------------------
# `git` as the command word of a top-level segment with a history-rewriting or
# destructive subcommand. Segment-aware via _iter_command_segments so a
# dangerous phrase inside a --grep value, an echo'd string, or a heredoc body
# cannot trip the gate — the old unanchored substring scan matched inside all
# of those. Command substitutions `$(...)` and backticks are scanned too, so an
# op hidden in `$(git reset --hard)` is still caught (no false-negative regress).
# (subcommand, required-arg pattern or None for "any form of this subcommand".)
_DANGEROUS_GIT = [
    ("reset", re.compile(r"--hard")),
    ("rebase", None),
    ("clean", None),
    ("filter-branch", None),
    ("checkout", re.compile(r"(?:--force|(?<![A-Za-z0-9])-f(?![A-Za-z0-9]))")),
    ("branch", re.compile(r"(?<![A-Za-z0-9])-D(?![A-Za-z0-9])")),
]
_DANGEROUS_GIT_LABEL = {
    "reset": "reset --hard", "rebase": "rebase", "clean": "clean",
    "filter-branch": "filter-branch", "checkout": "checkout --force",
    "branch": "branch -D",
}
_LEADING_NOISE = re.compile(r"^(?:sudo\s+)+")
_SUBSHELL_INNER = re.compile(r"\$\(([^)]*)\)")
_BACKTICK_INNER = re.compile(r"`([^`]*)`")


def _git_op_at_command_position(body: str):
    """If `body` (one command position, leading sudo stripped) starts with a
    dangerous git op, return its label; else None."""
    toks = body.split(None, 2)
    if len(toks) < 2 or toks[0].lower() != "git":
        return None
    subcmd = toks[1].lower()
    rest = toks[2] if len(toks) > 2 else ""
    for op, argpat in _DANGEROUS_GIT:
        if subcmd == op and (argpat is None or argpat.search(rest)):
            return _DANGEROUS_GIT_LABEL[op]
    return None


def _detect_dangerous_git(command: str):
    """Return a label for the first dangerous git op in `command`, else None.

    Splits on top-level ;/&&/||/|/newline (outside quotes) and flags `git` only
    when it is the command word of a segment (after stripping leading sudo), so
    `git log --grep="git reset --hard"` and `echo "git reset --hard"` do NOT
    match — the phrase is inside an argument, not a command. `$(...)` and
    backtick substitutions are scanned too, so `RESULT=$(git reset --hard)` IS
    caught. Replaces the old unanchored substring scan + its inconsistent
    double-regex label recovery (which missed checkout --force / branch -D).
    """
    for segment in _iter_command_segments(command):
        body = _LEADING_NOISE.sub("", segment).strip()
        label = _git_op_at_command_position(body)
        if label:
            return label
        for inner in (_SUBSHELL_INNER.findall(segment)
                      + _BACKTICK_INNER.findall(segment)):
            label = _git_op_at_command_position(_LEADING_NOISE.sub("", inner).strip())
            if label:
                return label
    return None


# --- F2 TDD commit gate ------------------------------------------------------
# Conductor's task-executor commits test+impl together at Step 8, so a feat/fix
# commit staging source WITHOUT a test is the real "implementation before test"
# signal. Caught at commit time (a tighter loop than the F3 coverage gate at
# dispatch-finalize). Exempt by commit TYPE — docs/chore/style/refactor/test and
# the chore(conductor) bookkeeping never need tests — so no task-tag lookup.
_TDD_GATED_TYPES = {"feat", "fix"}

_TEST_FILE_RE = re.compile(
    r'(?:^|/)(?:test|tests|spec|__tests__)(?:/|\.|\b)'
    r'|(?:[_\-.](?:test|spec)\.[A-Za-z0-9]+$)'
    r'|(?:\b(?:test|spec)_\w+\.[A-Za-z0-9]+$)',
    re.IGNORECASE,
)

_SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".cs", ".rb", ".php", ".swift",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".dart", ".scala", ".clj",
    ".ex", ".exs", ".erl", ".hs", ".ml", ".lua", ".pl", ".r", ".jl",
}


def _is_test_file(path: str) -> bool:
    return bool(_TEST_FILE_RE.search(path))


def _is_source_file(path: str) -> bool:
    return Path(path).suffix.lower() in _SOURCE_EXTENSIONS


def _staged_files(cwd: Path) -> list:
    """Files staged for commit (``git diff --cached``). Empty on any git error."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(cwd), capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return []
        return [f for f in proc.stdout.splitlines() if f.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def _commit_type_from_command(command: str):
    """Extract the conventional-commit type from a ``git commit -m "..."`` cmd."""
    m = re.search(r'(?<![\w-])-m\s*["\']?([^"\']+)["\']?', command)
    if not m:
        return None
    tm = re.match(r'([a-z]+)(?:\([^)]+\))?:', m.group(1).strip())
    return tm.group(1) if tm else None


def _check_f2_tdd_gate(cwd: Path, command: str) -> None:
    """F2 TDD gate: a feat/fix commit must stage a test alongside source code.

    Returns normally (caller proceeds to allow) when the gate passes or does
    not apply; calls ``write_hook_output(permission_decision="ask")`` — which
    exits the process — when it trips. ``ask`` (not ``deny``) keeps it
    overridable for the rare legitimate no-test feat/fix (test-infra, generated
    code), matching the campaign's confirmed enforcement philosophy.
    """
    if _commit_type_from_command(command) not in _TDD_GATED_TYPES:
        return  # docs/chore/style/refactor/test + conductor bookkeeping exempt

    staged = _staged_files(cwd)
    if not staged:
        return  # nothing staged or git unavailable — don't block blindly

    if any(_is_test_file(f) for f in staged):
        return  # test present → F2 satisfied

    if not any(_is_source_file(f) for f in staged):
        return  # only docs/config staged → not an impl commit

    source_eg = next(f for f in staged if _is_source_file(f))
    additional_context = (
        f'[Conductor] F2 TDD gate: this feat/fix commit stages source code '
        f'({source_eg}) without a test file. TDD requires a test in the same '
        f'commit (task-executor Step 3). Add a test, or if the task is exempt '
        f'(Docs/Config/Chore/Explore/Manual) use commit type docs/chore/etc.'
    )
    permission_reason = (
        'F2: feat/fix commit adds code without a test file. Add a test '
        '(Step 3) before committing, or use a non-gated commit type '
        '(docs/chore/style/refactor) if no test applies.'
    )
    write_hook_output(
        hook_event_name="PreToolUse",
        additional_context=additional_context,
        permission_decision="ask",
        permission_decision_reason=permission_reason,
    )


def main():
    """Main hook function"""
    input_data = read_hook_input()
    tool_name = input_data.get("tool_name", "")
    cwd_str = input_data.get("cwd", "")
    cwd = Path(cwd_str) if cwd_str else Path.cwd()

    # Only check Bash tool
    if tool_name != "Bash":
        write_hook_output(hook_event_name="PreToolUse")
        return

    # Extract command from tool input
    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")

    # Check for dangerous git operations (segment-aware — see _detect_dangerous_git)
    operation = _detect_dangerous_git(command)
    if operation:

        additional_context = (
            f'[Conductor] DANGER: Git {operation} command detected. '
            f'This may break state consistency. Run /conductor:revert if needed.'
        )
        permission_reason = (
            f'Git history-modifying operation ({operation}) detected. '
            'Use /conductor:revert workflow instead.'
        )

        write_hook_output(
            hook_event_name="PreToolUse",
            additional_context=additional_context,
            permission_decision="ask",
            permission_decision_reason=permission_reason,
        )
        return

    # Check for track-state lock violations
    if 'track-state' in command.lower():
        violations = find_track_state_violations(cwd, command)
        if violations:
            violations_str = '; '.join(violations)
            additional_context = (
                f'[Conductor] State lock violation detected: {violations_str}. '
                f'Complete or revert the in_progress task before modifying track files.'
            )
            permission_reason = (
                'Track has in_progress task. '
                'Complete or revert first to maintain state consistency.'
            )

            write_hook_output(
                hook_event_name="PreToolUse",
                additional_context=additional_context,
                permission_decision="ask",
                permission_decision_reason=permission_reason,
            )
            return

    # Check for direct modifications to track-state.json
    if is_direct_track_state_modification(command):
        additional_context = (
            '[Conductor] Direct track-state.json modification detected. '
            'Use track-state CLI commands instead to maintain consistency.'
        )
        permission_reason = (
            'Direct modification of track-state.json bypasses state machine. '
            'Use /conductor:revert or track-state CLI.'
        )

        write_hook_output(
            hook_event_name="PreToolUse",
            additional_context=additional_context,
            permission_decision="ask",
            permission_decision_reason=permission_reason,
        )
        return

    # Check for non-conventional commit messages (V10). The anchor matches the
    # -m flag in any shell form (-m "x", -m"x", -m'x', -mx); the lookbehind
    # avoids matching -m inside a word/flag like file-m.txt or --message=.
    if re.search(r'git\s+commit\s+.*(?<![\w-])-m', command, re.IGNORECASE):
        is_valid, suggested_fix = validate_commit_message(command)
        if not is_valid:
            additional_context = (
                f'[Conductor] V10 Violation: Commit message does not follow conventional format. '
                f'Expected: type(scope): description. '
                f'Types: feat|fix|docs|style|refactor|test|chore. '
                f'Suggested: {suggested_fix}'
            )
            permission_reason = (
                f'Non-conventional commit message. '
                f'Use format: type(scope): description. '
                f'Suggested: {suggested_fix}'
            )

            write_hook_output(
                hook_event_name="PreToolUse",
                additional_context=additional_context,
                permission_decision="ask",
                permission_decision_reason=permission_reason,
            )
            return

    # F2 TDD gate: feat/fix commits must stage a test alongside source code.
    # _check_f2_tdd_gate returns when the gate passes; it exits the process
    # (via write_hook_output) when it trips.
    if re.search(r'git\s+commit\b', command, re.IGNORECASE):
        _check_f2_tdd_gate(cwd, command)

    # Allow all other commands
    write_hook_output(hook_event_name="PreToolUse")


if __name__ == "__main__":
    main()
