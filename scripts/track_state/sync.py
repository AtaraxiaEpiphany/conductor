"""Plan.md sync operations."""
import re
import sys
from pathlib import Path

from .core import load, save
from .helpers import now_iso, out, _clean_trailing_markers, _any_phase_needs_checkpoint
from .constants import MARKER_MAP, SHA_MARKERS, TERMINAL_FOR_PARENT
from .task_profiles import derive_child_task_type


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
                        # Subtask inherits the parent's task_type (contract: never
                        # tag subtasks) — keeps the cache populated like init subtasks.
                        parent_subs.append({
                            "name": rest_clean,
                            "status": "pending",
                            "task_type": derive_child_task_type(parent_task),
                        })
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
        state["updated_at"] = now_iso()
        save(track_dir, state)

    return synced


def insert_subtask_lines(track_dir, p, t, after_subtask_index, names):
    """Insert indented ``- [ ] <name>`` subtask lines into plan.md.

    Splices the new subtask lines under task ``T{t}`` of ``Phase {p}``, positioned
    after subtask ``after_subtask_index`` (1-based; ``0`` = right under the task
    line, before any existing subtasks). Used by ``cmd_split`` to mirror in plan.md
    the subtasks it just appended to track-state.json, so the next ``_do_sync_plan``
    finds matching lines (its auto-absorb is a no-op when the lines already exist).

    Tolerant by design: if plan.md is missing or the task line cannot be located,
    emit a WARNING to stderr and return without raising — the JSON mutation is
    already committed and correct; the plan/state count mismatch surfaces on the
    next ``validate`` for repair rather than crashing the split.

    Atomic on write: temp file + ``os.replace`` (plan.md otherwise has no atomic
    write; a split is a rare, high-stakes mutation worth the safety).
    """
    import os
    import tempfile
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        print(f"WARNING: plan.md missing — split JSON applied, plan.md not spliced",
              file=sys.stderr)
        return

    with open(plan_path) as f:
        lines = f.readlines()

    phase_idx = 0
    task_idx = 0
    subtask_idx = 0
    task_line_i = None       # index into `lines` of the target task line
    subtask_indent = "  "    # fallback indent for new subtask lines
    insert_at = None         # line index where new subtasks go

    for i, raw in enumerate(lines):
        stripped = raw.rstrip("\n")
        pm = re.match(r"^##\s+Phase\s+(\d+)\b", stripped)
        if pm:
            phase_idx = int(pm.group(1))
            task_idx = 0
            subtask_idx = 0
            continue
        tm = re.match(r"^(\s*)-\s+\[([ x~!>#\-d])\]\s+(.*)", stripped)
        if not tm or phase_idx != p:
            continue
        indent = tm.group(1)
        is_subtask = len(indent) > 0
        if is_subtask:
            subtask_idx += 1
            # Once inside the target task's subtask block, track the last subtask
            # line at/before the insertion position; new lines append right after.
            if task_line_i is not None and subtask_idx <= max(after_subtask_index, 0):
                insert_at = i + 1
                subtask_indent = indent  # match the block's own indentation
        else:
            task_idx += 1
            subtask_idx = 0
            if task_idx == t:
                task_line_i = i
                insert_at = i + 1  # default: right under the task line
            elif task_line_i is not None and insert_at == task_line_i + 1 and subtask_idx == 0:
                # We've moved past the target task with no subtasks seen yet (or
                # after_subtask_index covered them) — insert_at already set; stop.
                break

    if task_line_i is None:
        print(f"WARNING: task P{p}.T{t} not found in plan.md — JSON split applied, "
              f"plan.md not spliced", file=sys.stderr)
        return
    # insert_at defaults to task_line_i+1 (before any subtasks); if we tracked
    # subtasks, it points just past the last one at/before after_subtask_index.
    if insert_at is None:
        insert_at = task_line_i + 1

    new_lines = [f"{subtask_indent}- [ ] {name}\n" for name in names]
    updated = lines[:insert_at] + new_lines + lines[insert_at:]

    # Atomic write: temp in same dir, fsync, os.replace.
    plan_dir = plan_path.parent
    fd, tmp = tempfile.mkstemp(dir=str(plan_dir), prefix=".plan-split-")
    try:
        with os.fdopen(fd, "w") as f:
            f.writelines(updated)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, plan_path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print(f"WARNING: plan.md splice failed — JSON split applied, plan.md "
              f"unchanged", file=sys.stderr)


def cmd_sync_plan(track_dir):
    synced = _do_sync_plan(track_dir)
    state = load(track_dir)
    result = dict(synced=synced)
    checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
    if checkpoint_pending is not None:
        result["phase_checkpoint_pending"] = checkpoint_pending
        result["next_action"] = "dispatch_phase_checker"
    out(result)
