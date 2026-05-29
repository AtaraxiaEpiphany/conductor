"""Quality scoring and track lifecycle commands."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .core import load, save
from .helpers import out, now_iso, conductor_dir, _reset_task
from .handoff import _ensure_handoff_index


def _checklist_status(track_dir):
    """Return verification status by reading track-state.json directly."""
    state = load(track_dir)
    total = 0
    verified = 0
    unverified = []
    for pi, phase in enumerate(state.get("phases", [])):
        for ti, task in enumerate(phase.get("tasks", [])):
            total += 1
            key = f"P{pi}.T{ti} {task['name']}"
            if task["status"] == "completed":
                verified += 1
            else:
                unverified.append(key)
            for si, sub in enumerate(task.get("subtasks", [])):
                total += 1
                subkey = f"P{pi}.T{ti}.S{si} {sub['name']}"
                if sub["status"] == "completed":
                    verified += 1
                else:
                    unverified.append(subkey)
    return dict(exists=True, total=total, verified=verified, unverified=unverified)


def cmd_checklist_verify(track_dir):
    """Check feature checklist verification status."""
    status = _checklist_status(track_dir)
    out(status)


# ── Init (Track Creation) ────────────────────────────────────────────


def cmd_init(track_dir, plan_structure_json, track_id, track_type, description, execution_mode=None):
    """Create track-state.json and index.md from PLAN_STRUCTURE.
    Returns compact result — eliminates duplicate JSON generation in orchestrator."""
    plan = json.loads(plan_structure_json)
    track_path = Path(track_dir)
    track_path.mkdir(parents=True, exist_ok=True)

    # Build track-state.json from PLAN_STRUCTURE
    phases = []
    for phase in plan.get("phases", []):
        tasks = []
        for task in phase.get("tasks", []):
            entry = {"name": task["name"], "status": "pending"}
            if "subtasks" in task:
                entry["subtasks"] = [
                    {"name": st["name"] if isinstance(st, dict) else st, "status": "pending"}
                    for st in task["subtasks"]
                ]
            tasks.append(entry)
        phases.append({"name": phase["name"], "status": "pending", "tasks": tasks})

    state = {
        "track_id": track_id,
        "type": track_type,
        "status": "new",
        "description": description,
        "current_phase_index": 0,
        "current_task_index": 0,
        "updated_at": now_iso(),
        "phases": phases,
    }
    if execution_mode:
        state["execution_mode"] = execution_mode

    save(str(track_path), state)

    # Create index.md from template
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    template_path = Path(plugin_root) / "templates" / "track-index.md" if plugin_root else None
    index_content = None
    if template_path and template_path.exists():
        index_content = template_path.read_text().replace("{TRACK_ID}", track_id)

    if index_content:
        with open(track_path / "index.md", "w") as f:
            f.write(index_content)
    else:
        # Fallback: minimal index
        with open(track_path / "index.md", "w") as f:
            f.write(f"# Track {track_id} Context\n\n")
            f.write("> Paths are relative to this track directory.\n\n")
            f.write("## Track Files\n")
            f.write("- [Specification](./spec.md)\n")
            f.write("- [Implementation Plan](./plan.md)\n")
            f.write("- [Track State](./track-state.json)\n")
            f.write("- [Handoff Index](./handoff.md) (task handoff logs)\n")

    # Create initial handoff.md
    _ensure_handoff_index(str(track_path), state)

    task_count = sum(len(p.get("tasks", [])) for p in phases)
    out(dict(ok=True, track_id=track_id, phases=len(phases), tasks=task_count))


# ── Handoff Commands ─────────────────────────────────────────────────────


def cmd_start(track_dir):
    """Transition a track from 'new' to 'in_progress'."""
    state = load(track_dir)
    if state.get("status") != "new":
        out(dict(ok=True, status=state.get("status"), message="already started"))
        return

    state["status"] = "in_progress"
    state["updated_at"] = now_iso()
    save(track_dir, state)
    out(dict(ok=True, status="in_progress"))

def cmd_finalize(track_dir):
    state = load(track_dir)
    state["current_phase_index"] = -1
    state["current_task_index"] = -1
    state.pop("current_subtask_index", None)

    statuses = []
    for phase in state["phases"]:
        for task in phase["tasks"]:
            statuses.append(task["status"])
            for sub in task.get("subtasks", []):
                statuses.append(sub["status"])

    if "blocked" in statuses:
        state["status"] = "blocked"
    elif "failed" in statuses:
        state["status"] = "failed"
    elif all(s in ("completed", "skipped", "deferred") for s in statuses):
        state["status"] = "completed"
    else:
        state["status"] = "completed"

    # Feature checklist verification
    checklist = _checklist_status(track_dir)

    # Quality score calculation
    quality_score = _compute_quality_score(track_dir, state, statuses, checklist)

    state["quality_score"] = quality_score
    state["updated_at"] = now_iso()
    save(track_dir, state)

    result = dict(
        status=state["status"],
        quality_score=quality_score,
    )
    if checklist["exists"]:
        result["checklist"] = dict(
            verified=checklist["verified"],
            total=checklist["total"],
            unverified=checklist["unverified"],
        )
    out(result)

def _compute_quality_score(track_dir, state, statuses, checklist):
    """Compute a 0-100 quality score for the track.
    Weights: completion 40%, checklist 30%, coverage 20%, retry penalty 10%."""
    total = len(statuses)
    if total == 0:
        return 100

    # Completion score (40%): ratio of completed tasks
    completed = statuses.count("completed")
    completion_ratio = completed / total

    # Checklist score (30%): ratio of verified items
    if checklist["exists"] and checklist["total"] > 0:
        checklist_ratio = checklist["verified"] / checklist["total"]
    else:
        checklist_ratio = 1.0  # No checklist = assume full

    # Coverage score (20%): from task evidence, fallback to git notes
    coverage_values = []
    for phase in state.get("phases", []):
        for task in phase.get("tasks", []):
            ev = task.get("evidence")
            if ev and ev.get("coverage_pct") is not None:
                coverage_values.append(ev["coverage_pct"])
            for sub in task.get("subtasks", []):
                sev = sub.get("evidence")
                if sev and sev.get("coverage_pct") is not None:
                    coverage_values.append(sev["coverage_pct"])
    if coverage_values:
        coverage_ratio = sum(coverage_values) / len(coverage_values) / 100
    else:
        coverage_ratio = 0.8  # Default assumption when no evidence

    # Retry penalty (10%): penalize high retry counts
    total_retries = 0
    for phase in state.get("phases", []):
        for task in phase.get("tasks", []):
            total_retries += task.get("retry_count", 0)
    retry_penalty = min(total_retries * 0.05, 0.3)  # Cap at 30% penalty

    score = (completion_ratio * 40 +
             checklist_ratio * 30 +
             coverage_ratio * 20 +
             (1.0 - retry_penalty) * 10)
    return round(min(score, 100))

def cmd_archive(track_dir):
    """Transition a completed track to archived status."""
    state = load(track_dir)
    current = state.get("status", "")
    if current != "completed":
        out(dict(ok=False, error=f"Cannot archive track with status '{current}'. Only 'completed' tracks can be archived.",
                 hint="Run track-state finalize first."))
        return

    state["status"] = "archived"
    state["archived_at"] = now_iso()
    state["updated_at"] = now_iso()
    save(track_dir, state)
    out(dict(ok=True, status="archived"))

def cmd_gc(track_dir):
    """Garbage collection: clean orphaned artifacts and detect stale state."""
    track_path = Path(track_dir)
    cond_dir = track_path / ".conductor"
    fixes = []

    # Clean orphaned temp files from interrupted save() / write-result operations
    for pattern in [".track-state.json.tmp*", ".result.tmp.*"]:
        for tmp_file in track_path.glob(pattern):
            try:
                tmp_file.unlink()
                fixes.append(f"Removed orphaned temp file: {tmp_file.name}")
            except OSError:
                pass
    for tmp_file in cond_dir.glob(".result.tmp.*"):
        try:
            tmp_file.unlink()
            fixes.append(f"Removed orphaned temp file: {tmp_file.name}")
        except OSError:
            pass

    # Load state once for all checks below
    try:
        state = load(track_dir)
    except (FileNotFoundError, json.JSONDecodeError):
        state = None

    # Clean orphaned result.json files (left from crashed sessions)
    # Only remove if no task is currently in_progress (i.e., no active processing)
    result_file = cond_dir / "result.json"
    if result_file.exists():
        has_active = False
        if state:
            for phase in state.get("phases", []):
                for task in phase.get("tasks", []):
                    if task.get("status") == "in_progress":
                        has_active = True
                        break
                    for sub in task.get("subtasks", []):
                        if sub.get("status") == "in_progress":
                            has_active = True
                            break
                if has_active:
                    break
        if not has_active:
            result_file.unlink()
            fixes.append("Removed orphaned .conductor/result.json")
        else:
            fixes.append("Skipped .conductor/result.json (active task may be processing it)")

    # Detect stale in_progress tasks (older than 24h)
    if state is None:
        out(dict(fixes=fixes, stale_count=0, age_hours=0))
        return
    now = datetime.now(timezone.utc)
    updated = datetime.fromisoformat(state.get("updated_at", now.isoformat()))
    age_hours = (now - updated).total_seconds() / 3600

    stale_tasks = []
    for pi, phase in enumerate(state.get("phases", [])):
        for ti, task in enumerate(phase.get("tasks", [])):
            if task.get("status") == "in_progress":
                stale_tasks.append(f"P{pi}.T{ti}: {task.get('name', '?')}")
            for si, sub in enumerate(task.get("subtasks", [])):
                if sub.get("status") == "in_progress":
                    stale_tasks.append(f"P{pi}.T{ti}.S{si}: {sub.get('name', '?')}")

    if stale_tasks and age_hours > 24:
        fixes.append(f"Stale in_progress tasks detected ({age_hours:.0f}h old): {'; '.join(stale_tasks)}")

    out(dict(fixes=fixes, stale_count=len(stale_tasks), age_hours=round(age_hours, 1)))
