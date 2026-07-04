"""Quality scoring and track lifecycle commands."""
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .core import load, save
from .git_ops import docs_synced_for_track
from .helpers import out, now_iso, conductor_dir, _reset_task, _resolve_conductor_root
from .constants import EXECUTION_MODES
from .handoff import _ensure_handoff_index
from .validate import _parse_plan_structure
from .plan_parse import parse_plan, to_plan_structure


def _checklist_status(track_dir):
    """Return verification status by reading track-state.json directly."""
    state = load(track_dir)
    total = 0
    verified = 0
    unverified = []
    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            total += 1
            key = f"P{pi}.T{ti} {task['name']}"
            if task["status"] == "completed":
                verified += 1
            else:
                unverified.append(key)
            for si, sub in enumerate(task.get("subtasks", []), 1):
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


def _validate_plan_structure(plan):
    """Validate PLAN_STRUCTURE input before building state. Returns list of errors."""
    errors = []
    phases = plan.get("phases")
    if not isinstance(phases, list) or len(phases) == 0:
        errors.append("PLAN_STRUCTURE must have at least 1 phase")
        return errors
    for pi, phase in enumerate(phases, 1):
        if not phase.get("name"):
            errors.append(f"Phase {pi}: missing name")
        tasks = phase.get("tasks")
        if not isinstance(tasks, list) or len(tasks) == 0:
            errors.append(f"Phase {pi} '{phase.get('name', '?')}': must have at least 1 task")
            continue
        for ti, task in enumerate(tasks, 1):
            if not task.get("name"):
                errors.append(f"Phase {pi} Task {ti}: missing name")
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if isinstance(sub, dict) and not sub.get("name"):
                    errors.append(f"Phase {pi} Task {ti} Subtask {si}: missing name")
    return errors


# Transient subagent artifacts that must never be swept into conductor commits.
# result.json is written by task-executor/explorer and deleted by dispatch-finalize
# each cycle; tracking it only churns git history (committed then re-deleted).
# new-track-progress.json is the new-track resume marker (skills/new-track/SKILL.md
# §0.5) — written before track-state.json exists and deleted once the track commits.
# parallel.json + wave-agent.marker are the worktree-wave parallelism runtime
# state (scripts/track_state/wave.py): the sidecar ledger tracks in-flight members
# and the marker short-circuits the SubagentStop hook for wave agents. Both are
# per-run and must never be committed — staging them would churn history and leak
# absolute worktree paths into the repo.
# .wave-drain-processed is the wave-step drain marker (scripts/track_state/wave.py
# cmd_wave_step): records that a drained wave's post-drain decisions (seam-review
# applicability) were made, keyed on (track_id, base_sha). Per-run bookkeeping —
# committing it would leak state across tracks and survive past the wave it marks.
_CONDUCTOR_GITIGNORE = """# Conductor runtime artifacts — transient, never commit.
result.json
.result.tmp.*
new-track-progress.json
parallel.json
wave-agent.marker
.wave-drain-processed
"""


def _ensure_conductor_gitignore(track_path):
    """Write .conductor/.gitignore (idempotent) so transient subagent artifacts
    are never staged by conductor commits. Self-contained per-track — no project
    -root .gitignore dependency."""
    cond = Path(track_path) / ".conductor"
    cond.mkdir(parents=True, exist_ok=True)
    (cond / ".gitignore").write_text(_CONDUCTOR_GITIGNORE)


def _mode_error(mode, allow_none=False):
    """Return an error string if ``mode`` is not a valid execution mode, else None.

    With ``allow_none`` (used by init, where None means "leave unset"), a null
    mode is accepted. Without it (used by set-mode), None is rejected.
    """
    if mode is None:
        if allow_none:
            return None
        return f"Missing execution_mode. Must be one of: {', '.join(EXECUTION_MODES)}."
    if mode not in EXECUTION_MODES:
        return (f"Invalid execution_mode {mode!r}. "
                f"Must be one of: {', '.join(EXECUTION_MODES)}.")
    return None


def _init_core(track_dir, plan, track_id, track_type, description, execution_mode=None,
               force=False):
    """Build track-state.json + index.md + handoff.md from a plan structure dict.

    Returns the result dict without printing. Consumed by cmd_init_from_plan
    (parsed from plan.md).

    ``force`` re-bootstraps an existing track (resets all progress to pending).
    Without it, an existing track-state.json is refused — re-running init on a
    live track would otherwise silently reconstruct state from plan.md and wipe
    every task's status/SHA (V7, core-contract.md).
    """
    errors = _validate_plan_structure(plan)
    if errors:
        return dict(ok=False, errors=errors)

    mode_err = _mode_error(execution_mode, allow_none=True)
    if mode_err:
        return dict(ok=False, errors=[mode_err])

    # schemas/track-state.schema.json:11 requires ^[a-z0-9_]+_\d{8}$ (shortname_YYYYMMDD).
    # "track" is the cli.py default when --track-id is omitted (ad-hoc CLI use); the
    # skills always pass a real id via `derive-name`, so enforce the format there.
    # Checked before mkdir so a bad id never creates a directory.
    if track_id != "track" and not re.match(r"^[a-z0-9_]+_\d{8}$", track_id):
        return dict(ok=False, errors=[
            f"track_id {track_id!r} must match shortname_YYYYMMDD "
            f"(e.g. auth_gateway_20260626). Run: track-state derive-name <shortname>"
        ])

    track_path = Path(track_dir)
    # V7 (core-contract.md): never reconstruct/overwrite EXISTING state from plan.md.
    # The mechanical parse is the sanctioned bootstrap ONLY when no state exists.
    # Re-running init on a live track would silently reset every task to pending
    # (data loss); refuse unless --force explicitly re-bootstraps. Checked before
    # mkdir so a refusal never creates a directory either.
    state_path = track_path / "track-state.json"
    if state_path.exists() and not force:
        return dict(ok=False, errors=[
            f"track-state.json already exists at {state_path}. "
            f"Pass --force to re-bootstrap (this resets all task progress to pending)."
        ])

    track_path.mkdir(parents=True, exist_ok=True)

    # Build track-state.json from the plan structure
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
        "current_phase_index": 1,
        "current_task_index": 1,
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

    # Ensure .conductor/.gitignore so transient subagent artifacts (result.json,
    # .result.tmp.*) aren't swept into conductor commits.
    _ensure_conductor_gitignore(track_path)

    # Cross-validate plan.md vs track-state.json for task/subtask count mismatches
    warnings = []
    plan_path = track_path / "plan.md"
    if plan_path.exists():
        try:
            plan_struct = _parse_plan_structure(plan_path)
            for pi, state_phase in enumerate(phases, 1):
                plan_phase = plan_struct.get(pi)
                if plan_phase is None:
                    warnings.append(f"Phase {pi}: heading missing in plan.md")
                    continue
                state_tasks = state_phase.get("tasks", [])
                plan_tasks = plan_phase["tasks"]
                if len(plan_tasks) != len(state_tasks):
                    warnings.append(
                        f"Phase {pi}: plan.md has {len(plan_tasks)} tasks, "
                        f"state has {len(state_tasks)}")
                for ti in range(min(len(state_tasks), len(plan_tasks))):
                    state_subs = len(state_tasks[ti].get("subtasks", []))
                    plan_subs = len(plan_tasks[ti].get("subtasks", []))
                    if plan_subs != state_subs:
                        warnings.append(
                            f"P{pi}.T{ti + 1}: plan.md has {plan_subs} subtasks, "
                            f"state has {state_subs}")
        except Exception:
            pass

    task_count = sum(len(p.get("tasks", [])) for p in phases)
    result = dict(ok=True, track_id=track_id, phases=len(phases), tasks=task_count)
    if warnings:
        result["warnings"] = warnings
    return result


def cmd_init_from_plan(track_dir, track_id, track_type, description,
                       execution_mode=None, check=False, force=False):
    """Create track-state.json by parsing <track-dir>/plan.md mechanically.

    Validates plan.md syntax first — errors block initialization so a malformed
    plan never produces state. This replaces the error-prone step of having the
    LLM transcribe plan.md into a PLAN_STRUCTURE JSON: every task and subtask is
    extracted deterministically.

    With --check, validate and print the derived structure without writing.
    """
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        out(dict(ok=False, errors=[f"plan.md not found at {plan_path}"]))
        return

    parsed = parse_plan(plan_path)
    plan_warnings = list(parsed["warnings"])

    if parsed["errors"]:
        result = dict(ok=False, errors=parsed["errors"], source=str(plan_path))
        if plan_warnings:
            result["warnings"] = plan_warnings
        out(result)
        return

    structure = to_plan_structure(parsed)
    phase_count = len(structure["phases"])
    task_count = sum(len(p["tasks"]) for p in structure["phases"])

    if check:
        result = dict(ok=True, check=True, source=str(plan_path),
                      phases=phase_count, tasks=task_count,
                      structure=structure)
        if plan_warnings:
            result["warnings"] = plan_warnings
        out(result)
        return

    result = _init_core(track_dir, structure, track_id, track_type,
                        description, execution_mode, force=force)
    # Structure was derived from plan.md itself, so count cross-checks always
    # pass; the only advisory notes are plan-syntax warnings from parse_plan.
    if plan_warnings and result.get("ok"):
        result["plan_warnings"] = plan_warnings
    out(result)


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


def cmd_set_mode(track_dir, mode):
    """Set ``execution_mode`` on an existing track without re-initializing state.

    Lets an in-progress track switch between pausing at phase checkpoints
    (interactive) and auto-proceeding through all phases (continuous).
    """
    mode_err = _mode_error(mode, allow_none=False)
    if mode_err:
        out(dict(ok=False, error=mode_err))
        return

    state = load(track_dir)
    previous = state.get("execution_mode", "interactive")
    state["execution_mode"] = mode
    state["updated_at"] = now_iso()
    save(track_dir, state)
    out(dict(ok=True, execution_mode=mode, previous=previous))


# Statuses that are acceptable end-states for a COMPLETED track (finalize).
# failed/blocked are intentionally excluded — they flip the track to failed/blocked
# via the earlier branches. pending/in_progress mean work remains and finalize
# must refuse false completion rather than declaring the track done. `cancelled`
# IS acceptable: a fully-cancelled track is a legitimate (if void) end-state.
_FINALIZE_OK_STATUSES = ("completed", "skipped", "deferred", "cancelled")


def _finalize_track(track_dir):
    """Compute+save half of ``finalize`` — returns the result dict, no emit.

    Extracted so ``cmd_post_loop_step`` (Rail B-min post-loop spine) can run the
    finalize step inline and route on its outcome (``halt`` on ok:false) in the
    same call. Mirrors ``finalize_dispatch`` / ``cmd_dispatch_finalize``.
    """
    state = load(track_dir)
    state["current_phase_index"] = 0
    state["current_task_index"] = 0
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
    elif all(s in _FINALIZE_OK_STATUSES for s in statuses):
        state["status"] = "completed"
    else:
        # Non-terminal tasks (pending/in_progress) remain — refuse false completion.
        # Keep the track in_progress (schema-valid, marker '~', validate-clean) and
        # surface the unfinished units so the caller can act. No quality_score: an
        # incomplete track has no honest score, and cmd_archive already refuses
        # unless status is 'completed', so archiving is correctly blocked too.
        incomplete = []
        for pi, phase in enumerate(state.get("phases", []), 1):
            for ti, task in enumerate(phase.get("tasks", []), 1):
                if task.get("status") not in _FINALIZE_OK_STATUSES:
                    incomplete.append(f"P{pi}.T{ti} {task.get('name', '?')}: {task.get('status')}")
                for si, sub in enumerate(task.get("subtasks", []), 1):
                    if sub.get("status") not in _FINALIZE_OK_STATUSES:
                        incomplete.append(f"P{pi}.T{ti}.S{si} {sub.get('name', '?')}: {sub.get('status')}")
        state["status"] = "in_progress"
        state["updated_at"] = now_iso()
        save(track_dir, state)
        return dict(ok=False, status="in_progress",
                    reason=f"{len(incomplete)} task(s) still non-terminal",
                    incomplete=incomplete)

    # Feature checklist verification
    checklist = _checklist_status(track_dir)

    # Quality score calculation
    quality_score = _compute_quality_score(track_dir, state, statuses, checklist)

    state["quality_score"] = quality_score
    state["updated_at"] = now_iso()
    save(track_dir, state)

    result = dict(
        ok=True,
        status=state["status"],
        quality_score=quality_score,
    )
    if checklist["exists"]:
        result["checklist"] = dict(
            verified=checklist["verified"],
            total=checklist["total"],
            unverified=checklist["unverified"],
        )
    return result


def cmd_finalize(track_dir):
    """CLI wrapper for :func:`_finalize_track` — emits the result."""
    out(_finalize_track(track_dir))

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

def cmd_archive(track_dir, force=False):
    """Transition a completed track to archived status AND relocate its directory.

    Flips ``status`` to ``archived`` and moves ``tracks/<id>`` → ``archive/<id>``
    (sibling of ``tracks/`` at the conductor root), so an archived track leaves
    the active set rather than merely being relabeled. The result envelope
    carries the NEW ``track_dir`` (and ``archived_dir``) — callers must use it
    for any subsequent ``registry-update``/commit, since the old path is gone.

    Refuses unless a doc-sync commit exists for this track — evidence the
    post-loop DOC SYNC phase ran and durable findings reached the wiki corpus.
    ``force`` skips the check (the result then carries a ``warning``).
    """
    track_path = Path(track_dir)
    state = load(track_dir)
    current = state.get("status", "")
    track_id = state.get("track_id", "") or track_path.name

    # Idempotent re-entry: a prior run already archived + relocated this track
    # (e.g. interrupted after the move but before the commit). Don't re-move or
    # error — just report the relocated path so the caller can finish the commit.
    if current == "archived" and "archive" in {p.name for p in track_path.parents}:
        dest = track_path.resolve()
        out(dict(ok=True, status="archived", track_dir=str(dest), archived_dir=str(dest),
                 note="already archived and relocated"))
        return

    if current != "completed":
        out(dict(ok=False, error=f"Cannot archive track with status '{current}'. Only 'completed' tracks can be archived.",
                 hint="Run track-state finalize first."))
        return

    synced = docs_synced_for_track(track_dir)
    if not synced and not force:
        out(dict(ok=False,
                 error=(f"Cannot archive track '{track_id}': no doc-sync commit found "
                        f"(docs(conductor): ...[{track_id}]). The post-loop DOC SYNC phase "
                        f"has not run, so durable findings have not been graduated into the wiki corpus."),
                 hint="Run the post-loop DOC SYNC phase (templates/post-loop.md §6.0), or pass --force to archive without it."))
        return

    # Resolve archive/<id> at the conductor root (sibling of tracks/). Fall back
    # for a non-standard layout (no tracks.md ancestor): if the track sits in a
    # dir literally named `tracks`, archive beside it; otherwise archive in place.
    root = _resolve_conductor_root(track_dir)
    if root is not None:
        archive_root = root / "archive"
    elif track_path.parent.name == "tracks":
        archive_root = track_path.parent.parent / "archive"
    else:
        archive_root = track_path.parent / "archive"
    dest = archive_root / track_id

    if dest.exists():
        out(dict(ok=False,
                 error=(f"Cannot archive track '{track_id}': destination already exists "
                        f"('{dest}'). Refusing to overwrite an existing archive entry."),
                 hint="Inspect the destination; rename or remove it, then re-run archive."))
        return

    # Save archived state in place FIRST so track-state.json travels with the move.
    state["status"] = "archived"
    state["archived_at"] = now_iso()
    state["updated_at"] = now_iso()
    save(track_dir, state)

    archive_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(track_path), str(dest))

    result = dict(ok=True, status="archived", track_dir=str(dest), archived_dir=str(dest))
    if not synced:
        result["warning"] = ("Archived without a doc-sync commit (--force); "
                             "durable findings may not be synced to the wiki corpus.")
    out(result)

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
    try:
        updated = datetime.fromisoformat(state.get("updated_at", now.isoformat()))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        updated = now
    age_hours = (now - updated).total_seconds() / 3600

    # Count stale tasks for reporting
    stale_tasks = []
    if age_hours > 24:
        for pi, phase in enumerate(state.get("phases", []), 1):
            for ti, task in enumerate(phase.get("tasks", []), 1):
                if task.get("status") == "in_progress":
                    stale_tasks.append(f"P{pi}.T{ti}: {task.get('name', '?')}")
                for si, sub in enumerate(task.get("subtasks", []), 1):
                    if sub.get("status") == "in_progress":
                        stale_tasks.append(f"P{pi}.T{ti}.S{si}: {sub.get('name', '?')}")
        if stale_tasks:
            fixes.append(f"Stale in_progress tasks detected ({age_hours:.0f}h old): {'; '.join(stale_tasks)}")

    out(dict(fixes=fixes, stale_count=len(stale_tasks), age_hours=round(age_hours, 1)))
