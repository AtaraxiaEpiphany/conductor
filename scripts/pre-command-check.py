#!/usr/bin/env python3
"""PreToolUse hook: real-time state protection for git and track-state operations.

Validates commands before execution, blocks suspicious operations.
Uses hookSpecificOutput.permissionDecision per the Claude Code hook protocol.
"""

import hashlib
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
from lib.validation import (
    validate_commit_message, _extract_commit_message, commit_arg_shell_broken_reason,
)
from lib.path_utils import find_tracks_registry, extract_track_dirs
from lib.logging import log_entry
from lib.env import get_logs_dir
from lib.locked_task import _cursor_target, _iter_track_states


def _audit_gate(gate: str, command: str) -> None:
    """Record that a PreToolUse ``gate`` fired (deny).

    The hook denies outright (no allow/deny prompt to wait on), so this logs
    that a gate *fired* — the actionable signal of which gate trips how often,
    and thus which denials a long-running session most often has to adapt
    around. The command is stored as a 12-char sha256 digest, not verbatim, to
    keep the audit log compact and avoid persisting full commands. Best-effort:
    a write failure must never block the gate decision itself.
    """
    try:
        log_dir = get_logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(command.encode("utf-8")).hexdigest()[:12]
        log_entry(log_dir / "override-audit.log",
                  f"gate={gate} digest={digest}")
    except Exception:
        pass


def _deny(gate: str, command: str, additional_context: str,
          permission_decision_reason: str) -> None:
    """Audit that ``gate`` fired, then deny the PreToolUse (exits the process).

    Every conductor gate denies outright (never ``ask``) so a long-running
    session is never blocked on a human prompt — the model adapts around the
    denial. Pairing the audit with the deny keeps the decision policy in one
    place. ``write_hook_output`` exits the process, so callers return nothing.
    """
    _audit_gate(gate, command)
    write_hook_output(
        hook_event_name="PreToolUse",
        additional_context=additional_context,
        permission_decision="deny",
        permission_decision_reason=permission_decision_reason,
    )


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


_SANCTIONED_TS_SUBCOMMANDS = {
    # Every subcommand in cli.py _COMMAND_GROUPS plus the hidden "setup" alias
    # and "help". A sanctioned subcommand bypasses the broad rm/mv/delete/move
    # verb scan (Layer A below): the track-state CLI never deletes/moves track
    # files, and the one catastrophic op (mutating track-state.json itself) is
    # already caught by is_direct_track_state_modification(). Keep in sync with
    # _COMMAND_GROUPS — the test suite asserts this set covers it.
    "add-checkpoint", "append-handoff", "archive", "block", "check",
    "checklist-verify", "complete", "defer", "deferred-report", "derive-name",
    "dispatch-finalize", "dispatch-next", "dispatch-prepare", "dispatch-wave",
    "fail", "finalize", "gc", "get-handoff", "harvest-candidates", "help",
    "indices", "init-from-plan", "lock", "new-track-finalize", "new-track-init",
    "new-track-resume", "new-track-set-mode", "new-track-step", "next",
    "phase-checkpoint-review", "phase-done", "phase-verdict",
    "post-loop-review", "post-loop-status",
    "post-loop-step", "preflight", "process-result", "quality-snapshot",
    "record-summary", "recover", "registry-add", "registry-update", "reset",
    "resolve-track", "set-mode", "setup", "shas", "skip",
    "skip-analyst-verdict", "skip-refute-review", "spec-integrity",
    "start", "step", "sync-handoff", "sync-plan", "validate", "wave-abort",
    "wave-finalize", "wave-status", "wave-step", "write-result",
}


def _track_state_subcommand(command: str):
    """Subcommand token of a ``track-state <sub> ...`` segment, else None.

    Segment-aware via _iter_command_segments so ``rm x; track-state append-handoff``
    resolves per-segment (the rm segment is irrelevant here). Only a segment whose
    command word is ``track-state`` counts — a stray "track-state" inside an
    argument or heredoc body does not. Leading sudo is stripped.
    """
    for seg in _iter_command_segments(command):
        toks = _LEADING_NOISE.sub("", seg).split()
        if len(toks) >= 2 and toks[0].lower() == "track-state":
            return toks[1].lower()
    return None


def _argv_only(command: str) -> str:
    """Drop a trailing heredoc body from ``command``.

    A heredoc (``<< 'EOF' ... EOF``) is stdin, not argv — findings/JSON piped to
    e.g. ``append-handoff`` live there and must not be verb-scanned. Keeps the
    command word and flags (everything before the ``<<``), discards the body.
    """
    m = re.search(r"<<-?\s*['\"]?(\w+)", command)
    return command[: m.start()] if m else command


def find_track_state_violations(cwd: Path, command: str) -> list[str]:
    """Find tracks with state lock violations.

    Layer A — sanctioned-subcommand allowlist: a track-state CLI subcommand never
    deletes/moves track files, so it never violates the lock even when its argv
    or piped heredoc body happens to contain rm/delete/mv/move. The explorer's
    ``append-handoff ... << EOF {"findings":["remove the handler"]} EOF`` is the
    load-bearing false positive this fixes. The catastrophic op — mutating
    track-state.json — is separately caught by is_direct_track_state_modification.

    Layer B — for any other command that still mentions track-state, scan only
    the argv (heredoc body stripped) with word-boundary verbs so ``move`` no
    longer matches ``remove``/``removed``/``movement``.
    """
    tracks_file = find_tracks_registry(cwd)
    if not tracks_file:
        return []

    if _track_state_subcommand(command) in _SANCTIONED_TS_SUBCOMMANDS:
        return []  # Layer A

    dirs = extract_track_dirs(tracks_file)
    scan = _argv_only(command).lower()  # Layer B: ignore heredoc bodies

    violations = []
    for d in dirs:
        state_file = cwd / d / "track-state.json"
        if not state_file.exists():
            continue

        if has_in_progress_task(state_file):
            if re.search(r'\brm\b', scan) or re.search(r'\bdelete\b', scan):
                violations.append(f'{d}: in_progress task + deletion command')
            elif re.search(r'\bmv\b', scan) or re.search(r'\bmove\b', scan):
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
# (subcommand, required-arg pattern or None, human-readable label). The label
# lives next to its tuple so adding an op is a one-line edit — no separate
# label dict to keep key-for-key in sync.
_DANGEROUS_GIT = [
    ("reset", re.compile(r"--hard"), "reset --hard"),
    ("rebase", None, "rebase"),
    ("clean", None, "clean"),
    ("filter-branch", None, "filter-branch"),
    ("checkout", re.compile(r"(?:--force|(?<![A-Za-z0-9])-f(?![A-Za-z0-9]))"), "checkout --force"),
    ("branch", re.compile(r"(?<![A-Za-z0-9])-D(?![A-Za-z0-9])"), "branch -D"),
]
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
    for op, argpat, label in _DANGEROUS_GIT:
        if subcmd == op and (argpat is None or argpat.search(rest)):
            return label
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


def _git_diff_name_only(cwd: Path, *diff_args) -> list:
    """Lines from ``git diff <diff_args> --name-only``; empty on any git error.

    Shared plumbing for the commit gates' two file-set queries: staged files
    (``--cached``) and a commit range's touched files (``--no-renames sha~1 sha``).
    """
    try:
        proc = subprocess.run(
            ["git", "diff", *diff_args, "--name-only"],
            cwd=str(cwd), capture_output=True, text=True, timeout=3,
        )
        if proc.returncode != 0:
            return []
        return [f for f in proc.stdout.splitlines() if f.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def _staged_files(cwd: Path) -> list:
    """Files staged for commit (``git diff --cached``). Empty on any git error."""
    return _git_diff_name_only(cwd, "--cached")


def _commit_type_from_command(command: str):
    """Extract the conventional-commit type from a ``git commit -m "..."`` cmd.

    Delegates ``-m`` extraction to lib.validation._extract_commit_message — the
    canonical extractor validate_commit_message also uses — so the two commit
    gates share one definition of what a commit message is (no divergent regex).
    Heredoc-built messages are read literally (so a heredoc feat/fix commit IS
    gated); ``-F file`` / dynamic ``$(...)`` messages return None, correctly
    exempting the commit from the F2 gate (can't be read statically).
    """
    message = _extract_commit_message(command)
    if not message:
        return None
    tm = re.match(r'([a-z]+)(?:\([^)]+\))?:', message.strip())
    return tm.group(1) if tm else None


def _check_f2_tdd_gate(cwd: Path, command: str) -> None:
    """F2 TDD gate: a feat/fix commit must stage a test alongside source code.

    Returns normally (caller proceeds to allow) when the gate passes or does
    not apply; calls ``_deny(...)`` — which exits the process — when it trips.
    Denied (not asked) so a long-running
    session is never blocked on a human prompt; the model adapts by using a
    non-gated commit type (docs/chore/refactor) for the rare legitimate
    no-test feat/fix (test-infra, generated code).
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
    _deny("f2_tdd", command, additional_context, permission_reason)


# --- refactor diff-scope gate ------------------------------------------------
# The orchestrator-dispatched refactorer (agents/refactorer.md) is a behavior-
# preserving patcher scoped to the task's own code diff (REVISION_RANGE =
# code_sha~1..code_sha, §3.6c). Its boundary was prose-only; this gate
# mechanizes Pillar 2 ("when documentation falls short, promote the rule into
# code") at commit time: a `refactor(area):` commit (the refactorer's mandated
# conventional type, agents/refactorer.md §3.0) may only stage files the
# completed task already touched. The bound is derived independently from
# track-state.json's cursor-target commit_sha (= the agent's code commit — set
# by mutations._do_complete; the SAME range §3.6c hands the refactorer).
# Fail-open on any ambiguity (no resolvable track, >1 candidate, git error, empty
# range) so the gate can never false-block legitimate work.
def _cursor_completed_code_sha(state):
    """commit_sha of the cursor-pointed task/subtask if it is `completed`, else None.

    Thin facade over ``lib.locked_task._cursor_target`` (status="completed"):
    the refactorer runs AFTER dispatch-finalize, so its task is terminal, and a
    non-empty ``commit_sha`` is required (no code commit → no range to scope). A
    stale cursor pointing at a non-completed target, or out-of-range indices,
    → None.
    """
    hit = _cursor_target(state, status="completed")
    if hit is None:
        return None
    sha = (hit[0].get("commit_sha") or "").strip()
    return sha or None


def _resolve_refactor_bound(cwd: Path):
    """The single completed-task code_sha bounding this refactor, or None.

    Scans conductor tracks under ``cwd`` (via ``lib.locked_task._iter_track_states``)
    and collects every track whose cursor resolves to a completed task with a
    commit_sha. Exactly one → that sha; zero or more than one → None (ambiguous →
    caller fails open). Conductor runs one track per session, so "exactly one" is
    the normal case; multi-track repos degrade to fail-open (unenforced, never a
    false block). Malformed/unreadable state files are skipped, never raised.
    """
    shas = []
    for _state_path, state in _iter_track_states(cwd):
        sha = _cursor_completed_code_sha(state)
        if sha:
            shas.append(sha)
    return shas[0] if len(shas) == 1 else None


def _refactor_allowed_files(cwd: Path, code_sha: str) -> set:
    """File set the completed task's code commit touched (the refactorer's bound).

    ``--no-renames`` for a stable set across renames. Empty on any git error —
    callers treat an empty derived bound as fail-open (the gate cannot decide
    what's in scope), NOT as "everything is out of scope".
    """
    return set(_git_diff_name_only(cwd, "--no-renames", f"{code_sha}~1", code_sha))


def _check_refactor_scope_gate(cwd: Path, command: str) -> None:
    """Refactor diff-scope gate: a ``refactor(area):`` commit may only stage files
    the completed task already touched.

    Returns normally (caller proceeds to allow) when the gate passes or does not
    apply; calls ``_deny(...)`` — which exits the process — when it trips. The
    ``refactor(`` prefix is the trigger (not agent identity — PreToolUse hooks
    can't see which agent is running), sidestepping the identity limitation
    entirely. Fail-open whenever the bound is unresolvable.
    """
    if _commit_type_from_command(command) != "refactor":
        return  # only the refactorer's own commits are in scope

    code_sha = _resolve_refactor_bound(cwd)
    if not code_sha:
        return  # no single resolvable completed task → fail open

    allowed = _refactor_allowed_files(cwd, code_sha)
    if not allowed:
        return  # bound undeterminable (git error / empty range) → fail open

    staged = set(_staged_files(cwd))
    if not staged:
        return  # nothing staged or git unavailable — don't block blindly (mirrors F2)

    out_of_scope = sorted(staged - allowed)
    if not out_of_scope:
        return  # every staged file is within the task's own code diff → OK

    eg = out_of_scope[0]
    more = f" (+{len(out_of_scope) - 1} more)" if len(out_of_scope) > 1 else ""
    additional_context = (
        f'[Conductor] refactor diff-scope violation: this `refactor(...)` commit '
        f'stages {eg}{more} outside the task\'s own code (REVISION_RANGE='
        f'{code_sha}~1..{code_sha}). The refactorer is behavior-preserving and '
        f'may only touch files the task changed. Unstage the out-of-scope file(s) '
        f'(`git restore --staged <file>`), or split them into their own commit.'
    )
    permission_reason = (
        f'refactor-scope: `{eg}` is outside the completed task\'s code diff '
        f'({code_sha}~1..{code_sha}). Unstage it; a refactor commit may only '
        f'touch files the task already changed.'
    )
    _deny("refactor-scope", command, additional_context, permission_reason)


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

        _deny("dangerous_git", command, additional_context, permission_reason)
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

            _deny("state_lock", command, additional_context, permission_reason)
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

        _deny("state_lock", command, additional_context, permission_reason)
        return

    # Check for non-conventional commit messages (V10). The anchor matches the
    # -m flag in any shell form (-m "x", -m"x", -m'x', -mx); the lookbehind
    # avoids matching -m inside a word/flag like file-m.txt or --message=.
    if re.search(r'git\s+commit\s+.*(?<![\w-])-m', command, re.IGNORECASE):
        # Hard-deny a shell-broken -m argument (e.g. `git commit -m ()` or
        # `git commit -m <commit_msg>`) BEFORE the V10 style check. V10 only asks
        # (so the broken command could still reach bash and die); this denies
        # outright and tells the model to quote the message. Catches the
        # orchestrator-placeholder mis-substitution class at the hook layer.
        broken = commit_arg_shell_broken_reason(command)
        if broken:
            _deny("commit_shell_broken", command, f"[Conductor] {broken}", broken)
            return
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

            _deny("v10_commit", command, additional_context, permission_reason)
            return

    # F2 TDD gate: feat/fix commits must stage a test alongside source code.
    # _check_f2_tdd_gate returns when the gate passes; it exits the process
    # (via write_hook_output) when it trips.
    if re.search(r'git\s+commit\b', command, re.IGNORECASE):
        _check_f2_tdd_gate(cwd, command)
        # Refactor diff-scope gate: a refactor(...) commit must stay within the
        # completed task's own code diff (agents/refactorer.md boundary). Same
        # fail-open / _deny contract as F2.
        _check_refactor_scope_gate(cwd, command)

    # Allow all other commands
    write_hook_output(hook_event_name="PreToolUse")


if __name__ == "__main__":
    main()
