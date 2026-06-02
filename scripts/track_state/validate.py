"""State validation and auto-fix operations."""
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .core import load, save
from .helpers import (
    out, now_iso, _is_phase_terminal, _clean_trailing_markers,
    _reset_task,
)
from .constants import TERMINAL_STATUSES, TERMINAL_FOR_PARENT
from .sync import _do_sync_plan
from .git_ops import _find_conductor_shas


def _validate_state_consistency(state, errors, warnings):
    """Check semantic consistency within track-state.json."""

    for pi, phase in enumerate(state.get("phases", [])):
        pname = phase.get("name", f"Phase {pi+1}")
        tasks = phase.get("tasks", [])

        if not tasks:
            errors.append(f"{pname}: phase has no tasks")
            continue

        all_terminal = _is_phase_terminal(phase)
        if all_terminal and phase["status"] not in TERMINAL_FOR_PARENT:
            warnings.append(f"{pname}: all tasks terminal but phase status is '{phase['status']}'")
        if phase["status"] in TERMINAL_FOR_PARENT and not all_terminal:
            warnings.append(f"{pname}: phase terminal but tasks still in progress")

        in_progress = [t.get("name", f"P{pi + 1}.T{ti + 1}")
                       for ti, t in enumerate(tasks) if t["status"] == "in_progress"]
        if len(in_progress) > 1:
            warnings.append(f"{pname}: multiple in_progress tasks ({', '.join(in_progress)})")

        for ti, task in enumerate(tasks):
            tname = task.get("name", f"P{pi + 1}.T{ti + 1}")

            if task["status"] in TERMINAL_STATUSES:
                pending_subs = [s.get("name", f"S{si + 1}")
                                for si, s in enumerate(task.get("subtasks", []))
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

    for pi, phase in enumerate(state.get("phases", [])):
        pname = phase.get("name", f"Phase {pi+1}")
        tasks = phase.get("tasks", [])
        phase_num = pi + 1

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
                tname = tasks[ti].get("name", f"P{pi + 1}.T{ti + 1}")
                errors.append(
                    f"{tname}: plan.md has {plan_sub_count} subtasks, state has {state_sub_count}")

        if _is_phase_terminal(phase) and not has_checkpoint.get(phase_num, False):
            warnings.append(f"{pname}: all tasks complete but no checkpoint in plan.md")


def _parse_plan_structure(plan_path):
    """Parse plan.md into a structure indexed by phase number.

    Returns dict mapping phase_number (int) to phase data. Each phase has
    'tasks' list. Each task has 'name' and 'subtasks' list.
    Names are cleaned (markers/SHAs stripped).
    """
    with open(plan_path) as f:
        lines = f.readlines()

    phases = {}
    current_phase = None
    current_task = None

    for line in lines:
        stripped = line.rstrip("\n")

        pm = re.match(r"^##\s+Phase\s+(\d+)\b", stripped)
        if pm:
            phase_num = int(pm.group(1))
            current_phase = {"tasks": []}
            phases[phase_num] = current_phase
            current_task = None
            continue

        tm = re.match(r"^(\s*)-\s+\[[ x~!>#\-d]\]\s+(.*)", stripped)
        if tm and current_phase is not None:
            indent = tm.group(1)
            rest = tm.group(2).strip()
            # Strip HTML comments before marker cleaning so SHA brackets are exposed
            rest = re.sub(r'<!--.*?-->', '', rest).strip()
            rest = _clean_trailing_markers(rest)
            is_subtask = len(indent) > 0

            if is_subtask:
                if current_task is not None:
                    current_task["subtasks"].append({"name": rest, "status": "pending"})
            else:
                current_task = {"name": rest, "subtasks": []}
                current_phase["tasks"].append(current_task)

    return phases

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
    for pi, state_phase in enumerate(state.get("phases", [])):
        # Match by phase number (1-indexed in plan headings) to handle gaps
        plan_phase = plan_struct.get(pi + 1)
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
                        f"P{pi + 1}.T{ti + 1}.S{si + 1}: added subtask from plan.md as pending")
            else:
                # Task doesn't exist in state — add it
                new_task = {"name": pt["name"], "status": "pending", "subtasks": pt["subtasks"]}
                state_tasks.append(new_task)
                fixes.append(
                    f"P{pi + 1}.T{ti + 1}.{pt['name']}: added task from plan.md as pending "
                    f"({len(pt['subtasks'])} subtasks)")

    return fixes

def _fix_indices(state):
    """Fix out-of-range current_*_index fields. Returns list of fixes."""
    fixes = []
    phases = state.get("phases", [])

    cpi = state.get("current_phase_index", -1)
    if cpi >= len(phases):
        state["current_phase_index"] = len(phases) - 1 if phases else -1
        fixes.append(f"current_phase_index: {cpi} → {state['current_phase_index']} (clamped)")

    cti = state.get("current_task_index", -1)
    fixed_pi = state.get("current_phase_index", -1)
    if fixed_pi >= 0 and fixed_pi < len(phases):
        max_tasks = len(phases[fixed_pi].get("tasks", []))
        if cti >= max_tasks:
            state["current_task_index"] = max_tasks - 1 if max_tasks > 0 else -1
            fixes.append(f"current_task_index: {cti} → {state['current_task_index']} (clamped)")

    csi = state.get("current_subtask_index")
    if csi is not None and fixed_pi >= 0 and fixed_pi < len(phases):
        fixed_ti = state.get("current_task_index", -1)
        if fixed_ti >= 0:
            tasks = phases[fixed_pi].get("tasks", [])
            if fixed_ti < len(tasks):
                subs = tasks[fixed_ti].get("subtasks", [])
                if csi >= len(subs):
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

    for pi, phase in enumerate(state.get("phases", [])):
        for ti, task in enumerate(phase.get("tasks", [])):
            if task.get("status") == "in_progress":
                _reset_task(task)
                fixes.append(
                    f"P{pi + 1}.T{ti + 1}.{task.get('name', '?')}: "
                    f"reset stale in_progress → pending ({age_hours:.0f}h old)")
            for si, sub in enumerate(task.get("subtasks", [])):
                if sub.get("status") == "in_progress":
                    _reset_task(sub)
                    fixes.append(
                        f"P{pi + 1}.T{ti + 1}.S{si + 1}.{sub.get('name', '?')}: "
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
    for pi, phase in enumerate(state.get("phases", [])):
        for ti, task in enumerate(phase.get("tasks", [])):
            # Check both the task itself and its subtasks in one pass
            items = [(task, f"P{pi + 1}.T{ti + 1}")]
            for si, sub in enumerate(task.get("subtasks", [])):
                items.append((sub, f"P{pi + 1}.T{ti + 1}.S{si + 1}"))
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
    Returns list of fixes applied.
    """
    fixes = []
    phases = state.get("phases", [])
    cpi = state.get("current_phase_index", -1)
    cti = state.get("current_task_index", -1)

    if cpi < 0 or cti < 0:
        return fixes

    # Check if current target is actually still active
    if cpi < len(phases):
        tasks = phases[cpi].get("tasks", [])
        if cti < len(tasks):
            task = tasks[cti]
            if task["status"] not in TERMINAL_FOR_PARENT:
                return fixes  # Current task is still active

            # Check subtask index too
            csi = state.get("current_subtask_index")
            if csi is not None and "subtasks" in task:
                if csi < len(task["subtasks"]):
                    if task["subtasks"][csi]["status"] not in TERMINAL_FOR_PARENT:
                        return fixes  # Current subtask is still active

    # Scan forward from current position for next pending task
    for pi in range(len(phases)):
        tasks = phases[pi].get("tasks", [])
        for ti in range(len(tasks)):
            task = tasks[ti]
            if task["status"] == "pending":
                state["current_phase_index"] = pi
                state["current_task_index"] = ti
                state.pop("current_subtask_index", None)
                fixes.append(
                    f"current indices: P{cpi + 1}.T{cti + 1} → P{pi + 1}.T{ti + 1} "
                    f"(advanced past terminal task)")
                return fixes
            # Check for pending subtasks in in_progress parents
            if task["status"] == "in_progress" and "subtasks" in task:
                for si, sub in enumerate(task["subtasks"]):
                    if sub["status"] in ("pending", "in_progress"):
                        state["current_phase_index"] = pi
                        state["current_task_index"] = ti
                        state["current_subtask_index"] = si
                        fixes.append(
                            f"current indices: P{cpi + 1}.T{cti + 1} → P{pi + 1}.T{ti + 1}.S{si + 1} "
                            f"(advanced to active subtask)")
                        return fixes

    # No pending tasks found — clear indices
    if cpi >= 0 or cti >= 0:
        state["current_phase_index"] = -1
        state["current_task_index"] = -1
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

    for pi, phase in enumerate(state.get("phases", [])):
        tasks = phase.get("tasks", [])
        if not tasks:
            continue

        for ti, task in enumerate(tasks):
            if task["status"] in TERMINAL_FOR_PARENT and "subtasks" in task:
                for sub in task["subtasks"]:
                    if sub["status"] not in TERMINAL_FOR_PARENT:
                        old = sub["status"]
                        sub["status"] = task["status"]
                        fixes.append(
                            f"P{pi + 1}.T{ti + 1}.{sub.get('name', '?')}: "
                            f"subtask '{old}' → '{task['status']}' (parent propagation)")

        if _is_phase_terminal(phase) and phase["status"] not in TERMINAL_FOR_PARENT:
            task_statuses = [t["status"] for t in tasks]
            best = Counter(task_statuses).most_common(1)[0][0]
            old = phase["status"]
            phase["status"] = best
            fixes.append(f"Phase {pi+1} '{phase.get('name', '?')}': '{old}' → '{best}' (all tasks terminal)")

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
    cpi = state.get("current_phase_index", -1)
    cti = state.get("current_task_index", -1)
    if cpi < -1 or cpi >= len(state.get("phases", [])):
        errors.append(f"current_phase_index {cpi} out of range")
    if cti < -1:
        errors.append(f"current_task_index {cti} out of range")

    # Phase validation
    for pi, phase in enumerate(state.get("phases", [])):
        pname = phase.get("name", f"Phase {pi+1}")
        if "name" not in phase:
            errors.append(f"Phase {pi + 1}: missing name")
        if "status" not in phase:
            errors.append(f"{pname}: missing status")
        elif phase["status"] not in TASK_STATUSES:
            errors.append(f"{pname}: invalid status '{phase['status']}'")
        if "tasks" not in phase:
            errors.append(f"{pname}: missing tasks array")
            continue

        for ti, task in enumerate(phase["tasks"]):
            tname = task.get("name", f"P{pi + 1}.T{ti + 1}")
            if "name" not in task:
                errors.append(f"Phase {pi + 1} Task {ti + 1}: missing name")
            if "status" not in task:
                errors.append(f"{tname}: missing status")
            elif task["status"] not in TASK_STATUSES:
                errors.append(f"{tname}: invalid status '{task['status']}'")

            for si, sub in enumerate(task.get("subtasks", [])):
                sname = sub.get("name", f"P{pi + 1}.T{ti + 1}.S{si + 1}")
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

