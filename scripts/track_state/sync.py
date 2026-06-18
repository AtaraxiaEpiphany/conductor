"""Plan.md sync operations."""
import re
import sys
from pathlib import Path

from .core import load, save
from .helpers import now_iso, out, _clean_trailing_markers, _any_phase_needs_checkpoint
from .constants import MARKER_MAP, SHA_MARKERS, TERMINAL_FOR_PARENT


def _do_sync_plan(track_dir, state=None):
    """Sync plan.md markers from state. Returns synced count.

    Auto-absorbs plan.md subtask lines that have no corresponding entry
    in track-state.json, adding them as pending so the dispatcher sees them.
    """
    if state is None:
        state = load(track_dir)
    plan_path = Path(track_dir) / "plan.md"

    with open(plan_path) as f:
        lines = f.readlines()

    result = []
    phase_idx = 0
    task_idx = 0
    subtask_idx = 0
    synced = 0
    absorbed = 0

    for line in lines:
        stripped = line.rstrip("\n")

        pm = re.match(r"^##\s+Phase\s+(\d+)\b", stripped)
        if pm:
            phase_idx = int(pm.group(1))
            task_idx = 0
            subtask_idx = 0
            result.append(stripped)
            continue

        tm = re.match(r"^(\s*)-\s+\[([ x~!>#\-d])\]\s+(.*)", stripped)
        if tm:
            if phase_idx < 1:
                # Task line found before any Phase heading — skip
                result.append(stripped)
                continue

            indent = tm.group(1)
            rest = tm.group(3)
            is_subtask = len(indent) > 0

            rest_clean = _clean_trailing_markers(rest)

            try:
                if is_subtask:
                    subtask_idx += 1
                    sub = state["phases"][phase_idx - 1]["tasks"][task_idx - 1]["subtasks"][subtask_idx - 1]
                    # Always show the subtask's OWN status, even if parent is completed
                    marker = MARKER_MAP.get(sub["status"], " ")
                    sha = sub.get("commit_sha", "")
                else:
                    task_idx += 1
                    subtask_idx = 0
                    t = state["phases"][phase_idx - 1]["tasks"][task_idx - 1]
                    marker = MARKER_MAP.get(t["status"], " ")
                    sha = t.get("commit_sha", "")

                new_line = f"{indent}- [{marker}] {rest_clean}"
                # Only append sha if it's a valid non-empty 7-char hex string
                if sha and marker in SHA_MARKERS and re.match(r"^[0-9a-f]{7}$", sha):
                    new_line += f" [{sha}]"
                result.append(new_line)
                synced += 1
                continue
            except (IndexError, KeyError):
                # Untracked subtask: auto-absorb into state as pending
                if is_subtask and phase_idx >= 1 and task_idx >= 1:
                    try:
                        parent_task = state["phases"][phase_idx - 1]["tasks"][task_idx - 1]
                        parent_subs = parent_task.setdefault("subtasks", [])
                        # Always absorb as 'pending' so the dispatcher sees new work.
                        # If parent was terminal, reopen it so the new subtask
                        # gets dispatched instead of being silently marked done.
                        parent_subs.append({"name": rest_clean, "status": "pending"})
                        if parent_task["status"] in TERMINAL_FOR_PARENT:
                            parent_task["status"] = "in_progress"
                            for k in ("commit_sha", "completed_at"):
                                parent_task.pop(k, None)
                        absorbed += 1
                        # Now retry the lookup with the absorbed entry
                        sub = parent_subs[-1]
                        marker = MARKER_MAP.get(sub["status"], " ")
                        new_line = f"{indent}- [{marker}] {rest_clean}"
                        result.append(new_line)
                        synced += 1
                        continue
                    except (IndexError, KeyError):
                        pass
                    print(
                        f"WARNING: Untracked subtask in plan.md at Phase {phase_idx}, "
                        f"Task {task_idx}, subtask index {subtask_idx}: "
                        f"{rest_clean[:60]}{'...' if len(rest_clean) > 60 else ''}",
                        file=sys.stderr,
                    )
                result.append(stripped)
                continue

        result.append(stripped)

    with open(plan_path, "w") as f:
        f.write("\n".join(result))
        f.write("\n")

    if absorbed > 0:
        # NOTE: intentionally on plain save(), not update(). This site is the
        # most entangled of the RMW paths: absorption is interleaved with the
        # plan.md line rewrite above, and (pre-name-matching) absorption is
        # positional and NOT idempotent — re-applying it inside update()'s
        # reload could double-append. Migrating it cleanly wants name-matched
        # absorption first, so this defers to that pass. The race is also the
        # rarest: absorption only fires when plan.md has subtasks state lacks.
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
