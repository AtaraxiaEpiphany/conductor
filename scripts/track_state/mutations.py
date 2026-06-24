"""State mutation operations: lock, complete, fail, skip, block, defer."""
from .core import load, save, transaction
from .helpers import target, clean, now_iso, out, _last_subtask_sha, _reset_task, _propagate_to_subtasks, _any_phase_needs_checkpoint, _normalize_sha
from .constants import TERMINAL_FOR_PARENT, AUTO_COMPLETE_OK, MAX_RETRIES


class F1StateLockError(ValueError):
    """Raised by ``_do_lock`` when locking would violate F1 (Global State Lock).

    Subclass of ``ValueError`` so existing callers that catch ``ValueError``
    (e.g. dispatch) still handle it, while letting code distinguish a genuine
    F1 violation if it wants to. Raised *before* any mutation, so the
    transaction aborts and on-disk state is untouched.
    """


def _foreign_in_progress(state, pi, ti, si):
    """F1 helper: in_progress tasks OTHER than the lock target and its parent.

    Returns a list of ``P{p}.T{t}[.S{s}]`` location strings. F1 permits at most
    one in_progress task (flat), or one parent ``[~]`` plus one active child
    ``[~]``. When locking target ``(pi, ti, si)`` the target itself and its
    parent ``(pi, ti)`` are excluded, so legitimate subtask locking and re-lock
    (resume of an already-in_progress task) are allowed; any *other*
    in_progress task is a violation.
    """
    foreign = []
    for p, phase in enumerate(state.get("phases", []), 1):
        for tk, task in enumerate(phase.get("tasks", []), 1):
            if task.get("status") == "in_progress" and not (p == pi and tk == ti):
                foreign.append(f"P{p}.T{tk}")
            for sk, sub in enumerate(task.get("subtasks", []), 1):
                if (sub.get("status") == "in_progress"
                        and not (p == pi and tk == ti and sk == si)):
                    foreign.append(f"P{p}.T{tk}.S{sk}")
    return foreign


def _set_current_indices(state, pi, ti, si=None):
    """Update current_*_index fields so recovery always points to the latest state."""
    state["current_phase_index"] = pi
    state["current_task_index"] = ti
    if si is not None:
        state["current_subtask_index"] = si
    else:
        state.pop("current_subtask_index", None)


def _do_lock(track_dir, p, t, s=None):
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    with transaction(track_dir) as state:
        # F1 — Global State Lock: at most one in_progress task (or one parent
        # [~] + one active child [~]). Exclude the target and its parent so
        # subtask locking and resume (re-lock of an in_progress task) pass.
        foreign = _foreign_in_progress(state, pi, ti, si)
        if foreign:
            tgt_loc = f"P{pi}.T{ti}" + (f".S{si}" if si else "")
            raise F1StateLockError(
                f"F1 Global State Lock: cannot lock {tgt_loc} — another task is "
                f"already in_progress: {foreign[0]}"
                + (f" (+{len(foreign) - 1} more)" if len(foreign) > 1 else "")
                + ". Run `track-state validate --fix` to clear stale locks."
            )

        tgt = target(state, pi, ti, si)
        tgt["status"] = "in_progress"
        # retry_count/last_failure_summary are intrinsic task history, never reset on lock.
        clean(tgt, {"status", "retry_count", "last_failure_summary"})

        _set_current_indices(state, pi, ti, si)
        if si is not None:
            parent = state["phases"][pi - 1]["tasks"][ti - 1]
            if parent["status"] != "in_progress":
                parent["status"] = "in_progress"

        state["updated_at"] = now_iso()

def _do_complete(track_dir, p, t, s=None, sha=None):
    """Returns ``(parent_completed, state)``.

    ``state`` is the post-transaction dict (already saved), handed back so the
    dispatch/process-result hot paths don't re-load immediately after — that
    reload was always fetching exactly this dict.
    """
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    with transaction(track_dir) as state:
        tgt = target(state, pi, ti, si)

        # Guard: parent task cannot be completed while subtasks are still non-terminal
        if si is None and "subtasks" in tgt:
            pending = [sub["name"] for sub in tgt["subtasks"]
                       if sub["status"] not in TERMINAL_FOR_PARENT]
            if pending:
                raise ValueError(
                    f"Cannot complete P{pi}.T{ti} — {len(pending)} subtask(s) still "
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
            parent = state["phases"][pi - 1]["tasks"][ti - 1]
            if all(sub["status"] in AUTO_COMPLETE_OK for sub in parent.get("subtasks", [])):
                # Inherit SHA from last completed subtask if parent SHA is empty.
                # Normalize like the subtask itself (line above) so the parent record
                # matches the 7-char form siblings hold; a raw 40-char sha would
                # otherwise drop out of plan.md [sha] markers and break sibling-dedup.
                parent_sha = _normalize_sha(sha) or _last_subtask_sha(parent)
                parent["status"] = "completed"
                parent["commit_sha"] = parent_sha
                parent["completed_at"] = now_iso()
                clean(parent, {"status", "commit_sha", "completed_at"})
                parent_completed = True

        # Update current indices so recovery always points to the latest state
        _set_current_indices(state, pi, ti, None if parent_completed else si)

        state["updated_at"] = now_iso()
        return parent_completed, state

def _do_fail(track_dir, p, t, s=None, summary="", retryable=True):
    """Returns ``(retry_count, state)``.

    ``state`` is the post-transaction dict (already saved), handed back so the
    dispatch/process-result hot paths don't re-load immediately after.

    When retryable=True (default, used by dispatch-finalize) and retry_count
    has not reached MAX_RETRIES, the task is re-queued as "pending" so
    dispatch-next finds it for automatic re-dispatch. When retry_count reaches
    MAX_RETRIES, or retryable=False (manual CLI fail), status is set to "failed"
    permanently.
    """
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    with transaction(track_dir) as state:
        tgt = target(state, pi, ti, si)
        tgt["retry_count"] = tgt.get("retry_count", -1) + 1
        tgt["last_failure_summary"] = summary

        if retryable and tgt["retry_count"] < MAX_RETRIES:
            # Re-queue for retry — pending so dispatch-next finds it again.
            # retry_count and last_failure_summary are preserved for the retry agent.
            tgt["status"] = "pending"
        else:
            # Max retries exhausted or manual fail — permanently failed.
            tgt["status"] = "failed"
        clean(tgt, {"status", "retry_count", "last_failure_summary"})

        # Update current indices so recovery always points to the latest state
        _set_current_indices(state, pi, ti, si)

        state["updated_at"] = now_iso()
        return tgt["retry_count"], state

def _do_fail_parent(track_dir, p, t, summary="", sha=None):
    """Mark a PARENT task failed because its subtasks exhausted retries.

    The parent-stuck dispatch path previously called _do_complete, which rendered
    the parent ``[x]`` even though it had ``[!]`` failed subtasks — dishonest, and
    it caused the parent (and its failed subtasks) to be skipped on the next
    /implement run since ``completed`` is terminal for dispatch.

    Failing the parent instead renders it ``[!]`` and pins ``retry_count`` to
    MAX_RETRIES so recover() surfaces it as ``failed + retry >= max`` (the §2.0
    route that lets the user decide retry/skip/block) rather than re-dispatching.
    Subtasks keep their individual statuses; ``commit_sha`` is preserved for
    traceability to the last completed subtask.
    """
    pi, ti = int(p), int(t)
    with transaction(track_dir) as state:
        tgt = target(state, pi, ti, None)

        failed_names = [sub["name"] for sub in tgt.get("subtasks", [])
                        if sub.get("status") == "failed"]
        tgt["status"] = "failed"
        tgt["retry_count"] = MAX_RETRIES
        tgt["last_failure_summary"] = summary or (
            "Subtasks failed: " + ", ".join(failed_names) if failed_names
            else "Subtasks failed"
        )
        resolved_sha = _normalize_sha(sha) or _last_subtask_sha(tgt)
        tgt["commit_sha"] = resolved_sha
        # Keep status (not a reset field) + the three reset fields we just set.
        clean(tgt, {"retry_count", "last_failure_summary", "commit_sha"})

        _set_current_indices(state, pi, ti, None)

        state["updated_at"] = now_iso()
        return tgt["last_failure_summary"]

def _do_skip(track_dir, p, t, s=None, reason=""):
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    with transaction(track_dir) as state:
        tgt = target(state, pi, ti, si)
        tgt["status"] = "skipped"
        tgt["skip_analysis"] = reason
        clean(tgt, {"status", "skip_analysis"})

        if si is None:
            _propagate_to_subtasks(tgt, "skipped", "skip_analysis", reason)

        # Update current indices so recovery always points to the latest state
        _set_current_indices(state, pi, ti, si)

        state["updated_at"] = now_iso()

def _do_block(track_dir, p, t, s=None, reason=""):
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    with transaction(track_dir) as state:
        tgt = target(state, pi, ti, si)
        tgt["status"] = "blocked"
        tgt["skip_analysis"] = reason
        clean(tgt, {"status", "skip_analysis"})

        if si is None:
            _propagate_to_subtasks(tgt, "blocked", "skip_analysis", reason)

        # Update current indices so recovery always points to the latest state
        _set_current_indices(state, pi, ti, si)

        state["updated_at"] = now_iso()

def _do_defer(track_dir, p, t, s=None, reason=""):
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    with transaction(track_dir) as state:
        tgt = target(state, pi, ti, si)
        tgt["status"] = "deferred"
        tgt["defer_reason"] = reason
        clean(tgt, {"status", "defer_reason"})

        parent_deferred = False
        if si is not None:
            parent = state["phases"][pi - 1]["tasks"][ti - 1]
            if all(sub["status"] in TERMINAL_FOR_PARENT for sub in parent.get("subtasks", [])):
                parent["status"] = "deferred"
                parent["defer_reason"] = "All subtasks deferred or completed"
                clean(parent, {"status", "defer_reason"})
                parent_deferred = True
        elif si is None:
            _propagate_to_subtasks(tgt, "deferred", "defer_reason", reason)

        # Update current indices so recovery always points to the latest state
        _set_current_indices(state, pi, ti, si)

        state["updated_at"] = now_iso()
        return parent_deferred

def cmd_lock(track_dir, p, t, s=None):
    _do_lock(track_dir, p, t, s)
    out(dict(ok=True))

def cmd_fail(track_dir, p, t, s=None, summary=""):
    retry_count, _state = _do_fail(track_dir, p, t, s, summary, retryable=False)
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
