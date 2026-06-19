"""Handoff file management for cross-session continuity."""
import json
from pathlib import Path

from .core import load
from .helpers import conductor_dir, now_iso, out, _safe_task_name, _display_loc
from .constants import MAX_RETRIES


def _get_handoff_dir(track_dir):
    """Get or create .conductor/handoff directory."""
    handoff_dir = conductor_dir(track_dir) / "handoff"
    handoff_dir.mkdir(exist_ok=True)
    return handoff_dir

def _get_handoff_file(track_dir, phase, task):
    """Get handoff file path for a specific task (1-based display names)."""
    try:
        p1, t1 = int(phase), int(task)
    except (ValueError, TypeError):
        p1, t1 = phase, task
    return _get_handoff_dir(track_dir) / f"P{p1}T{t1}.md"

def _ensure_handoff_index(track_dir, state=None):
    """Ensure handoff.md index exists. Create if missing."""
    handoff_path = Path(track_dir) / "handoff.md"
    if handoff_path.exists():
        return handoff_path.read_text()

    if state is None:
            try:
                state = load(track_dir)
            except (FileNotFoundError, json.JSONDecodeError):
                state = None

    track_id = state.get("track_id", "unknown") if state else "unknown"
    description = state.get("description", "") if state else ""

    content = f"""# Handoff: {track_id}

**Track ID**: {track_id}
**Description**: {description}
**Status**: Initializing
**Updated**: {now_iso()}

---

## Execution Summary

| Metric | Value |
|--------|-------|
| Completed | 0/0 tasks |
| Failed | 0 tasks |
| Skipped | 0 tasks |
| Blocked | 0 tasks |

### Current Focus
Initializing...

### Risk Radar
No risks recorded.

---

## Phase Index

*Phases will be indexed as tasks progress.*

---

## Risks & Coordination

No high-priority risks or coordination needs.

---

## Technical Decisions

No decisions recorded yet.

---

## Deviation Report

No deviations recorded.
"""
    handoff_path.write_text(content)
    return content

def _sync_handoff_index(track_dir, state=None):
    """Sync handoff.md index with current state."""
    if state is None:
        state = load(track_dir)

    handoff_path = Path(track_dir) / "handoff.md"
    handoff_dir = _get_handoff_dir(track_dir)

    # Gather statistics
    total_tasks = 0
    completed_tasks = 0
    failed_tasks = 0
    skipped_tasks = 0
    blocked_tasks = 0

    phase_sections = []

    for pi, phase in enumerate(state.get("phases", []), 1):
        phase_name = phase.get("name", f"Phase {pi}")
        phase_status = phase.get("status", "pending")

        # Determine phase emoji
        if phase_status == "completed":
            phase_emoji = "✅"
        elif phase_status == "in_progress":
            phase_emoji = "🔄"
        elif phase_status == "blocked":
            phase_emoji = "🚫"
        else:
            phase_emoji = "⏸️"

        task_rows = []
        for ti, task in enumerate(phase.get("tasks", []), 1):
            total_tasks += 1
            task_status = task.get("status", "pending")
            task_name = task.get("name", f"Task {ti}")

            if task_status == "completed":
                completed_tasks += 1
                task_emoji = "✅"
            elif task_status == "failed":
                failed_tasks += 1
                task_emoji = "❌"
            elif task_status == "skipped":
                skipped_tasks += 1
                task_emoji = "⏭️"
            elif task_status == "blocked":
                blocked_tasks += 1
                task_emoji = "🚫"
            elif task_status == "in_progress":
                task_emoji = "🔄"
            else:
                task_emoji = "[ ]"

            retry_count = task.get("retry_count", 0)
            retry_info = f" ({retry_count}/{MAX_RETRIES})" if retry_count > 0 else ""

            task_rows.append(
                f"| {ti}. | {task_emoji} {task_name}{retry_info} | "
                f"[{_display_loc(pi, ti)}](.conductor/handoff/P{pi}T{ti}.md) |"
            )

            # Count subtasks
            for si, sub in enumerate(task.get("subtasks", []), 1):
                total_tasks += 1
                sub_status = sub.get("status", "pending")
                sub_name = sub.get("name", f"Subtask {si}")

                if sub_status == "completed":
                    completed_tasks += 1
                elif sub_status == "failed":
                    failed_tasks += 1
                elif sub_status == "skipped":
                    skipped_tasks += 1
                elif sub_status == "blocked":
                    blocked_tasks += 1

        if task_rows:
            table_header = "| # | Task | Details |\n|---|------|---------|"
            phase_sections.append(
                f"### Phase {pi}: {phase_name} {phase_emoji}\n\n"
                f"{table_header}\n" +
                "\n".join(task_rows)
            )

    # Build updated index
    track_id = state.get("track_id", "unknown")
    description = state.get("description", "")
    current_phase = state.get("current_phase_index", 0)
    current_task = state.get("current_task_index", 0)

    current_focus = f"Phase {current_phase}, Task {current_task}" if current_phase >= 1 and current_task >= 1 else "Initializing"

    content = f"""# Handoff: {track_id}

**Track ID**: {track_id}
**Description**: {description}
**Status**: Phase {current_phase}/{len(state.get('phases', []))} | {total_tasks - completed_tasks} tasks remaining
**Updated**: {now_iso()}

---

## Execution Summary

| Metric | Value |
|--------|-------|
| Completed | {completed_tasks}/{total_tasks} tasks |
| Failed | {failed_tasks} tasks |
| Skipped | {skipped_tasks} tasks |
| Blocked | {blocked_tasks} tasks |

### Current Focus
**Phase {current_phase}**: Next task
**Next**: {_safe_task_name(state, current_phase, current_task)}

### Risk Radar
"""

    # Add risk summary based on failed/blocked tasks
    if failed_tasks > 0 or blocked_tasks > 0:
        content += f"- 🔴 **High**: {failed_tasks + blocked_tasks} tasks with issues\n"
    if skipped_tasks > 0:
        content += f"- 🟡 **Medium**: {skipped_tasks} tasks skipped\n"

    content += "\n---\n\n## Phase Index\n\n"
    content += "\n\n".join(phase_sections) if phase_sections else "*No tasks started yet.*\n"

    content += "\n\n---\n\n## Risks & Coordination\n\n*See individual task handoff files for details.*\n"

    content += "\n\n---\n\n## Technical Decisions\n\n*See .conductor/handoff/decisions.md for details.*\n"

    content += "\n\n---\n\n## Deviation Report\n\n*See individual task handoff files for deviations.*\n"

    handoff_path.write_text(content)

def _write_task_handoff(track_dir, phase, task, content, state=None):
    """Write content to a task's handoff file. Creates/updates as needed."""
    handoff_file = _get_handoff_file(track_dir, phase, task)

    if state is None:
        state = load(track_dir)

    # Get task context
    try:
        task_obj = state["phases"][int(phase) - 1]["tasks"][int(task) - 1]
        task_name = task_obj.get("name", f"Task {int(task)}")
        phase_name = state["phases"][int(phase) - 1].get("name", f"Phase {int(phase)}")
    except (IndexError, KeyError):
        task_name = f"Task {int(task)}"
        phase_name = f"Phase {int(phase)}"

    # If file doesn't exist, create header
    if not handoff_file.exists():
        header = f"""# Phase {int(phase)} Task {int(task)}: {task_name}

**Phase**: {phase_name}
**Status**: pending
**Type**: implementation
**AC Coverage**: TBD

---

## Subtasks

*Subtask sections will be added as the task progresses.*

---

## Execution History

*Execution records will be added as the task progresses.*

---

## Exploration Notes

*Exploration notes will be added if an [Explore] task runs first.*

---

## Dependencies & Risks

*Dependencies and risks will be recorded as discovered.*

"""
        handoff_file.write_text(header + "\n" + content + "\n")
    else:
        # File exists, append or update
        existing = handoff_file.read_text()
        # Append content with separator
        handoff_file.write_text(existing + "\n" + content + "\n")

    _sync_handoff_index(track_dir, state)
    return str(handoff_file)

def _append_execution_record(track_dir, phase, task, subtask, result_data, state=None):
    """Append an execution record (success or failure) to task handoff."""
    if state is None:
        state = load(track_dir)

    task_name = result_data.get("task_name", "unknown")
    attempt = result_data.get("attempt", 1)
    max_retries = result_data.get("max_retries", 3)
    ts = now_iso()

    status = result_data.get("status", "").upper()

    # Build execution record
    if status == "FAILURE":
        detail = result_data.get("failure_detail", {})
        record = f"""
### Attempt {attempt}/{max_retries} | {ts} ❌

**What Was Done**: {detail.get('what_was_done', 'N/A')}
**Failure Reason**: {detail.get('failure_reason', 'N/A')}
**Suggested Next Step**: {detail.get('suggested_next_step', 'N/A')}
"""
    elif status == "SUCCESS":
        sha = result_data.get("commit_sha", "")
        files_changed = result_data.get("files_changed", "")
        tc_coverage = result_data.get("tc_coverage", "")
        record = f"""
### Attempt {attempt}/{max_retries} | {ts} ✅

**Commit**: {sha}
**Files Changed**: {files_changed}
**TC Coverage**: {tc_coverage}
**Summary**: {result_data.get('summary', 'Success')}
"""
    else:
        record = f"""
### Attempt {attempt}/{max_retries} | {ts} ❓

**Status**: {status}
**Summary**: {result_data.get('summary', 'N/A')}
"""

    # Determine section to write to
    if subtask is not None:
        section_header = f"\n## Subtask {int(subtask)}: {task_name}\n\n{record}\n"
    else:
        section_header = f"\n## Execution Record\n\n{record}\n"

    _write_task_handoff(track_dir, phase, task, section_header, state)

def _append_failure_legacy(track_dir, result_data):
    """Legacy: Append a failure entry to issues.md for backward compatibility."""
    issues_path = Path(track_dir) / "issues.md"
    ts = now_iso()
    task_name = result_data.get("task_name", "unknown")
    attempt = result_data.get("attempt", 1)
    max_retries = result_data.get("max_retries", 3)
    detail = result_data.get("failure_detail", {})

    header = ""
    if not issues_path.exists():
        track_id = result_data.get("track_id", "unknown")
        header = f"# Track: {track_id} — Failure Reports\n\n"

    entry = (
        f"### Task: {task_name} | Attempt: {attempt}/{max_retries} | {ts}\n"
        f"**What Was Done**: {detail.get('what_was_done', 'N/A')}\n"
        f"**Failure Reason**: {detail.get('failure_reason', 'N/A')}\n"
        f"**Suggested Next Step**: {detail.get('suggested_next_step', 'N/A')}\n"
        "---\n\n"
    )

    with open(issues_path, "a") as f:
        f.write(header + entry)

def _append_deviation_legacy(track_dir, task_name, dev):
    """Legacy: Append a spec deviation entry to issues.md for backward compatibility."""
    issues_path = Path(track_dir) / "issues.md"
    ts = now_iso()

    header = ""
    if not issues_path.exists():
        track_id = "unknown"
        # Try to get track_id from state
        try:
            state = load(track_dir)
            track_id = state.get("track_id", "unknown")
        except (FileNotFoundError, json.JSONDecodeError):
            track_id = "unknown"
        header = f"# Track: {track_id} — Failure Reports\n\n"

    entry = (
        f"### Spec Deviation: {task_name} | {ts}\n"
        f"**AC**: {dev.get('ac_id', 'N/A')} | "
        f"**Reason**: {dev.get('reason', 'N/A')} | "
        f"**Revision**: {dev.get('suggested_revision', 'N/A')} | "
        f"**Status**: pending-review\n"
        "---\n\n"
    )

    with open(issues_path, "a") as f:
        f.write(header + entry)

def cmd_get_handoff(track_dir, phase, task, subtask=None):
    """Get handoff content for a specific task/subtask.
    Returns the relevant section only to minimize context."""
    handoff_file = _get_handoff_file(track_dir, phase, task)

    if not handoff_file.exists():
        out(dict(error="Handoff file not found", path=str(handoff_file)))
        return

    content = handoff_file.read_text()

    # If subtask specified, extract only that section
    if subtask is not None:
        sub_1based = int(subtask)
        lines = content.split("\n")
        result = []
        capturing = False
        for line in lines:
            if line.strip().startswith(f"## Subtask {sub_1based}:") or \
               line.strip().startswith(f"### Subtask {sub_1based}:"):
                capturing = True
            if capturing:
                result.append(line)
                # Stop at next section
                if line.startswith("## ") and not \
                   (line.startswith(f"## Subtask {sub_1based}:") or \
                    line.startswith(f"### Subtask {sub_1based}:")):
                    result.pop()  # Remove the next section header
                    break

        if not result:
            out(dict(error=f"Subtask {sub_1based} not found in handoff"))
            return

        content = "\n".join(result)

    out(dict(content=content, path=str(handoff_file)))


def cmd_sync_handoff(track_dir):
    """Sync handoff.md index with current state."""
    state = load(track_dir)
    _sync_handoff_index(track_dir, state)
    out(dict(ok=True, updated=True))


def cmd_append_handoff(track_dir, phase, task, entry_type, content_json, subtask=None):
    """Append content to a task's handoff file.
    Types: explore, success, failure, skip, decision, risk, deviation"""
    try:
        content_data = json.loads(content_json) if content_json != "{}" else {}
    except json.JSONDecodeError:
        out(dict(error="Invalid JSON in --content"))
        return

    ts = now_iso()
    state = load(track_dir)

    # Get task context
    try:
        task_obj = state["phases"][int(phase) - 1]["tasks"][int(task) - 1]
        task_name = task_obj.get("name", f"Task {int(task)}")
    except (IndexError, KeyError):
        task_name = f"Task {int(task)}"

    # Build entry based on type
    if entry_type == "explore":
        findings = content_data.get("findings", [])
        architecture = content_data.get("architecture", "")
        gotchas = content_data.get("gotchas", [])
        recommended = content_data.get("recommended", "")
        files_inventory = content_data.get("files_inventory", [])
        out_of_scope = content_data.get("out_of_scope", [])
        graduation = content_data.get("graduation_candidates", [])

        # Files Inventory table (preserves the explorer's schema; Related Docs
        # links into the conductor/design + conductor/resource corpus).
        if files_inventory:
            inv = ["| Path | Purpose | Key Exports | Related Docs |",
                   "|------|---------|-------------|--------------|"]
            for fi in files_inventory:
                if isinstance(fi, dict):
                    inv.append(f"| {fi.get('path', '')} | {fi.get('purpose', '')} | "
                               f"{fi.get('key_exports', '')} | {fi.get('related_docs', '')} |")
                else:
                    inv.append(f"| {fi} | | | |")
            inventory_md = "\n".join(inv)
        else:
            inventory_md = "_None_"

        entry = f"""
## Exploration Notes | {ts}

### Summary
{content_data.get('summary', '...')}

### Key Findings
{chr(10).join(f'- {f}' for f in findings) if findings else '- None'}

### Architecture
{architecture}

### Gotchas & Constraints
{chr(10).join(f'- {g}' for g in gotchas) if gotchas else '- None'}

### Files Inventory
{inventory_md}

### Recommended Approach
{recommended}

### Out-of-Scope Notes
{chr(10).join(f'- {o}' for o in out_of_scope) if out_of_scope else '_None_'}

### Graduation Candidates (durable → corpus; for doc-syncer harvest)
{chr(10).join(f'- {g}' for g in graduation) if graduation else '_None_'}
"""

    elif entry_type == "decision":
        title = content_data.get("title", "Technical Decision")
        options = content_data.get("options", "")
        chosen = content_data.get("chosen", "")
        reasoning = content_data.get("reasoning", "")
        tradeoffs = content_data.get("tradeoffs", "")

        entry = f"""
## Technical Decision: {title} | {ts}

**Options**: {options}
**Chosen**: {chosen}
**Reasoning**: {reasoning}
**Tradeoffs**: {tradeoffs}
"""

    elif entry_type == "risk":
        risk = content_data.get("risk", "")
        impact = content_data.get("impact", "")
        mitigation = content_data.get("mitigation", "")

        entry = f"""
## Risk Note | {ts}

**Risk**: {risk}
**Impact**: {impact}
**Mitigation**: {mitigation}
"""

    elif entry_type == "deviation":
        ac = content_data.get("ac_id", "N/A")
        reason = content_data.get("reason", "")
        suggested = content_data.get("suggested_revision", "")

        entry = f"""
## Spec Deviation | {ts}

**AC**: {ac}
**Reason**: {reason}
**Suggested Revision**: {suggested}
**Status**: pending-review
"""

    else:
        entry = f"\n## Note | {ts}\n\n{content_data.get('text', '')}\n"

    # Add subtask header if needed
    if subtask is not None:
        section = f"\n## Subtask {int(subtask)}: {task_name}\n\n{entry}\n"
    else:
        section = entry

    _write_task_handoff(track_dir, phase, task, section, state)

    out(dict(ok=True, type=entry_type, handoff_file=str(_get_handoff_file(track_dir, phase, task))))


# ── Dispatch Composite Commands ──────────────────────────────────────

