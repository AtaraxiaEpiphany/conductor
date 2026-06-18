"""Plan.md sync operations.

Consolidated onto plan_parse.py's regexes (_PHASE_HEADING, _TASK_LINE) — the
single canonical definition shared with validate.py and misc.py.
"""
import re
from pathlib import Path

from .core import load, save
from .helpers import now_iso, _clean_trailing_markers, _any_phase_needs_checkpoint
from .constants import MARKER_MAP, SHA_MARKERS, TERMINAL_FOR_PARENT
from .plan_parse import _PHASE_HEADING, _TASK_LINE


def _do_sync_plan(track_dir, state=None):
    """Sync plan.md markers from state. Returns synced count.

    Rewrites plan.md checkboxes to mirror track-state.json (state->plan, the
    safe direction). Auto-absorbs plan.md subtask lines that have no same-named
    entry in state, adding them as pending so the dispatcher sees them.

    Subtasks are matched by NAME, not position, so a plan subtask inserted or
    reordered mid-list absorbs correctly instead of mis-mapping to a sibling's
    status or being duplicated.
    """
    if state is None:
        state = load(track_dir)
    plan_path = Path(track_dir) / "plan.md"

    with open(plan_path) as f:
        lines = f.readlines()

    result = []
    phase_idx = 0
    task_idx = 0
    synced = 0
    absorbed = 0

    for line in lines:
        stripped = line.rstrip("\n")

        if _PHASE_HEADING.match(stripped):
            phase_idx = int(_PHASE_HEADING.match(stripped).group(1))
            task_idx = 0
            result.append(stripped)
            continue

        tm = _TASK_LINE.match(stripped)
        if tm:
            if phase_idx < 1:
                # Task/subtask before any Phase heading — preserve unchanged.
                result.append(stripped)
                continue

            indent = tm.group(1)
            rest = tm.group(3)
            rest_clean = _clean_trailing_markers(rest)
            if not rest_clean:
                # Empty checkbox line — preserve unchanged (not a real task).
                result.append(stripped)
                continue

            is_subtask = len(indent) > 0
            if is_subtask and task_idx < 1:
                # Subtask with no preceding parent task this phase — preserve.
                result.append(stripped)
                continue

            marker = " "
            sha = ""
            try:
                if is_subtask:
                    parent_task = state["phases"][phase_idx - 1]["tasks"][task_idx - 1]
                    parent_subs = parent_task.setdefault("subtasks", [])
                    sub = next((s for s in parent_subs if s.get("name") == rest_clean), None)
                    if sub is None:
                        sub = {"name": rest_clean, "status": "pending"}
                        parent_subs.append(sub)
                        absorbed += 1
                        # Reopen a terminal parent so the new subtask dispatches.
                        if parent_task.get("status") in TERMINAL_FOR_PARENT:
                            parent_task["status"] = "in_progress"
                            for k in ("commit_sha", "completed_at"):
                                parent_task.pop(k, None)
                    marker = MARKER_MAP.get(sub.get("status"), " ")
                    sha = sub.get("commit_sha", "")
                else:
                    task_idx += 1
                    t = state["phases"][phase_idx - 1]["tasks"][task_idx - 1]
                    marker = MARKER_MAP.get(t.get("status"), " ")
                    sha = t.get("commit_sha", "")
            except (IndexError, KeyError):
                # No matching state entry (e.g. a new top-level task not yet
                # absorbed, or stale indices) — preserve the line unchanged.
                result.append(stripped)
                continue

            new_line = f"{indent}- [{marker}] {rest_clean}"
            if sha and marker in SHA_MARKERS and re.match(r"^[0-9a-f]{7}$", sha):
                new_line += f" [{sha}]"
            result.append(new_line)
            synced += 1
            continue

        result.append(stripped)

    with open(plan_path, "w") as f:
        f.write("\n".join(result))
        f.write("\n")

    if absorbed > 0:
        state["updated_at"] = now_iso()
        save(track_dir, state)

    return synced


def cmd_sync_plan(track_dir):
    synced = _do_sync_plan(track_dir)
    state = load(track_dir)
    result = dict(synced=synced)
    checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
    if checkpoint_pending is not None:
        result["phase_checkpoint_pending"] = checkpoint_pending
        result["next_action"] = "dispatch_phase_checker"
    out(result)