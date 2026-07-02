"""Utility functions shared across track-state modules."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path


from .core import load, save
from .constants import (
    MARKER_MAP, SHA_MARKERS, TERMINAL_STATUSES,
    TERMINAL_FOR_PARENT, AUTO_COMPLETE_OK, _RE_TRAILING_MARKER, _RESET_FIELDS,
)


def _resolve_conductor_root(track_dir):
    """Walk up from ``track_dir`` to the conductor root (the dir holding tracks.md).

    Returns the conductor root ``Path``, or ``None`` when no ancestor contains
    ``tracks.md`` — the fail-open signal that tells ``cmd_preflight`` to skip the
    workflow-files check rather than guess a location. Standard track layout is
    ``conductor/tracks/<name>``, so the ancestor two levels up (``conductor/``)
    is the root; walking is robust to nesting depth and to relative paths.
    """
    try:
        p = Path(track_dir).resolve(strict=False)
    except OSError:
        return None
    for cand in (p, *p.parents):
        if (cand / "tracks.md").exists():
            return cand
    return None


def flag(args, name):
    """Parse a --flag value from args list. Supports --flag=val and --flag val."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a[len(name) + 1:]
    return None


def flags_all(args, name):
    """All values for a repeatable --flag, in order (supports --flag val and --flag=val).

    Unlike ``flag`` (first match only), returns every occurrence — for repeatable
    flags such as write-result's ``--deviation``. Empty list if absent.
    """
    vals = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == name and i + 1 < len(args):
            vals.append(args[i + 1])
            i += 2
            continue
        if a.startswith(name + "="):
            vals.append(a[len(name) + 1:])
        i += 1
    return vals


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def _display_loc(pi, ti, si=None):
    """Format 1-based indices as display string: P1.T3 or P1.T3.S2."""
    loc = f"P{int(pi)}.T{int(ti)}"
    if si is not None:
        loc += f".S{int(si)}"
    return loc


# Per-command allowlists for default-compact envelopes. "Compact" is pure
# subtraction: each dispatch-loop command keeps only the fields the orchestrator
# consumes (skills/implement/SKILL.md); --full restores the complete envelope.
# Adding a key to a tuple means the skill now relies on it, so update the
# consumer before, not after, emitting it.
COMPACT_FIELDS = {
    "next": ("phase", "task", "subtask", "name", "execution_mode"),
    "recover": ("status", "phase", "task", "subtask", "name",
                "retry_count", "max_retries", "phase_checkpoint_pending",
                "execution_mode", "fixes_applied"),
    "dispatch-next": ("action", "phase", "task", "subtask", "name",
                      "execution_mode"),
    "dispatch-prepare": ("action", "phase", "task", "subtask", "name",
                         "sha", "commit_msg", "is_resume", "retry_count",
                         "max_retries", "execution_mode"),
    "dispatch-finalize": ("status", "sha", "deviations", "committed",
                          "phase_checkpoint_pending", "retry_count",
                          "phase", "task", "subtask", "summary",
                          "coverage_gate", "tdd_gate", "coverage_pct",
                          "ac_integrity_gate", "ears_gate", "tc_consistency_gate"),
    # Wave parallelism (skills/parallel/SKILL.md). dispatch-wave carries the
    # member list (worktree/branch/worktree_track_dir) so the orchestrator can
    # fan out pinned task-executor agents; `deferred` surfaces the eligible-but-
    # capped members the skill must announce (no-silent-caps); `ineligible`
    # surfaces the per-task reason each pending task was rejected on a
    # no_ready_tasks (no-silent-X: tells the author WHICH gate excluded each
    # candidate — subtasked / non_executor / no_deps_comment / deps_unsatisfied);
    # wave-active refusal reuses `wave`.
    "dispatch-wave": ("action", "phase", "base_sha", "wave", "deferred",
                      "reason", "ineligible"),
    "wave-status": ("active", "phase", "base_sha", "members"),
    # wave-finalize emits the _finalize_task result envelope PLUS its own keys;
    # subset of dispatch-finalize's since the transition is shared.
    "wave-finalize": ("action", "status", "sha", "retry_count", "committed",
                      "phase", "task", "subtask", "summary", "deviations",
                      "member_status", "drained", "phase_checkpoint_pending"),
    "wave-abort": ("action", "aborted", "ok"),
}


def emit(obj, command, compact=True):
    """Emit obj as JSON, filtered to the command's compact allowlist by default.

    Output always stays a single JSON object (the test harness json.loads it),
    so --full falls back to the whole envelope rather than a non-JSON pipe form.
    Error envelopes (carrying an ``error`` key or ``status == "error"``) bypass
    the allowlist — diagnostics must survive compaction, not be stripped to an
    empty object.
    """
    if compact and "error" not in obj and obj.get("status") != "error":
        out({k: obj[k] for k in COMPACT_FIELDS[command] if k in obj})
    else:
        out(obj)


def _index_map(state):
    """Build a compact index→name map for error messages. Uses 1-based display."""
    lines = []
    for pi, ph in enumerate(state.get("phases", []), 1):
        lines.append(f"  Phase {pi}: {ph.get('name', '?')}")
        for ti, tk in enumerate(ph.get("tasks", []), 1):
            status = tk.get("status", "?")
            lines.append(f"    Task {ti}: [{status}] {tk.get('name', '?')}")
            for si, sub in enumerate(tk.get("subtasks", []), 1):
                ss = sub.get("status", "?")
                lines.append(f"      Subtask {si}: [{ss}] {sub.get('name', '?')}")
    return "\n".join(lines)


def target(state, p, t, s=None):
    try:
        task = state["phases"][p - 1]["tasks"][t - 1]
    except IndexError:
        n_phases = len(state.get("phases", []))
        idx_info = _index_map(state)
        if p < 1 or p > n_phases:
            raise IndexError(
                f"Phase index {p} out of range (track has {n_phases} phases). "
                f"Run 'track-state validate --fix' to correct state.\n"
                f"Available indices:\n{idx_info}") from None
        n_tasks = len(state["phases"][p - 1].get("tasks", []))
        raise IndexError(
            f"Task index {t} out of range in phase {p} (has {n_tasks} tasks). "
            f"Run 'track-state validate --fix' to correct state.\n"
            f"Available indices:\n{idx_info}") from None
    if s is not None and "subtasks" in task:
        try:
            return task["subtasks"][s - 1]
        except IndexError:
            n_subs = len(task["subtasks"])
            idx_info = _index_map(state)
            raise IndexError(
                f"Subtask index {s} out of range in P{p}.T{t} "
                f"(has {n_subs} subtasks). "
                f"Run 'track-state validate --fix' to correct state.\n"
                f"Available indices:\n{idx_info}") from None
    return task


def clean(tgt, keep):
    for k in _RESET_FIELDS:
        if k not in keep:
            tgt.pop(k, None)


def _normalize_sha(sha):
    """Normalize a git SHA to 7-char short form for consistent storage."""
    if not sha or not isinstance(sha, str):
        return ""
    sha = sha.strip()
    if not re.match(r"^[0-9a-f]+$", sha):
        return ""
    return sha[:7]


def extract_tags(name):
    """Extract task tags like [Docs], [Config] from name.

    Tags should be at the start or end of the task name, not in the middle.
    HTML comments are stripped before matching to avoid false positives
    from tag-like text inside <!-- ... --> annotations.
    Returns unique tags in the order they appear.
    """
    if not name:
        return []
    # Strip HTML comments to prevent false-positive matches from AC/TC
    # annotations. re.DOTALL so multi-line <!-- ... --> comments are stripped
    # whole — without it a newline inside the comment leaves tag-like text
    # (e.g. [Config]) sitting in the name to false-positive below.
    clean_name = re.sub(r'<!--.*?-->', '', name, flags=re.DOTALL)
    # Use lookahead/lookbehind to avoid consuming whitespace between consecutive tags
    pattern = r'(?<!\S)\[(Explore|Docs|Config|Chore|Manual)\](?!\S)'
    matches = re.findall(pattern, clean_name)
    # Extract tag names and preserve order while removing duplicates
    seen = set()
    result = []
    for tag in matches:
        if tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result


def _inherit_tags(sub_tags, parent_name):
    """Return sub_tags if non-empty, otherwise inherit from parent task name."""
    return sub_tags if sub_tags else extract_tags(parent_name)


def conductor_dir(track_dir):
    d = Path(track_dir) / ".conductor"
    d.mkdir(exist_ok=True)
    return d


def _propagate_to_subtasks(tgt, status, reason_key, reason_value):
    """Propagate status to non-terminal subtasks when parent enters terminal state.

    Preserves 'failed' subtasks — their audit trail (retry_count,
    last_failure_summary) must not be destroyed by parent-level operations.
    """
    if "subtasks" not in tgt:
        return
    for sub in tgt["subtasks"]:
        if sub["status"] not in TERMINAL_FOR_PARENT:
            sub["status"] = status
            sub[reason_key] = reason_value


def _clean_trailing_markers(text):
    """Iteratively remove trailing [sha], [sha1,sha2,...], [N/A], [verified] markers."""
    prev = text
    while True:
        cleaned = _RE_TRAILING_MARKER.sub('', prev)
        if cleaned == prev:
            return prev
        prev = cleaned


def _safe_task_name(state, phase_idx, task_idx):
    """Safely get task name from state, returning '...' on any index error."""
    try:
        if not state or phase_idx < 1 or task_idx < 1:
            return '...'
        phases = state.get('phases', [])
        if phase_idx > len(phases):
            return '...'
        tasks = phases[phase_idx - 1].get('tasks', [])
        if task_idx > len(tasks):
            return '...'
        return tasks[task_idx - 1].get('name', '...')
    except (IndexError, KeyError, TypeError):
        return '...'


def _is_phase_terminal(phase):
    """Check if all tasks and subtasks in a phase are in terminal status."""
    for task in phase.get("tasks", []):
        if task["status"] not in TERMINAL_FOR_PARENT:
            return False
        for sub in task.get("subtasks", []):
            if sub["status"] not in TERMINAL_FOR_PARENT:
                return False
    return True


def _last_subtask_sha(task):
    """Return the commit_sha of the last completed subtask, or empty string."""
    for sub in reversed(task.get("subtasks", [])):
        sha = sub.get("commit_sha", "")
        if sha:
            return sha
    return ""


def _store_evidence(state, track_dir, p, t, s, r):
    """Write evidence from result onto the completed task/subtask node."""
    tgt = target(state, int(p), int(t), int(s) if s is not None else None)
    tgt["evidence"] = {
        "coverage_pct": r.get("coverage_pct"),
        "tc_coverage": r.get("tc_coverage", ""),
        "deviations": len(r.get("spec_deviation_detail", [])),
    }
    save(track_dir, state)


def _extract_tags_for_task(state, phase_str, task_str):
    """Extract tags from task name for gate exemption checks."""
    try:
        pi, ti = int(phase_str), int(task_str)
        task = state["phases"][pi - 1]["tasks"][ti - 1]
        return extract_tags(task["name"])
    except (IndexError, KeyError, ValueError):
        return []



def _tag_exempt_from_coverage(tags):
    """Tags that don't require coverage gate enforcement."""
    return bool(set(tags) & {"Docs", "Config", "Chore", "Manual"})



def _tag_exempt_from_tdd(tags):
    """Tags that don't require TDD gate enforcement."""
    return bool(set(tags) & {"Explore", "Docs", "Config", "Chore", "Manual"})



def _phase_needs_checkpoint(track_dir, state, phase_index):
    """Check if a phase needs a checkpoint (all tasks done, no checkpoint in plan.md).

    Returns phase index if checkpoint is needed, None otherwise."""
    # Skip invalid phase indices
    if phase_index < 1:
        return None

    try:
        phase = state["phases"][phase_index - 1]
    except (IndexError, KeyError):
        return None

    # Check if all tasks in phase are in terminal state
    terminal = TERMINAL_FOR_PARENT
    for task in phase.get("tasks", []):
        if task.get("status") not in terminal:
            return None
        for sub in task.get("subtasks", []):
            if sub.get("status") not in terminal:
                return None

    # Check if checkpoint exists in plan.md
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        return phase_index  # No plan, needs checkpoint

    try:
        content = plan_path.read_text()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return phase_index

    # Check for checkpoint marker: [checkpoint: <sha>]
    pattern = rf"^##\s+Phase\s+{phase_index}\b.*\[checkpoint:\s*[0-9a-f]+\]"
    if re.search(pattern, content, re.MULTILINE):
        return None  # Checkpoint exists

    return phase_index  # Phase done but no checkpoint


def _any_phase_needs_checkpoint(track_dir, state):
    """Check if any phase needs a checkpoint. Returns first phase index that needs one, or None."""
    for pi, _phase in enumerate(state.get("phases", []), 1):
        if _phase_needs_checkpoint(track_dir, state, pi) is not None:
            return pi
    return None


def _reset_task(tgt):
    """Reset a single task/subtask dict to pending, clearing all completion fields."""
    tgt["status"] = "pending"
    for k in _RESET_FIELDS:
        tgt.pop(k, None)
