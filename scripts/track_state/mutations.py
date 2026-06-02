"""State mutation operations: lock, complete, fail, skip, block, defer."""
from .core import load, save
from .helpers import target, clean, now_iso, out, _last_subtask_sha, _reset_task, _propagate_to_subtasks, _any_phase_needs_checkpoint, _normalize_sha
from .constants import TERMINAL_FOR_PARENT, AUTO_COMPLETE_OK


def _do_lock(track_dir, p, t, s=None):
    state = load(track_dir)
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None

    tgt = target(state, pi, ti, si)
    tgt["status"] = "in_progress"
    clean(tgt, {"status"})

    state["current_phase_index"] = pi
    state["current_task_index"] = ti
    if si is not None:
        state["current_subtask_index"] = si
        parent = state["phases"][pi]["tasks"][ti]
        if parent["status"] != "in_progress":
            parent["status"] = "in_progress"
    else:
        state.pop("current_subtask_index", None)

    state["updated_at"] = now_iso()
    save(track_dir, state)

def _do_complete(track_dir, p, t, s=None, sha=None):
    """Returns parent_completed bool."""
    state = load(track_dir)
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None

    tgt = target(state, pi, ti, si)

    # Guard: parent task cannot be completed while subtasks are still non-terminal
    if si is None and "subtasks" in tgt:
        pending = [sub["name"] for sub in tgt["subtasks"]
                   if sub["status"] not in TERMINAL_FOR_PARENT]
        if pending:
            raise ValueError(
                f"Cannot complete P{pi + 1}.T{ti + 1} — {len(pending)} subtask(s) still "
                f"non-terminal: {pending[0]}"
                + (f" (+{len(pending)-1} more)" if len(pending) > 1 else "")
            )

    tgt["status"] = "completed"
    # For parent-complete (si=None) with empty sha, inherit from last subtask
    resolved_sha = _normalize_sha(sha) or ""
    if not resolved_sha and si is None and "subtasks" in tgt:
        resolved_sha = _last_subtask_sha(tgt)
    tgt["commit_sha"] = resolved_sha
    tgt["completed_at"] = now_iso()
    clean(tgt, {"status", "commit_sha", "completed_at"})

    parent_completed = False
    if si is not None:
        parent = state["phases"][pi]["tasks"][ti]
        if all(sub["status"] in AUTO_COMPLETE_OK for sub in parent.get("subtasks", [])):
            # Inherit SHA from last completed subtask if parent SHA is empty
            parent_sha = sha or _last_subtask_sha(parent)
            parent["status"] = "completed"
            parent["commit_sha"] = parent_sha
            parent["completed_at"] = now_iso()
            clean(parent, {"status", "commit_sha", "completed_at"})
            parent_completed = True

    # Update current indices so recovery always points to the latest state
    if parent_completed:
        # Parent was auto-completed — clear subtask index since parent is done
        state["current_phase_index"] = pi
        state["current_task_index"] = ti
        state.pop("current_subtask_index", None)
    else:
        state["current_phase_index"] = pi
        state["current_task_index"] = ti
        if si is not None:
            state["current_subtask_index"] = si
        else:
            state.pop("current_subtask_index", None)

    state["updated_at"] = now_iso()
    save(track_dir, state)
    return parent_completed

def _do_fail(track_dir, p, t, s=None, summary=""):
    """Returns retry_count."""
    state = load(track_dir)
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None

    tgt = target(state, pi, ti, si)
    tgt["status"] = "failed"
    tgt["retry_count"] = tgt.get("retry_count", -1) + 1
    tgt["last_failure_summary"] = summary
    clean(tgt, {"status", "retry_count", "last_failure_summary"})

    # Update current indices so recovery always points to the latest state
    state["current_phase_index"] = pi
    state["current_task_index"] = ti
    if si is not None:
        state["current_subtask_index"] = si
    else:
        state.pop("current_subtask_index", None)

    state["updated_at"] = now_iso()
    save(track_dir, state)
    return tgt["retry_count"]

def _do_skip(track_dir, p, t, s=None, reason=""):
    state = load(track_dir)
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None

    tgt = target(state, pi, ti, si)
    tgt["status"] = "skipped"
    tgt["skip_analysis"] = reason
    clean(tgt, {"status", "skip_analysis"})

    if si is None:
        _propagate_to_subtasks(tgt, "skipped", "skip_analysis", reason)

    # Update current indices so recovery always points to the latest state
    state["current_phase_index"] = pi
    state["current_task_index"] = ti
    if si is not None:
        state["current_subtask_index"] = si
    else:
        state.pop("current_subtask_index", None)

    state["updated_at"] = now_iso()
    save(track_dir, state)

def _do_block(track_dir, p, t, s=None, reason=""):
    state = load(track_dir)
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None

    tgt = target(state, pi, ti, si)
    tgt["status"] = "blocked"
    tgt["skip_analysis"] = reason
    clean(tgt, {"status", "skip_analysis"})

    if si is None:
        _propagate_to_subtasks(tgt, "blocked", "skip_analysis", reason)

    # Update current indices so recovery always points to the latest state
    state["current_phase_index"] = pi
    state["current_task_index"] = ti
    if si is not None:
        state["current_subtask_index"] = si
    else:
        state.pop("current_subtask_index", None)

    state["updated_at"] = now_iso()
    save(track_dir, state)

def _do_defer(track_dir, p, t, s=None, reason=""):
    state = load(track_dir)
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None

    tgt = target(state, pi, ti, si)
    tgt["status"] = "deferred"
    tgt["defer_reason"] = reason
    clean(tgt, {"status", "defer_reason"})

    parent_deferred = False
    if si is not None:
        parent = state["phases"][pi]["tasks"][ti]
        if all(sub["status"] in TERMINAL_FOR_PARENT for sub in parent.get("subtasks", [])):
            parent["status"] = "deferred"
            parent["defer_reason"] = "All subtasks deferred or completed"
            clean(parent, {"status", "defer_reason"})
            parent_deferred = True
    elif si is None:
        _propagate_to_subtasks(tgt, "deferred", "defer_reason", reason)

    # Update current indices so recovery always points to the latest state
    state["current_phase_index"] = pi
    state["current_task_index"] = ti
    if si is not None:
        state["current_subtask_index"] = si
    else:
        state.pop("current_subtask_index", None)

    state["updated_at"] = now_iso()
    save(track_dir, state)
    return parent_deferred

def cmd_lock(track_dir, p, t, s=None):
    _do_lock(track_dir, p, t, s)
    out(dict(ok=True))

def cmd_fail(track_dir, p, t, s=None, summary=""):
    retry_count = _do_fail(track_dir, p, t, s, summary)
    out(dict(retry_count=retry_count))

def cmd_skip(track_dir, p, t, s=None, reason=""):
    _do_skip(track_dir, p, t, s, reason)
    out(dict(ok=True))

def cmd_block(track_dir, p, t, s=None, reason=""):
    _do_block(track_dir, p, t, s, reason)
    out(dict(ok=True))

def cmd_defer(track_dir, p, t, s=None, reason=""):
    parent_deferred = _do_defer(track_dir, p, t, s, reason)
    state = load(track_dir)
    result = dict(ok=True, parent_deferred=parent_deferred)
    checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
    if checkpoint_pending is not None:
        result["phase_checkpoint_pending"] = checkpoint_pending
        result["next_action"] = "dispatch_phase_checker"
    out(result)
