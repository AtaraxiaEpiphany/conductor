#!/usr/bin/env python3
"""PreToolUse:Bash guard — deny ``write-result --status success`` on a dirty tree.

The problem this solves
-----------------------
The conductor has **two commits per task**, with deliberately *different* staging
sets. The **implementation commit** is owned by ``task-executor`` Step 8
(``agents/task-executor.md`` §4.0) — the *only* place implementation files ever
get committed. The **conductor commit** (``_finalize_task`` → ``_git_commit``,
``git_ops.py:174``) stages **only** conductor-managed files
(``track-state.json``, ``plan.md``, ``.conductor/``, ``issues.md``) by design:
that narrow staging is a load-bearing invariant that keeps unrelated brownfield
WIP out of conductor commits (the setup skill repeats "never ``git add -A``"
three times for exactly this reason).

So when ``task-executor`` — under the §7.0 38-round tripwire pressure — skips or
botches Step 8 and then reports SUCCESS anyway, the conductor happily records
SUCCESS and the implementation files sit **uncommitted forever**. Finalize will
never rescue them (its narrow staging is a feature, not a bug), and the
failure-synthesis path (``_synthesize_result_from_state``) only fires when
``result.json`` is *missing* — a present-but-lying ``--status success`` bypasses
it entirely. This is the "lots of files not committed" symptom on large
techstack-upgrade tracks.

This hook makes the invariant **deterministic**: it sees task-executor's
``write-result --status success`` call *before* the lie is written and
``permissionDecision: "deny"`` it when the working tree has uncommitted
implementation files. The deny reason hands back the exact Step 8 cure (the
``git add -A && … || commit`` idiom) so a weak model copies it verbatim, plus an
honest escape (report FAILURE) for genuinely-stuck cases. Same shape as
``on-dispatch-dedupe.py`` (PreToolUse deny) and ``on-pre-tool-tripwire.py``
(intra-subagent PreToolUse): prose a small-window model can ignore → code it
can't.

How it fires
------------
PreToolUse fires inside a subagent's own tool loop; ``agent_type`` is added to
the input under ``--agent``. This hook filters in-code on
``agent_type == "task-executor"`` AND ``tool_name == "Bash"`` AND the command
being a ``write-result`` invocation carrying ``--status success`` (visible flag
form only). Everything else is a no-op (allow).

Resolution + uncommitted-file test
----------------------------------
- Resolve the locked ``in_progress`` task via ``lib.locked_task.resolve`` →
  ``(track_dir, …)``. No locked task → allow.
- List working-tree implementation files via
  ``lib.git_utils.implementation_uncommitted_files`` (shared with the finalize
  telemetry so the two cannot drift on what counts as implementation work).
  Empty list → allow. Non-empty → deny with the recipe-as-reason.

Edge case — status not visible
------------------------------
``--status`` can also arrive via ``--data '<json>'`` or piped stdin, where this
hook cannot read it. A ``write-result`` call that carries ``--data`` or no
visible ``--status`` flag is **allowed** (fail-open): the flag channel is the
documented primary path (``result.py:279``) and the escape hatches are rare /
expert. The telemetry field ``stranded_files_count`` on the finalize envelope
surfaces any bypass for monitoring.

Fail-open
---------
This hook must **never block productive work**. Any resolution, I/O, git, or
parsing error → allow + stderr warning. A misbehaving guard is worse than none.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.locked_task import resolve as resolve_locked_task
from lib.git_utils import implementation_uncommitted_files


_TARGET_AGENT = "task-executor"
_MAX_LISTED = 10  # cap the file list in the deny reason so it stays readable

# A write-result invocation. Anchored on the subcommand token so we don't match
# an unrelated command that merely mentions the string.
_WRITE_RESULT_RE = re.compile(r"\bwrite-result\b")

# Visible --status success flag (case-insensitive, tolerates --status=success
# and surrounding quotes). Does NOT match --data or stdin-supplied status.
_STATUS_SUCCESS_RE = re.compile(
    r"--status[ =]+['\"]?success['\"]?", re.IGNORECASE)

# --data present → status may be embedded in JSON we can't see → fail-open.
_DATA_RE = re.compile(r"--data\b")


def _is_target_call(command):
    """True iff ``command`` is a write-result reporting SUCCESS via a visible
    flag. Returns (matched, status_visible): when status is not visible
    (--data / no flag) the caller allows."""
    if not _WRITE_RESULT_RE.search(command):
        return False, True
    if _DATA_RE.search(command):
        # Raw-JSON escape hatch — status may be inside; we can't see it.
        return False, True
    if not _STATUS_SUCCESS_RE.search(command):
        # --status failure, or no visible flag (stdin path) → not a success claim.
        return False, True
    return True, True


def main():
    input_data = read_hook_input()

    # Only task-executor reporting success is in scope. PreToolUse adds
    # agent_type inside a subagent; main-session / other agents → no-op.
    if input_data.get("agent_type") != _TARGET_AGENT:
        write_hook_output(permission_decision="allow")
        return

    if input_data.get("tool_name") != "Bash":
        write_hook_output(permission_decision="allow")
        return

    command = (input_data.get("tool_input") or {}).get("command", "") or ""
    matched, _ = _is_target_call(command)
    if not matched:
        write_hook_output(permission_decision="allow")
        return

    cwd = input_data.get("cwd") or str(Path.cwd())
    try:
        locked = resolve_locked_task(cwd)
    except Exception:
        locked = None
    if locked is None:
        # No locked in_progress task → not our state to guard. Fail-open.
        write_hook_output(permission_decision="allow")
        return

    track_dir = locked[0]
    stranded = implementation_uncommitted_files(track_dir)
    if not stranded:
        write_hook_output(permission_decision="allow")
        return

    # Implementation files are uncommitted — deny the false success.
    shown = ", ".join(stranded[:_MAX_LISTED])
    if len(stranded) > _MAX_LISTED:
        shown += f" (+{len(stranded) - _MAX_LISTED} more)"
    reason = (
        f"CONDUCTOR CLEAN-TREE: write-result --status success denied — "
        f"{len(stranded)} implementation file(s) are uncommitted: {shown}. "
        f"The conductor finalize commit stages ONLY conductor-managed files "
        f"(track-state.json, plan.md, .conductor/, issues.md); it will NEVER "
        f"commit your code. Commit your implementation work first (Step 8):\n"
        f"    git add -A && git diff --cached --quiet || "
        f"git commit -m \"<type>(<scope>): <description>\"\n"
        f"then re-run write-result. If the work is genuinely incomplete, "
        f"report FAILURE instead:\n"
        f"    track-state write-result \"{track_dir}\" --status failure "
        f"--summary \"...\" --failure-done \"...\" --failure-reason \"...\"\n"
        f"Never claim success with an uncommitted tree."
    )
    print(f"⚠️  CONDUCTOR CLEAN-TREE: denied task-executor success with "
          f"{len(stranded)} uncommitted implementation file(s).",
          file=sys.stderr)
    write_hook_output(
        permission_decision="deny",
        permission_decision_reason=reason,
        system_message=(
            f"⚠️ CONDUCTOR CLEAN-TREE: denied write-result success — "
            f"{len(stranded)} uncommitted implementation file(s). See the "
            f"denied command's reason for the Step 8 commit recipe."
        ),
    )


if __name__ == "__main__":
    main()
