"""State validation and auto-fix operations."""
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .core import load, save
from .helpers import (
    out, now_iso, _is_phase_terminal,
    _reset_task,
)
from .constants import TERMINAL_STATUSES, TERMINAL_FOR_PARENT
from .plan_parse import parse_plan
from .sync import _do_sync_plan
from .git_ops import _find_conductor_shas


def _validate_state_consistency(state, errors, warnings):
    """Check semantic consistency within track-state.json."""

    for pi, phase in enumerate(state.get("phases", []), 1):
        pname = phase.get("name", f"Phase {pi}")
        tasks = phase.get("tasks", [])

        if not tasks:
            errors.append(f"{pname}: phase has no tasks")
            continue

        all_terminal = _is_phase_terminal(phase)
        if all_terminal and phase["status"] not in TERMINAL_FOR_PARENT:
            warnings.append(f"{pname}: all tasks terminal but phase status is '{phase['status']}'")
        if phase["status"] in TERMINAL_FOR_PARENT and not all_terminal:
            warnings.append(f"{pname}: phase terminal but tasks still in progress")

        in_progress = [t.get("name", f"P{pi}.T{ti}")
                       for ti, t in enumerate(tasks, 1) if t["status"] == "in_progress"]
        if len(in_progress) > 1:
            warnings.append(f"{pname}: multiple in_progress tasks ({', '.join(in_progress)})")

        for ti, task in enumerate(tasks, 1):
            tname = task.get("name", f"P{pi}.T{ti}")

            if task["status"] in TERMINAL_STATUSES:
                pending_subs = [s.get("name", f"S{si}")
                                for si, s in enumerate(task.get("subtasks", []), 1)
                                if s["status"] in ("pending", "in_progress")]
                if pending_subs:
                    warnings.append(
                        f"{tname}: parent '{task['status']}' but subtasks still pending: "
                        f"{', '.join(pending_subs)}")

            if task["status"] == "completed" and not task.get("commit_sha"):
                warnings.append(f"{tname}: completed but no commit_sha (run 'validate --fix' to attempt recovery)")


def _validate_plan_consistency(track_dir, state, errors, warnings):
    """Cross-check plan.md against track-state.json."""
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        warnings.append("plan.md not found — skipping plan consistency checks")
        return

    try:
        plan_struct = _parse_plan_structure(plan_path)
    except Exception:
        warnings.append("plan.md parse failed — skipping plan consistency checks")
        return

    # Check for checkpoint markers via line scan
    has_checkpoint = {}
    with open(plan_path) as f:
        for line in f:
            m = re.match(r"^##\s+Phase\s+(\d+)\b", line.rstrip("\n"))
            if m:
                phase_num = int(m.group(1))
                has_checkpoint[phase_num] = bool(re.search(r"\[checkpoint:\s*[0-9a-f]{7}\]", line))

    for pi, phase in enumerate(state.get("phases", []), 1):
        pname = phase.get("name", f"Phase {pi}")
        tasks = phase.get("tasks", [])
        phase_num = pi

        plan_phase = plan_struct.get(phase_num)
        if plan_phase is None:
            warnings.append(f"{pname}: phase heading missing in plan.md")
            continue

        plan_tasks = plan_phase["tasks"]
        state_tasks = len(tasks)
        plan_task_count = len(plan_tasks)
        if plan_task_count != state_tasks:
            errors.append(f"{pname}: plan.md has {plan_task_count} tasks, state has {state_tasks}")

        # Per-task subtask count validation (not just aggregate)
        for ti in range(min(state_tasks, plan_task_count)):
            state_sub_count = len(tasks[ti].get("subtasks", []))
            plan_sub_count = len(plan_tasks[ti].get("subtasks", []))
            if plan_sub_count != state_sub_count:
                tname = tasks[ti].get("name", f"P{pi}.T{ti + 1}")
                errors.append(
                    f"{tname}: plan.md has {plan_sub_count} subtasks, state has {state_sub_count}")

        if _is_phase_terminal(phase) and not has_checkpoint.get(phase_num, False):
            warnings.append(f"{pname}: all tasks complete but no checkpoint in plan.md")


def _parse_plan_structure(plan_path):
    """Parse plan.md into a structure indexed by phase number.

    Returns ``{phase_number: {"tasks": [{"name", "subtasks": [{"name","status"}]}]}}``.
    Thin reshape over :func:`plan_parse.parse_plan` (the single plan.md parser)
    so the phase/task regexes and name-cleaning live in one place rather than
    drifting again. plan_parse produces subtasks as cleaned name strings; this
    view re-keys by phase number and tags each subtask ``status:"pending"`` —
    the shape the plan-vs-state consistency checks and the plan-absorb fixer
    consume. parse_plan's diagnostics are intentionally ignored here; this view
    is structural only.
    """
    parsed = parse_plan(plan_path)
    out = {}
    for ph in parsed["phases"]:
        tasks = []
        for t in ph["tasks"]:
            subtasks = [{"name": s, "status": "pending"} for s in t["subtasks"]]
            tasks.append({"name": t["name"], "subtasks": subtasks})
        out[ph["number"]] = {"tasks": tasks}
    return out

def _fix_plan_mismatches(track_dir, state, errors=None):
    """Absorb plan.md entries missing from state. Returns list of fixes.

    Always attempts reconciliation regardless of validate errors — the function
    is idempotent (only adds, never removes) so running without errors is safe.
    """
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        return []

    try:
        plan_struct = _parse_plan_structure(plan_path)
    except Exception:
        return []

    fixes = []
    for pi, state_phase in enumerate(state.get("phases", []), 1):
        # Match by phase number (1-indexed in plan headings) to handle gaps
        plan_phase = plan_struct.get(pi)
        if plan_phase is None:
            continue
        state_tasks = state_phase.setdefault("tasks", [])

        # Absorb missing tasks (plan has more than state)
        for ti, pt in enumerate(plan_phase["tasks"]):
            if ti < len(state_tasks):
                # Task exists — check for missing subtasks
                # setdefault ensures the list is attached to the task dict
                state_subs = state_tasks[ti].setdefault("subtasks", [])
                plan_subs = pt.get("subtasks", [])
                for si in range(len(state_subs), len(plan_subs)):
                    sub_name = plan_subs[si]["name"]
                    state_subs.append({"name": sub_name, "status": "pending"})
                    fixes.append(
                        f"P{pi}.T{ti + 1}.S{si + 1}: added subtask from plan.md as pending")
            else:
                # Task doesn't exist in state — add it
                new_task = {"name": pt["name"], "status": "pending", "subtasks": pt["subtasks"]}
                state_tasks.append(new_task)
                fixes.append(
                    f"P{pi}.T{ti + 1}.{pt['name']}: added task from plan.md as pending "
                    f"({len(pt['subtasks'])} subtasks)")

    return fixes

def _fix_indices(state):
    """Fix out-of-range current_*_index fields. Returns list of fixes."""
    fixes = []
    phases = state.get("phases", [])

    cpi = state.get("current_phase_index", 0)
    if cpi > len(phases):
        state["current_phase_index"] = len(phases) if phases else 0
        fixes.append(f"current_phase_index: {cpi} → {state['current_phase_index']} (clamped)")

    cti = state.get("current_task_index", 0)
    fixed_pi = state.get("current_phase_index", 0)
    if fixed_pi >= 1 and fixed_pi <= len(phases):
        max_tasks = len(phases[fixed_pi - 1].get("tasks", []))
        if cti > max_tasks:
            state["current_task_index"] = max_tasks if max_tasks > 0 else 0
            fixes.append(f"current_task_index: {cti} → {state['current_task_index']} (clamped)")

    csi = state.get("current_subtask_index")
    if csi is not None and fixed_pi >= 1 and fixed_pi <= len(phases):
        fixed_ti = state.get("current_task_index", 0)
        if fixed_ti >= 1:
            tasks = phases[fixed_pi - 1].get("tasks", [])
            if fixed_ti <= len(tasks):
                subs = tasks[fixed_ti - 1].get("subtasks", [])
                if csi > len(subs):
                    state.pop("current_subtask_index", None)
                    fixes.append(f"current_subtask_index: {csi} → removed (out of range)")

    if "updated_at" not in state:
        state["updated_at"] = now_iso()
        fixes.append("updated_at: set to current time (was missing)")

    return fixes


def _fix_stale_in_progress(state, threshold_hours=24):
    """Reset in_progress tasks that haven't been updated within threshold.

    Handles agent crashes that leave tasks stuck as in_progress indefinitely.
    Returns list of fixes applied.
    """
    fixes = []
    try:
        state_updated = datetime.fromisoformat(state.get("updated_at", ""))
    except (ValueError, TypeError):
        return fixes

    now = datetime.now(timezone.utc)
    if state_updated.tzinfo is None:
        state_updated = state_updated.replace(tzinfo=timezone.utc)
    age_hours = (now - state_updated).total_seconds() / 3600

    if age_hours < threshold_hours:
        return fixes

    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            if task.get("status") == "in_progress":
                _reset_task(task)
                fixes.append(
                    f"P{pi}.T{ti}.{task.get('name', '?')}: "
                    f"reset stale in_progress → pending ({age_hours:.0f}h old)")
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if sub.get("status") == "in_progress":
                    _reset_task(sub)
                    fixes.append(
                        f"P{pi}.T{ti}.S{si}.{sub.get('name', '?')}: "
                        f"reset stale in_progress → pending ({age_hours:.0f}h old)")

    return fixes


def _fix_missing_shas(state, track_dir):
    """Recover missing commit SHAs for completed tasks.

    Fetches all conductor completion commits from git log in a single call,
    then matches task names. Returns list of fixes applied.
    """
    sha_map = _find_conductor_shas(track_dir)
    if not sha_map:
        return []

    fixes = []
    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            # Check both the task itself and its subtasks in one pass
            items = [(task, f"P{pi}.T{ti}")]
            for si, sub in enumerate(task.get("subtasks", []), 1):
                items.append((sub, f"P{pi}.T{ti}.S{si}"))
            for item, label in items:
                if item.get("status") == "completed" and not item.get("commit_sha"):
                    sha = sha_map.get(item.get("name", ""))
                    if sha:
                        item["commit_sha"] = sha
                        fixes.append(f"{label}: recovered SHA {sha}")
    return fixes


def _fix_terminal_current_indices(state):
    """Advance current_*_index past terminal tasks to the next pending task.

    If current indices point to a completed/failed/skipped task, scan forward
    through phases and tasks to find the next dispatchable target.

    Also handles legacy 0-based stored indices: when current_phase_index is 0
    but pending tasks exist, scans from the beginning to migrate to 1-based.
    Returns list of fixes applied.
    """
    fixes = []
    phases = state.get("phases", [])
    cpi = state.get("current_phase_index", 0)
    cti = state.get("current_task_index", 0)

    # Check if the current target is still active (only when both >= 1). The
    # most-specific index present IS the active target: when a subtask index
    # is set it points at the subtask even if the parent is in_progress, so a
    # terminal subtask under an active parent must still be advanced —
    # otherwise cmd_recover feeds a stale/completed subtask target.
    if cpi >= 1 and cti >= 1 and cpi <= len(phases):
        tasks = phases[cpi - 1].get("tasks", [])
        if cti <= len(tasks):
            task = tasks[cti - 1]
            csi = state.get("current_subtask_index")
            if csi is not None and "subtasks" in task and csi <= len(task["subtasks"]):
                # Subtask is the active target — return only if IT is active.
                if task["subtasks"][csi - 1]["status"] not in TERMINAL_FOR_PARENT:
                    return fixes  # Current subtask is still active
                # Subtask is terminal → fall through to the scan so the stale
                # current_subtask_index advances to the next pending subtask.
            elif task["status"] not in TERMINAL_FOR_PARENT:
                return fixes  # Current task is still active (no subtask index)
            # else: task is terminal → fall through to the scan

    # Build display label for old indices (0=sentinel → show "N/A")
    _old = (f"P{cpi}.T{cti}" if cpi >= 1 else "N/A")

    # Scan forward from the beginning for next pending task.
    # Handles both: terminal current task AND legacy cpi=0 sentinel.
    for pi in range(1, len(phases) + 1):
        tasks = phases[pi - 1].get("tasks", [])
        for ti in range(1, len(tasks) + 1):
            task = tasks[ti - 1]
            if task["status"] == "pending":
                state["current_phase_index"] = pi
                state["current_task_index"] = ti
                state.pop("current_subtask_index", None)
                fixes.append(
                    f"current indices: {_old} → P{pi}.T{ti} "
                    f"(migrated to 1-based)" if cpi < 1 else
                    f"current indices: {_old} → P{pi}.T{ti} "
                    f"(advanced past terminal task)")
                return fixes
            # Check for pending subtasks in in_progress parents
            if task["status"] == "in_progress" and "subtasks" in task:
                for si, sub in enumerate(task["subtasks"], 1):
                    if sub["status"] in ("pending", "in_progress"):
                        state["current_phase_index"] = pi
                        state["current_task_index"] = ti
                        state["current_subtask_index"] = si
                        fixes.append(
                            f"current indices: {_old} → P{pi}.T{ti}.S{si} "
                            f"(migrated to 1-based)" if cpi < 1 else
                            f"current indices: {_old} → P{pi}.T{ti}.S{si} "
                            f"(advanced to active subtask)")
                        return fixes

    # No pending tasks found — clear indices
    if cpi >= 1 or cti >= 1:
        state["current_phase_index"] = 0
        state["current_task_index"] = 0
        state.pop("current_subtask_index", None)
        fixes.append("current indices: cleared (no pending tasks remain)")

    return fixes


def _auto_fix(state, track_dir=None, errors=None, stale_threshold_hours=24):
    """Auto-fix repairable inconsistencies. Returns list of fixes applied."""
    fixes = []
    if errors is None:
        errors = []

    # Fix out-of-range indices
    fixes.extend(_fix_indices(state))

    # Absorb plan.md entries missing from state
    if track_dir:
        plan_fixes = _fix_plan_mismatches(track_dir, state, errors)
        fixes.extend(plan_fixes)

    # Reset stale in_progress tasks
    fixes.extend(_fix_stale_in_progress(state, threshold_hours=stale_threshold_hours))

    for pi, phase in enumerate(state.get("phases", []), 1):
        tasks = phase.get("tasks", [])
        if not tasks:
            continue

        for ti, task in enumerate(tasks, 1):
            if task["status"] in TERMINAL_FOR_PARENT and "subtasks" in task:
                for sub in task["subtasks"]:
                    if sub["status"] not in TERMINAL_FOR_PARENT:
                        old = sub["status"]
                        sub["status"] = task["status"]
                        fixes.append(
                            f"P{pi}.T{ti}.{sub.get('name', '?')}: "
                            f"subtask '{old}' → '{task['status']}' (parent propagation)")

        if _is_phase_terminal(phase) and phase["status"] not in TERMINAL_FOR_PARENT:
            task_statuses = [t["status"] for t in tasks]
            best = Counter(task_statuses).most_common(1)[0][0]
            old = phase["status"]
            phase["status"] = best
            fixes.append(f"Phase {pi} '{phase.get('name', '?')}': '{old}' → '{best}' (all tasks terminal)")

    # Advance indices past terminal tasks (after all other fixes)
    fixes.extend(_fix_terminal_current_indices(state))

    # Recover missing SHAs for completed tasks (searches git log)
    if track_dir:
        fixes.extend(_fix_missing_shas(state, track_dir))

    if fixes:
        state["updated_at"] = now_iso()

    return fixes

def _run_all_checks(track_dir, state, errors, warnings):
    """Run the full validation suite: structural, enum, index, phase/task, semantic, plan."""
    TASK_STATUSES = {"pending", "in_progress", "completed", "failed", "skipped",
                     "deferred", "blocked", "cancelled"}

    # Enum checks
    if state["type"] not in ("feature", "bugfix", "chore", "docs"):
        errors.append(
            f"Invalid type: '{state['type']}'. Valid types: feature, bugfix, chore, docs. "
            f"Run track-state init to recreate the track."
        )
    if state["status"] not in ("new", "in_progress", "completed", "archived", "blocked", "cancelled"):
        errors.append(
            f"Invalid track status: '{state['status']}'. Valid statuses: new, in_progress, completed, archived, blocked, cancelled. "
            f"Run track-state start/finalize/archive to transition."
        )

    # Index checks
    cpi = state.get("current_phase_index", 0)
    cti = state.get("current_task_index", 0)
    if cpi < 0 or cpi > len(state.get("phases", [])):
        errors.append(f"current_phase_index {cpi} out of range")
    if cti < 0:
        errors.append(f"current_task_index {cti} out of range")

    # Legacy 0-based index detection: both indices 0 but pending tasks exist
    if cpi == 0 and cti == 0:
        has_pending = any(
            t["status"] == "pending"
            for p in state.get("phases", [])
            for t in p.get("tasks", [])
        )
        if has_pending:
            warnings.append(
                "current_phase_index and current_task_index are both 0 but pending "
                "tasks exist — likely legacy 0-based data "
                "(run 'validate --fix' to migrate to 1-based)")

    # Phase validation
    for pi, phase in enumerate(state.get("phases", []), 1):
        pname = phase.get("name", f"Phase {pi}")
        if "name" not in phase:
            errors.append(f"Phase {pi}: missing name")
        if "status" not in phase:
            errors.append(f"{pname}: missing status")
        elif phase["status"] not in TASK_STATUSES:
            errors.append(f"{pname}: invalid status '{phase['status']}'")
        if "tasks" not in phase:
            errors.append(f"{pname}: missing tasks array")
            continue

        for ti, task in enumerate(phase["tasks"], 1):
            tname = task.get("name", f"P{pi}.T{ti}")
            if "name" not in task:
                errors.append(f"Phase {pi} Task {ti}: missing name")
            if "status" not in task:
                errors.append(f"{tname}: missing status")
            elif task["status"] not in TASK_STATUSES:
                errors.append(f"{tname}: invalid status '{task['status']}'")

            for si, sub in enumerate(task.get("subtasks", []), 1):
                sname = sub.get("name", f"P{pi}.T{ti}.S{si}")
                if "status" not in sub:
                    errors.append(f"{sname}: missing status")
                elif sub["status"] not in TASK_STATUSES:
                    errors.append(f"{sname}: invalid status '{sub['status']}'")

            # Validate optional fields
            sha = task.get("commit_sha")
            if sha is not None and sha != "" and (not isinstance(sha, str) or len(sha) > 7):
                errors.append(f"{tname}: commit_sha must be empty or 1-7 hex chars")

    # Semantic consistency checks
    _validate_state_consistency(state, errors, warnings)
    _validate_plan_consistency(track_dir, state, errors, warnings)


def cmd_validate(track_dir, fix=False):
    """Validate track-state.json and plan.md against structural and semantic rules.

    Always runs auto-fix analysis and reports what WOULD be fixed.
    With --fix, persists fixes to disk and re-validates.
    """
    errors = []
    warnings = []
    try:
        state = load(track_dir)
    except Exception as e:
        out(dict(valid=False, errors=[f"Cannot read track-state.json: {e}"], warnings=[], fixes=[]))
        return

    # Top-level required fields
    for field in ["track_id", "type", "status", "description", "current_phase_index",
                   "current_task_index", "updated_at", "phases"]:
        if field not in state:
            errors.append(f"Missing required field: {field}")

    if errors:
        out(dict(valid=False, errors=errors, warnings=[], fixes=[]))
        return

    _run_all_checks(track_dir, state, errors, warnings)

    # Always run auto-fix analysis (dry-run)
    fixes = _auto_fix(state, track_dir=track_dir, errors=errors)

    if fix and fixes:
        # Persist fixes
        save(track_dir, state)
        _do_sync_plan(track_dir, state)

        # Re-validate after fixes
        new_errors = []
        new_warnings = []
        try:
            fixed_state = load(track_dir)
        except Exception:
            fixed_state = state
        _run_all_checks(track_dir, fixed_state, new_errors, new_warnings)
        out(dict(
            valid=len(new_errors) == 0,
            errors=new_errors if new_errors != errors else errors,
            warnings=new_warnings if new_warnings != warnings else warnings,
            fixes=fixes,
            fixed=True,
        ))
        return

    out(dict(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        fixes=fixes,
        fixable=len(fixes) > 0,
    ))


def ensure_healthy(track_dir):
    """Load, validate, auto-fix, save, and re-validate.

    Returns (state, fixes, errors) after applying all repairs.
    Used by dispatch/recover to guarantee a clean state before operating.
    """
    errors = []
    warnings = []
    try:
        state = load(track_dir)
    except Exception as e:
        return None, [], [f"Cannot read track-state.json: {e}"]

    # Check required fields — can't auto-fix these
    for field in ["track_id", "type", "status", "phases"]:
        if field not in state:
            errors.append(f"Missing required field: {field}")
    if errors:
        return state, [], errors

    _run_all_checks(track_dir, state, errors, warnings)
    fixes = _auto_fix(state, track_dir=track_dir, errors=errors)

    if fixes:
        save(track_dir, state)
        _do_sync_plan(track_dir, state)
        # Reload after save to get the persisted version
        try:
            state = load(track_dir)
        except Exception:
            pass

    return state, fixes, errors

