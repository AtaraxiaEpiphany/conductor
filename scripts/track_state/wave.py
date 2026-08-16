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
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from .core import load, transaction
from .helpers import emit, now_iso, conductor_dir, target, extract_tags, clean
from .constants import TERMINAL_FOR_PARENT, MAX_RETRIES
from .mutations import _lock_inplace
from lib.atomic_io import atomic_write_json
from lib.json_utils import load_json_safe
from .dispatch import _classify_task, _finalize_task, _emit_quiescent_leaf
from .validate import ensure_healthy
from .plan_parse import parse_plan, collect_deps
from .git_ops import (
    _git_rev_parse_toplevel, _git_head_sha, _git_branch_tip,
    _git_range_commit_count, _git_merge_squash, _git_worktree_add,
    _git_worktree_remove, _git_branch_delete,
)


# Cap the fan-out. Conservative small-window default: two concurrent
# worktree-isolated agents is enough headroom for a phase of independent tasks
# without flooding a small context budget or the worktree object store. Override
# via the env knob (e.g. a large-window operator sets CONDUCTOR_WAVE_SIZE=4).
DEFAULT_WAVE_SIZE = 2


def _wave_size() -> int:
    """Resolved wave cap. Env-overridable via ``CONDUCTOR_WAVE_SIZE``.

    Mirrors ``on-batch-complete._budget_threshold``: a positive integer in the
    env wins; anything missing/empty/non-positive/non-numeric falls back to
    ``DEFAULT_WAVE_SIZE``. Read at each dispatch so a mid-session env change is
    honored without restart.
    """
    raw = os.environ.get("CONDUCTOR_WAVE_SIZE", "")
    try:
        n = int(raw)
        return n if n > 0 else DEFAULT_WAVE_SIZE
    except (TypeError, ValueError):
        return DEFAULT_WAVE_SIZE

# Filenames single-homed in lib.constants (imported so the quality gitignore
# derives from the same home). Historical public names preserved.
from lib.constants import RESULT_MARKER, WAVE_LEDGER_NAME, WAVE_MARKER_NAME  # noqa: E402

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
    return load_json_safe(_wave_ledger_path(track_dir))


def _save_ledger(track_dir, ledger):
    """Atomically write the wave ledger."""
    atomic_write_json(_wave_ledger_path(track_dir), ledger)


def _is_active(ledger):
    """A ledger is *active* (blocks the serial spine + new waves) iff it has
    at least one in-flight member. A fully drained ledger is inert —
    ``cmd_dispatch_wave`` recycles it."""
    return isinstance(ledger, dict) and any(
        isinstance(m, dict) and m.get("status") == MEMBER_IN_FLIGHT
        for m in ledger.get("wave", []))


def _in_flight_members(ledger):
    """Well-formed ``in_flight`` member dicts in ``ledger``, or ``[]``.

    Single hardened source for "which wave members are currently locked":
    skips anything that isn't a dict, isn't ``in_flight``, or lacks
    ``phase``/``task``. A corrupt ledger — a non-dict entry or a partial member
    left by a crashed wave session — must not crash the F1-guard consumers
    (validate, dispatch, lint-track-state) that rely on
    ``active_wave_member_locs``; this filter extends ``_load_ledger``'s
    "treated as absent" contract member-by-member, so a half-written ledger
    degrades to "no wave" instead of a crash that disables the F1 backstop.
    """
    if not isinstance(ledger, dict):
        return []
    out = []
    for m in ledger.get("wave", []):
        if not isinstance(m, dict):
            continue
        if m.get("status") != MEMBER_IN_FLIGHT:
            continue
        if m.get("phase") is None or m.get("task") is None:
            continue
        out.append(m)
    return out


def active_wave_member_locs(track_dir):
    """Set of ``(phase, task)`` currently locked by an active wave ledger.

    The F1-guard consumer for the serial spine's backstops: ``validate`` (stale-
    lock reaper, multi-in_progress warning), ``lint-track-state`` (F1 rule), and
    ``dispatch`` (mutual-exclusion refusal) all call this to exempt wave members
    — a wave's authority to hold several in_progress tasks comes from its ledger,
    not F1, so those members must not be reaped, flagged, or refused as if they
    were serial-spine F1 violations. Returns an empty set when there is no ledger
    or the ledger is drained (no well-formed in-flight members); corrupt/partial
    members are skipped (see ``_in_flight_members``), never raised on.
    """
    ledger = _load_ledger(track_dir)
    if not _is_active(ledger):
        return set()
    return {(m["phase"], m["task"]) for m in _in_flight_members(ledger)}


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


def _plan_deps_index(parsed):
    """Derive the per-task deps opt-in flag + declared-deps map from a plan.

    Returns ``(has_comment, deps_of)``:
    - ``has_comment``: ``{(phase, task): bool}`` — whether the task line carried a
      ``<!-- deps: -->`` comment (the wave opt-in gate; presence, not content).
    - ``deps_of``: ``{(phase, task): [(p, t), ...]}`` — declared dependency
      targets for that task (empty list when none declared). Covers ALL phases —
      a dep may target an earlier phase's task.

    Shared by ``_eligible_members`` (the gate) and ``_pending_ineligibility``
    (the per-task "why rejected" classifier) so the two never diverge on what
    counts as a deps comment or a declared edge.
    """
    has_comment = {}
    for pi, ph in enumerate(parsed.get("phases", []), 1):
        for ti, tk in enumerate(ph.get("tasks", []), 1):
            has_comment[(pi, ti)] = bool(tk.get("deps_has_comment"))
    deps_of = {}
    for src, tgt in collect_deps(parsed):
        deps_of.setdefault(src, []).append(tgt)
    return has_comment, deps_of


def _eligible_members(state, parsed, phase):
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

    **Uncapped** — returns EVERY eligible member in plan order. The caller splits
    this into the capped ready-set (``DEFAULT_WAVE_SIZE``) and the deferred
    overflow (eligible-but-capped), which is surfaced in the dispatch-wave
    envelope so the skill can announce it (no-silent-caps). Returns a list of
    ``{phase, task, name}``.
    """
    if phase < 1 or phase > len(state.get("phases", [])):
        return []

    # Per-task declared deps + opt-in flag, drawn from plan.md (the source of
    # truth for deps annotations). Shared with _pending_ineligibility so the
    # eligibility filter and the ineligibility classifier reason about one index.
    has_comment, deps_of = _plan_deps_index(parsed)

    eligible = []
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
        eligible.append({"phase": phase, "task": ti, "name": task.get("name", "")})
    return eligible


def _ready_set(state, parsed, phase):
    """The capped wave ready-set: first ``_wave_size()`` eligible members.

    Thin cap over ``_eligible_members`` — eligibility is separable from the cap
    so the deferred overflow (eligible-but-capped members) can be surfaced to the
    orchestrator rather than silently dropped (no-silent-caps). ``cmd_dispatch_wave``
    calls ``_eligible_members`` directly and splits; this wrapper keeps the
    historical capped-list contract (``tests/test_wave_ready_set.py`` imports it).
    """
    return _eligible_members(state, parsed, phase)[:_wave_size()]


def _pending_ineligibility(state, parsed, phase):
    """Classify WHY each pending task in ``phase`` is NOT wave-eligible.

    The mirror of ``_eligible_members``: instead of the eligible list, returns
    one ``{"phase", "task", "name", "reason"}`` dict per pending task that failed
    at least one gate, with ``reason`` set to the FIRST gate that rejected it —
    in the SAME check order as ``_eligible_members`` (flat → executor → opt-in →
    deps-satisfied), so the reported cause is exactly the gate that excluded it.

    Reason codes:
    - ``"subtasked"``        — has subtasks (flat-only, v1; subtasks are
                              sequentially decomposed, never parallel candidates);
    - ``"non_executor"``     — routed ``[Manual]``/``[Explore]`` (no parallelizable
                              body);
    - ``"no_deps_comment"``  — missing the ``<!-- deps: -->`` opt-in comment
                              (the author did not declare the task independent);
    - ``"deps_unsatisfied"`` — has a deps comment but a declared target is not yet
                              completed/skipped/deferred.

    Non-pending tasks (in_progress / terminal) are NOT reported — they are not
    candidates. An empty list means every pending task in the phase is eligible.
    Consumed by ``cmd_dispatch_wave`` so a ``no_ready_tasks`` envelope tells the
    orchestrator *which* gate killed each candidate, not just that none qualified
    (no-silent-X: a generic "no eligible tasks" hides the common case where every
    task is subtasked or missing the opt-in comment).
    """
    if phase < 1 or phase > len(state.get("phases", [])):
        return []
    has_comment, deps_of = _plan_deps_index(parsed)
    out = []
    for ti, task in enumerate(state["phases"][phase - 1]["tasks"], 1):
        if task.get("status") != "pending":
            continue
        name = task.get("name", "")
        if task.get("subtasks"):
            reason = "subtasked"
        elif _classify_task(extract_tags(name)) != "executor":
            reason = "non_executor"
        elif not has_comment.get((phase, ti)):
            reason = "no_deps_comment"
        else:
            deps = deps_of.get((phase, ti), [])
            if not all(_dep_satisfied(state, dp, dt) for dp, dt in deps):
                reason = "deps_unsatisfied"
            else:
                continue  # eligible — nothing to report
        out.append({"phase": phase, "task": ti, "name": name, "reason": reason})
    return out


# --- worktree plumbing ------------------------------------------------------

def _branch_slug(track_dir):
    """Git-branch-safe identifier for the track (used in wave branch names).

    ``conductor/wave/<slug>/P{p}.T{t}`` must be a valid ref. The track_dir name
    is the canonical short id; chars outside ``[A-Za-z0-9._-]`` are replaced so
    a shortname with ``/`` or spaces can't break the ref.
    """
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
    atomic_write_json(conductor_dir(wt_track_dir) / WAVE_MARKER_NAME, payload)


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
    return load_json_safe(result_path)


def _failure_result(member, summary, tip=None):
    """Minimal FAILURE result envelope for synthesis paths."""
    return {"status": "FAILURE", "commit_sha": tip or "N/A",
            "summary": summary, "phase": member["phase"],
            "task": member["task"], "subtask": None,
            "task_name": member.get("name", "unknown")}


# Orchestrator-facing member projection. skills/parallel/SKILL.md §3.2 consumes
# only these 6 keys to fan out pinned task-executor agents; the rest (track_id,
# base_sha, locked_at, status) are ledger-only and bloat the main session context
# for nothing when emitted (the wave envelope ships straight to the orchestrator
# as Bash stdout, unfiltered — no track-state PostToolUse trimmer exists). Project
# at emit; the full member dict is still what _save_ledger persists and what
# wave-status/wave-finalize/wave-abort read back from disk.
_ORCH_MEMBER_KEYS = ("phase", "task", "name",
                     "worktree", "branch", "worktree_track_dir")


def _slim_member(m):
    """Project a ledger member dict to the orchestrator-facing 6-key shape."""
    return {k: m[k] for k in _ORCH_MEMBER_KEYS if k in m}


# --- commands ---------------------------------------------------------------

def prepare_wave(track_dir):
    """Compute-only half of ``cmd_dispatch_wave`` — returns the envelope dict, no emit.

    Extracted so ``cmd_wave_step`` (Rail B) can compose the wave-prep step (compute
    ready-set, create worktrees, lock members, write ledger) and route on its
    outcome in the same call. The CLI wrapper ``cmd_dispatch_wave`` is now a thin
    ``emit()`` over this.

    Returns ``action``:
      - ``dispatch_wave``  — ready-set formed; ``wave`` lists members (with
        ``worktree``/``branch``/``worktree_track_dir`` for the orchestrator to
        dispatch N pinned task-executor agents). ``deferred`` lists any
        eligible-but-capped members (beyond ``DEFAULT_WAVE_SIZE``) for the skill
        to announce — they are NOT locked this wave and run in the next one.
      - ``wave_active``    — a non-terminal ledger already exists; refuse.
      - ``no_ready_tasks`` — phase has no eligible deps-declared file-disjoint
        pending tasks (orchestrator falls back to the serial spine).

    Refuses if an active wave ledger exists (mutual exclusion with the serial
    spine and with concurrent waves). Recycles a drained ledger.
    """
    repo_root = _git_rev_parse_toplevel(track_dir)
    if not repo_root:
        return dict(error="cannot resolve git repo root "
                          "(git rev-parse --show-toplevel failed)", status="error")

    ledger = _load_ledger(track_dir)
    if _is_active(ledger):
        return dict(action="wave_active", phase=ledger.get("phase"),
                    wave=[_slim_member(m) for m in ledger.get("wave", [])])
    # Recycle a drained prior ledger: its members' worktrees were torn down by
    # wave-finalize, so only the empty temp root remains to reap.
    if ledger:
        _cleanup_wave_root(ledger)

    base_sha = _git_head_sha(track_dir)
    if not base_sha:
        return dict(error="cannot resolve HEAD SHA", status="error")

    state = load(track_dir)
    phase = _current_phase(state)
    if phase < 1:
        return dict(action="no_ready_tasks", phase=0, reason="all phases terminal")

    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        return dict(error="plan.md missing — cannot compute ready-set", status="error")
    parsed = parse_plan(plan_path)

    eligible = _eligible_members(state, parsed, phase)
    if not eligible:
        return dict(action="no_ready_tasks", phase=phase,
                    reason="no deps-declared file-disjoint pending tasks in phase",
                    ineligible=_pending_ineligibility(state, parsed, phase))
    # Split into the capped ready-set and the deferred overflow. The deferred
    # members are NOT locked/worktreed this wave — they stay pending and surface
    # in the envelope so the skill announces them (no-silent-caps); the next
    # dispatch-wave (after this one drains) picks them up automatically.
    cap = _wave_size()
    ready = eligible[:cap]
    deferred = eligible[cap:]

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
            return dict(error=f"git worktree add failed for P{p}.T{t}", status="error")
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

    return dict(action="dispatch_wave", phase=phase, base_sha=base_sha,
                wave=[_slim_member(m) for m in members], deferred=deferred)


def cmd_dispatch_wave(track_dir, compact=True):
    """Compute the ready-set, fan out worktree-isolated members, write the ledger.

    Thin ``emit()`` wrapper over :func:`prepare_wave` (extracted so ``cmd_wave_step``
    can compose the wave-prep step). See ``prepare_wave`` for the envelope contract.
    """
    emit(prepare_wave(track_dir), "dispatch-wave", compact)


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


def finalize_wave_member(track_dir, p, t):
    """Compute-only half of ``cmd_wave_finalize`` — returns the result dict, no emit.

    Extracted so ``cmd_wave_step`` (Rail B) can integrate one member inline and
    route on its outcome (drained → seam review / next wave) in the same call.
    The CLI wrapper ``cmd_wave_finalize`` is now a thin ``emit()`` over this.

    SUCCESS path: squash-merge the member's branch onto the track branch (one
    code commit), then run the SUCCESS transition (``_finalize_task`` makes the
    conductor audit commit on top). FAILURE / no-commits path: no integration.
    Conflict (merge aborts) → FAILURE with a SPEC_DEVIATION note. The member's
    worktree + branch are torn down regardless. The returned dict carries
    ``drained=True`` when the last in-flight member settles.
    """
    ledger = _load_ledger(track_dir)
    if not ledger:
        return dict(error="no wave ledger to finalize", status="error")
    member = _member_by_loc(ledger, int(p), int(t))
    if member is None:
        return dict(error=f"P{p}.T{t} is not a member of the current wave",
                    status="error")
    if member.get("status") != MEMBER_IN_FLIGHT:
        return dict(error=f"P{p}.T{t} already finalized as {member['status']}",
                    status="error")

    repo_root = ledger.get("repo_root") or _git_rev_parse_toplevel(track_dir)
    branch = member["branch"]
    base = member["base_sha"]
    worktree = member["worktree"]
    wt_td = (member.get("worktree_track_dir")
             or _wt_track_dir(worktree, track_dir, repo_root))

    r = _read_worktree_result(conductor_dir(wt_td) / RESULT_MARKER)
    status = (r.get("status", "") if r else "").upper()
    tip = _git_branch_tip(repo_root, branch)
    n_commits = _git_range_commit_count(repo_root, base, tip) if tip else 0
    conflict = False

    # ── INTEGRATION RACE — why §4.0's "serialized" finalize is load-bearing ──
    # The block below mutates the SHARED main-worktree git index + the track
    # branch HEAD: `_git_merge_squash(repo_root, ...)` writes a squash commit
    # onto the track branch (cwd=repo_root, the single shared worktree), and
    # `_finalize_task` then stacks a conductor audit commit on top. Neither runs
    # inside `transaction()` — that flock guards ONLY track-state.json (the one
    # flock in this file lives in cmd_wave_abort). So two concurrent finalize
    # calls would race the same git index/HEAD: lost squash commits, a corrupt
    # index, or one member's audit commit landing on another's half-written HEAD.
    # ``cmd_wave_step`` emits ONE ``wave_integrate`` per call for exactly this
    # reason — do not naïvely parallelize it.
    #
    # Safe parallelization would need either per-member integration branches
    # (conductor/integrate/<slug>/P{p}.T{t}) squash-merged then serially
    # fast-forwarded, or a process-level integration lock serializing just this
    # block (little speedup, since the git work is the expensive part). Both are
    # non-trivial wave.py + git_ops.py rewrites — deferred.
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
    return {**result, "action": "wave_finalized", "phase": int(p), "task": int(t),
            "member_status": member["status"], "drained": drained}


def cmd_wave_finalize(track_dir, p, t, compact=True):
    """Integrate one member's work: squash-merge → transition → teardown.

    Thin ``emit()`` wrapper over :func:`finalize_wave_member` (extracted so
    ``cmd_wave_step`` can compose integration). See ``finalize_wave_member`` for
    the envelope contract.
    """
    emit(finalize_wave_member(track_dir, p, t), "wave-finalize", compact)


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


# ---------------------------------------------------------------------------
# Rail B-min: `wave-step` — a state-driven wave spine that collapses the
# dispatch-wave + wave-finalize loop into ONE leaf action per call. The
# orchestrator becomes a teleoperator: read `action`, do exactly that, call
# `wave-step` again. See conductor/design/rail-b-wave-step.md and
# skills/parallel-step/SKILL.md. Sibling of dispatch.cmd_step.
# ---------------------------------------------------------------------------

# Sidecar marker recording that a drained wave's post-drain decisions (seam
# review applicability) have been made. A sidecar FILE (not a ledger field)
# because finalize_wave_member re-loads + full-overwrites the ledger each call —
# a field would be lost on concurrent re-entry and re-fire seam_review. Keyed on
# (track_id, base_sha) so a new wave self-invalidates it. Gitignored (quality.py).
from lib.constants import WAVE_DRAIN_MARKER_NAME  # noqa: E402


def _drain_marker_path(track_dir):
    """Path to the drain-processed sidecar (``.conductor/.wave-drain-processed``)."""
    return conductor_dir(track_dir) / WAVE_DRAIN_MARKER_NAME


def _drain_processed(track_dir, ledger):
    """True iff the drain of THIS wave (track_id + base_sha) was already processed.

    Missing/malformed marker → not processed. Keyed on the wave's
    (track_id, base_sha) so a new wave (new base_sha) does not inherit a prior
    wave's marker — the self-invalidation that makes the marker safe across waves.
    """
    marker = load_json_safe(_drain_marker_path(track_dir))
    if not isinstance(marker, dict):
        return False
    return (marker.get("track_id") == ledger.get("track_id")
            and marker.get("base_sha") == ledger.get("base_sha"))


def _mark_drain_processed(track_dir, ledger):
    """Stamp the drain-processed marker for this wave BEFORE emitting the drain
    decision, so a re-entry (concurrent or after a resume) doesn't re-fire it."""
    atomic_write_json(_drain_marker_path(track_dir),
                      {"track_id": ledger.get("track_id"),
                       "base_sha": ledger.get("base_sha")})


def _wave_assemble_member_prompt(member, attempt=1):
    """Pre-assemble one member's task-executor prompt (worktree-pinned).

    Port of dispatch._step_assemble_prompt + the parallel §3.2 fan-out template.
    Built in code so the orchestrator pastes it verbatim — no per-member field
    interpolation (the N× weak-model failure surface this spine removes). SUBTASK
    is omitted (wave members are flat-only); ATTEMPT defaults to 1 (v1 does not
    retry in-wave). The leading ``cd "{worktree}"`` lands the agent's first Bash
    call in the worktree so every later git/edit targets it.
    """
    wt = member["worktree"]
    lines = [
        f'cd "{wt}"',
        f"WORKTREE_DIR={wt}",
        f"TRACK_DIR={member['worktree_track_dir']}",
        f"PHASE={member['phase']}",
        f"TASK={member['task']}",
        f"NAME={member.get('name', '?')}",
        f"ATTEMPT={attempt}",
        f"MAX_RETRIES={MAX_RETRIES}",
    ]
    return "\n".join(lines)


def _wave_member_unstarted(track_dir, ledger, member):
    """No-retry-burn discriminator: was this member's dispatch interrupted before
    the agent ran? True iff the worktree still exists, no result.json was written,
    AND the member's branch has zero commits past base_sha.

    Mirrors dispatch._is_start_commit's role for the serial spine — but wave
    members have NO start-commit (only the serial spine makes one), so the
    discriminator is ``n_commits == 0`` (the same primitive finalize_wave_member
    computes), not a commit-message pattern. Returns False when the worktree is
    gone (a partial abort tore it down) or the branch is missing — finalize
    synthesizes FAILURE correctly in both cases.
    """
    if not Path(member.get("worktree", "")).exists():
        return False
    repo_root = ledger.get("repo_root") or _git_rev_parse_toplevel(track_dir)
    wt_td = (member.get("worktree_track_dir")
             or _wt_track_dir(member["worktree"], track_dir, repo_root))
    if (conductor_dir(wt_td) / RESULT_MARKER).exists():
        return False
    tip = _git_branch_tip(repo_root, member["branch"])
    if not tip:
        return False
    return _git_range_commit_count(repo_root, member["base_sha"], tip) == 0


def _wave_member_retry_count(state, member):
    """A wave member's retry_count (read from track-state.json, not the ledger)."""
    try:
        return target(state, member["phase"], member["task"]).get("retry_count", 0)
    except (IndexError, KeyError):
        return 0


def _serial_in_progress(state, wave_locs):
    """True iff an ``in_progress`` task is NOT a wave member.

    When the model re-invokes ``wave-step`` mid-serial-task (a non-wave task left
    in_progress after a ``serial`` leaf), the serial spine owns its finalize — a
    new wave must not start concurrent with it (matches parallel §3.3: complete
    the serial task, then re-check for waves). ``wave_locs`` is the active wave's
    member locs (empty when no wave is active, which is the only state from which
    ``cmd_wave_step`` reaches this check).
    """
    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            if task.get("status") == "in_progress" and (pi, ti) not in wave_locs:
                return True
    return False


def _wave_step_emit_batch(track_dir, pre, compact):
    """Fresh wave: emit ``dispatch_batch`` with one pre-assembled ``prompt`` per
    member. The orchestrator fires ALL members in ONE message (concurrent Agent
    calls) — that parallelism is the entire point of the wave spine."""
    members = [_slim_member(m) for m in pre.get("wave", [])]
    for m in members:
        m["prompt"] = _wave_assemble_member_prompt(m, attempt=1)
    emit(dict(action="dispatch_batch", phase=pre.get("phase"),
              base_sha=pre.get("base_sha"), wave=members,
              deferred=pre.get("deferred", [])),
         "wave-step", compact)


def _wave_step_emit_redispatch(track_dir, ledger, member, retry_count, compact):
    """Interrupted member (no-retry-burn): emit a single-member ``dispatch_batch``
    to re-run it WITHOUT finalizing. Reuses the member's existing worktree/branch;
    ``is_resume`` signals the orchestrator this is a re-dispatch."""
    m = _slim_member(member)
    m["prompt"] = _wave_assemble_member_prompt(m, attempt=retry_count + 1)
    emit(dict(action="dispatch_batch", phase=ledger.get("phase"),
              base_sha=ledger.get("base_sha"), wave=[m],
              deferred=[], is_resume=True, attempt=retry_count + 1),
         "wave-step", compact)


def _wave_step_emit_integrate(track_dir, member, compact):
    """Emit ``wave_integrate`` for one in_flight member — the orchestrator runs
    ``wave-finalize`` verbatim. Members integrate SERIALLY (one per wave-step
    call) because the squash-merge block mutates the shared main-worktree index
    (see finalize_wave_member's INTEGRATION RACE note)."""
    emit(dict(action="wave_integrate", phase=member["phase"],
              task=member["task"], name=member.get("name", "?")),
         "wave-step", compact)


def _wave_step_route_quiescent(track_dir, state, compact, ineligible=None):
    """No wave work and no active wave → surface a failed-member decision, a phase
    checkpoint, delegate ONE leaf to the serial step spine, or done. The shared
    terminal/quiescent router (dispatch._emit_quiescent_leaf) owns the first three;
    only dispatchable serial work is wave-specific (→ ``serial``)."""
    nxt = _emit_quiescent_leaf(track_dir, state, compact, "wave-step")
    if nxt is None:
        return
    # Dispatchable serial work → one step leaf, then re-invoke wave-step (the
    # serial task may satisfy a dep that unlocks the next wave, §3.3).
    emit(dict(action="serial", ineligible=ineligible or [],
              execution_mode=state.get("execution_mode", "interactive")),
         "wave-step", compact)


def cmd_wave_step(track_dir, compact=True):
    """State-driven wave-loop step — the Rail B-min wave spine entry point.

    Composes dispatch-wave + wave-finalize into ONE leaf action per call, then
    returns. The orchestrator reads ``action`` and does exactly that — fire the
    batch, run wave-finalize for one member, hand off to the seam review / serial
    / phase-checkpoint branch, or stop — then calls ``wave-step`` again.

    Action set:
      - ``dispatch_batch``   : fire N pinned task-executor agents (one message),
                               each prompt verbatim. [spine]
      - ``wave_integrate``   : run ``wave-finalize <td> --phase <p> --task <t>``. [spine]
      - ``seam_review``      : ≥2 finalized this wave → hand to parallel §4.15. [non-spine]
      - ``serial``           : run ``track-state step`` once, then re-invoke. [non-spine]
      - ``phase_checkpoint`` : hand to phase-checker fan-out. [non-spine]
      - ``ask``/``skip_analyze`` : failed member Retry/Skip/Block. [spine]
      - ``done``/``error``   : terminal.

    Internal-only transitions (drain-marker bookkeeping, interrupted-member
    re-dispatch) are fully resolved before emitting, so the model never sees them.
    """
    # Open with ensure_healthy (like cmd_step), NOT cmd_recover — its wave-active
    # guard would refuse, and wave-step IS the wave spine.
    state, fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        emit(dict(action="error", errors=verrors), "wave-step", compact)
        return

    ledger = _load_ledger(track_dir)

    # STATE A — active wave with in_flight members: integrate one (or re-dispatch
    # an interrupted member without burning its retry).
    in_flight = _in_flight_members(ledger)
    if in_flight:
        m = in_flight[0]  # lowest (phase,task); deterministic for tests
        if _wave_member_unstarted(track_dir, ledger, m):
            return _wave_step_emit_redispatch(
                track_dir, ledger, m, _wave_member_retry_count(state, m), compact)
        return _wave_step_emit_integrate(track_dir, m, compact)

    # STATE B — a drained ledger not yet processed: decide seam-review
    # applicability once (idempotent via the sidecar marker).
    if ledger and ledger.get("wave") and not _is_active(ledger) \
            and not _drain_processed(track_dir, ledger):
        _mark_drain_processed(track_dir, ledger)  # before emit — re-entry-safe
        finalized = [m for m in ledger.get("wave", [])
                     if m.get("status") == "finalized"]
        if len(finalized) >= 2:
            emit(dict(action="seam_review", phase=ledger.get("phase"),
                      finalized_count=len(finalized),
                      revision_range=f"{ledger.get('base_sha', '')}..HEAD"),
                 "wave-step", compact)
            return
        # <2 finalized: no seam review; fall through to next-wave / serial / done.

    # STATE C — no active wave: compute the next wave (or route quiescent).
    # If a serial task is in_progress (not a wave member), the serial spine owns
    # its finalize — delegate before starting a new wave (parallel §3.3: complete
    # the serial task, then re-check for waves).
    if _serial_in_progress(state, active_wave_member_locs(track_dir)):
        return _wave_step_route_quiescent(track_dir, state, compact, None)

    pre = prepare_wave(track_dir)
    act = pre.get("action")
    if act == "dispatch_wave":
        return _wave_step_emit_batch(track_dir, pre, compact)
    if act == "wave_active":
        # Became active between the in_flight check and prepare_wave (rare);
        # prepare_wave refused with the active ledger — integrate a member.
        reflown = _in_flight_members(_load_ledger(track_dir))
        if reflown:
            return _wave_step_emit_integrate(track_dir, reflown[0], compact)
    if pre.get("error") or pre.get("status") == "error":
        emit(dict(action="error", error=pre.get("error", "wave prepare failed")),
             "wave-step", compact)
        return

    # no_ready_tasks → serial / phase_checkpoint / failed-exhausted / done.
    return _wave_step_route_quiescent(track_dir, state, compact, pre.get("ineligible"))
