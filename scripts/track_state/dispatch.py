"""Task dispatch orchestration: find next, prepare, finalize."""
import json
import sys
from pathlib import Path

from .core import load
from .helpers import (
    emit, now_iso, extract_tags, _inherit_tags,
    conductor_dir, _store_evidence, _last_subtask_sha, _any_phase_needs_checkpoint,
    flag, _normalize_sha, target, _extract_tags_for_task,
)
from .constants import AUTO_COMPLETE_OK, MAX_RETRIES
from .mutations import _do_lock, _do_complete, _do_fail, _do_fail_parent
from .result import _evaluate_gates, _tc_consistency_gate
from .spec_integrity import _ac_integrity_gate, _ears_gate
from .sync import _do_sync_plan
from .git_ops import (
    _git_commit, _git_commit_ensured, _git_head_sha, _write_git_note,
    _has_sibling_sha, _update_task_sha, _recover_git_notes,
    _is_start_commit, _git_uncommitted_files, _finalize_parent,
)
from .handoff import (
    _append_execution_record, _append_deviation_legacy, _append_failure_legacy,
)
from .validate import _fix_plan_mismatches, ensure_healthy


def _classify_task(tags):
    """Canonical task category from tags: ``"manual"`` | ``"explore"`` | ``"executor"``.

    Single source of truth for the Manual/Explore/default routing decision
    shared by ``cmd_dispatch_next`` and ``cmd_dispatch_prepare`` — which had
    near-identical tag-routing with two different action vocabularies. Add a
    new routed tag type here once; each caller maps the category to its own
    action enum. Returns ``"executor"`` (the default task-executor path) when
    no routing tag is present.
    """
    if "Manual" in tags:
        return "manual"
    if "Explore" in tags:
        return "explore"
    return "executor"


def _active_wave(track_dir):
    """Return the active wave ledger, or ``None``.

    Lazy-imports :mod:`wave` (which imports this module) to avoid a load-time
    dispatch↔wave cycle. Mutual-exclusion gate: while a wave is in flight, the
    serial spine (dispatch-next/prepare/recover) must not interleave on the same
    track — the wave owns those members' lifecycle. Returns the ledger dict when
    active, else ``None``.
    """
    try:
        from .wave import _load_ledger, _is_active
    except Exception:
        return None
    ledger = _load_ledger(track_dir)
    return ledger if _is_active(ledger) else None


def _find_next_task(state):
    """Find the next task to execute. Returns result dict or None."""
    result = None
    stuck = None
    # Pass 1: in_progress tasks (recovery / dispatch continuation)
    for pi, phase in enumerate(state["phases"], 1):
        for ti, task in enumerate(phase["tasks"], 1):
            if task["status"] == "in_progress":
                subs = task.get("subtasks")
                if subs:
                    for si, sub in enumerate(subs, 1):
                        if sub["status"] in ("in_progress", "pending"):
                            result = dict(phase=pi, task=ti, subtask=si,
                                          name=sub["name"], type="subtask",
                                          tags=_inherit_tags(extract_tags(sub["name"]), task["name"]))
                            break
                    if not result:
                        # All subtasks are non-dispatchable — auto-complete only if no failures
                        if all(sub["status"] in AUTO_COMPLETE_OK for sub in subs):
                            result = dict(phase=pi, task=ti, subtask=None,
                                          name=task["name"], type="parent-complete",
                                          tags=extract_tags(task["name"]))
                        else:
                            # Parent has failed subtasks — record as stuck but keep looking
                            if stuck is None:
                                stuck = dict(phase=pi, task=ti, subtask=None,
                                             name=task["name"], type="parent-stuck",
                                             tags=extract_tags(task["name"]))
                else:
                    result = dict(phase=pi, task=ti, subtask=None,
                                  name=task["name"], type="flat",
                                  tags=extract_tags(task["name"]))
                if result:
                    break
        if result:
            break
    # Pass 2: pending tasks (new dispatch)
    if not result:
        for pi, phase in enumerate(state["phases"], 1):
            for ti, task in enumerate(phase["tasks"], 1):
                if task["status"] == "pending":
                    subs = task.get("subtasks")
                    if subs:
                        for si, sub in enumerate(subs, 1):
                            if sub["status"] in ("in_progress", "pending"):
                                result = dict(phase=pi, task=ti, subtask=si,
                                              name=sub["name"], type="subtask",
                                              tags=_inherit_tags(extract_tags(sub["name"]), task["name"]))
                                break
                        if not result:
                            # All subtasks are terminal but parent is still pending
                            # Only auto-complete if no subtasks are failed
                            if all(sub["status"] in AUTO_COMPLETE_OK for sub in subs):
                                result = dict(phase=pi, task=ti, subtask=None,
                                              name=task["name"], type="parent-complete",
                                              tags=extract_tags(task["name"]))
                            else:
                                # Parent has failed subtasks — record as stuck but keep looking
                                if stuck is None:
                                    stuck = dict(phase=pi, task=ti, subtask=None,
                                                 name=task["name"], type="parent-stuck",
                                                 tags=extract_tags(task["name"]))
                                continue
                    else:
                        result = dict(phase=pi, task=ti, subtask=None,
                                      name=task["name"], type="flat",
                                      tags=extract_tags(task["name"]))
                    break
            if result:
                break
    # If nothing else found but a stuck parent exists, return it for handling
    if not result:
        result = stuck
    if not result:
        result = dict(phase=0, task=0, subtask=None, name=None, type=None, tags=[])
    return result


def cmd_next(track_dir, compact=True):
    state = load(track_dir)
    execution_mode = state.get("execution_mode", "interactive")
    result = _find_next_task(state)
    result["execution_mode"] = execution_mode
    emit(result, "next", compact)
    return result

def cmd_dispatch_next(track_dir, compact=True):
    """One-call dispatch decision: next + parent-complete resolution + tag routing.
    Returns action enum for orchestrator to switch on."""
    # Mutual exclusion: a wave in flight owns this track — refuse the serial spine.
    wave = _active_wave(track_dir)
    if wave:
        emit(dict(action="wave_active", phase=wave.get("phase")),
             "dispatch-next", compact)
        return

    # Auto-fix state before dispatching
    _, fixes, _ = ensure_healthy(track_dir)
    if fixes:
        print(f"Dispatch auto-fixed {len(fixes)} issue(s): {'; '.join(fixes)}", file=sys.stderr)

    # Loop instead of recursion to avoid stack overflow on many parent-complete tasks
    max_iterations = 50
    for _ in range(max_iterations):
        state = load(track_dir)
        execution_mode = state.get("execution_mode", "interactive")

        # Check if any phase needs a checkpoint before doing anything else
        cp = _any_phase_needs_checkpoint(track_dir, state)
        if cp is not None:
            emit(dict(action="dispatch_phase_checker", phase=cp,
                      execution_mode=execution_mode), "dispatch-next", compact)
            return

        # Find next task
        result = _find_next_task(state)

        if result.get("phase", 0) < 1:
            emit(dict(action="finalize"), "dispatch-next", compact)
            return

        # Resolve action from type + tags
        rtype = result["type"]
        tags = result["tags"]

        if rtype == "parent-complete":
            # Auto-complete parent, resolve SHA from subtasks, then loop again
            parent_task = state["phases"][result["phase"] - 1]["tasks"][result["task"] - 1]
            sha = _last_subtask_sha(parent_task)
            try:
                _, state = _do_complete(track_dir, result["phase"], result["task"], None, sha)
            except (ValueError, IndexError) as e:
                emit(dict(error=str(e), status="error"), "dispatch-next", compact)
                return
            _do_sync_plan(track_dir, state)

            # Conductor commit first (before finalize so the note targets the
            # final SHA), then the shared post-commit audit-trail sequence.
            parent_name = parent_task.get("name", "unknown")
            committed = _git_commit_ensured(
                track_dir, f"chore(conductor): Complete parent '{parent_name}' [{sha}]")
            if not committed:
                print(f"WARNING: conductor commit failed for parent-complete of '{parent_name}'",
                      file=sys.stderr)
            _finalize_parent(track_dir, result["phase"], result["task"], sha)

            continue

        if rtype == "parent-stuck":
            # Parent has failed subtasks and no other work exists. Fail the
            # parent (renders [!], not [x]) so recover() surfaces it on the next
            # /implement run as 'failed + retry >= max' → user decides
            # retry/skip/block. See _do_fail_parent for why retry_count is pinned.
            parent_task = state["phases"][result["phase"] - 1]["tasks"][result["task"] - 1]
            sha = _last_subtask_sha(parent_task)
            try:
                _do_fail_parent(track_dir, result["phase"], result["task"], "", sha)
            except (ValueError, IndexError) as e:
                # All subtasks should be terminal (failed counts), but guard
                # against edge cases where non-terminal subtasks still exist.
                emit(dict(error=str(e), status="error"), "dispatch-next", compact)
                return
            state = load(track_dir)
            _do_sync_plan(track_dir, state)
            parent_name = parent_task.get("name", "unknown")
            _git_commit_ensured(
                track_dir,
                f"chore(conductor): Fail parent '{parent_name}' (subtasks exhausted retries)")
            # Same post-commit audit trail as parent-complete, minus the evidence
            # seed (a failed parent carries no coverage evidence).
            final_sha = _finalize_parent(
                track_dir, result["phase"], result["task"], sha, ensure_evidence=False)

            emit(dict(action="parent_stuck", phase=result["phase"], task=result["task"],
                      name=parent_name, sha=final_sha,
                      execution_mode=execution_mode), "dispatch-next", compact)
            return

        # Route by tags
        category = _classify_task(tags)
        if category == "manual":
            # continuous: auto-defer (no human in the loop). interactive: surface
            # to the user — a [Manual] task can't be auto-executed (task-executor
            # has no Manual handling) and must not be silently deferred.
            action = "defer_manual" if execution_mode == "continuous" else "manual_task"
        elif category == "explore":
            action = "dispatch_explorer"
        else:
            action = "dispatch_executor"

        result["action"] = action
        result["execution_mode"] = execution_mode
        emit(result, "dispatch-next", compact)
        return

    emit(dict(error="dispatch-next exceeded max iterations — possible state corruption",
              status="error"), "dispatch-next", compact)


def _emit_no_active_task(track_dir, state, fixes, compact):
    """Emit the no-active-task result shared by both cmd_recover guards."""
    result = dict(status="no_active_task")
    checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
    if checkpoint_pending is not None:
        result["phase_checkpoint_pending"] = checkpoint_pending
    if fixes:
        result["fixes_applied"] = fixes
    emit(result, "recover", compact)


def cmd_recover(track_dir, compact=True):
    """Recover current task after interruption, with auto-fix and smart advancement.

    1. Runs ensure_healthy() to validate and auto-fix state.
    2. If current indices point to a terminal task, advances to next pending.
    3. Includes fixes_applied in output for caller visibility.
    """
    # Mutual exclusion: a wave in flight owns this track — wave-abort/wave-finalize
    # are its recovery paths, not the serial recover spine.
    wave = _active_wave(track_dir)
    if wave:
        emit(dict(status="wave_active", phase=wave.get("phase")), "recover", compact)
        return

    state, fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        result = dict(status="error", errors=verrors)
        emit(result, "recover", compact)
        return

    if fixes:
        print(f"Recover auto-fixed {len(fixes)} issue(s): {'; '.join(fixes)}", file=sys.stderr)

    pi = state.get("current_phase_index", 0)
    ti = state.get("current_task_index", 0)
    si = state.get("current_subtask_index")

    if pi < 1 or ti < 1:
        # No active task → any leftover result.json is an orphan from a crashed
        # finalize. Reap it so the next dispatch-finalize can't misread a stale
        # file as this run's result.
        _clear_stale_result(track_dir)
        _emit_no_active_task(track_dir, state, fixes, compact)
        return

    try:
        task = state["phases"][pi - 1]["tasks"][ti - 1]
    except IndexError:
        _clear_stale_result(track_dir)
        _emit_no_active_task(track_dir, state, fixes, compact)
        return

    # Resolve subtask or flat task
    if si is not None and "subtasks" in task and len(task["subtasks"]) > 0:
        si = min(si, len(task["subtasks"]))
        tgt = task["subtasks"][si - 1]
        name = tgt["name"]
        ttype = "subtask"
    else:
        tgt = task
        name = task["name"]
        ttype = "flat"

    # Reap an orphaned result.json when the resolved target is not in_progress
    # (a stale lock just reaped to pending by ensure_healthy, or a terminal
    # task). dispatch-finalize owns the in_progress case — leave that result.json
    # for it to consume.
    if tgt.get("status") != "in_progress":
        _clear_stale_result(track_dir)

    # Best-effort: recover missing git notes for completed tasks
    _recover_git_notes(track_dir, state)

    # For subtasks, inherit parent tags when subtask has none
    sub_tags = extract_tags(name)
    if ttype == "subtask" and not sub_tags:
        sub_tags = extract_tags(task["name"])

    result = dict(
        status=tgt.get("status", "pending"),
        phase=pi, task=ti, subtask=si,
        name=name, type=ttype,
        retry_count=tgt.get("retry_count", 0),
        max_retries=MAX_RETRIES,
        last_failure_summary=tgt.get("last_failure_summary"),
        tags=sub_tags,
        execution_mode=state.get("execution_mode", "interactive"),
    )
    if fixes:
        result["fixes_applied"] = fixes

    # Check if any phase needs a checkpoint — scan ALL phases
    checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
    if checkpoint_pending is not None:
        result["phase_checkpoint_pending"] = checkpoint_pending

    emit(result, "recover", compact)


def _clear_stale_result(track_dir):
    """Remove any prior attempt's result.json before dispatching a fresh run.

    dispatch-finalize reads ``.conductor/result.json`` on existence (not
    freshness), so an agent that stops without writing a fresh file could
    otherwise leave a STALE result from a previous attempt/retry read as the
    current task's result. Clearing here guarantees the next result.json is
    genuinely from this run. ``missing_ok=True`` makes it a no-op on a fresh task.
    """
    (conductor_dir(track_dir) / "result.json").unlink(missing_ok=True)


def cmd_dispatch_prepare(track_dir, compact=True):
    """Lock + sync-plan + return commit message template. Reduces CLI round trips."""
    # Mutual exclusion: a wave in flight owns this track — refuse the serial spine.
    wave = _active_wave(track_dir)
    if wave:
        emit(dict(action="wave_active", phase=wave.get("phase")),
             "dispatch-prepare", compact)
        return

    # Auto-fix state (includes plan reconciliation + all other fixes)
    state, fixes, _ = ensure_healthy(track_dir)
    if state is None:
        emit(dict(action="error", error="Cannot read track-state.json"),
             "dispatch-prepare", compact)
        return
    if fixes:
        print(f"Dispatch-prepare auto-fixed {len(fixes)} issue(s): {'; '.join(fixes)}", file=sys.stderr)

    # Find next task directly — avoid calling cmd_next() which prints to stdout,
    # causing duplicate JSON output that confuses the orchestrator.
    execution_mode = state.get("execution_mode", "interactive")
    nxt = _find_next_task(state)
    nxt["execution_mode"] = execution_mode

    if nxt.get("phase", 0) < 1:
        emit(dict(action="done"), "dispatch-prepare", compact)
        return
    pi, ti = nxt["phase"], nxt["task"]
    si = nxt.get("subtask")
    name = nxt.get("name", "?")
    tags = nxt.get("tags", [])

    # Route
    if nxt.get("type") == "parent-complete":
        sha = _last_subtask_sha_from_state(track_dir, pi, ti)
        action = "parent-complete"
    elif nxt.get("type") == "parent-stuck":
        sha = _last_subtask_sha_from_state(track_dir, pi, ti)
        action = "parent_stuck"
    else:
        category = _classify_task(tags)
        if category == "manual":
            action = "defer" if execution_mode == "continuous" else "manual_task"
        elif category == "explore":
            action = "explore"
        else:
            action = "execute"

    if action == "parent-complete":
        emit(dict(action=action, phase=pi, task=ti, name=name,
                  sha=sha, next=nxt), "dispatch-prepare", compact)
        return
    if action == "parent_stuck":
        emit(dict(action=action, phase=pi, task=ti, name=name,
                  sha=sha, execution_mode=execution_mode, next=nxt),
             "dispatch-prepare", compact)
        return
    if action == "manual_task":
        # Interactive: surface to the user — no lock (manual tasks aren't executed).
        emit(dict(action="manual_task", phase=pi, task=ti, name=name,
                  execution_mode=execution_mode, next=nxt),
             "dispatch-prepare", compact)
        return
    if action == "defer":
        # Auto-defer (continuous): lock not needed
        emit(dict(action="defer", phase=pi, task=ti, name=name,
                  reason="Deferred: manual task requires human verification",
                  next=nxt), "dispatch-prepare", compact)
        return

    # Lock + sync-plan for explore/execute
    # Detect resume: if the target is already in_progress, this is a recovery
    # from a previous interrupted run — avoid duplicate "Start task" commits.
    tgt = target(state, pi, ti, si)
    is_resume = tgt.get("status") == "in_progress"

    # Clear any result.json left by a prior attempt so finalize can't read it
    # as this run's result (see _clear_stale_result).
    _clear_stale_result(track_dir)
    _do_lock(track_dir, pi, ti, si)
    synced = _do_sync_plan(track_dir)

    if is_resume:
        commit_msg = None  # Already started — skip the start commit
    else:
        commit_msg = f"chore(conductor): Start task '{name}' [P{pi}.T{ti}]"

    emit(dict(action=action, phase=pi, task=ti, subtask=si, name=name,
              tags=tags, sync_count=synced, commit_msg=commit_msg,
              is_resume=is_resume,
              retry_count=tgt.get("retry_count", 0),
              max_retries=MAX_RETRIES,
              last_failure_summary=tgt.get("last_failure_summary"),
              execution_mode=nxt.get("execution_mode", "interactive"),
              next=nxt), "dispatch-prepare", compact)


def _last_subtask_sha_from_state(track_dir, pi, ti):
    """Get last completed subtask SHA for parent-complete."""
    state = load(track_dir)
    try:
        parent = state["phases"][pi - 1]["tasks"][ti - 1]
        return _last_subtask_sha(parent)
    except (IndexError, KeyError):
        return ""


def _synthesize_result_from_state(track_dir):
    """Build a result dict from the currently locked task in track-state.json.

    Used when .conductor/result.json is missing (agent exhausted turns or lost
    context before reaching Section 6.0). Derives phase/task/subtask/task_name
    from the current_*_index fields set by dispatch-prepare's _do_lock call.

    Smart detection:
    - If agent committed implementation code → SUCCESS with HEAD SHA
    - If only uncommitted changes → FAILURE with partial-work details
    - If no work at all → FAILURE with "agent produced no result"
    The FAILURE path ensures handoff records are written so retry agents get context.
    """

    state = load(track_dir)
    pi = state.get("current_phase_index", 0)
    ti = state.get("current_task_index", 0)

    if pi < 1 or ti < 1:
        return None

    try:
        task = state["phases"][pi - 1]["tasks"][ti - 1]
    except (IndexError, KeyError):
        return None

    si = state.get("current_subtask_index")
    if si is not None:
        try:
            tgt = task["subtasks"][si - 1]
            name = tgt["name"]
        except (IndexError, KeyError):
            tgt = task
            name = task["name"]
            si = None
    else:
        tgt = task
        name = task["name"]

    if tgt.get("status") != "in_progress":
        return None

    head_sha = _git_head_sha(track_dir) or ""

    # Detect whether the agent produced any real implementation work.
    # If HEAD is still a "Start task" commit, the agent made no commits.
    start_only = _is_start_commit(track_dir)
    uncommitted = _git_uncommitted_files(track_dir)

    if start_only:
        # No implementation commit → FAILURE so callers write handoff/retry context.
        if uncommitted:
            what = ", ".join(uncommitted[:10])
            if len(uncommitted) > 10:
                what += f" (+{len(uncommitted) - 10} more)"
            what_was_done = f"Partial work left uncommitted: {what}"
            suggested = "Review and salvage uncommitted changes before retry"
        else:
            what_was_done = "No implementation work found in working tree"
            suggested = "Re-dispatch from scratch"

        return dict(
            status="FAILURE",
            commit_sha="N/A",
            summary="Task executor did not produce result (exhausted turns or lost context)",
            failure_detail={
                "what_was_done": what_was_done,
                "failure_reason": "Agent stopped without writing result.json or committing code",
                "suggested_next_step": suggested,
            },
            phase=pi,
            task=ti,
            subtask=si,
            task_name=name,
            attempt=tgt.get("retry_count", 0) + 1,
            max_retries=MAX_RETRIES,
        )
    else:
        # Agent committed implementation code but forgot to write result.json.
        # This is a legitimate completion — synthesize SUCCESS.
        return dict(
            status="SUCCESS",
            commit_sha=head_sha,
            summary="Synthesized from locked task (result.json missing, implementation commit found)",
            phase=pi,
            task=ti,
            subtask=si,
            task_name=name,
            attempt=tgt.get("retry_count", 0) + 1,
            max_retries=MAX_RETRIES,
        )


def _resolve_finalize_target(track_dir, result_path):
    """Load result.json (or synthesize from the locked task), apply --override
    patches, and resolve the (p, t, s) target.

    The result-prep half of dispatch-finalize, extracted so the commit/note
    sequence below reads as straight-line success/failure handling. Returns
    ``(result_dict, p, t, s, task_name, status)`` or ``None`` when there is no
    result file AND no locked task to synthesize from.

    Index resolution prefers the locked in_progress indices from
    track-state.json (set by dispatch-prepare) over result.json's, so a stale
    result can't misroute finalization — but only when the locked target is
    actually in_progress (else keep result.json's defaults).
    """
    if result_path.exists():
        with open(result_path) as f:
            r = json.load(f)
    else:
        # Fallback: synthesize result from locked task in track-state.json
        r = _synthesize_result_from_state(track_dir)
        if r is None:
            return None

    # Apply overrides: merge CLI-supplied values into result (only if empty/falsy)
    overrides = flag(sys.argv[3:], "--override")
    if overrides:
        for pair in overrides.split(","):
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            if not r.get(k):
                r[k] = v

    status = r.get("status", "").upper()
    p = str(r.get("phase") if r.get("phase") is not None else "")
    t = str(r.get("task") if r.get("task") is not None else "")
    s = str(r.get("subtask")) if r.get("subtask") is not None else None

    state = load(track_dir)
    locked_pi = state.get("current_phase_index")
    locked_ti = state.get("current_task_index")
    locked_si = state.get("current_subtask_index")
    if locked_pi is not None and locked_ti is not None and locked_pi >= 1 and locked_ti >= 1:
        try:
            locked_tgt = state["phases"][locked_pi - 1]["tasks"][locked_ti - 1]
            if locked_si is not None:
                locked_tgt = locked_tgt["subtasks"][locked_si - 1]
            if locked_tgt.get("status") == "in_progress":
                p = str(locked_pi)
                t = str(locked_ti)
                s = str(locked_si) if locked_si is not None else None
        except (IndexError, KeyError):
            pass  # keep result.json defaults

    task_name = r.get("task_name", "unknown")
    return r, p, t, s, task_name, status


def _finalize_task(track_dir, p, t, s, r, task_name, status):
    """Run the SUCCESS/FAILURE state transition + conductor commit + git note.

    Returns ``(result_dict, clear_result_json)``. The dict is the envelope the
    caller emits under its own command name; ``clear_result_json`` tells the
    caller whether to delete the source ``result.json`` (True on a committed
    transition or a stale-index error; False to preserve for manual recovery).

    Factored out of :func:`cmd_dispatch_finalize` so :func:`cmd_wave_finalize`
    can reuse the EXACT transition with explicit ``(p, t, s)`` — the serial
    resolver (:func:`_resolve_finalize_target`) routes by the singleton cursor,
    which is unset under a wave. The caller owns ``result.json`` cleanup (and,
    for waves, worktree teardown) so this function stays pure transition logic.
    """
    if status == "SUCCESS":
        code_sha = _normalize_sha(r.get("commit_sha", ""))
        try:
            parent_completed, state = _do_complete(track_dir, p, t, s, code_sha)
        except ValueError as e:
            # Parent has non-terminal subtasks — retryable, keep result.json
            return dict(error=str(e), status="error"), False
        except IndexError as e:
            # Stale indices — unrecoverable, clean up result.json
            return dict(error=str(e), status="error"), True

        _store_evidence(state, track_dir, p, t, s, r)

        synced = _do_sync_plan(track_dir, state)
        _append_execution_record(track_dir, p, t, s, r, state)
        for dev in r.get("spec_deviation_detail", []):
            _append_deviation_legacy(track_dir, task_name, dev)

        # Create conductor commit to get a unique SHA per task/subtask
        commit_msg = f"chore(conductor): Complete '{task_name}' [{code_sha}]"
        committed = _git_commit_ensured(track_dir, commit_msg)
        if not committed:
            print(f"WARNING: conductor commit failed for '{task_name}'",
                  file=sys.stderr)
        final_sha = code_sha
        if committed:
            final_sha = _git_head_sha(track_dir) or code_sha
        # If subagent provided no code SHA but conductor committed, store conductor SHA
        if committed and not code_sha and final_sha:
            state = _update_task_sha(track_dir, p, t, s, final_sha)

        # Deduplicate: if SHA collides with a sibling subtask, force a unique SHA
        if _has_sibling_sha(state, p, t, s, final_sha):
            _git_commit(track_dir, f"chore(conductor): Dedup '{task_name}' [{final_sha}]",
                        allow_empty=True)
            dedup_sha = _git_head_sha(track_dir)
            if dedup_sha and dedup_sha != final_sha:
                final_sha = dedup_sha
                state = _update_task_sha(track_dir, p, t, s, final_sha)

        # Write git note using the SHA stored in track-state.json (not the conductor commit SHA).
        # This ensures `git notes show <plan_sha>` works since plan.md shows the same SHA.
        try:
            note_tgt = target(state, int(p), int(t), int(s) if s is not None else None)
            note_sha = note_tgt.get("commit_sha", "") or final_sha
        except (IndexError, KeyError):
            note_sha = final_sha
        r["commit_sha"] = note_sha
        _write_git_note(track_dir, r, state)

        # F2/F3 advisory gates on the hot path (WARN-only — matches process-result
        # via the shared _evaluate_gates helper). Computed AFTER _do_complete so a
        # gate status never blocks completion; real teeth stay at the commit-time
        # F2 ask gate + on-batch-complete F3 probe. Sub-80% coverage now surfaces
        # in the envelope instead of completing silently.
        tags = _extract_tags_for_task(state, p, t)
        coverage_gate, tdd_gate, cov_pct = _evaluate_gates(tags, r, code_sha, track_dir)
        result = dict(status="success", sha=final_sha, parent_completed=parent_completed,
                      deviations=len(r.get("spec_deviation_detail", [])),
                      sync_count=synced, committed=committed,
                      coverage_gate=coverage_gate, tdd_gate=tdd_gate,
                      ac_integrity_gate=_ac_integrity_gate(track_dir),
                      ears_gate=_ears_gate(track_dir),
                      tc_consistency_gate=_tc_consistency_gate(track_dir, r, tags),
                      phase=int(p), task=int(t),
                      subtask=(int(s) if s is not None else None))
        if cov_pct is not None:
            result["coverage_pct"] = cov_pct

        # Check if ANY phase needs checkpoint after this completion
        checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
        if checkpoint_pending is not None:
            result["phase_checkpoint_pending"] = checkpoint_pending

        # clear_result_json mirrors the original inline behavior: delete on a
        # committed transition, preserve for manual recovery on commit failure.
        return result, bool(committed)

    if status == "FAILURE":
        summary = r.get("summary", "")
        retry_count, state = _do_fail(track_dir, p, t, s, summary)
        synced = _do_sync_plan(track_dir, state)
        _append_execution_record(track_dir, p, t, s, r, state)
        _append_failure_legacy(track_dir, r)

        commit_msg = f"chore(conductor): '{task_name}' failed (attempt {retry_count})"
        # Use _git_commit_ensured (allow-empty fallback) to mirror the SUCCESS
        # path. The failure has already been fully ingested into track-state.json
        # + handoff + issues.md above, and the task is no longer in_progress, so a
        # preserved result.json here would only surface as an "orphaned result.json"
        # complaint on the next Stop hook. Ensured commit unlinks it reliably; the
        # genuine-git-breakage case still preserves it (both attempts return False).
        committed = _git_commit_ensured(track_dir, commit_msg)
        return (dict(status="failure", retry_count=retry_count, summary=summary,
                     sync_count=synced, committed=committed,
                     phase=int(p), task=int(t),
                     subtask=(int(s) if s is not None else None)),
                bool(committed))

    return dict(error=f"Unknown status: {status}"), False


def cmd_dispatch_finalize(track_dir, compact=True):
    """Process result + create conductor commit + sync-plan.
    Creates the conductor commit internally so each task/subtask gets a unique SHA.
    Accepts --override key=value to patch result fields before processing.
    When result.json is missing, synthesizes result from the locked task in state."""
    result_path = conductor_dir(track_dir) / "result.json"

    resolved = _resolve_finalize_target(track_dir, result_path)
    if resolved is None:
        emit(dict(error="No result file at .conductor/result.json and no locked task in state"),
             "dispatch-finalize", compact)
        return
    if not result_path.exists():
        print("NOTE: result.json missing — synthesized from locked task state",
              file=sys.stderr)
    r, p, t, s, task_name, status = resolved

    result, clear_result = _finalize_task(track_dir, p, t, s, r, task_name, status)

    # result.json cleanup is the serial caller's job (wave-finalize manages its
    # own worktree result.json during teardown). _finalize_task signals whether
    # the source result.json should be deleted vs preserved for manual recovery.
    if clear_result:
        result_path.unlink(missing_ok=True)
    elif result.get("status") != "error":
        print("WARNING: result.json preserved due to commit failure", file=sys.stderr)

    emit(result, "dispatch-finalize", compact)
