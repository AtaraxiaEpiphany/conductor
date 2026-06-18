"""Task dispatch orchestration: find next, prepare, finalize."""
import json
import sys
from pathlib import Path

from .core import load, save, update
from .helpers import (
    out, out_compact, now_iso, extract_tags, _inherit_tags,
    conductor_dir, _store_evidence, _last_subtask_sha, _any_phase_needs_checkpoint,
    flag, _normalize_sha, target,
)
from .constants import AUTO_COMPLETE_OK, MAX_RETRIES
from .mutations import _do_lock, _do_complete, _do_fail
from .sync import _do_sync_plan
from .git_ops import (
    _git_commit, _git_commit_ensured, _git_head_sha, _write_git_note, _ensure_note,
    _has_sibling_sha, _update_task_sha, _recover_git_notes,
    _is_start_commit, _git_uncommitted_files,
)
from .handoff import (
    _append_execution_record, _append_deviation_legacy, _append_failure_legacy,
)
from .validate import _fix_plan_mismatches, ensure_healthy


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


def cmd_next(track_dir, compact=False):
    state = load(track_dir)
    execution_mode = state.get("execution_mode", "interactive")
    result = _find_next_task(state)
    result["execution_mode"] = execution_mode
    if compact:
        out_compact(result)
    else:
        out(result)
    return result

def cmd_dispatch_next(track_dir):
    """One-call dispatch decision: next + parent-complete resolution + tag routing.
    Returns action enum for orchestrator to switch on."""
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
            out(dict(action="dispatch_phase_checker", phase=cp,
                     execution_mode=execution_mode))
            return

        # Find next task
        result = _find_next_task(state)

        if result.get("phase", 0) < 1:
            out(dict(action="finalize"))
            return

        # Resolve action from type + tags
        rtype = result["type"]
        tags = result["tags"]

        if rtype == "parent-complete":
            # Auto-complete parent, resolve SHA from subtasks, then loop again
            parent_task = state["phases"][result["phase"] - 1]["tasks"][result["task"] - 1]
            sha = _last_subtask_sha(parent_task)
            try:
                _do_complete(track_dir, result["phase"], result["task"], None, sha)
            except (ValueError, IndexError) as e:
                out(dict(error=str(e), status="error"))
                return
            state = load(track_dir)
            _do_sync_plan(track_dir, state)

            # Create conductor commit for parent completion (before note so note targets final SHA)
            parent_name = parent_task.get("name", "unknown")
            commit_msg = f"chore(conductor): Complete parent '{parent_name}' [{sha}]"
            committed = _git_commit_ensured(track_dir, commit_msg)
            if not committed:
                print(f"WARNING: conductor commit failed for parent-complete of '{parent_name}'",
                      file=sys.stderr)
            final_sha = _git_head_sha(track_dir) or sha
            if final_sha != sha:
                def _record_parent_sha(state):
                    state["phases"][result["phase"] - 1]["tasks"][result["task"] - 1]["commit_sha"] = final_sha
                    return state
                state = update(track_dir, _record_parent_sha)
                _do_sync_plan(track_dir, state)

            # Write git note AFTER conductor commit so it targets the same SHA track-state.json references
            state = load(track_dir)
            parent_tgt = state["phases"][result["phase"] - 1]["tasks"][result["task"] - 1]
            _ensure_note(track_dir, state, result["phase"], result["task"], None, parent_tgt)

            # Store minimal evidence for parent if none exists
            if "evidence" not in parent_tgt:
                parent_tgt["evidence"] = {"coverage_pct": None, "tc_coverage": "", "deviations": 0}
                save(track_dir, state)

            continue

        if rtype == "parent-stuck":
            # Parent has failed subtasks and no other work exists.
            # Auto-complete using TERMINAL_FOR_PARENT (includes 'failed') as fallback.
            parent_task = state["phases"][result["phase"] - 1]["tasks"][result["task"] - 1]
            sha = _last_subtask_sha(parent_task)
            try:
                _do_complete(track_dir, result["phase"], result["task"], None, sha)
            except (ValueError, IndexError) as e:
                # All subtasks should be terminal (failed counts), but guard
                # against edge cases where non-terminal subtasks still exist.
                out(dict(error=str(e), status="error"))
                return
            state = load(track_dir)
            _do_sync_plan(track_dir, state)
            parent_name = parent_task.get("name", "unknown")
            commit_msg = f"chore(conductor): Complete stuck parent '{parent_name}' [{sha}]"
            committed = _git_commit_ensured(track_dir, commit_msg)
            final_sha = _git_head_sha(track_dir) or sha
            if final_sha != sha:
                def _record_parent_sha(state):
                    state["phases"][result["phase"] - 1]["tasks"][result["task"] - 1]["commit_sha"] = final_sha
                    return state
                state = update(track_dir, _record_parent_sha)
                _do_sync_plan(track_dir, state)

            # Write git note for stuck parent (same as parent-complete path)
            parent_tgt = state["phases"][result["phase"] - 1]["tasks"][result["task"] - 1]
            _ensure_note(track_dir, state, result["phase"], result["task"], None, parent_tgt)

            out(dict(action="parent_stuck", phase=result["phase"], task=result["task"],
                     name=parent_name, sha=final_sha,
                     execution_mode=execution_mode))
            return

        # Route by tags
        if "Manual" in tags:
            action = "defer_manual"
        elif "Explore" in tags:
            action = "dispatch_explorer"
        else:
            action = "dispatch_executor"

        result["action"] = action
        result["execution_mode"] = execution_mode
        out(result)
        return

    out(dict(error="dispatch-next exceeded max iterations — possible state corruption",
             status="error"))

def cmd_recover(track_dir, compact=False):
    """Recover current task after interruption, with auto-fix and smart advancement.

    1. Runs ensure_healthy() to validate and auto-fix state.
    2. If current indices point to a terminal task, advances to next pending.
    3. Includes fixes_applied in output for caller visibility.
    """
    state, fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        result = dict(status="error", errors=verrors)
        if compact:
            print("ERROR")
        else:
            out(result)
        return

    if fixes:
        print(f"Recover auto-fixed {len(fixes)} issue(s): {'; '.join(fixes)}", file=sys.stderr)

    pi = state.get("current_phase_index", 0)
    ti = state.get("current_task_index", 0)
    si = state.get("current_subtask_index")

    if pi < 1 or ti < 1:
        result = dict(status="no_active_task")
        checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
        if checkpoint_pending is not None:
            result["phase_checkpoint_pending"] = checkpoint_pending
        if fixes:
            result["fixes_applied"] = fixes
        if compact:
            print("NO_ACTIVE_TASK")
        else:
            out(result)
        return

    try:
        task = state["phases"][pi - 1]["tasks"][ti - 1]
    except IndexError:
        result = dict(status="no_active_task")
        checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
        if checkpoint_pending is not None:
            result["phase_checkpoint_pending"] = checkpoint_pending
        if fixes:
            result["fixes_applied"] = fixes
        if compact:
            print("NO_ACTIVE_TASK")
        else:
            out(result)
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

    if compact:
        out_compact(result)
    else:
        out(result)


def cmd_dispatch_prepare(track_dir):
    """Lock + sync-plan + return commit message template. Reduces CLI round trips."""
    # Auto-fix state (includes plan reconciliation + all other fixes)
    state, fixes, _ = ensure_healthy(track_dir)
    if state is None:
        out(dict(action="error", error="Cannot read track-state.json"))
        return
    if fixes:
        print(f"Dispatch-prepare auto-fixed {len(fixes)} issue(s): {'; '.join(fixes)}", file=sys.stderr)

    # Find next task directly — avoid calling cmd_next() which prints to stdout,
    # causing duplicate JSON output that confuses the orchestrator.
    execution_mode = state.get("execution_mode", "interactive")
    nxt = _find_next_task(state)
    nxt["execution_mode"] = execution_mode

    if nxt.get("phase", 0) < 1:
        out(dict(action="done", next=nxt))
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
    elif "Manual" in tags:
        action = "defer"
    elif "Explore" in tags:
        action = "explore"
    else:
        action = "execute"

    if action == "parent-complete":
        out(dict(action=action, phase=pi, task=ti, name=name,
                 sha=sha, next=nxt))
        return
    if action == "parent_stuck":
        out(dict(action=action, phase=pi, task=ti, name=name,
                 sha=sha, execution_mode=execution_mode, next=nxt))
        return
    if action == "defer":
        # Auto-defer: lock not needed
        out(dict(action="defer", phase=pi, task=ti, name=name,
                 reason="Deferred: manual task requires human verification",
                 next=nxt))
        return

    # Lock + sync-plan for explore/execute
    # Detect resume: if the target is already in_progress, this is a recovery
    # from a previous interrupted run — avoid duplicate "Start task" commits.
    tgt = target(state, pi, ti, si)
    is_resume = tgt.get("status") == "in_progress"

    _do_lock(track_dir, pi, ti, si)
    synced = _do_sync_plan(track_dir)

    if is_resume:
        commit_msg = None  # Already started — skip the start commit
    else:
        commit_msg = f"chore(conductor): Start task '{name}' [P{pi}.T{ti}]"

    out(dict(action=action, phase=pi, task=ti, subtask=si, name=name,
             tags=tags, sync_count=synced, commit_msg=commit_msg,
             is_resume=is_resume,
             retry_count=tgt.get("retry_count", 0),
             last_failure_summary=tgt.get("last_failure_summary"),
             execution_mode=nxt.get("execution_mode", "interactive"),
             next=nxt))


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


def cmd_dispatch_finalize(track_dir):
    """Process result + create conductor commit + sync-plan.
    Creates the conductor commit internally so each task/subtask gets a unique SHA.
    Accepts --override key=value to patch result fields before processing.
    When result.json is missing, synthesizes result from the locked task in state."""
    result_path = conductor_dir(track_dir) / "result.json"

    if result_path.exists():
        with open(result_path) as f:
            r = json.load(f)
    else:
        # Fallback: synthesize result from locked task in track-state.json
        r = _synthesize_result_from_state(track_dir)
        if r is None:
            out(dict(error="No result file at .conductor/result.json and no locked task in state"))
            return
        print("NOTE: result.json missing — synthesized from locked task state",
              file=sys.stderr)

    # Apply overrides: merge CLI-supplied values into result (only if currently empty/falsy)
    overrides = flag(sys.argv[3:], "--override")
    if overrides:
        for pair in overrides.split(","):
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            if not r.get(k):
                r[k] = v

    status = r.get("status", "").upper()
    # Resolve phase/task/subtask indices.
    # Default to result.json (1-based from task-executor), then override
    # with locked indices from track-state.json (1-based, set by dispatch-prepare)
    # if they point to an in_progress task.
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

    if status == "SUCCESS":
        code_sha = _normalize_sha(r.get("commit_sha", ""))
        try:
            parent_completed = _do_complete(track_dir, p, t, s, code_sha)
        except ValueError as e:
            # Parent has non-terminal subtasks — retryable, keep result.json
            out(dict(error=str(e), status="error"))
            return
        except IndexError as e:
            # Stale indices — unrecoverable, clean up result.json
            result_path.unlink(missing_ok=True)
            out(dict(error=str(e), status="error"))
            return
        # Reload state after _do_complete modified the file
        state = load(track_dir)

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

        # Delete result.json after all commits succeed
        # If commit failed, result.json is preserved for manual recovery
        if committed:
            result_path.unlink(missing_ok=True)
        else:
            print(f"WARNING: result.json preserved due to commit failure", file=sys.stderr)

        # Write git note using the SHA stored in track-state.json (not the conductor commit SHA).
        # This ensures `git notes show <plan_sha>` works since plan.md shows the same SHA.
        try:
            note_tgt = target(state, int(p), int(t), int(s) if s is not None else None)
            note_sha = note_tgt.get("commit_sha", "") or final_sha
        except (IndexError, KeyError):
            note_sha = final_sha
        r["commit_sha"] = note_sha
        _write_git_note(track_dir, r, state)

        result = dict(status="success", sha=final_sha, parent_completed=parent_completed,
                      deviations=len(r.get("spec_deviation_detail", [])),
                      sync_count=synced, committed=committed)

        # Check if ANY phase needs checkpoint after this completion
        checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
        if checkpoint_pending is not None:
            result["phase_checkpoint_pending"] = checkpoint_pending

        out(result)

    elif status == "FAILURE":
        summary = r.get("summary", "")
        retry_count = _do_fail(track_dir, p, t, s, summary)
        # Reload state after _do_fail modified the file
        state = load(track_dir)
        synced = _do_sync_plan(track_dir, state)
        _append_execution_record(track_dir, p, t, s, r, state)
        _append_failure_legacy(track_dir, r)

        commit_msg = f"chore(conductor): '{task_name}' failed (attempt {retry_count})"
        committed = _git_commit(track_dir, commit_msg)
        if committed:
            result_path.unlink(missing_ok=True)
        else:
            print(f"WARNING: result.json preserved due to commit failure", file=sys.stderr)
        out(dict(status="failure", retry_count=retry_count, summary=summary,
                 sync_count=synced, committed=committed,
                 phase=p, task=t, subtask=s))

    else:
        out(dict(error=f"Unknown status: {status}"))
