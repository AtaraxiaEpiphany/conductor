"""State mutation operations: lock, complete, fail, skip, block, defer."""
import time
from .core import load, save, transaction
from .helpers import target, clean, now_iso, out, _last_subtask_sha, _reset_task, _propagate_to_subtasks, _any_phase_needs_checkpoint, _normalize_sha
from .constants import TERMINAL_FOR_PARENT, AUTO_COMPLETE_OK, MAX_RETRIES, LOCKED_AT_FIELD, task_max_retries
from lib.recovery import RECOVERY_TURN_FIELD


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


def _lock_inplace(state, pi, ti, si=None):
    """Mark target ``(pi, ti[, si])`` in_progress with a fresh recovery budget.

    The lock-the-target half of :func:`_do_lock`, operating on an already-loaded
    ``state`` (caller holds the transaction). Omits the two serial-spine-only
    concerns so a wave can lock several members in one transaction:

    - **No F1 check** — :func:`_foreign_in_progress` is the serial spine's
      "exactly one in_progress" gate; a wave's authority to hold multiple
      in_progress tasks comes from the ``.conductor/parallel.json`` ledger, not
      F1. The wave guards (``_fix_stale_lock`` / ``check_f1_rule`` / dispatch
      refusal) honor that ledger instead.
    - **No cursor / parent propagation** — the singleton ``current_*_index``
      stays untouched (the serial spine owns it); wave members are flat
      top-level tasks, so subtask parent-propagation never applies.

    ``_do_lock`` calls this for its serial path; the wave scheduler
    (``wave._wave_lock``) calls it per member inside one transaction.
    """
    tgt = target(state, pi, ti, si)
    tgt["status"] = "in_progress"
    # retry_count/last_failure_summary are intrinsic task history, never reset on lock.
    clean(tgt, {"status", "retry_count", "last_failure_summary"})
    # Fresh recovery budget — a new (re)lock is a new attempt. The SubagentStop
    # hook bumps RECOVERY_TURN_FIELD each time a result-file agent stops without
    # a fresh result.json, bounded by lib.recovery.MAX_RECOVERY_TURNS. Not in
    # _RESET_FIELDS, so clean() above leaves it; set explicitly every lock so a
    # resumed/re-locked task starts at zero.
    tgt[RECOVERY_TURN_FIELD] = 0
    # Heartbeat for the stale-lock reaper (validate._fix_stale_lock): a task
    # still in_progress past STALE_LOCK_SECONDS is a killed-session orphan.
    tgt[LOCKED_AT_FIELD] = time.time()


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

        _lock_inplace(state, pi, ti, si)

        _set_current_indices(state, pi, ti, si)
        if si is not None:
            parent = state["phases"][pi - 1]["tasks"][ti - 1]
            if parent["status"] != "in_progress":
                parent["status"] = "in_progress"

        state["updated_at"] = now_iso()

def increment_recovery_turns(track_dir, p, t, s=None):
    """Atomically bump the SubagentStop recovery counter on the locked task.

    Called by ``on-subagent-stop`` each time a result-file agent (task-executor,
    explorer) stops without a fresh ``result.json``. Returns the new count, or
    ``None`` if the target isn't currently ``in_progress`` (already finalized,
    or stale indices) — the hook treats ``None`` as "can't bound this one" and
    falls back to blocking (fail-safe toward recovery).

    The counter is reset to 0 by ``_do_lock`` (a new lock is a fresh budget), so
    this only ever counts recovery turns within one lock lifetime. The hook
    bounds it against ``lib.recovery.MAX_RECOVERY_TURNS``.
    """
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    with transaction(track_dir) as state:
        try:
            tgt = target(state, pi, ti, si)
        except IndexError:
            return None
        if tgt.get("status") != "in_progress":
            return None
        tgt[RECOVERY_TURN_FIELD] = tgt.get(RECOVERY_TURN_FIELD, 0) + 1
        state["updated_at"] = now_iso()
        return tgt[RECOVERY_TURN_FIELD]

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

        if retryable and tgt["retry_count"] < task_max_retries(tgt):
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
        tgt["retry_count"] = task_max_retries(tgt)
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

def cmd_set_max_retries(track_dir, p, t, s=None, max_retries=None):
    """Set a per-task ``max_retries`` override (the A.4 CLI backing).

    Idempotent overwrite; ``max_retries`` is a reset-able field (it's not task
    history like ``retry_count``/``last_failure_summary``), so it's written via
    ``clean`` like the other mutable singletons (status, defer_reason). The CLI
    layer parses + validates ``>= 1``; this mutator trusts its caller.
    """
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    if not isinstance(max_retries, int) or max_retries < 1:
        out(dict(error=f"max_retries must be a positive integer, got {max_retries!r}"))
        return
    with transaction(track_dir) as state:
        tgt = target(state, pi, ti, si)
        tgt["max_retries"] = max_retries
        clean(tgt, {"max_retries"})
        _set_current_indices(state, pi, ti, si)
        state["updated_at"] = now_iso()
    out(dict(ok=True, phase=pi, task=ti, subtask=si, max_retries=max_retries))


def reactivate_for_modified_retry(track_dir, p, t, s=None):
    """Flip a ``failed`` task back to ``pending`` for a failure-analyst
    ``retry_modified`` re-dispatch — WITHOUT resetting retry history.

    Distinct from ``cmd_reset`` (which clears ``retry_count`` /
    ``last_failure_summary`` via ``_RESET_FIELDS``): a modified retry must still
    count against the per-task budget, so those intrinsic-history fields are
    preserved (mirrors ``_do_fail``'s preservation). Only ``status`` flips to
    ``pending`` so ``dispatch-next`` re-dispatches the task; the analyst's
    modification reaches the executor via the modified-guidance marker (B.5),
    not via state.
    """
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    with transaction(track_dir) as state:
        tgt = target(state, pi, ti, si)
        tgt["status"] = "pending"
        clean(tgt, {"status", "retry_count", "last_failure_summary"})
        _set_current_indices(state, pi, ti, si)
        state["updated_at"] = now_iso()
    return load(track_dir)


# Statuses that may be meaningfully decomposed. Terminal-clean states
# (completed/skipped/blocked/cancelled) have no pending work to split out.
_SPLITTABLE = {"pending", "in_progress", "failed"}


def _do_split(track_dir, p, t, s, subtask_names, note=None):
    """Split task/subtask ``(p, t, s)`` into new pending sibling subtasks under
    its parent task, skipping the original with ``commit_sha`` + a decomposition
    note preserved.

    The decompose invariant (failure-analyst ``decompose``): the original's
    committed work is sound — it is *skipped* (not reverted), its ``commit_sha``
    is kept on the skipped record for traceability, and the pieces are appended as
    ``pending`` subtasks so dispatch re-runs them. Two depth cases, both producing
    dispatchable two-depth units (never sub-subtasks, which nothing can dispatch):

      * task split (``s is None``) → pieces append under the task itself (it
        becomes a parent, gaining a ``subtasks`` array — the same shape auto-absorb
        produces in sync.py).
      * subtask split (``s`` set) → pieces append under the *parent task* as
        siblings of the original, so every piece stays a top-level subtask.

    Returns the post-commit state (mirrors ``reactivate_for_modified_retry``).
    plan.md insertion + sync + commit are the caller's job (``cmd_split``), run
    sequentially AFTER this transaction commits — transactions must not nest
    (core.py) and ``_do_sync_plan`` saves outside any held lock.
    """
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    with transaction(track_dir) as state:
        parent = target(state, pi, ti, None)
        orig = target(state, pi, ti, si)
        if orig.get("status") not in _SPLITTABLE:
            raise ValueError(
                f"cannot split {orig.get('status')!r} task "
                f"(only {sorted(_SPLITTABLE)} are splittable)")
        # Skip the original; KEEP commit_sha (mirrors _do_fail_parent's keep-set,
        # NOT plain _do_skip which drops it) so the partial-work SHA survives.
        orig["status"] = "skipped"
        orig["skip_analysis"] = note or "Decomposed via failure-analyst"
        clean(orig, {"status", "skip_analysis", "commit_sha"})
        # Append the pieces as pending subtasks of the parent task.
        subs = parent.setdefault("subtasks", [])
        for name in subtask_names:
            subs.append({"name": name, "status": "pending"})
        # Point at the parent so dispatch-next/step picks up the first new piece.
        _set_current_indices(state, pi, ti, None)
        state["updated_at"] = now_iso()
    return load(track_dir)


def cmd_split(track_dir, p, t, s=None, subtask_names=None, note=None):
    """``track-state split`` backing: mutate JSON, splice plan.md, sync, commit.

    Orchestrates the four steps SEQUENTIALLY (not nested — see core.py's
    no-nest constraint on ``transaction()``): ``_do_split`` (atomic JSON) →
    ``insert_subtask_lines`` (plan.md) → ``_do_sync_plan`` → bookkeeping commit.
    """
    from .sync import insert_subtask_lines, _do_sync_plan
    from .git_ops import _git_commit_ensured
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    names = subtask_names or []
    if not names:
        out(dict(error="--subtasks requires at least one non-empty name"))
        return
    try:
        state = _do_split(track_dir, pi, ti, si, names, note=note)
    except ValueError as exc:
        out(dict(error=str(exc)))
        return
    # Resolve a readable name + loc for the commit message + plan.md splice.
    parent = target(state, pi, ti, None)
    name = parent.get("name", "?")
    loc = f"P{pi}.T{ti}" + (f".S{si}" if si else "")
    # plan.md splice + sync are best-effort: the JSON mutation is already
    # committed. A missing/unwritable plan.md degrades to a WARNING + the
    # plan/state mismatch surfaces on the next `validate` (no crash — the split
    # must not fail after the authoritative state is already updated).
    try:
        insert_subtask_lines(track_dir, pi, ti, si or 0, names)
        _do_sync_plan(track_dir)
    except OSError as exc:
        print(f"WARNING: plan.md sync skipped ({exc}); JSON split applied",
              file=__import__("sys").stderr)
    _git_commit_ensured(
        track_dir,
        f"chore(conductor): Decompose '{name}' [{loc}] "
        f"into {len(names)} subtasks (failure-analyst)")
    out(dict(ok=True, phase=pi, task=ti, subtask=si, added=len(names)))

