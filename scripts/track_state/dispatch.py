"""Task dispatch orchestration: find next, prepare, finalize."""
import json
import sys
from pathlib import Path

from .core import load, save
from .helpers import (
    out, out_compact, now_iso, extract_tags, _inherit_tags,
    conductor_dir, _store_evidence, _last_subtask_sha, _any_phase_needs_checkpoint,
    flag,
)
from .constants import AUTO_COMPLETE_OK
from .mutations import _do_lock, _do_complete, _do_fail
from .sync import _do_sync_plan
from .git_ops import (
    _git_commit, _git_head_sha, _write_git_note, _ensure_note,
    _has_sibling_sha, _update_task_sha, _recover_git_notes,
)
from .handoff import (
    _append_execution_record, _append_deviation_legacy, _append_failure_legacy,
)
from .validate import _fix_plan_mismatches


def _find_next_task(state):
    """Find the next task to execute. Returns result dict or None."""
    result = None
    stuck = None
    # Pass 1: in_progress tasks (recovery / dispatch continuation)
    for pi, phase in enumerate(state["phases"]):
        for ti, task in enumerate(phase["tasks"]):
            if task["status"] == "in_progress":
                subs = task.get("subtasks")
                if subs:
                    for si, sub in enumerate(subs):
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
        for pi, phase in enumerate(state["phases"]):
            for ti, task in enumerate(phase["tasks"]):
                if task["status"] == "pending":
                    subs = task.get("subtasks")
                    if subs:
                        for si, sub in enumerate(subs):
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
        result = dict(phase=-1, task=-1, subtask=None, name=None, type=None, tags=[])
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
    # Loop instead of recursion to avoid stack overflow on many parent-complete tasks
    max_iterations = 50
    for _ in range(max_iterations):
        state = load(track_dir)
        execution_mode = state.get("execution_mode", "interactive")

        # Find next task
        result = _find_next_task(state)

        if result.get("phase", -1) < 0:
            # No more tasks — check if any phase needs a checkpoint before finalizing
            checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
            if checkpoint_pending is not None:
                out(dict(action="dispatch_phase_checker", phase=checkpoint_pending,
                         execution_mode=execution_mode))
                return
            out(dict(action="finalize"))
            return

        # Resolve action from type + tags
        rtype = result["type"]
        tags = result["tags"]

        if rtype == "parent-complete":
            # Auto-complete parent, resolve SHA from subtasks, then loop again
            parent_task = state["phases"][result["phase"]]["tasks"][result["task"]]
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
            committed = _git_commit(track_dir, commit_msg)
            if not committed:
                print(f"WARNING: conductor commit failed for parent-complete of '{parent_name}'",
                      file=sys.stderr)
            final_sha = _git_head_sha(track_dir) or sha
            if final_sha != sha:
                state = load(track_dir)
                state["phases"][result["phase"]]["tasks"][result["task"]]["commit_sha"] = final_sha
                save(track_dir, state)
                _do_sync_plan(track_dir, state)

            # Write git note AFTER conductor commit so it targets the same SHA track-state.json references
            state = load(track_dir)
            parent_tgt = state["phases"][result["phase"]]["tasks"][result["task"]]
            _ensure_note(track_dir, state, result["phase"], result["task"], None, parent_tgt)

            # Store minimal evidence for parent if none exists
            if "evidence" not in parent_tgt:
                parent_tgt["evidence"] = {"coverage_pct": None, "tc_coverage": "", "deviations": 0}
                save(track_dir, state)

            # Check if ANY phase needs a checkpoint (not just the current one)
            cp = _any_phase_needs_checkpoint(track_dir, load(track_dir))
            if cp is not None:
                out(dict(action="dispatch_phase_checker", phase=cp,
                         execution_mode=execution_mode))
                return
            continue

        if rtype == "parent-stuck":
            # Parent has failed subtasks and no other work exists.
            # Auto-complete using TERMINAL_FOR_PARENT (includes 'failed') as fallback.
            parent_task = state["phases"][result["phase"]]["tasks"][result["task"]]
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
            _git_commit(track_dir, commit_msg)
            final_sha = _git_head_sha(track_dir) or sha
            if final_sha != sha:
                state = load(track_dir)
                state["phases"][result["phase"]]["tasks"][result["task"]]["commit_sha"] = final_sha
                save(track_dir, state)
                _do_sync_plan(track_dir, state)

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
    state = load(track_dir)
    pi = state.get("current_phase_index", -1)
    ti = state.get("current_task_index", -1)
    si = state.get("current_subtask_index")

    if pi < 0 or ti < 0:
        result = dict(status="no_active_task")
        # Check if any phase needs a checkpoint (scan all phases)
        checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
        if checkpoint_pending is not None:
            result["phase_checkpoint_pending"] = checkpoint_pending
        if compact:
            print("NO_ACTIVE_TASK")
        else:
            out(result)
        return

    try:
        task = state["phases"][pi]["tasks"][ti]
    except IndexError:
        result = dict(status="no_active_task")
        # Check if any phase needs a checkpoint (scan all phases)
        checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
        if checkpoint_pending is not None:
            result["phase_checkpoint_pending"] = checkpoint_pending
        if compact:
            print("NO_ACTIVE_TASK")
        else:
            out(result)
        return

    if si is not None and "subtasks" in task and len(task["subtasks"]) > 0:
        si = min(si, len(task["subtasks"]) - 1)
        tgt = task["subtasks"][si]
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

    # Check if any phase needs a checkpoint — scan ALL phases, not just current.
    # This ensures missed checkpoints from interrupted sessions are always recovered.
    checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
    if checkpoint_pending is not None:
        result["phase_checkpoint_pending"] = checkpoint_pending

    if compact:
        out_compact(result)
    else:
        out(result)


def cmd_dispatch_prepare(track_dir):
    """Lock + sync-plan + return commit message template. Reduces CLI round trips."""
    # Auto-reconcile plan.md changes before dispatch
    state = load(track_dir)
    fixes = _fix_plan_mismatches(track_dir, state)
    if fixes:
        state["updated_at"] = now_iso()
        save(track_dir, state)

    nxt = cmd_next(track_dir)
    if nxt.get("phase", -1) < 0:
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
    if action == "defer":
        # Auto-defer: lock not needed
        out(dict(action="defer", phase=pi, task=ti, name=name,
                 reason="Deferred: manual task requires human verification",
                 next=nxt))
        return

    # Lock + sync-plan for explore/execute
    _do_lock(track_dir, pi, ti, si)
    synced = _do_sync_plan(track_dir)
    commit_msg = f"chore(conductor): Start task '{name}' [{pi}.{ti}]"

    out(dict(action=action, phase=pi, task=ti, subtask=si, name=name,
             tags=tags, sync_count=synced, commit_msg=commit_msg,
             execution_mode=nxt.get("execution_mode", "interactive"),
             next=nxt))


def _last_subtask_sha_from_state(track_dir, pi, ti):
    """Get last completed subtask SHA for parent-complete."""
    state = load(track_dir)
    try:
        parent = state["phases"][pi]["tasks"][ti]
        return _last_subtask_sha(parent)
    except (IndexError, KeyError):
        return ""


def cmd_dispatch_finalize(track_dir):
    """Process result + create conductor commit + sync-plan.
    Creates the conductor commit internally so each task/subtask gets a unique SHA.
    Accepts --override key=value to patch result fields before processing."""
    result_path = conductor_dir(track_dir) / "result.json"
    if not result_path.exists():
        out(dict(error="No result file at .conductor/result.json"))
        return

    with open(result_path) as f:
        r = json.load(f)

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
    p = str(r.get("phase", ""))
    t = str(r.get("task", ""))
    s = r.get("subtask")
    if s is not None:
        s = str(s)
    task_name = r.get("task_name", "unknown")

    state = load(track_dir)

    if status == "SUCCESS":
        code_sha = r.get("commit_sha", "")
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
        committed = _git_commit(track_dir, commit_msg)
        if not committed:
            print(f"WARNING: conductor commit failed for '{task_name}'",
                  file=sys.stderr)
        final_sha = code_sha
        if committed:
            final_sha = _git_head_sha(track_dir) or code_sha
        # Deduplicate: if SHA collides with a sibling subtask, force a unique SHA
        if _has_sibling_sha(state, p, t, s, final_sha):
            _git_commit(track_dir, f"chore(conductor): Dedup '{task_name}' [{final_sha}]",
                        allow_empty=True)
            dedup_sha = _git_head_sha(track_dir)
            if dedup_sha and dedup_sha != final_sha:
                final_sha = dedup_sha
                _update_task_sha(track_dir, p, t, s, final_sha)

        # Delete result.json after all commits succeed
        # If commit failed, result.json is preserved for manual recovery
        if committed:
            result_path.unlink(missing_ok=True)
        else:
            print(f"WARNING: result.json preserved due to commit failure", file=sys.stderr)

        # Write git note AFTER conductor commit so it's on the same SHA track-state.json references
        r["commit_sha"] = final_sha
        _write_git_note(track_dir, r, load(track_dir))

        result = dict(status="success", sha=final_sha, parent_completed=parent_completed,
                      deviations=len(r.get("spec_deviation_detail", [])),
                      sync_count=synced, committed=committed)

        # Check if ANY phase needs checkpoint after this completion
        checkpoint_pending = _any_phase_needs_checkpoint(track_dir, load(track_dir))
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
                 sync_count=synced, committed=committed))

    else:
        out(dict(error=f"Unknown status: {status}"))
