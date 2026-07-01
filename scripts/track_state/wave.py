"""Worktree wave parallelism — opt-in within-track parallel execution.

The conductor's serial spine runs exactly one ``in_progress`` task at a time
under F1 (Global State Lock). This module adds an **opt-in** parallel mode: the
orchestrator computes a *ready-set* of file-disjoint top-level tasks whose
declared ``<!-- deps: -->`` are all terminal, fans out N ``task-executor``
subagents concurrently — each in its own ``git worktree`` so no two ever share a
git index, ``track-state.json``, or ``result.json`` — then serially integrates
each member's commit back via squash-merge and runs the existing finalize
transition. Serial execution stays the default; F1 is relaxed to a *wave lock*
only while a sidecar ledger (``.conductor/parallel.json``) records in-flight
members.

Lifecycle (orchestrator-driven):

    dispatch-wave  → create N worktrees + lock members + write ledger;
                     emit the wave so the orchestrator fans out N agents
                     (one per member, each pinned to its worktree).
    wave-finalize  → per returned member: squash-merge its branch, run the
                     SUCCESS/FAILURE transition, tear down its worktree.
    wave-status    → read-only ledger snapshot (active? which members pending).
    wave-abort     → recovery: reset in-flight members to pending, tear down
                     their worktrees, delete the ledger.

Design notes live in ``conductor/design/decision-serial-execution.md`` (the
serial-default record, extended by the wave escape hatch) and the plan at
``.claude/plans/majestic-popping-feather.md``.
"""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from .core import load, transaction
from .helpers import emit, now_iso, conductor_dir, target, extract_tags, clean
from .constants import TERMINAL_FOR_PARENT
from .mutations import _lock_inplace
from .dispatch import _classify_task, _finalize_task
from .plan_parse import parse_plan, collect_deps
from .git_ops import (
    _git_rev_parse_toplevel, _git_head_sha, _git_branch_tip,
    _git_range_commit_count, _git_merge_squash, _git_worktree_add,
    _git_worktree_remove, _git_branch_delete,
)
from lib.atomic_io import atomic_write_json


# Cap the fan-out. Conservative default: four concurrent worktree-isolated
# agents is enough headroom for a phase of independent tasks without flooding
# the context budget or the worktree object store.
DEFAULT_WAVE_SIZE = 4

WAVE_LEDGER_NAME = "parallel.json"      # sidecar under .conductor/
WAVE_MARKER_NAME = "wave-agent.marker"  # per-worktree SubagentStop short-circuit

# Member statuses within the ledger.
MEMBER_IN_FLIGHT = "in_flight"
_TERMINAL_MEMBER = {"finalized", "failed", "conflict"}

# A dependency is "satisfied" (so a dependent task may run in parallel) only
# when it succeeded or was benignly bypassed. ``blocked``/``cancelled``/``failed``
# deps do NOT release the dependent — those mean the prerequisite did not
# happen, so the dependent must wait (or never run). More conservative than
# TERMINAL_STATUSES on purpose.
_DEP_SATISFIED = {"completed", "skipped", "deferred"}


# --- ledger I/O -------------------------------------------------------------

def _wave_ledger_path(track_dir):
    """Path to the sidecar wave ledger (``.conductor/parallel.json``)."""
    return conductor_dir(track_dir) / WAVE_LEDGER_NAME


def _load_ledger(track_dir):
    """Read the wave ledger, or ``None`` when absent/corrupt.

    Corrupt (unparseable) ledgers are treated as absent so a half-written file
    from a crashed session can't permanently wedge dispatch-wave — the worst
    case is re-running the wave from scratch, which re-locks already-pending
    tasks. ``_wave_ledger_path`` creates ``.conductor/`` if needed.
    """
    path = _wave_ledger_path(track_dir)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_ledger(track_dir, ledger):
    """Atomically write the wave ledger."""
    atomic_write_json(_wave_ledger_path(track_dir), ledger)


def _is_active(ledger):
    """A ledger is *active* (blocks the serial spine + new waves) iff it has
    at least one in-flight member. A fully drained ledger is inert —
    ``cmd_dispatch_wave`` recycles it."""
    return bool(ledger) and any(
        m.get("status") == MEMBER_IN_FLIGHT for m in ledger.get("wave", []))


def active_wave_member_locs(track_dir):
    """Set of ``(phase, task)`` currently locked by an active wave ledger.

    The F1-guard consumer for the serial spine's backstops: ``validate`` (stale-
    lock reaper, multi-in_progress warning), ``lint-track-state`` (F1 rule), and
    ``dispatch`` (mutual-exclusion refusal) all call this to exempt wave members
    — a wave's authority to hold several in_progress tasks comes from its ledger,
    not F1, so those members must not be reaped, flagged, or refused as if they
    were serial-spine F1 violations. Returns an empty set when there is no ledger
    or the ledger is drained (no in-flight members).
    """
    ledger = _load_ledger(track_dir)
    if not _is_active(ledger):
        return set()
    return {(m.get("phase"), m.get("task"))
            for m in ledger.get("wave", []) if m.get("status") == MEMBER_IN_FLIGHT}


def _member_by_loc(ledger, p, t):
    """Find the ledger member at ``(phase, task)``; ``None`` if not a member."""
    for m in ledger.get("wave", []):
        if m.get("phase") == p and m.get("task") == t:
            return m
    return None


# --- ready-set --------------------------------------------------------------

def _current_phase(state):
    """Earliest phase that still has dispatchable work, or 0 if all terminal.

    Mirrors the serial spine's notion of "where dispatch would pick next": the
    first phase with any non-terminal-for-parent task. Waves run one phase at a
    time (v1 scope), so this is the phase the ready-set is drawn from.
    """
    for pi, phase in enumerate(state.get("phases", []), 1):
        if any(t.get("status") not in TERMINAL_FOR_PARENT
               for t in phase.get("tasks", [])):
            return pi
    return 0


def _dep_satisfied(state, p, t):
    """True iff task ``(p, t)`` is in a deps-satisfied status. Dangling dep
    targets (no such task) are conservatively NOT satisfied."""
    try:
        tgt = state["phases"][p - 1]["tasks"][t - 1]
    except (IndexError, KeyError):
        return False
    return tgt.get("status") in _DEP_SATISFIED


def _ready_set(state, parsed, phase):
    """File-disjoint, deps-resolved pending tasks in ``phase`` eligible for a wave.

    A top-level task qualifies when ALL of:
      - **pending** (not in_progress / terminal);
      - **flat** (no subtasks — subtasks are sequentially decomposed, never
        parallel candidates; v1);
      - routed to the **executor** (not ``[Manual]``/``[Explore]`` — those have
        no parallelizable body);
      - **opt-in via a ``<!-- deps: -->`` comment** — the author's declaration
        that this task's file-surface is exactly its declared dependencies. A
        task with no deps comment is assumed serial-order-dependent and kept on
        the serial spine (conservative: only declared-independent tasks
        parallelize);
      - **every declared dep target is satisfied** (completed/skipped/deferred).

    Capped at ``DEFAULT_WAVE_SIZE``. Returns a list of ``{phase, task, name}``.
    """
    if phase < 1 or phase > len(state.get("phases", [])):
        return []

    # Per-task declared deps + opt-in flag, drawn from plan.md (the source of
    # truth for deps annotations). deps_of covers ALL phases — a dep may target
    # an earlier phase's task.
    deps_of = {}
    has_comment = {}
    for pi, ph in enumerate(parsed.get("phases", []), 1):
        for ti, tk in enumerate(ph.get("tasks", []), 1):
            has_comment[(pi, ti)] = bool(tk.get("deps_has_comment"))
    for src, tgt in collect_deps(parsed):
        deps_of.setdefault(src, []).append(tgt)

    ready = []
    for ti, task in enumerate(state["phases"][phase - 1]["tasks"], 1):
        if task.get("status") != "pending":
            continue
        if task.get("subtasks"):  # flat top-level only (v1)
            continue
        if _classify_task(extract_tags(task.get("name", ""))) != "executor":
            continue
        if not has_comment.get((phase, ti)):  # opt-in gate
            continue
        deps = deps_of.get((phase, ti), [])
        if not all(_dep_satisfied(state, dp, dt) for dp, dt in deps):
            continue
        ready.append({"phase": phase, "task": ti, "name": task.get("name", "")})
        if len(ready) >= DEFAULT_WAVE_SIZE:
            break
    return ready


# --- worktree plumbing ------------------------------------------------------

def _branch_slug(track_dir):
    """Git-branch-safe identifier for the track (used in wave branch names).

    ``conductor/wave/<slug>/P{p}.T{t}`` must be a valid ref. The track_dir name
    is the canonical short id; chars outside ``[A-Za-z0-9._-]`` are replaced so
    a shortname with ``/`` or spaces can't break the ref.
    """
    import re
    raw = Path(track_dir).name or "track"
    return re.sub(r"[^A-Za-z0-9._-]", "-", raw)


def _wt_track_dir(worktree_path, track_dir, repo_root):
    """The track_dir path *inside* a worktree.

    A worktree is a full checkout rooted at ``worktree_path``; the track lives
    at the same repo-relative location as in the main worktree. Resolved on
    absolute paths so symlinks/``.`` segments don't skew the relpath.
    """
    rel = os.path.relpath(str(Path(track_dir).resolve()),
                          str(Path(repo_root).resolve()))
    return str(Path(worktree_path) / rel)


def _write_marker(wt_track_dir, member, branch):
    """Drop the SubagentStop short-circuit marker in a member's worktree.

    The on-subagent-stop hook keys recovery off the singleton cursor, which a
    wave never sets — without this marker a stopping wave agent would be forced
    into a spurious recovery turn. The marker's presence tells the hook "this
    is a wave agent; wave-finalize owns result synthesis, do not bound it."
    Content is advisory (for debugging); the hook checks existence only.
    """
    payload = {"phase": member["phase"], "task": member["task"],
               "branch": branch, "track_id": member.get("track_id", "")}
    marker = conductor_dir(wt_track_dir) / WAVE_MARKER_NAME
    marker.write_text(json.dumps(payload))


def _teardown_member(repo_root, member):
    """Remove a member's worktree + branch (best-effort, idempotent)."""
    _git_worktree_remove(repo_root, member.get("worktree", ""))
    _git_branch_delete(repo_root, member.get("branch", ""))


def _cleanup_wave_root(ledger):
    """Remove the wave's temp worktree root once its members are torn down.

    Members live at ``<wave_root>/P{p}.T{t}``; after teardown the root is empty.
    Best-effort — a missing/non-empty root (stranded files) is left for the OS
    temp cleaner rather than force-removed mid-recovery.
    """
    root = ledger.get("wave_root") if ledger else None
    if root:
        shutil.rmtree(root, ignore_errors=True)


def _read_worktree_result(result_path):
    """Parse a worktree's ``result.json``; ``None`` if missing/unparseable."""
    if not result_path.exists():
        return None
    try:
        with open(result_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _failure_result(member, summary, tip=None):
    """Minimal FAILURE result envelope for synthesis paths."""
    return {"status": "FAILURE", "commit_sha": tip or "N/A",
            "summary": summary, "phase": member["phase"],
            "task": member["task"], "subtask": None,
            "task_name": member.get("name", "unknown")}


# --- commands ---------------------------------------------------------------

def cmd_dispatch_wave(track_dir, compact=True):
    """Compute the ready-set, fan out worktree-isolated members, write the ledger.

    Emits ``action``:
      - ``dispatch_wave``  — ready-set formed; ``wave`` lists members (with
        ``worktree``/``branch``/``worktree_track_dir`` for the orchestrator to
        dispatch N pinned task-executor agents).
      - ``wave_active``    — a non-terminal ledger already exists; refuse.
      - ``no_ready_tasks`` — phase has no eligible deps-declared file-disjoint
        pending tasks (orchestrator falls back to the serial spine).

    Refuses if an active wave ledger exists (mutual exclusion with the serial
    spine and with concurrent waves). Recycles a drained ledger.
    """
    repo_root = _git_rev_parse_toplevel(track_dir)
    if not repo_root:
        emit(dict(error="cannot resolve git repo root "
                        "(git rev-parse --show-toplevel failed)", status="error"),
             "dispatch-wave", compact)
        return

    ledger = _load_ledger(track_dir)
    if _is_active(ledger):
        emit(dict(action="wave_active", phase=ledger.get("phase"),
                  wave=ledger.get("wave", [])), "dispatch-wave", compact)
        return
    # Recycle a drained prior ledger: its members' worktrees were torn down by
    # wave-finalize, so only the empty temp root remains to reap.
    if ledger:
        _cleanup_wave_root(ledger)

    base_sha = _git_head_sha(track_dir)
    if not base_sha:
        emit(dict(error="cannot resolve HEAD SHA", status="error"),
             "dispatch-wave", compact)
        return

    state = load(track_dir)
    phase = _current_phase(state)
    if phase < 1:
        emit(dict(action="no_ready_tasks", phase=0,
                  reason="all phases terminal"), "dispatch-wave", compact)
        return

    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        emit(dict(error="plan.md missing — cannot compute ready-set",
                  status="error"), "dispatch-wave", compact)
        return
    parsed = parse_plan(plan_path)

    ready = _ready_set(state, parsed, phase)
    if not ready:
        emit(dict(action="no_ready_tasks", phase=phase,
                  reason="no deps-declared file-disjoint pending tasks in phase"),
             "dispatch-wave", compact)
        return

    # Create worktrees BEFORE mutating state: a failure here tears down the
    # partial wave_root and leaves track-state.json untouched (no half-locks).
    slug = _branch_slug(track_dir)
    wave_root = tempfile.mkdtemp(prefix="conductor-wave-")
    members = []
    for m in ready:
        p, t = m["phase"], m["task"]
        branch = f"conductor/wave/{slug}/P{p}.T{t}"
        worktree = str(Path(wave_root) / f"P{p}.T{t}")
        if not _git_worktree_add(repo_root, worktree, branch, base_sha):
            for created in members:  # roll back the partial wave
                _teardown_member(repo_root, created)
            Path(wave_root).rmdir()  # mkdtemp dir; empty after teardown
            emit(dict(error=f"git worktree add failed for P{p}.T{t}",
                      status="error"), "dispatch-wave", compact)
            return
        wt_td = _wt_track_dir(worktree, track_dir, repo_root)
        _write_marker(wt_td, {**m, "track_id": slug}, branch)
        members.append({
            "phase": p, "task": t, "name": m["name"], "track_id": slug,
            "worktree": worktree, "branch": branch, "base_sha": base_sha,
            "locked_at": time.time(), "status": MEMBER_IN_FLIGHT,
            "worktree_track_dir": wt_td,
        })

    # Lock every member in ONE transaction — no F1 check (the ledger, not F1,
    # authorizes multiple in_progress tasks), no cursor move (serial spine owns it).
    with transaction(track_dir) as st:
        for m in members:
            _lock_inplace(st, m["phase"], m["task"])
        st["updated_at"] = now_iso()

    # Persist the ledger (members carry worktree_track_dir so wave-finalize and
    # the orchestrator need no re-derivation). Recyles any drained prior ledger.
    _save_ledger(track_dir, {
        "track_id": slug, "started_at": now_iso(), "base_sha": base_sha,
        "repo_root": repo_root, "phase": phase, "wave_root": wave_root,
        "wave": members,
    })

    emit(dict(action="dispatch_wave", phase=phase, base_sha=base_sha,
              wave=members), "dispatch-wave", compact)


def cmd_wave_status(track_dir, compact=True):
    """Read-only ledger snapshot."""
    ledger = _load_ledger(track_dir)
    if not ledger:
        emit(dict(active=False), "wave-status", compact)
        return
    emit(dict(active=_is_active(ledger), phase=ledger.get("phase"),
              base_sha=ledger.get("base_sha"),
              members=[
                  {"phase": m.get("phase"), "task": m.get("task"),
                   "name": m.get("name"), "status": m.get("status"),
                   "branch": m.get("branch")}
                  for m in ledger.get("wave", [])]),
         "wave-status", compact)


def cmd_wave_finalize(track_dir, p, t, compact=True):
    """Integrate one member's work: squash-merge → transition → teardown.

    SUCCESS path: squash-merge the member's branch onto the track branch (one
    code commit), then run the SUCCESS transition (``_finalize_task`` makes the
    conductor audit commit on top). FAILURE / no-commits path: no integration.
    Conflict (merge aborts) → FAILURE with a SPEC_DEVIATION note. The member's
    worktree + branch are torn down regardless. Emits ``drained=True`` when the
    last in-flight member settles (orchestrator may then start the next wave).
    """
    ledger = _load_ledger(track_dir)
    if not ledger:
        emit(dict(error="no wave ledger to finalize", status="error"),
             "wave-finalize", compact)
        return
    member = _member_by_loc(ledger, int(p), int(t))
    if member is None:
        emit(dict(error=f"P{p}.T{t} is not a member of the current wave",
                  status="error"), "wave-finalize", compact)
        return
    if member.get("status") != MEMBER_IN_FLIGHT:
        emit(dict(error=f"P{p}.T{t} already finalized as {member['status']}",
                  status="error"), "wave-finalize", compact)
        return

    repo_root = ledger.get("repo_root") or _git_rev_parse_toplevel(track_dir)
    branch = member["branch"]
    base = member["base_sha"]
    worktree = member["worktree"]
    wt_td = (member.get("worktree_track_dir")
             or _wt_track_dir(worktree, track_dir, repo_root))

    r = _read_worktree_result(conductor_dir(wt_td) / "result.json")
    status = (r.get("status", "") if r else "").upper()
    tip = _git_branch_tip(repo_root, branch)
    n_commits = _git_range_commit_count(repo_root, base, tip) if tip else 0
    conflict = False

    if status == "SUCCESS" and n_commits > 0 and tip:
        # Integrate the agent's squashed work as one code commit on the track
        # branch. None ⇒ the member's files overlapped another member's despite
        # declared deps — treat as a spec deviation / failure.
        squash_sha = _git_merge_squash(
            repo_root, branch,
            f"feat(conductor-wave): integrate '{member['name']}' [P{p}.T{t}]")
        if squash_sha is None:
            conflict = True
            status = "FAILURE"
            r = _failure_result(
                member, "Wave merge conflict: deps declared file-disjoint but "
                        "tasks touched overlapping files", tip)
        else:
            # The result's commit_sha is the agent's worktree tip (meaningless in
            # the main repo). Replace with the squash SHA so _finalize_task /
            # _do_complete store + note the real commit.
            r = dict(r)
            r["commit_sha"] = squash_sha
    else:
        # FAILURE, or SUCCESS with no commits (agent claimed success but produced
        # nothing), or no result.json — all route through the FAILURE transition.
        if status != "FAILURE":
            status = "FAILURE"
            if n_commits == 0:
                reason = ("Agent produced no commits in its worktree "
                          "(exhausted turns or lost context)")
            else:
                reason = "Agent reported no SUCCESS result"
            r = r or _failure_result(member, reason, tip)

    result, _clear = _finalize_task(
        track_dir, str(p), str(t), None, r, member.get("name", "unknown"), status)

    # Worktree + branch come down regardless of outcome; the (possibly squashed)
    # commit lives on the track branch now.
    _teardown_member(repo_root, member)

    member["status"] = ("conflict" if conflict
                        else ("finalized" if status == "SUCCESS" else "failed"))
    _save_ledger(track_dir, ledger)

    drained = all(m.get("status") in _TERMINAL_MEMBER for m in ledger["wave"])
    emit({**result, "action": "wave_finalized", "phase": int(p), "task": int(t),
          "member_status": member["status"], "drained": drained},
         "wave-finalize", compact)


def cmd_wave_abort(track_dir, compact=True):
    """Recovery: reset in-flight members to pending, tear down worktrees, drop ledger.

    Finalized/failed/conflict members keep their (already-applied) terminal
    status — abort only abandons work still in flight. The reset preserves
    ``retry_count``/``last_failure_summary`` (a wave that didn't finish isn't a
    failure attempt) so the serial spine re-dispatches members with their
    pre-wave history intact.
    """
    ledger = _load_ledger(track_dir)
    if not ledger:
        emit(dict(action="no_wave", ok=True), "wave-abort", compact)
        return
    repo_root = ledger.get("repo_root") or _git_rev_parse_toplevel(track_dir)

    with transaction(track_dir) as st:
        for m in ledger.get("wave", []):
            if m.get("status") in _TERMINAL_MEMBER:
                continue
            try:
                tgt = target(st, m["phase"], m["task"])
            except (IndexError, KeyError):
                continue
            tgt["status"] = "pending"
            # Keep status + history (retry_count/last_failure_summary); drop only
            # completion-era fields a stale lock shouldn't carry forward.
            clean(tgt, {"status", "retry_count", "last_failure_summary"})
        st["updated_at"] = now_iso()

    aborted = []
    for m in ledger.get("wave", []):
        if m.get("status") in _TERMINAL_MEMBER:
            continue
        _teardown_member(repo_root, m)
        aborted.append(f"P{m['phase']}.T{m['task']}")

    _cleanup_wave_root(ledger)
    _wave_ledger_path(track_dir).unlink(missing_ok=True)
    emit(dict(action="wave_aborted", aborted=aborted), "wave-abort", compact)
