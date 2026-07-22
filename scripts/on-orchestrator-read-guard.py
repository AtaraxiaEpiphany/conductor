#!/usr/bin/env python3
"""PreToolUse:Read guard — deny business-file reads while a task is in flight.

The problem this solves
-----------------------
The conductor orchestrator skills (``implement``, ``implement-step``,
``parallel-step``, ``new-track``) carry a thin-router contract: "NEVER read
``spec.md``/``plan.md`` — subagents self-load all business context." That rule
is **prose a small-window model under context pressure ignores** — the same
class of gap ``on-dispatch-dedupe.py`` (a second ``Agent`` spawn) and
``on-pre-tool-tripwire.py`` (turn overrun) were built to close.

The concrete failure: when a subagent (spec-planner, task-executor) returns
**no** result block — it exhausted turns mid-work — the orchestrator model
"helpfully" improvises by reading ``spec.md``/``plan.md`` itself and doing the
work. That is the thin-router violation. ``dispatch-finalize`` is the correct
recovery (it synthesizes a result from git state); the model must not fill the
vacuum with its own file reads. This hook makes the invariant
**deterministic**: it denies the ``Read`` of business files while a dispatch
transaction is open.

How it fires
------------
PreToolUse fires in the orchestrator's own tool loop. For a ``Read``,
``tool_name == "Read"`` and ``tool_input.file_path`` names the target. This
hook:

- Resolves the locked ``in_progress`` task via ``lib.locked_task.resolve`` →
  ``(track_dir, phase, task, subtask)``. No locked task → allow (nothing to
  protect; the orchestrator is between tasks and may read freely).
- Reads the inflight marker for that task (the *same* marker
  ``prepare_dispatch`` stamps and ``on-dispatch-dedupe.py`` reads). Missing →
  allow.
- In flight iff ``git HEAD == marker.start_sha`` AND no
  ``.conductor/result.json`` — the *same predicate* ``on-dispatch-dedupe.py``
  and ``cmd_step`` use. In flight AND the read targets this track's
  ``spec.md``/``plan.md`` → ``permissionDecision: "deny"``. Otherwise allow.

Scope of the deny set
---------------------
Only ``{TRACK_DIR}/spec.md`` and ``{TRACK_DIR}/plan.md`` — the two
thin-router-critical files and the concrete observed violation. Arbitrary
source under the project tree is intentionally NOT denied (false-positive
risk would break legitimate tooling). Broaden later only with evidence.

Fail-open
---------
This hook must **never block productive work**. Any resolution, I/O, SHA, or
parsing error → allow + stderr warning (mirrors ``on-dispatch-dedupe.py``'s
fail-open contract). A misbehaving guard is worse than none.
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from lib.logging import init_logging, log_entry
from lib.locked_task import resolve as resolve_locked_task
from lib import dispatch_inflight as inflight

# Business files the orchestrator must read ONLY via a dispatched subagent,
# never itself while a task is in flight. Basenames keep this locale- and
# path-agnostic (works for any track dir). Add with evidence, not enthusiasm.
_DENY_BASENAMES = frozenset({"spec.md", "plan.md"})


def _head_sha(track_dir):
    """Short (7-char) HEAD sha, matching git_ops._git_head_sha's format so the
    comparison against marker.start_sha is apples-to-apples. None on failure
    (fail-open). Mirrors on-dispatch-dedupe._head_sha exactly."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, cwd=track_dir, timeout=5,
        )
        sha = result.stdout.strip()
        return sha if re.match(r"^[0-9a-f]{7}$", sha) else None
    except Exception:
        return None


def _result_exists(track_dir):
    try:
        return (Path(track_dir) / ".conductor" / "result.json").exists()
    except Exception:
        return False


def _deny_target(file_path, cwd, track_dir):
    """True if ``file_path`` is this track's ``spec.md``/``plan.md``.

    The thin-router violation is reading *this track's* spec/plan to compensate
    for a missing subagent result. A Read may carry an absolute OR relative
    path, and may target a file the subagent hasn't written yet (so a
    filesystem ``resolve()`` is unreliable). We:

      1. anchor a relative path to the hook's ``cwd`` (the orchestrator's
         working dir = repo root), making it absolute;
      2. strip any leading ``./`` and collapse ``..`` lexically just enough to
         land under the repo root (no symlink chase — not needed for the match);
      3. match on the normalized path ending in ``<track_dir>/<basename>``.

    ``track_dir`` is itself absolute (``locked_task.resolve`` returns the
    resolved track dir), so both sides are absolute for the suffix compare.
    """
    try:
        name = Path(file_path).name
    except TypeError:
        return False
    if name not in _DENY_BASENAMES:
        return False
    fp = str(file_path).replace("\\", "/")
    if not fp.startswith("/"):
        fp = f"{str(cwd).rstrip('/')}/{fp}"
    # Collapse redundant slashes/./ for a clean suffix compare.
    while "//" in fp:
        fp = fp.replace("//", "/")
    while "/./" in fp:
        fp = fp.replace("/./", "/")
    td = str(track_dir).rstrip("/")
    return fp == f"{td}/{name}" or fp.endswith(f"{td}/{name}")


def main():
    input_data = read_hook_input()

    # Only the orchestrator's Reads are in scope. PreToolUse fires inside
    # subagents too (agent_type set) — those inner Reads are the subagent
    # self-loading its own context and MUST be allowed; the thin-router rule
    # constrains only the orchestrator. A subagent has tool_name == "Read" but
    # arrives with a populated agent_type, so gate on that.
    if input_data.get("tool_name") != "Read":
        write_hook_output(permission_decision="allow")
        return
    if input_data.get("agent_type"):
        write_hook_output(permission_decision="allow")
        return

    file_path = (input_data.get("tool_input") or {}).get("file_path", "")
    cwd = input_data.get("cwd") or str(Path.cwd())

    log_file = init_logging("on-orchestrator-read-guard")
    log_entry(log_file, f"event=read_probe path={file_path}")

    # No locked in_progress task → orchestrator is between tasks → allow.
    try:
        locked = resolve_locked_task(cwd)
    except Exception:
        locked = None
    if locked is None:
        write_hook_output(permission_decision="allow")
        return

    track_dir, phase, task, subtask = locked

    # Is this Read even targeting a denied file? Cheap check before the in-flight
    # probe — most Reads are unrelated (logs, scripts, anything) and should allow
    # without touching git.
    if not _deny_target(file_path, cwd, track_dir):
        write_hook_output(permission_decision="allow")
        return

    # Read the inflight marker — same one on-dispatch-dedupe reads. Missing or
    # corrupt → not in flight → allow (fail-open).
    try:
        marker = inflight.read(track_dir, phase, task, subtask)
    except Exception:
        marker = None
    if marker is None:
        write_hook_output(permission_decision="allow")
        return

    start_sha = marker.get("start_sha")
    head = _head_sha(track_dir)
    result_present = _result_exists(track_dir)

    # In flight iff the Start commit is still HEAD AND no result.json — the same
    # predicate on-dispatch-dedupe.py and cmd_step use (finalize-vs-redispatch).
    in_flight = bool(start_sha) and head == start_sha and not result_present

    if not in_flight:
        # The prior dispatch finalized/returned; the orchestrator may read again.
        write_hook_output(permission_decision="allow")
        return

    loc = f"P{phase}T{task}" + (f".S{subtask}" if subtask is not None else "")
    sha_hint = (start_sha or "?")[:8]
    reason = (
        f"Conductor thin-router invariant: {Path(file_path).name} must be read "
        f"by the dispatched subagent, not the orchestrator. A task is in flight "
        f"for {loc} (Start {sha_hint} still HEAD, no result.json yet). The "
        f"subagent either is still working or returned no result block. Do NOT "
        f"read the spec/plan to compensate. In serial mode, run "
        f"`track-state dispatch-finalize \"{track_dir}\"` to synthesize a result "
        f"from git state (committed code → SUCCESS; nothing → FAILURE + retry "
        f"handoff); in wave mode, run `track-state wave-step \"{track_dir}\"` "
        f"and let `wave-finalize` own the member's result. That clears this guard."
    )
    log_entry(log_file, f"event=deny loc={loc} path={file_path} start={sha_hint}")
    print(f"⚠️  CONDUCTOR READ GUARD: denied orchestrator Read of "
          f"{Path(file_path).name} for {loc} (task in flight).",
          file=sys.stderr)
    write_hook_output(permission_decision="deny",
                      permission_decision_reason=reason)


if __name__ == "__main__":
    main()
