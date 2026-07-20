"""Handoff file management for cross-session continuity."""
import json
import re
import sys
from pathlib import Path

from .core import load
from .helpers import conductor_dir, now_iso, out, _safe_task_name, _display_loc
from .constants import MAX_RETRIES, task_max_retries


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
            retry_info = (f" ({retry_count}/{task_max_retries(task)})"
                          if retry_count > 0 else "")

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
    max_retries = result_data.get("max_retries", MAX_RETRIES)
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

def _extract_subtask_section(content, subtask):
    """Return the ``## Subtask {n}`` slice of *content*, or ``''`` if absent.

    Shared by ``cmd_get_handoff`` (CLI) and the SubagentStart retry-context
    probe — both need "just this subtask's history" without re-deriving the
    slice. Captures from the subtask heading until the next ``## `` section
    header (mirrors the original inline scan in cmd_get_handoff).
    """
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
    return "\n".join(result)


def get_handoff_content(track_dir, phase, task, subtask=None):
    """Return the handoff text for a task, or one subtask's slice.

    Programmatic counterpart to ``cmd_get_handoff`` — used by the SubagentStart
    retry-context probe so it can read the prior-attempt record without forking
    the CLI (which prints JSON to stdout). Returns ``None`` when no handoff file
    exists; when *subtask* is given, returns just that subtask's ``## Subtask N:``
    section (possibly ``''`` if the subtask has no recorded section yet).
    """
    handoff_file = _get_handoff_file(track_dir, phase, task)
    if not handoff_file.exists():
        return None
    content = handoff_file.read_text()
    if subtask is not None:
        content = _extract_subtask_section(content, subtask)
    return content


def cmd_get_handoff(track_dir, phase, task, subtask=None):
    """Get handoff content for a specific task/subtask.
    Returns the relevant section only to minimize context."""
    handoff_file = _get_handoff_file(track_dir, phase, task)

    if not handoff_file.exists():
        out(dict(error="Handoff file not found", path=str(handoff_file)))
        return

    content = get_handoff_content(track_dir, phase, task, subtask)

    # subtask slice may be '' when that subtask has no section yet
    if subtask is not None and not content:
        out(dict(error=f"Subtask {int(subtask)} not found in handoff"))
        return

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
        consulted_docs = content_data.get("consulted_docs", [])

        # Completeness gate (agents/explorer.md §4.2): the Exploration Notes are
        # the downstream task-executor's Layer-0 map. Reject a sparse map so the
        # explorer is retried with the rejection as context rather than shipping a
        # useless "looks fine" handoff. Exit non-zero so the explorer's Bash call
        # fails and its on-failure→FAILURE contract triggers a retry.
        missing = []
        if len(str(content_data.get("summary", "")).strip()) < 20:
            missing.append("summary (>= 20 chars)")
        if not findings:
            missing.append("findings (>= 1)")
        if not files_inventory:
            missing.append("files_inventory (>= 1)")
        if missing:
            sys.stderr.write(
                "explore handoff rejected — sparse map, missing: "
                + "; ".join(missing) + "\n"
            )
            out(dict(error="sparse_explore_handoff", missing=missing))
            sys.exit(1)

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

### Corpus Consulted (Layer-0 provenance — docs this exploration extends)
{chr(10).join(f'- {cd.get("path", cd) if isinstance(cd, dict) else cd}: {cd.get("relevance", "") if isinstance(cd, dict) else ""}' for cd in consulted_docs) if consulted_docs else '_None recorded — corpus not consulted (verify greenfield/novel area; otherwise a contract violation)_'}

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

### Graduation Candidates (durable → corpus; for corpus-writer harvest)
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


# ── Graduation Harvest (durable findings → wiki corpus) ──────────────

# Heading rendered by cmd_append_handoff's explore block (handoff.py:502).
_GRAD_HEADING = re.compile(r"^###\s+Graduation Candidates\b")
# `## Technical Decision: {title} | {ts}` rendered by the decision block.
_DECISION_HEADING = re.compile(r"^##\s+Technical Decision:\s*(.+?)\s*(?:\|[^|]*)?$")
_DECISION_FIELD = re.compile(r"^\*\*(Options|Chosen|Reasoning|Tradeoffs)\*\*:\s*(.*)$")


def _extract_candidates(handoff_dir):
    """Parse durable findings from every ``P*T*.md`` handoff file in *handoff_dir*.

    Returns ``{"graduation": [...], "decisions": [...]}``:
    - graduation: ``{"text", "source"}`` per non-``_None_`` bullet under any
      ``### Graduation Candidates`` section (de-duplicated by text; multiple
      sections per file — e.g. one per subtask — are all collected).
    - decisions: ``{"title", "chosen", "reasoning", "source"}`` per
      ``## Technical Decision:`` block (``--type decision`` entries).

    ``source`` is the handoff stem (``P1T2``). Read-only; never creates the dir.
    """
    graduation, decisions = [], []
    if not handoff_dir.is_dir():
        return {"graduation": graduation, "decisions": decisions}

    seen = set()  # de-dup identical candidate text across handoffs
    for hf in sorted(handoff_dir.glob("P*T*.md")):
        source = hf.stem
        try:
            lines = hf.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        # Graduation candidates (possibly multiple sections per file).
        for idx, line in enumerate(lines):
            if not _GRAD_HEADING.match(line):
                continue
            for l in lines[idx + 1:]:
                if l.startswith("#"):
                    break
                s = l.strip()
                if not s:
                    continue
                if s == "_None_":
                    break
                if s.startswith("- "):
                    cand = s[2:].strip()
                    if cand and cand not in seen:
                        seen.add(cand)
                        graduation.append({"text": cand, "source": source})
                else:
                    break  # non-bullet content ends the candidate list

        # Technical decisions.
        for idx, line in enumerate(lines):
            m = _DECISION_HEADING.match(line)
            if not m:
                continue
            entry = {"title": m.group(1).strip(), "chosen": "",
                     "reasoning": "", "source": source}
            for l in lines[idx + 1:]:
                if l.startswith("## "):
                    break
                fm = _DECISION_FIELD.match(l)
                if fm and fm.group(1) in ("Chosen", "Reasoning"):
                    entry[{"Chosen": "chosen", "Reasoning": "reasoning"}[fm.group(1)]] = fm.group(2).strip()
            decisions.append(entry)

    return {"graduation": graduation, "decisions": decisions}


def cmd_harvest_candidates(track_dir):
    """Extract durable findings from this track's handoffs for corpus-writer to
    graduate into the wiki corpus (``conductor/design/`` + ``conductor/resource/``).

    Reads ONLY the sanctioned ``.conductor/handoff/`` channel — not
    ``.conductor/notes/`` (off-contract; flagged separately by check_misplaced_docs).
    corpus-writer calls this in Phase 1 (agents/corpus-writer.md §3.1b) to load the
    harvest queue alongside spec.md. Output JSON:
    ``{"graduation": [...], "decisions": [...], "count": N}``.
    """
    handoff_dir = Path(track_dir) / ".conductor" / "handoff"
    result = _extract_candidates(handoff_dir)
    result["count"] = len(result["graduation"]) + len(result["decisions"])
    out(result)


# ── Track Findings (durable findings compiled for later phases) ──────

def _track_findings_path(track_dir):
    """Path to the compiled track-findings doc (track-scoped scratch)."""
    return conductor_dir(track_dir) / "track-findings.md"


def _render_track_findings(track_dir, state, harvested):
    """Render the track-findings markdown from a harvested candidate set.

    Pure function of its inputs — rewritten from scratch at every compile,
    so stale entries never accumulate and no merge logic is needed. Returns
    the markdown text (or ``None`` when the harvest is empty and nothing
    has been compiled yet, signalling the caller may skip the write).
    """
    graduation = harvested.get("graduation", [])
    decisions = harvested.get("decisions", [])
    description = state.get("description", "") if state else ""
    track_id = state.get("track_id", "track") if state else "track"
    title = description.strip() or track_id

    lines = [
        f"# Track Findings — {title}",
        "",
        ("Compiled at each phase checkpoint from explorer handoffs. Prior art "
         "for later phases — read *before* re-exploring (verify against code, "
         "don't rediscover). Track-scoped; superseded by the project corpus at "
         "archive."),
        "",
        f"_Last compiled: {now_iso()}_",
        "",
    ]

    if not graduation and not decisions:
        lines.append("_No durable findings recorded yet._")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Durable Findings")
    lines.append("")
    if graduation:
        for g in graduation:
            lines.append(f"- {g['text']} _— source: {g.get('source', '?')}_")
    else:
        lines.append("_None._")
    lines.append("")
    lines.append("## Technical Decisions")
    lines.append("")
    if decisions:
        for dec in decisions:
            lines.append(f"### {dec.get('title', '(untitled)')} "
                         f"_— source: {dec.get('source', '?')}_")
            if dec.get("chosen"):
                lines.append(f"- **Chosen**: {dec['chosen']}")
            if dec.get("reasoning"):
                lines.append(f"- **Reasoning**: {dec['reasoning']}")
            lines.append("")
    else:
        lines.append("_None._")
        lines.append("")
    return "\n".join(lines)


def compile_track_findings(track_dir):
    """Compile durable findings from every handoff into a track-scoped doc.

    The cross-phase durability bridge: ``get-handoff`` is keyed per-task, so a
    later phase's task-executor/explorer cannot read an earlier phase's
    exploration. This compiles the existing harvest (``_extract_candidates`` —
    deduped across all ``P*T*.md`` handoffs) into
    ``{TRACK_DIR}/.conductor/track-findings.md``, which later phases read before
    re-exploring. Idempotent: rewritten from scratch every call.

    Returns a dict (``path``, ``graduation_count``, ``decisions_count``,
    ``compiled`` bool). Does not emit — the CLI wrapper and the F5 hook decide
    what to do with the result.
    """
    state = load(track_dir)
    handoff_dir = Path(track_dir) / ".conductor" / "handoff"
    harvested = _extract_candidates(handoff_dir)
    md = _render_track_findings(track_dir, state, harvested)
    path = _track_findings_path(track_dir)
    compiled = False
    if md is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md, encoding="utf-8")
        compiled = True
    return {
        "path": str(path),
        "graduation_count": len(harvested.get("graduation", [])),
        "decisions_count": len(harvested.get("decisions", [])),
        "compiled": compiled,
    }


def cmd_compile_track_findings(track_dir):
    """CLI wrapper — compile ``.conductor/track-findings.md`` from handoffs.

    Runs automatically at every PASSED phase checkpoint
    (``cmd_phase_checkpoint_review``); exposed as a command so it can be invoked
    and tested in isolation. The compile is advisory (fail-open at the F5 hook):
    a findings-compile error never blocks a phase advance.
    """
    result = compile_track_findings(track_dir)
    result["ok"] = True
    out(result)


# ── Dispatch Composite Commands ──────────────────────────────────────

