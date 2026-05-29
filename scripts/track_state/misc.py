"""Miscellaneous track-state commands."""
import json
import re
import sys
from pathlib import Path

from .core import load, save
from .helpers import (
    out, now_iso, target, extract_tags, _reset_task,
    _any_phase_needs_checkpoint, conductor_dir,
)
from .mutations import _do_complete
from .sync import _do_sync_plan
from .git_ops import _git_commit, _git_head_sha, _ensure_note
from .constants import TERMINAL_FOR_PARENT
from .quality import _checklist_status


def cmd_reset(track_dir, scope, p=None, t=None):
    """Reset task(s) to pending, clearing all completion fields.

    Scopes:
      task  — reset a single task (and its subtasks) at phase p, task t
      phase — reset ALL tasks in phase p
      track — reset ALL tasks across ALL phases
    """
    state = load(track_dir)

    if scope == "task":
        if p is None or t is None:
            out(dict(error="task scope requires phase and task index"))
            sys.exit(1)
        pi, ti = int(p), int(t)
        if pi < 0 or ti < 0:
            out(dict(error=f"Negative indices not allowed: phase={pi}, task={ti}"))
            sys.exit(1)
        tgt = target(state, pi, ti)
        _reset_task(tgt)
        for sub in tgt.get("subtasks", []):
            _reset_task(sub)
        # If parent phase was terminal, bring it back to in_progress
        parent_phase = state["phases"][pi]
        if parent_phase.get("status") in TERMINAL_FOR_PARENT:
            parent_phase["status"] = "in_progress"
        state["current_phase_index"] = pi
        state["current_task_index"] = ti
        state.pop("current_subtask_index", None)

    elif scope == "phase":
        if p is None:
            out(dict(error="phase scope requires phase index"))
            sys.exit(1)
        pi = int(p)
        if pi < 0:
            out(dict(error=f"Negative index not allowed: phase={pi}"))
            sys.exit(1)
        phases = state.get("phases", [])
        if pi >= len(phases):
            out(dict(error=f"Phase index {pi} out of range (track has {len(phases)} phases)"))
            sys.exit(1)
        phase = phases[pi]
        for task in phase.get("tasks", []):
            _reset_task(task)
            for sub in task.get("subtasks", []):
                _reset_task(sub)
        phase["status"] = "in_progress"
        state["current_phase_index"] = pi
        state["current_task_index"] = 0
        state.pop("current_subtask_index", None)

    elif scope == "track":
        for phase in state.get("phases", []):
            for task in phase.get("tasks", []):
                _reset_task(task)
                for sub in task.get("subtasks", []):
                    _reset_task(sub)
            phase["status"] = "in_progress"
        state["current_phase_index"] = 0
        state["current_task_index"] = 0
        state.pop("current_subtask_index", None)
        state["status"] = "in_progress"

    else:
        out(dict(error=f"Unknown scope: {scope}. Use task, phase, or track."))
        sys.exit(1)

    state["updated_at"] = now_iso()
    save(track_dir, state)
    _do_sync_plan(track_dir, state)

    out(dict(ok=True, scope=scope, phase=int(p) if p is not None else None,
             task=int(t) if t is not None else None))

def cmd_indices(track_dir):
    """Print phase/task/subtask index mapping for the track."""
    state = load(track_dir)
    phases = state.get("phases", [])
    if not phases:
        out(dict(indices=[]))
        return

    result = []
    for pi, ph in enumerate(phases):
        phase_info = dict(
            index=pi, name=ph.get("name", "?"), status=ph.get("status", "?"),
            tasks=[])
        for ti, tk in enumerate(ph.get("tasks", [])):
            task_info = dict(
                index=ti, name=tk.get("name", "?"), status=tk.get("status", "?"),
                subtasks=[])
            for si, sub in enumerate(tk.get("subtasks", [])):
                task_info["subtasks"].append(dict(
                    index=si, name=sub.get("name", "?"),
                    status=sub.get("status", "?")))
            phase_info["tasks"].append(task_info)
        result.append(phase_info)

    out(dict(indices=result))

def _get_all_shas(state):
    """Extract all commit SHAs from state. Returns list."""
    shas = []
    for phase in state["phases"]:
        for task in phase["tasks"]:
            sha = task.get("commit_sha", "")
            if sha and task["status"] in ("completed", "skipped", "failed", "blocked", "deferred", "cancelled"):
                shas.append(sha)
            for sub in task.get("subtasks", []):
                sha = sub.get("commit_sha", "")
                if sha and sub["status"] in ("completed", "skipped", "failed", "blocked", "deferred", "cancelled"):
                    shas.append(sha)
    return shas


def cmd_shas(track_dir):
    """Extract all commit SHAs from completed tasks. Returns first/last for revision range."""
    state = load(track_dir)
    shas = _get_all_shas(state)
    out(dict(
        shas=shas,
        first=shas[0] if shas else None,
        last=shas[-1] if shas else None,
        count=len(shas),
    ))


def cmd_add_checkpoint(track_dir, p, sha):
    """Add or update checkpoint SHA for a phase in plan.md."""
    plan_path = Path(track_dir) / "plan.md"

    if not plan_path.exists():
        out(dict(error="plan.md not found"))
        return

    # Validate sha format
    if not re.match(r"^[0-9a-f]{7}$", sha):
        out(dict(error="Invalid SHA format: must be 7 hex characters"))
        return

    with open(plan_path) as f:
        lines = f.readlines()

    result = []
    phase_num = int(p) + 1  # Convert to 1-based for heading match
    found = False

    for line in lines:
        stripped = line.rstrip("\n")
        # Match phase heading: ## Phase 1: ... or ## Phase 1
        pm = re.match(rf"^##\s+Phase\s+{phase_num}\b", stripped)
        if pm:
            # Remove existing checkpoint if present
            base = re.sub(r"\s+\[checkpoint:\s*[0-9a-f]+\]$", "", stripped)
            # Add new checkpoint
            updated = f"{base} [checkpoint: {sha}]"
            result.append(updated)
            found = True
        else:
            result.append(stripped)

    if not found:
        out(dict(error=f"Phase {p} heading not found in plan.md"))
        return

    with open(plan_path, "w") as f:
        f.write("\n".join(result))
        if result and not result[-1].endswith("\n"):
            f.write("\n")

    out(dict(ok=True, phase=p, sha=sha))


def cmd_deferred_report(track_dir):
    """List all deferred tasks with their context for final verification."""
    state = load(track_dir)
    deferred = []
    for pi, phase in enumerate(state["phases"]):
        for ti, task in enumerate(phase["tasks"]):
            if task["status"] == "deferred":
                deferred.append(dict(
                    phase=pi, task=ti, subtask=None,
                    name=task["name"], reason=task.get("defer_reason", ""),
                    phase_name=phase["name"],
                ))
            for si, sub in enumerate(task.get("subtasks", [])):
                if sub["status"] == "deferred":
                    deferred.append(dict(
                        phase=pi, task=ti, subtask=si,
                        name=sub["name"], reason=sub.get("defer_reason", ""),
                        phase_name=phase["name"],
                    ))
    out(dict(deferred=deferred, count=len(deferred)))

def cmd_phase_done(track_dir, p):
    state = load(track_dir)
    phase = state["phases"][int(p)]
    terminal = TERMINAL_FOR_PARENT
    total = 0
    done = 0
    for task in phase["tasks"]:
        total += 1
        if task["status"] in terminal:
            done += 1
        for sub in task.get("subtasks", []):
            total += 1
            if sub["status"] in terminal:
                done += 1
    out(dict(complete=done == total, terminal=done, total=total))

def cmd_registry_update(track_dir, tracks_md_path):
    """Update a track's entry in the Tracks Registry (tracks.md) based on track-state.json status.

    Handles two formats:
    1. Section-based: ### TrackID ... - **Status:** value ... - **Path:** [link](dir/)
    2. Checkbox: - [marker] description (path/)
    """
    state = load(track_dir)
    track_dir_path = Path(track_dir).resolve()
    track_status = state.get("status", "new")
    track_id = state.get("track_id", "")

    registry_path = Path(tracks_md_path)
    if not registry_path.exists():
        out(dict(error=f"Tracks registry not found: {tracks_md_path}"))
        return

    content = registry_path.read_text()
    track_dir_name = track_dir_path.name

    status_to_marker = {
        "new": " ",
        "in_progress": "~",
        "completed": "x",
        "archived": "@",
        "blocked": "#",
        "cancelled": "-",
        "deferred": "d",
        "skipped": ">",
        "failed": "!",
    }
    new_marker = status_to_marker.get(track_status, " ")

    lines = content.split("\n")
    updated = False
    in_track_section = False

    for i, line in enumerate(lines):
        # Detect track section start: ### heading containing track dir name or track_id
        if re.match(r"^###\s+", line):
            in_track_section = track_dir_name in line or track_id in line

        # Format 1: Section-based — **Status:** value
        if in_track_section and re.match(r"^\s*-\s+\*\*Status:\*\*\s+", line):
            old_status = re.sub(r"^\s*-\s+\*\*Status:\*\*\s+", "", line).strip()
            if old_status != track_status:
                lines[i] = f"- **Status:** {track_status}"
                updated = True
            continue

        # Format 2: Checkbox — [marker] ... (path/)
        m = re.match(r"^(\s*-\s+\[)([ x~!>#\-d@])(\]\s+.*?\()([^)]*)(\).*)$", line)
        if m:
            prefix, old_marker, mid, link_path, suffix = m.groups()
            if track_dir_name in link_path or str(track_dir_path) in link_path:
                if old_marker != new_marker:
                    lines[i] = f"{prefix}{new_marker}{mid}{link_path}{suffix}"
                    updated = True
                break

    # Also update table row if present: | id | type | status | desc |
    for i, line in enumerate(lines):
        if re.match(r"^\|", line) and (track_id in line or track_dir_name in line):
            parts = [p.strip() for p in line.split("|")]
            # Find the status column (typically 3rd, index 3 after split)
            if len(parts) >= 4:
                new_line = line
                # Replace status in table — status is 3rd data column
                new_line = re.sub(
                    r"\|\s*(new|in_progress|completed|blocked|cancelled|deferred|skipped|failed|archived)\s*\|",
                    f"| {track_status} |",
                    new_line,
                    count=1,
                )
                if new_line != line:
                    lines[i] = new_line
                    updated = True

    if updated:
        registry_path.write_text("\n".join(lines))
        out(dict(updated=True, marker=new_marker, status=track_status))
    else:
        out(dict(updated=False, status=track_status))

def cmd_record_summary(track_dir):
    """Record a compact task summary for context recovery after compaction."""
    summaries_path = conductor_dir(track_dir) / "task-summaries.json"
    # Read from stdin: JSON with {phase, task, sha, status, summary}
    data = json.loads(sys.stdin.read() if not sys.stdin.isatty() else "{}")
    p, t = data.get("phase", "?"), data.get("task", "?")
    sha = data.get("sha", "")
    status = data.get("status", "?")
    summary = data.get("summary", "")

    summaries = {}
    if summaries_path.exists():
        try:
            summaries = json.loads(summaries_path.read_text())
        except (json.JSONDecodeError, ValueError):
            pass

    summaries[f"P{p}.T{t}"] = {"sha": sha, "status": status, "summary": summary}
    summaries_path.write_text(json.dumps(summaries, indent=2))
    out(dict(ok=True, recorded=f"P{p}.T{t}"))
