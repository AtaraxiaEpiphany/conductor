"""Task dispatch orchestration: find next, prepare, finalize."""
import json
import re
import shlex
import sys
from pathlib import Path

from .core import load
from .helpers import (
    out, emit, now_iso, extract_tags, _inherit_tags,
    conductor_dir, _store_evidence, _last_subtask_sha, _any_phase_needs_checkpoint,
    flag, _normalize_sha, target, _extract_tags_for_task, _resolve_conductor_root,
    _terminal_gate_group_members,
)
from .constants import (AUTO_COMPLETE_OK, MAX_RETRIES, task_max_retries,
                        MAX_ANALYSIS_ROUNDS)
from .mutations import (_do_lock, _do_complete, _do_fail, _do_fail_parent,
                        _do_defer, _do_skip, reactivate_for_modified_retry)
from .result import _advisory_gates
from .sync import _do_sync_plan
from lib import dispatch_inflight as _inflight
from lib import dispatch_lock as _dispatch_lock
from lib.git_utils import implementation_uncommitted_files
from .git_ops import (
    _git_commit, _git_commit_ensured, _git_head_sha, _write_git_note,
    _has_sibling_sha, _update_task_sha, _recover_git_notes,
    _is_start_commit, _git_uncommitted_files, _finalize_parent,
    docs_synced_for_track, wiki_phase2_committed_for_track,
    _git_rev_parse_toplevel,
)
from .handoff import _append_execution_record, compile_track_findings
from .misc import _get_all_shas, _stamp_checkpoint_in_plan
from .quality import _finalize_track
from .validate import _fix_plan_mismatches, ensure_healthy


def _classify_task(tags):
    """Canonical task category from tags: ``"manual"`` | ``"explore"`` | ``"executor"``.

    Single source of truth for the Manual/Explore/default routing decision
    shared by ``cmd_dispatch_next`` and ``cmd_dispatch_prepare`` — which had
    near-identical tag-routing with two different action vocabularies. Add a
    new routed tag type by adding a row to the registry
    (templates/workflow/task-type-profiles.json) once; each caller maps the
    category to its own action enum. Returns ``"executor"`` (the default
    task-executor path) when no routing tag is present.
    """
    from .task_profiles import route_for
    return route_for(tags)


def _active_wave(track_dir):
    """Return the active wave ledger, or ``None``.

    Lazy-imports :mod:`wave` (which imports this module) to avoid a load-time
    dispatch↔wave cycle. Mutual-exclusion gate: while a wave is in flight, the
    serial spine (dispatch-next/prepare/recover) must not interleave on the same
    track — the wave owns those members' lifecycle. Returns the ledger dict when
    active, else ``None``.
    """
    try:
        from .wave import _load_ledger, _is_active
    except Exception:
        return None
    ledger = _load_ledger(track_dir)
    return ledger if _is_active(ledger) else None


def _task_dict(state, pi, ti, si=None):
    """Tolerant accessor for a task (or subtask) dict by 1-indexed coords.

    Returns ``None`` on any out-of-range/missing path so callers can ``.get()``
    without guarding — mirrors the inline ``state["phases"][pi-1]["tasks"][ti-1]``
    pattern repeated throughout this module, now centralized.
    """
    try:
        task = state["phases"][pi - 1]["tasks"][ti - 1]
    except (IndexError, KeyError, TypeError):
        return None
    if si is None:
        return task
    try:
        return task["subtasks"][si - 1]
    except (IndexError, KeyError, TypeError):
        return None


def _find_next_task(state):
    """Find the next task to execute. Returns result dict or None."""
    result = None
    stuck = None
    # Pass 1: in_progress tasks (recovery / dispatch continuation)
    for pi, phase in enumerate(state["phases"], 1):
        for ti, task in enumerate(phase["tasks"], 1):
            if task["status"] == "in_progress":
                subs = task.get("subtasks")
                if subs:
                    for si, sub in enumerate(subs, 1):
                        if sub["status"] in ("in_progress", "pending"):
                            result = dict(phase=pi, task=ti, subtask=si,
                                          name=sub["name"], type="subtask",
                                          tags=_inherit_tags(extract_tags(sub["name"]), task["name"]))
                            break
                    if not result:
                        # All subtasks are non-dispatchable — auto-complete only if no failures
                        if all(sub["status"] in AUTO_COMPLETE_OK for sub in subs):
                            result = dict(phase=pi, task=ti, subtask=None,
                                          name=task["name"], type="parent-complete",
                                          tags=extract_tags(task["name"]))
                        else:
                            # Parent has failed subtasks — record as stuck but keep looking
                            if stuck is None:
                                stuck = dict(phase=pi, task=ti, subtask=None,
                                             name=task["name"], type="parent-stuck",
                                             tags=extract_tags(task["name"]))
                else:
                    result = dict(phase=pi, task=ti, subtask=None,
                                  name=task["name"], type="flat",
                                  tags=extract_tags(task["name"]))
                if result:
                    break
        if result:
            break
    # Pass 2: pending tasks (new dispatch)
    if not result:
        for pi, phase in enumerate(state["phases"], 1):
            for ti, task in enumerate(phase["tasks"], 1):
                if task["status"] == "pending":
                    subs = task.get("subtasks")
                    if subs:
                        for si, sub in enumerate(subs, 1):
                            if sub["status"] in ("in_progress", "pending"):
                                result = dict(phase=pi, task=ti, subtask=si,
                                              name=sub["name"], type="subtask",
                                              tags=_inherit_tags(extract_tags(sub["name"]), task["name"]))
                                break
                        if not result:
                            # All subtasks are terminal but parent is still pending
                            # Only auto-complete if no subtasks are failed
                            if all(sub["status"] in AUTO_COMPLETE_OK for sub in subs):
                                result = dict(phase=pi, task=ti, subtask=None,
                                              name=task["name"], type="parent-complete",
                                              tags=extract_tags(task["name"]))
                            else:
                                # Parent has failed subtasks — record as stuck but keep looking
                                if stuck is None:
                                    stuck = dict(phase=pi, task=ti, subtask=None,
                                                 name=task["name"], type="parent-stuck",
                                                 tags=extract_tags(task["name"]))
                                continue
                    else:
                        result = dict(phase=pi, task=ti, subtask=None,
                                      name=task["name"], type="flat",
                                      tags=extract_tags(task["name"]))
                    break
            if result:
                break
    # If nothing else found but a stuck parent exists, return it for handling
    if not result:
        result = stuck
    if not result:
        result = dict(phase=0, task=0, subtask=None, name=None, type=None, tags=[])
    return result


def cmd_next(track_dir, compact=True):
    state = load(track_dir)
    execution_mode = state.get("execution_mode", "interactive")
    result = _find_next_task(state)
    result["execution_mode"] = execution_mode
    emit(result, "next", compact)
    return result

def cmd_dispatch_next(track_dir, compact=True):
    """One-call dispatch decision: next + parent-complete resolution + tag routing.
    Returns action enum for orchestrator to switch on."""
    # Mutual exclusion: a wave in flight owns this track — refuse the serial spine.
    wave = _active_wave(track_dir)
    if wave:
        emit(dict(action="wave_active", phase=wave.get("phase")),
             "dispatch-next", compact)
        return

    # Auto-fix state before dispatching
    _, fixes, _ = ensure_healthy(track_dir)
    if fixes:
        print(f"Dispatch auto-fixed {len(fixes)} issue(s): {'; '.join(fixes)}", file=sys.stderr)

    # Loop instead of recursion to avoid stack overflow on many parent-complete tasks
    max_iterations = 50
    for _ in range(max_iterations):
        state = load(track_dir)
        execution_mode = state.get("execution_mode", "interactive")

        # Check if any phase needs a checkpoint before doing anything else
        cp = _any_phase_needs_checkpoint(track_dir, state)
        if cp is not None:
            # Rail A/B unification: attach the SAME pre-assembled verifier
            # fan-out the `step` spine emits (dispatch_batch), so
            # skills/implement/SKILL.md §3.2 Step 1 pastes the member `prompt`
            # fields verbatim instead of hand-interpolating TRACK_DIR/TRACK_ID/
            # PHASE_INDEX. One source (build_dispatch_prompt) feeds both rails.
            # The synth (phase-checker) prompt stays §3.2 Step 3 prose because it
            # consumes the just-collected verifier verdicts from ephemeral
            # context (not yet on disk) — `_build_phase_checker` rebuilds that
            # exact field set; the `step` spine pre-assembles the synth too.
            #
            # resolve_phase_gate composes the gate plan once (verifier set +
            # verify-mode directive + gate-group membership) — the single
            # dispatch-side chokepoint for the three checkpoint-relevant axes.
            # Threading verifiers through avoids a double-resolve in the wave
            # builder; the modes/group are the #6 composition contract (read by
            # phase-checker's binding branches via the heading, not this envelope).
            gate_plan = resolve_phase_gate(track_dir, state, cp)
            wave = _build_verifier_wave(track_dir, state, cp,
                                        verifiers=gate_plan["verifiers"])
            emit(dict(action="dispatch_phase_checker", phase=cp,
                      execution_mode=execution_mode, wave=wave),
                 "dispatch-next", compact)
            return

        # Find next task
        result = _find_next_task(state)

        if result.get("phase", 0) < 1:
            emit(dict(action="finalize"), "dispatch-next", compact)
            return

        # Resolve action from type + tags
        rtype = result["type"]
        tags = result["tags"]

        if rtype == "parent-complete":
            # Auto-complete parent, resolve SHA from subtasks, then loop again
            parent_task = state["phases"][result["phase"] - 1]["tasks"][result["task"] - 1]
            sha = _last_subtask_sha(parent_task)
            try:
                _, state = _do_complete(track_dir, result["phase"], result["task"], None, sha)
            except (ValueError, IndexError) as e:
                emit(dict(error=str(e), status="error"), "dispatch-next", compact)
                return
            _do_sync_plan(track_dir, state)

            # Conductor commit first (before finalize so the note targets the
            # final SHA), then the shared post-commit audit-trail sequence.
            parent_name = parent_task.get("name", "unknown")
            committed = _git_commit_ensured(
                track_dir, f"chore(conductor): Complete parent '{parent_name}' [{sha}]")
            if not committed:
                print(f"WARNING: conductor commit failed for parent-complete of '{parent_name}'",
                      file=sys.stderr)
            _finalize_parent(track_dir, result["phase"], result["task"], sha)

            continue

        if rtype == "parent-stuck":
            # Parent has failed subtasks and no other work exists. Fail the
            # parent (renders [!], not [x]) so recover() surfaces it on the next
            # /implement run as 'failed + retry >= max' → user decides
            # retry/skip/block. See _do_fail_parent for why retry_count is pinned.
            parent_task = state["phases"][result["phase"] - 1]["tasks"][result["task"] - 1]
            sha = _last_subtask_sha(parent_task)
            try:
                _do_fail_parent(track_dir, result["phase"], result["task"], "", sha)
            except (ValueError, IndexError) as e:
                # All subtasks should be terminal (failed counts), but guard
                # against edge cases where non-terminal subtasks still exist.
                emit(dict(error=str(e), status="error"), "dispatch-next", compact)
                return
            state = load(track_dir)
            _do_sync_plan(track_dir, state)
            parent_name = parent_task.get("name", "unknown")
            _git_commit_ensured(
                track_dir,
                f"chore(conductor): Fail parent '{parent_name}' (subtasks exhausted retries)")
            # Same post-commit audit trail as parent-complete, minus the evidence
            # seed (a failed parent carries no coverage evidence).
            final_sha = _finalize_parent(
                track_dir, result["phase"], result["task"], sha, ensure_evidence=False)

            emit(dict(action="parent_stuck", phase=result["phase"], task=result["task"],
                      name=parent_name, sha=final_sha,
                      execution_mode=execution_mode), "dispatch-next", compact)
            return

        # Route by tags
        category = _classify_task(tags)
        if category == "manual":
            # continuous: auto-defer (no human in the loop). interactive: surface
            # to the user — a [Manual] task can't be auto-executed (task-executor
            # has no Manual handling) and must not be silently deferred.
            action = "defer_manual" if execution_mode == "continuous" else "manual_task"
        elif category == "explore":
            action = "dispatch_explorer"
        else:
            action = "dispatch_executor"

        result["action"] = action
        result["execution_mode"] = execution_mode
        # Rail A/B unification: attach the SAME pre-assembled (agent, prompt)
        # envelope the `step` spine builds, so skills/implement/SKILL.md §3.3/§3.4
        # paste `prompt` verbatim instead of re-deriving the KEY=value lines in
        # prose. One source (build_dispatch_prompt) feeds both rails. Only the
        # executor/explorer dispatch branches carry a prompt — the others (manual,
        # defer) are decided in-spine and need no subagent dispatch. The attempt
        # is resolved from the task's real retry_count (1 for a fresh dispatch,
        # retry_count+1 on a resume), making the prompt authoritative instead of
        # leaving ATTEMPT for the orchestrator to hand-interpolate.
        if action in ("dispatch_explorer", "dispatch_executor"):
            tgt = _task_dict(state, result["phase"], result["task"], result.get("subtask"))
            retry_count = tgt.get("retry_count", 0) if tgt else 0
            attempt = retry_count + 1
            pre = dict(phase=result["phase"], task=result["task"],
                       subtask=result.get("subtask"), name=result.get("name", "?"),
                       tags=result.get("tags", []))
            agent, prompt = build_dispatch_prompt(
                "dispatch_executor", track_dir, pre=pre, attempt=attempt)
            result["agent"] = agent
            result["prompt"] = prompt
            result["attempt"] = attempt
            result["max_retries"] = task_max_retries(tgt) if tgt else MAX_RETRIES
        emit(result, "dispatch-next", compact)
        return

    emit(dict(error="dispatch-next exceeded max iterations — possible state corruption",
              status="error"), "dispatch-next", compact)


def _emit_no_active_task(track_dir, state, fixes, compact):
    """Emit the no-active-task result shared by both cmd_recover guards."""
    result = dict(status="no_active_task")
    checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
    if checkpoint_pending is not None:
        result["phase_checkpoint_pending"] = checkpoint_pending
    if fixes:
        result["fixes_applied"] = fixes
    emit(result, "recover", compact)


def _find_failed_exhausted(state):
    """First failed task whose retries are exhausted, scanning subtasks first
    (most specific). Returns ``(pi, ti, si, tgt, name)`` or ``None``.

    The bridge that makes the §2.0 failed+max decision actually reachable:
    ``ensure_healthy`` → ``_fix_terminal_current_indices`` treats ``failed`` as
    terminal-for-advance (it is in ``TERMINAL_FOR_PARENT``), so once a failed
    task's indices are advanced/cleared past, ``cmd_recover``'s main resolution
    path never sees it. When no active task remains, this scan recovers the
    failed+exhausted task so the retry/skip/block ``decision`` can surface
    instead of a bare ``no_active_task`` (matching the intent recorded in
    ``_do_fail_parent``'s docstring and the §2.0 routing table).
    """
    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if (sub.get("status") == "failed"
                        and sub.get("retry_count", 0) >= task_max_retries(sub)):
                    return pi, ti, si, sub, sub.get("name", "...")
            if (task.get("status") == "failed"
                    and task.get("retry_count", 0) >= task_max_retries(task)):
                return pi, ti, None, task, task.get("name", "...")
    return None


def _find_failed_task(state):
    """First ``failed`` task (regardless of retry exhaustion), scanning subtasks
    first (most specific). Returns ``(pi, ti, si, tgt, name)`` or ``None``.

    Broader than :func:`_find_failed_exhausted` (which requires
    ``retry_count >= max``) — used by the failure-analyze handshake, which can
    fire *before* exhaustion (the pre-exhaustion tier, B.6) when a task is
    ``failed`` but still has retry budget. When the analyst fires post-exhaustion
    (the skip-analyst ``retry_with_modification`` hand-off), the failed task is
    also exhausted, so this still finds it.
    """
    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if sub.get("status") == "failed":
                    return pi, ti, si, sub, sub.get("name", "...")
            if task.get("status") == "failed":
                return pi, ti, None, task, task.get("name", "...")
    return None


def _emit_no_active_or_decision(track_dir, state, fixes, compact):
    """No active (pending/in_progress) task remains. If a failed+exhausted task
    exists on an interactive track, surface it for the §2.0 retry/skip/block
    decision; otherwise emit ``no_active_task``.

    Continuous mode never surfaces a decision — skip-analyst (§3.6) owns the
    failed-task path there.
    """
    if state.get("execution_mode", "interactive") == "interactive":
        found = _find_failed_exhausted(state)
        if found is not None:
            fpi, fti, fsi, tgt, name = found
            result = dict(
                status="failed",
                phase=fpi, task=fti, subtask=fsi,
                name=name,
                retry_count=tgt.get("retry_count", 0),
                max_retries=task_max_retries(tgt),
                execution_mode="interactive",
            )
            if fixes:
                result["fixes_applied"] = fixes
            checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
            if checkpoint_pending is not None:
                result["phase_checkpoint_pending"] = checkpoint_pending
            result["decision"] = _failed_task_decision(
                track_dir, fpi, fti, fsi, name, result["retry_count"])
            emit(result, "recover", compact)
            return
    _emit_no_active_task(track_dir, state, fixes, compact)


def _bookkeeping_commit_line(message):
    """A relayed-envelope commit one-liner: stage, then commit ONLY if something
    is staged — the robust replacement for the ad-hoc ``git commit -m`` /
    ``git commit -am`` lines the post-loop ``post`` and implement-loop ``decision``
    blobs hand the teleoperator.

    ``git add -A`` (not bare ``-m`` / ``-a``) stages new + modified + deleted
    artifacts: the track-state mutators never stage what they write (so bare
    ``-m`` found nothing staged and failed), and ``-a`` only stages modifications
    to already-tracked files (so the first untracked sidecar/sentinel missed its
    own commit). Safe because the conductor flow reaches each gate with a clean
    working tree — only conductor-managed artifacts are pending. The
    ``git diff --cached --quiet ||`` guard makes an empty commit a no-op: every
    gate advances on a durable marker (a state field or a sidecar stamp set by a
    SEPARATE line in the same ``post``), never on this commit existing, so
    idempotent re-entry after an interruption is a no-op instead of a hard git
    failure.
    """
    return ("git add -A && git diff --cached --quiet || git commit -m "
            + shlex.quote(message))


def _failed_task_decision(track_dir, pi, ti, si, name, retry_count):
    """Build the pre-computed Retry/Skip/Block ``decision`` blob for an
    interactive failed+exhausted task (the #1 transducer win).

    Today the skill (implement/SKILL.md §2.2) asks the orchestrator to (a)
    judge retry-exhaustion and (b) construct three multi-line bash blocks,
    pasting the free-text task name into ``git commit -m "..."`` lines. A weak
    model fumbles both. This blob moves that construction into code: the
    orchestrator does ``AskUserQuestion(decision.question, decision.header,
    decision.options)`` → map the chosen label → run
    ``decision.commands[label]`` verbatim → ``decision.next[label]``. No
    judgment, no bash authoring.

    Shell-safety: the free-text task name is embedded ONLY inside
    :func:`shlex.quote`-wrapped ``git commit -m`` / ``--reason`` arguments, so
    a name containing quotes/backticks/``$`` can't break the shell line the
    orchestrator runs verbatim. The whole blob is then JSON-escaped by
    :func:`emit` → :func:`out` (``json.dumps``), so the same name round-trips
    the JSON transport too. Track-dir paths are trusted (conductor-owned) and
    kept in the readable double-quoted form the skill already uses.
    """
    td = str(track_dir)
    loc = f"P{pi}.T{ti}" + (f".S{si}" if si else "")

    reset_cmd = f'track-state reset "{td}" task --phase {pi} --task {ti}'
    sync_cmd = f'track-state sync-plan "{td}"'
    skip_cmd = (
        f'track-state skip "{td}" --phase {pi} --task {ti} '
        f'--reason {shlex.quote("Skipped: failed task not required")}'
    )
    block_cmd = (
        f'track-state block "{td}" --phase {pi} --task {ti} '
        f'--reason {shlex.quote("Blocked: failed task needs human intervention")}'
    )

    commit_retry = _bookkeeping_commit_line(
        f"chore(conductor): Reset failed task '{name}' for retry [{loc}]")
    commit_skip = _bookkeeping_commit_line(
        f"chore(conductor): Skip failed task '{name}' [{loc}]")
    commit_block = _bookkeeping_commit_line(
        f"chore(conductor): Block failed task '{name}' [{loc}]")

    return dict(
        question=f"Task '{name}' ({loc}) failed after {retry_count} attempt(s). What next?",
        header="Failed task",
        options=[
            {"label": "Retry", "description": "Reset and re-dispatch from scratch"},
            {"label": "Skip", "description": "Mark skipped — not required"},
            {"label": "Block", "description": "Block — needs human intervention (HALT)"},
        ],
        commands={
            "Retry": [reset_cmd, sync_cmd, commit_retry],
            "Skip": [skip_cmd, sync_cmd, commit_skip],
            "Block": [block_cmd, sync_cmd, commit_block],
        },
        next={"Retry": "3.1", "Skip": "3.1", "Block": "HALT"},
    )


def _parse_decompose_names(modification):
    """Best-effort extraction of proposed subtask names from failure-analyst's
    ``modification`` text.

    The analyst returns free-form prose for a ``decompose`` verdict (e.g.
    ``"1. Add validator\\n2. Wire CLI\\n3. Add tests"`` or ``"- parse\\n- emit"``).
    This pulls the non-empty lines, stripping leading ``-``/``*``/``N.`` markers,
    into candidate subtask names. Returns at least ``["Part 1"]`` on total parse
    failure so the ``ask`` still proceeds (the human can edit before applying).
    """
    names = []
    if modification:
        for line in modification.splitlines():
            cleaned = line.strip().lstrip("-*").strip()
            # Strip a leading enumerator like "1." / "2)" / "1:"
            cleaned = re.sub(r"^\d+[.):]\s*", "", cleaned)
            if cleaned and cleaned not in names:
                names.append(cleaned)
    return names or ["Part 1"]


def _decompose_decision(track_dir, marker):
    """Build the Apply/Skip/Escalate ``decision`` blob for a failure-analyst
    ``decompose`` verdict.

    Replaces the old prose-only halt: the recovery recipe told a human to hand-edit
    plan.md, skip the original, and insert remainder tasks — exactly the kind of
    structured-state authoring a teleoperator shouldn't do in prose. This blob
    moves it into code: the human just confirms the analyst's proposed split
    (built from the marker's ``modification``), and the teleoperator runs
    ``track-state split`` verbatim. Mirrors ``_failed_task_decision``'s
    {question, header, options, commands, next} shape (dispatch.py:380).
    """
    td = str(track_dir)
    pi = marker.get("phase")
    ti = marker.get("task")
    si = marker.get("subtask")
    name = marker.get("name", "?")
    loc = f"P{pi}.T{ti}" + (f".S{si}" if si else "")

    names = _parse_decompose_names(marker.get("modification"))
    what_was_done = (marker.get("what_was_done") or "").strip()
    sha = marker.get("sha") or ""  # present when the analyst carried the partial SHA
    note = "Decomposed via failure-analyst"
    if what_was_done:
        note += f"; partial work: {what_was_done}"
    if sha:
        note += f" (sha {sha})"

    sub_pos = f"{pi} {ti}" + (f" {si}" if si else "")
    split_cmd = (
        f'track-state split "{td}" {sub_pos} '
        f'--subtasks {shlex.quote(";".join(names))} '
        f'--note {shlex.quote(note)}')
    sync_cmd = f'track-state sync-plan "{td}"'
    skip_cmd = (
        f'track-state skip "{td}" --phase {pi} --task {ti} '
        + (f'--subtask {si} ' if si else '')
        + f'--reason {shlex.quote("Decomposed: skipped without splitting")}')
    commit_split = _bookkeeping_commit_line(
        f"chore(conductor): Decompose '{name}' [{loc}] via ask")
    commit_skip = _bookkeeping_commit_line(
        f"chore(conductor): Skip failed task '{name}' [{loc}] (decompose ask)")

    return dict(
        question=(
            f"failure-analyst recommends DECOMPOSING '{name}' ({loc}) into "
            f"{len(names)} subtask(s): {', '.join(names)}. Apply the split?"),
        header="Decompose",
        options=[
            {"label": "Apply split",
             "description": "Skip original (SHA kept), add these subtasks, resume"},
            {"label": "Skip original only",
             "description": "Skip the task without splitting — resume without it"},
            {"label": "Escalate",
             "description": "Stop for human investigation (HALT)"},
        ],
        commands={
            "Apply split": [split_cmd, commit_split],
            "Skip original only": [skip_cmd, sync_cmd, commit_skip],
            "Escalate": [],
        },
        next={"Apply split": "step", "Skip original only": "step", "Escalate": "HALT"},
    )


def cmd_recover(track_dir, compact=True):
    """Recover current task after interruption, with auto-fix and smart advancement.

    1. Runs ensure_healthy() to validate and auto-fix state.
    2. If current indices point to a terminal task, advances to next pending.
    3. Includes fixes_applied in output for caller visibility.
    """
    # Mutual exclusion: a wave in flight owns this track — wave-abort/wave-finalize
    # are its recovery paths, not the serial recover spine.
    wave = _active_wave(track_dir)
    if wave:
        emit(dict(status="wave_active", phase=wave.get("phase")), "recover", compact)
        return

    state, fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        result = dict(status="error", errors=verrors)
        emit(result, "recover", compact)
        return

    if fixes:
        print(f"Recover auto-fixed {len(fixes)} issue(s): {'; '.join(fixes)}", file=sys.stderr)

    pi = state.get("current_phase_index", 0)
    ti = state.get("current_task_index", 0)
    si = state.get("current_subtask_index")

    if pi < 1 or ti < 1:
        # No active task → any leftover result.json is an orphan from a crashed
        # finalize. Reap it so the next dispatch-finalize can't misread a stale
        # file as this run's result.
        _clear_stale_result(track_dir)
        _dispatch_inflight_clear_all(track_dir)  # cursor invalid → reap any stale marker
        _emit_no_active_or_decision(track_dir, state, fixes, compact)
        return

    try:
        task = state["phases"][pi - 1]["tasks"][ti - 1]
    except IndexError:
        _clear_stale_result(track_dir)
        _dispatch_inflight_clear_all(track_dir)  # cursor invalid → reap any stale marker
        _emit_no_active_or_decision(track_dir, state, fixes, compact)
        return

    # Resolve subtask or flat task
    if si is not None and "subtasks" in task and len(task["subtasks"]) > 0:
        si = min(si, len(task["subtasks"]))
        tgt = task["subtasks"][si - 1]
        name = tgt["name"]
        ttype = "subtask"
    else:
        tgt = task
        name = task["name"]
        ttype = "flat"

    # Reap an orphaned result.json when the resolved target is not in_progress
    # (a stale lock just reaped to pending by ensure_healthy, or a terminal
    # task). dispatch-finalize owns the in_progress case — leave that result.json
    # for it to consume.
    if tgt.get("status") != "in_progress":
        _clear_stale_result(track_dir)
        # No active dispatch for this task anymore → drop a stale inflight
        # marker too, so a crashed run can't leave the dedupe hook guarding a
        # task that's no longer in flight.
        _dispatch_inflight_clear(track_dir, pi, ti, si)

    # Best-effort: recover missing git notes for completed tasks
    _recover_git_notes(track_dir, state)

    # For subtasks, inherit parent tags when subtask has none
    sub_tags = extract_tags(name)
    if ttype == "subtask" and not sub_tags:
        sub_tags = extract_tags(task["name"])

    result = dict(
        status=tgt.get("status", "pending"),
        phase=pi, task=ti, subtask=si,
        name=name, type=ttype,
        retry_count=tgt.get("retry_count", 0),
        max_retries=task_max_retries(tgt),
        last_failure_summary=tgt.get("last_failure_summary"),
        tags=sub_tags,
        execution_mode=state.get("execution_mode", "interactive"),
    )
    if fixes:
        result["fixes_applied"] = fixes

    # Check if any phase needs a checkpoint — scan ALL phases
    checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
    if checkpoint_pending is not None:
        result["phase_checkpoint_pending"] = checkpoint_pending

    # Interactive failed+exhausted → attach a pre-computed Retry/Skip/Block
    # decision blob (the #1 transducer: the orchestrator stops judging
    # retry-exhaustion and constructing bash — it AskUserQuestion → run
    # decision.commands[choice] verbatim). Continuous mode is left to
    # skip-analyst (§3.6), so no blob there.
    if (result.get("status") == "failed"
            and result.get("retry_count", 0) >= result.get("max_retries", 0)
            and result.get("execution_mode") == "interactive"):
        result["decision"] = _failed_task_decision(
            track_dir, pi, ti, si, name, result["retry_count"])

    emit(result, "recover", compact)


def _clear_stale_result(track_dir):
    """Remove any prior attempt's result.json before dispatching a fresh run.

    dispatch-finalize reads ``.conductor/result.json`` on existence (not
    freshness), so an agent that stops without writing a fresh file could
    otherwise leave a STALE result from a previous attempt/retry read as the
    current task's result. Clearing here guarantees the next result.json is
    genuinely from this run. ``missing_ok=True`` makes it a no-op on a fresh task.
    """
    (conductor_dir(track_dir) / "result.json").unlink(missing_ok=True)


def _dispatch_inflight_write(track_dir, pi, ti, si, start_sha, written_at_iso):
    """Stamp the inflight dispatch marker (see lib/dispatch_inflight). Thin
    wrapper so callers stay within the ``_``-prefixed private-helper convention.

    Bumps the dispatch ``gen`` (read-modify-write): a re-dispatch of the same
    ``(phase, task, subtask)`` stamps a NEW generation, so the dedupe hook sees
    a fresh in-flight state rather than the stale one from the prior dispatch.
    First write for a key → gen 1 (``read_gen`` returns 0 when no marker).

    Atomicity
    ---------
    The read-modify-write is wrapped in ``dispatch_lock.acquire`` (an exclusive
    ``fcntl.flock`` on ``<track_dir>/.conductor/.dispatch.lock``). Under the
    normal synchronous model the lock is uncontended and held for microseconds;
    under background-mode concurrency it serializes the bump so two racing
    ``prepare_dispatch`` calls cannot both read ``gen=N`` and stamp ``gen=N+1``
    (which would collapse two fresh dispatches into one in the dedupe hook's
    ``gen``-disambiguation). Fail-open: a lock error leaves this unguarded, and
    the marker + git-HEAD predicate remains the safety net."""
    with _dispatch_lock.acquire(track_dir):
        prev_gen = _inflight.read_gen(track_dir, pi, ti, si)
        _inflight.write(track_dir, pi, ti, si, start_sha, written_at_iso,
                        gen=prev_gen + 1)


def _dispatch_inflight_clear(track_dir, pi, ti, si):
    """Clear the inflight dispatch marker for a task (see lib/dispatch_inflight)."""
    _inflight.clear(track_dir, pi, ti, si)


def _dispatch_inflight_clear_all(track_dir):
    """Clear every inflight marker in this track (crash-recovery; see lib/dispatch_inflight)."""
    _inflight.clear_all(track_dir)


def _emit_redispatch_telemetry(track_dir, pi, ti, si):
    """Emit a ``re-dispatch`` dispatch-lifecycle event for the interrupted branch.

    ``cmd_step`` re-dispatches a task *without* finalize when HEAD is still the
    Start commit and no result.json landed — i.e. the prior dispatch never ran
    (interrupted / pre-empted before any work). That re-dispatch burns no retry
    and is otherwise indistinguishable from a fresh dispatch in the log. This
    makes it visible: a grep for ``event=re-dispatch`` surfaces an
    interrupted→re-dispatch loop (``re-dispatch … re-dispatch … re-dispatch``
    with no intervening ``finalize``) — the signature that would justify routing
    interrupted-dispatch to FAILURE rather than silently retrying.

    Telemetry-only: no control-flow change, and best-effort (lazy import + broad
    except) so a logging fault can never perturb the spine.

    The ``gen`` recorded is the PRIOR (interrupted) dispatch's generation — read
    before ``_step_emit_dispatch`` re-stamps the marker with gen+1. A run of
    ``re-dispatch gen=1``, ``gen=2``, ``gen=3`` (each one higher than the last)
    is the loop signature: successive interruptions of fresh dispatches.
    """
    try:
        from lib import dispatch_lifecycle as lifecycle
        gen = _inflight.read_gen(track_dir, pi, ti, si)
        lifecycle.emit(
            event="re-dispatch", session="-",
            agent="task-executor",
            phase=pi, task=ti, subtask=si,
            gen=str(gen) if gen else "-",
        )
    except Exception:
        pass


def prepare_dispatch(track_dir):
    """Compute-only half of ``dispatch-prepare`` — returns the result dict, no emit.

    Extracted so ``cmd_step`` (Rail B) can compose the prepare step (find next,
    route, lock, sync-plan, start-commit) without re-implementing its sequencing.
    The CLI wrapper ``cmd_dispatch_prepare`` is now a thin ``emit()`` over this.

    The stderr ``fixes`` notice is kept here (not in the wrapper) so every caller
    — CLI and ``cmd_step`` alike — reports auto-fixes identically. It is a benign
    side effect; only the returned dict is the contract.
    """
    # Mutual exclusion: a wave in flight owns this track — refuse the serial spine.
    wave = _active_wave(track_dir)
    if wave:
        return dict(action="wave_active", phase=wave.get("phase"))

    # Auto-fix state (includes plan reconciliation + all other fixes)
    state, fixes, _ = ensure_healthy(track_dir)
    if state is None:
        return dict(action="error", error="Cannot read track-state.json")
    if fixes:
        print(f"Dispatch-prepare auto-fixed {len(fixes)} issue(s): {'; '.join(fixes)}", file=sys.stderr)

    # Find next task directly — avoid calling cmd_next() which prints to stdout,
    # causing duplicate JSON output that confuses the orchestrator.
    execution_mode = state.get("execution_mode", "interactive")
    nxt = _find_next_task(state)
    nxt["execution_mode"] = execution_mode

    if nxt.get("phase", 0) < 1:
        return dict(action="done")
    pi, ti = nxt["phase"], nxt["task"]
    si = nxt.get("subtask")
    name = nxt.get("name", "?")
    tags = nxt.get("tags", [])

    # Route
    if nxt.get("type") == "parent-complete":
        sha = _last_subtask_sha_from_state(track_dir, pi, ti)
        action = "parent-complete"
    elif nxt.get("type") == "parent-stuck":
        sha = _last_subtask_sha_from_state(track_dir, pi, ti)
        action = "parent_stuck"
    else:
        sha = None
        category = _classify_task(tags)
        if category == "manual":
            action = "defer" if execution_mode == "continuous" else "manual_task"
        elif category == "explore":
            action = "explore"
        else:
            action = "execute"

    if action == "parent-complete":
        return dict(action=action, phase=pi, task=ti, name=name, sha=sha, next=nxt)
    if action == "parent_stuck":
        return dict(action=action, phase=pi, task=ti, name=name, sha=sha,
                    execution_mode=execution_mode, next=nxt)
    if action == "manual_task":
        # Interactive: surface to the user — no lock (manual tasks aren't executed).
        return dict(action="manual_task", phase=pi, task=ti, name=name,
                    execution_mode=execution_mode, next=nxt)
    if action == "defer":
        # Auto-defer (continuous): lock not needed
        return dict(action="defer", phase=pi, task=ti, name=name,
                    reason="Deferred: manual task requires human verification", next=nxt)

    # Lock + sync-plan for explore/execute
    # Detect resume: if the target is already in_progress, this is a recovery
    # from a previous interrupted run — avoid duplicate "Start task" commits.
    # ALSO treat it as a resume when HEAD is already a Start-task commit, even
    # if the stale-lock reaper flipped status to pending (validate._fix_stale_lock
    # reaps in_progress tasks older than STALE_LOCK_SECONDS to pending so a
    # killed session unblocks). A HEAD that's still the Start commit means the
    # prior dispatch produced no implementation work — re-dispatching it must NOT
    # emit a second Start commit (mirrors cmd_step's interrupted-dispatch
    # discriminator at the _is_start_commit gate). retry_count survives the reap
    # + re-lock (both preserve it), so this path counts correctly against budget.
    tgt = target(state, pi, ti, si)
    is_resume = tgt.get("status") == "in_progress" or _is_start_commit(track_dir)

    # Clear any result.json left by a prior attempt so finalize can't read it
    # as this run's result (see _clear_stale_result).
    _clear_stale_result(track_dir)
    _do_lock(track_dir, pi, ti, si)
    synced = _do_sync_plan(track_dir)

    if not is_resume:
        # Start-task commit is performed HERE (deterministic), not by the
        # orchestrator. Previously a <commit_msg> placeholder was emitted for
        # the orchestrator to paste into a shell `git commit -m "…"` line, and
        # a mis-substitution could turn it into a bash syntax error
        # (e.g. `git commit -m ()`). Mirroring dispatch-finalize's internal
        # completion commit keeps both lifecycle ends in code. _git_commit
        # stages only conductor-managed files and commits only if something is
        # staged (no --allow-empty: the start commit is a sentinel detected by
        # message pattern at recovery time, not a SHA the machinery requires).
        _git_commit(track_dir, f"chore(conductor): Start task '{name}' [P{pi}.T{ti}]")

    # Stamp the inflight marker so the PreToolUse:Agent dedupe hook can deny a
    # second spawn for this same task while this dispatch is still in flight
    # (HEAD == start_sha, no result.json yet). Captured AFTER the Start commit
    # so start_sha is the commit HEAD actually sits on — on the is_resume path
    # no new commit is written, so this is the prior Start commit (correct:
    # that's the SHA the hook compares the live HEAD against). See
    # lib/dispatch_inflight.
    _dispatch_inflight_write(track_dir, pi, ti, si, _git_head_sha(track_dir), now_iso())

    return dict(action=action, phase=pi, task=ti, subtask=si, name=name,
                tags=tags, sync_count=synced, is_resume=is_resume,
                retry_count=tgt.get("retry_count", 0),
                max_retries=task_max_retries(tgt),
                last_failure_summary=tgt.get("last_failure_summary"),
                execution_mode=nxt.get("execution_mode", "interactive"),
                next=nxt)


def cmd_dispatch_prepare(track_dir, compact=True):
    """Lock + sync-plan + emit commit message template. Thin emit wrapper."""
    emit(prepare_dispatch(track_dir), "dispatch-prepare", compact)


def _last_subtask_sha_from_state(track_dir, pi, ti):
    """Get last completed subtask SHA for parent-complete."""
    state = load(track_dir)
    try:
        parent = state["phases"][pi - 1]["tasks"][ti - 1]
        return _last_subtask_sha(parent)
    except (IndexError, KeyError):
        return ""


def _synthesize_result_from_state(track_dir):
    """Build a result dict from the currently locked task in track-state.json.

    Used when .conductor/result.json is missing (agent exhausted turns or lost
    context before reaching Section 6.0). Derives phase/task/subtask/task_name
    from the current_*_index fields set by dispatch-prepare's _do_lock call.

    Smart detection:
    - If agent committed implementation code → SUCCESS with HEAD SHA
    - If only uncommitted changes → FAILURE with partial-work details
    - If no work at all → FAILURE with "agent produced no result"
    The FAILURE path ensures handoff records are written so retry agents get context.
    """

    state = load(track_dir)
    pi = state.get("current_phase_index", 0)
    ti = state.get("current_task_index", 0)

    if pi < 1 or ti < 1:
        return None

    try:
        task = state["phases"][pi - 1]["tasks"][ti - 1]
    except (IndexError, KeyError):
        return None

    si = state.get("current_subtask_index")
    if si is not None:
        try:
            tgt = task["subtasks"][si - 1]
            name = tgt["name"]
        except (IndexError, KeyError):
            tgt = task
            name = task["name"]
            si = None
    else:
        tgt = task
        name = task["name"]

    if tgt.get("status") != "in_progress":
        return None

    head_sha = _git_head_sha(track_dir) or ""

    # Detect whether the agent produced any real implementation work.
    # If HEAD is still a "Start task" commit, the agent made no commits.
    start_only = _is_start_commit(track_dir)
    uncommitted = _git_uncommitted_files(track_dir)

    if start_only:
        # No implementation commit → FAILURE so callers write handoff/retry context.
        if uncommitted:
            what = ", ".join(uncommitted[:10])
            if len(uncommitted) > 10:
                what += f" (+{len(uncommitted) - 10} more)"
            what_was_done = f"Partial work left uncommitted: {what}"
            suggested = "Review and salvage uncommitted changes before retry"
        else:
            what_was_done = "No implementation work found in working tree"
            suggested = "Re-dispatch from scratch"

        return dict(
            status="FAILURE",
            commit_sha="N/A",
            summary="Task executor did not produce result (exhausted turns or lost context)",
            failure_detail={
                "what_was_done": what_was_done,
                "failure_reason": "Agent stopped without writing result.json or committing code",
                "suggested_next_step": suggested,
            },
            phase=pi,
            task=ti,
            subtask=si,
            task_name=name,
            attempt=tgt.get("retry_count", 0) + 1,
            max_retries=task_max_retries(tgt),
        )
    else:
        # Agent committed implementation code but forgot to write result.json.
        # This is a legitimate completion — synthesize SUCCESS.
        return dict(
            status="SUCCESS",
            commit_sha=head_sha,
            summary="Synthesized from locked task (result.json missing, implementation commit found)",
            phase=pi,
            task=ti,
            subtask=si,
            task_name=name,
            attempt=tgt.get("retry_count", 0) + 1,
            max_retries=task_max_retries(tgt),
        )


def _resolve_finalize_target(track_dir, result_path):
    """Load result.json (or synthesize from the locked task), apply --override
    patches, and resolve the (p, t, s) target.

    The result-prep half of dispatch-finalize, extracted so the commit/note
    sequence below reads as straight-line success/failure handling. Returns
    ``(result_dict, p, t, s, task_name, status)`` or ``None`` when there is no
    result file AND no locked task to synthesize from.

    Index resolution prefers the locked in_progress indices from
    track-state.json (set by dispatch-prepare) over result.json's, so a stale
    result can't misroute finalization — but only when the locked target is
    actually in_progress (else keep result.json's defaults).
    """
    if result_path.exists():
        with open(result_path) as f:
            r = json.load(f)
    else:
        # Fallback: synthesize result from locked task in track-state.json
        r = _synthesize_result_from_state(track_dir)
        if r is None:
            return None

    # Apply overrides: merge CLI-supplied values into result (only if empty/falsy)
    overrides = flag(sys.argv[3:], "--override")
    if overrides:
        for pair in overrides.split(","):
            if "=" not in pair:
                continue
            k, v = pair.split("=", 1)
            if not r.get(k):
                r[k] = v

    status = r.get("status", "").upper()
    p = str(r.get("phase") if r.get("phase") is not None else "")
    t = str(r.get("task") if r.get("task") is not None else "")
    s = str(r.get("subtask")) if r.get("subtask") is not None else None

    state = load(track_dir)
    locked_pi = state.get("current_phase_index")
    locked_ti = state.get("current_task_index")
    locked_si = state.get("current_subtask_index")
    if locked_pi is not None and locked_ti is not None and locked_pi >= 1 and locked_ti >= 1:
        try:
            locked_tgt = state["phases"][locked_pi - 1]["tasks"][locked_ti - 1]
            if locked_si is not None:
                locked_tgt = locked_tgt["subtasks"][locked_si - 1]
            if locked_tgt.get("status") == "in_progress":
                p = str(locked_pi)
                t = str(locked_ti)
                s = str(locked_si) if locked_si is not None else None
        except (IndexError, KeyError):
            pass  # keep result.json defaults

    task_name = r.get("task_name", "unknown")
    return r, p, t, s, task_name, status


def _finalize_task(track_dir, p, t, s, r, task_name, status):
    """Run the SUCCESS/FAILURE state transition + conductor commit + git note.

    Returns ``(result_dict, clear_result_json)``. The dict is the envelope the
    caller emits under its own command name; ``clear_result_json`` tells the
    caller whether to delete the source ``result.json`` (True on a committed
    transition or a stale-index error; False to preserve for manual recovery).

    Factored out of :func:`cmd_dispatch_finalize` so :func:`cmd_wave_finalize`
    can reuse the EXACT transition with explicit ``(p, t, s)`` — the serial
    resolver (:func:`_resolve_finalize_target`) routes by the singleton cursor,
    which is unset under a wave. The caller owns ``result.json`` cleanup (and,
    for waves, worktree teardown) so this function stays pure transition logic.
    """
    if status == "SUCCESS":
        code_sha = _normalize_sha(r.get("commit_sha", ""))
        try:
            parent_completed, state = _do_complete(track_dir, p, t, s, code_sha)
        except ValueError as e:
            # Parent has non-terminal subtasks — retryable, keep result.json
            return dict(error=str(e), status="error"), False
        except IndexError as e:
            # Stale indices — unrecoverable, clean up result.json
            return dict(error=str(e), status="error"), True

        _store_evidence(state, track_dir, p, t, s, r)

        synced = _do_sync_plan(track_dir, state)
        _append_execution_record(track_dir, p, t, s, r, state)
        # Spec deviations are recorded in handoff.md above; the legacy
        # issues.md mirror was removed.

        # Create conductor commit to get a unique SHA per task/subtask
        commit_msg = f"chore(conductor): Complete '{task_name}' [{code_sha}]"
        committed = _git_commit_ensured(track_dir, commit_msg)
        if not committed:
            print(f"WARNING: conductor commit failed for '{task_name}'",
                  file=sys.stderr)
        final_sha = code_sha
        if committed:
            final_sha = _git_head_sha(track_dir) or code_sha
        # If subagent provided no code SHA but conductor committed, store conductor SHA
        if committed and not code_sha and final_sha:
            state = _update_task_sha(track_dir, p, t, s, final_sha)

        # Deduplicate: if SHA collides with a sibling subtask, force a unique SHA
        if _has_sibling_sha(state, p, t, s, final_sha):
            _git_commit(track_dir, f"chore(conductor): Dedup '{task_name}' [{final_sha}]",
                        allow_empty=True)
            dedup_sha = _git_head_sha(track_dir)
            if dedup_sha and dedup_sha != final_sha:
                final_sha = dedup_sha
                state = _update_task_sha(track_dir, p, t, s, final_sha)

        # Write git note using the SHA stored in track-state.json (not the conductor commit SHA).
        # This ensures `git notes show <plan_sha>` works since plan.md shows the same SHA.
        try:
            note_tgt = target(state, int(p), int(t), int(s) if s is not None else None)
            note_sha = note_tgt.get("commit_sha", "") or final_sha
        except (IndexError, KeyError):
            note_sha = final_sha
        r["commit_sha"] = note_sha
        _write_git_note(track_dir, r, state)

        # Advisory gates on the hot path (WARN-only — matches process-result via
        # the shared _advisory_gates helper). Computed AFTER _do_complete so a
        # gate status never blocks completion; real teeth stay at the commit-time
        # F2 ask gate + on-batch-complete F3 probe. The AC-integrity snapshot is
        # computed once here (ac_integrity + ears gates share it).
        tags = _extract_tags_for_task(state, p, t)
        (coverage_gate, tdd_gate, ac_integrity_gate, ears_gate,
         tc_consistency_gate, cov_pct) = _advisory_gates(
             track_dir, r, tags, code_sha)
        result = dict(status="success", sha=final_sha, code_sha=code_sha,
                      parent_completed=parent_completed,
                      deviations=len(r.get("spec_deviation_detail", [])),
                      sync_count=synced, committed=committed,
                      coverage_gate=coverage_gate, tdd_gate=tdd_gate,
                      ac_integrity_gate=ac_integrity_gate,
                      ears_gate=ears_gate,
                      tc_consistency_gate=tc_consistency_gate,
                      phase=int(p), task=int(t),
                      subtask=(int(s) if s is not None else None))
        # Telemetry: implementation files left uncommitted after a SUCCESS. The
        # PreToolUse clean-tree hook should make this 0 on the flag channel; a
        # non-zero value means the result was written via --data/stdin (which
        # the hook can't inspect) — a monitoring signal, surfaced so the leak
        # is visible in finalize logs instead of silent. Uses the shared
        # lib.git_utils helper (with --untracked-files=all + the full conductor
        # namespace exclusion) so it agrees with the hook's classification.
        # See git_ops._git_commit for why finalize never stages these itself.
        result["stranded_files_count"] = len(implementation_uncommitted_files(track_dir))
        if cov_pct is not None:
            result["coverage_pct"] = cov_pct

        # Check if ANY phase needs checkpoint after this completion
        checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
        if checkpoint_pending is not None:
            result["phase_checkpoint_pending"] = checkpoint_pending

        # clear_result_json mirrors the original inline behavior: delete on a
        # committed transition, preserve for manual recovery on commit failure.
        return result, bool(committed)

    if status == "FAILURE":
        summary = r.get("summary", "")
        retry_count, state = _do_fail(track_dir, p, t, s, summary)
        synced = _do_sync_plan(track_dir, state)
        _append_execution_record(track_dir, p, t, s, r, state)

        commit_msg = f"chore(conductor): '{task_name}' failed (attempt {retry_count})"
        # Use _git_commit_ensured (allow-empty fallback) to mirror the SUCCESS
        # path. The failure has already been fully ingested into track-state.json
        # + handoff above, and the task is no longer in_progress, so a
        # preserved result.json here would only surface as an "orphaned result.json"
        # complaint on the next Stop hook. Ensured commit unlinks it reliably; the
        # genuine-git-breakage case still preserves it (both attempts return False).
        committed = _git_commit_ensured(track_dir, commit_msg)
        return (dict(status="failure", retry_count=retry_count, summary=summary,
                     sync_count=synced, committed=committed,
                     phase=int(p), task=int(t),
                     subtask=(int(s) if s is not None else None)),
                bool(committed))

    return dict(error=f"Unknown status: {status}"), False


def finalize_dispatch(track_dir):
    """Compute-only half of ``dispatch-finalize`` — returns the result dict, no emit.

    Extracted so ``cmd_step`` (Rail B) can run the finalize step (resolve result,
    completion/failure mutation, conductor commit, result.json cleanup) inline and
    route on its outcome in the same call. ``--override`` still works: the underlying
    ``_resolve_finalize_target`` reads ``sys.argv`` exactly as before.
    """
    result_path = conductor_dir(track_dir) / "result.json"

    resolved = _resolve_finalize_target(track_dir, result_path)
    if resolved is None:
        return dict(error="No result file at .conductor/result.json and no locked task in state")
    if not result_path.exists():
        print("NOTE: result.json missing — synthesized from locked task state",
              file=sys.stderr)
    r, p, t, s, task_name, status = resolved

    result, clear_result = _finalize_task(track_dir, p, t, s, r, task_name, status)

    # result.json cleanup is the serial caller's job (wave-finalize manages its
    # own worktree result.json during teardown). _finalize_task signals whether
    # the source result.json should be deleted vs preserved for manual recovery.
    if clear_result:
        result_path.unlink(missing_ok=True)
    elif result.get("status") != "error":
        print("WARNING: result.json preserved due to commit failure", file=sys.stderr)

    # A dispatch returned a verdict → it is no longer in flight. Clear the
    # inflight marker so the dedupe hook stops guarding this task. On an error
    # outcome leave it (the task is still locked + unfinished; the hook keeps
    # guarding until a real finalize advances state). See lib/dispatch_inflight.
    if result.get("status") != "error":
        _dispatch_inflight_clear(track_dir, p, t, s)

    return result


def cmd_dispatch_finalize(track_dir, compact=True):
    """Process result + create conductor commit + sync-plan. Thin emit wrapper.

    Creates the conductor commit internally so each task/subtask gets a unique SHA.
    Accepts --override key=value to patch result fields before processing.
    When result.json is missing, synthesizes result from the locked task in state."""
    emit(finalize_dispatch(track_dir), "dispatch-finalize", compact)


# ---------------------------------------------------------------------------
# Rail B-min: `step` — a state-driven spine that collapses §2.0 recover +
# §3.0 dispatch routing into ONE leaf action per call. The orchestrator becomes
# a teleoperator: read `action`, do exactly that, call `step` again. See
# conductor/design/rail-b-step.md and skills/implement-step/SKILL.md.
# ---------------------------------------------------------------------------


# --------------------------------------------------------------------------- #
# Single source for subagent dispatch prompts (Rail A/B unification).
#
# ``build_dispatch_prompt`` is the ONE pure function that turns a dispatch action
# + shared state into the ``(agent, prompt)`` envelope every subagent receives.
# Both rails resolve to it: Rail B (``step``/``wave`` JSON's pre-assembled
# ``prompt`` field) calls it directly; Rail A (``skills/implement/SKILL.md``
# §3.2/§3.3/§3.4) fetches the same envelope by reading that JSON ``prompt``
# field rather than re-deriving the ``KEY=value`` lines in prose. Keeping it a
# deterministic pure function of (action, state) is what makes a locked step
# replay/resume byte-identical — the dynamic-workflow requirement that breaks
# the moment prompt-building is split across two mechanisms.
#
# The per-action builders below (``_build_executor``, ``_build_verifier``,
# ``_build_phase_checker``) are the bodies the legacy ``_step_assemble_*``
# wrappers delegate to. Adding a dispatch action = one ``elif`` arm + one
# ``_build_*`` body here; both rails pick it up.
# --------------------------------------------------------------------------- #


def _build_executor(track_dir, pre, attempt):
    """``dispatch_executor`` / ``dispatch_explorer`` envelope body.

    ``SUBTASK`` is emitted only when present (flat tasks omit the line); the
    agent is ``explorer`` for an explore-classified task, else ``task-executor``.
    """
    td = str(track_dir)
    lines = [f"TRACK_DIR={td}", f"PHASE={pre['phase']}", f"TASK={pre['task']}"]
    si = pre.get("subtask")
    if si is not None:
        lines.append(f"SUBTASK={si}")
    lines.append(f"NAME={pre.get('name', '?')}")
    if _classify_task(pre.get("tags", [])) == "explore":
        return "explorer", "\n".join(lines)
    lines.append(f"ATTEMPT={attempt}")
    lines.append(f"MAX_RETRIES={MAX_RETRIES}")
    return "task-executor", "\n".join(lines)


def resolve_phase_gate(track_dir, state, phase):
    """The single dispatch-side composition of a phase's checkpoint gate plan.

    The checkpoint decision consults three otherwise-scattered sources — the
    verifier set (#4, the workflow-shape axis joined to the verifier axis), the
    phase-verify directive (``plan_parse._extract_verify``, the verify-mode
    axis), and gate-group membership (``helpers._phase_gate_group_membership``,
    the cross-phase gate-groups axis). This composes them into one return so the
    dispatch checkpoint branch (and any future consumer) reads ONE chokepoint
    instead of three lookups tied together by prose.

    Returns a dict::

        {
          "verifiers":   ("ac-tracer", "test-runner", ...),  # the fan-out set
          "verify_modes": ["compile", ...] | [],              # directive modes
          "gate_group":   {"name": str|None, "is_terminal": bool},
        }

    Pure and fail-open: any lookup miss degrades to the safe default
    (standard pair / no directive / no group) rather than raising — dispatch
    must never deadlock over a malformed plan.md or registry. This is the
    *code* that was previously implicit in the checkpoint branch + prose in
    phase-checker.md; the agent's BINDING precedence prose (directive >
    gate-group terminal > migration branch, which governs *verdict handling*)
    is unchanged — this function governs *dispatch composition*, not verdicts.
    """
    from .workflow_shapes import resolve_shape, verifiers_for
    from .helpers import _phase_gate_group_membership
    from . import plan_parse

    shape = resolve_shape(state.get("workflow_shape"))
    verifiers = verifiers_for(shape)
    # Re-derive the directive from the raw heading (the plan_parse contract —
    # verify_modes is advisory metadata re-parsed at every read, NOT persisted).
    verify_modes = []
    try:
        plan_text = (Path(track_dir) / "plan.md").read_text(encoding="utf-8")
        m = re.search(rf"^##\s+Phase\s+{phase}\b.*$",
                      plan_text, re.MULTILINE)
        if m:
            # _extract_verify expects the heading text AFTER the `## Phase N:`
            # marker — pass the whole matched heading; it scans for the verify
            # comment and ignores stray `verify` inside AC/TC comments.
            verify_modes, _has, _fails = plan_parse._extract_verify(m.group(0))
    except (OSError, UnicodeDecodeError):
        pass
    gname, is_terminal = _phase_gate_group_membership(track_dir, phase)
    return {
        "verifiers": verifiers,
        "verify_modes": verify_modes,
        "gate_group": {"name": gname, "is_terminal": is_terminal},
    }


def _build_verifier_wave(track_dir, state, phase, verifiers=None):
    """The pre-assembled checkpoint verifier fan-out — one wave member per
    verifier the resolved workflow-shape fans out.

    The single source for both rails (``cmd_dispatch_next`` and
    ``_step_emit_dispatch_batch``): the verifier set comes from
    :func:`workflow_shapes.verifiers_for` (the third and fourth axes joined at
    the checkpoint — a project shape omitting ``test-runner`` simply doesn't fan
    it out), and each member's ``prompt`` is built by
    :func:`build_dispatch_prompt` / :func:`_build_verifier` (registry-driven
    field-set). Byte-identical to the pre-#4 hardcoded
    ``[ac-tracer, test-runner]`` pair for both shipped shapes (they declare that
    pair); a custom shape controls the set.

    ``verifiers`` lets a caller that already resolved the gate plan via
    :func:`resolve_phase_gate` pass the tuple through (avoids a double-resolve);
    when ``None`` it is resolved here.

    Read-only verifiers run on the main checkout (no worktree pinning, unlike
    wave members). ``name`` mirrors the wave member shape (a display label).
    """
    from .workflow_shapes import resolve_shape, verifiers_for
    from .verifier_profiles import agent_for
    if verifiers is None:
        shape = resolve_shape(state.get("workflow_shape"))
        verifiers = verifiers_for(shape)
    members = []
    for verifier in verifiers:
        agent = agent_for(verifier)
        members.append({
            "agent": agent,
            "name": verifier,
            "prompt": build_dispatch_prompt(
                "dispatch_batch", track_dir, state=state, phase=phase,
                agent=agent)[1],
        })
    return members


def _build_verifier(track_dir, state, phase, agent):
    """``dispatch_batch`` member envelope body for a read-only phase verifier
    (``ac-tracer`` / ``test-runner`` — or any verifier the resolved shape fans
    out via :func:`workflow_shapes.verifiers_for`).

    Registry-driven: the field-set each verifier needs is read from the
    verifier registry (:func:`verifier_profiles.field_set_for`) — NOT a
    hardcoded ``if agent == "test-runner"`` branch. Each token in the
    verifier's ``field_set`` resolves to one ``KEY=value`` line: ``TRACK_DIR``
    and ``TRACK_ID`` (the floor every registered verifier declares), and
    ``PHASE_INDEX`` (the checkpoint phase number — reporting-only, never a state
    index, per agents/test-runner.md §2.0). An unknown token is dropped
    (fail-open). An unknown VERIFIER (empty ``field_set``) falls back to the
    ``TRACK_DIR``+``TRACK_ID`` floor so dispatch never emits a bodyless envelope.
    Read-only verifiers run on the main checkout (no worktree pinning, unlike
    wave members).
    """
    td = str(track_dir)
    track_id = state.get("track_id", "")
    # The floor every verifier gets; emitted as a fallback for an unknown
    # verifier (empty field_set) and as the leading lines when the registry
    # field_set omits them.
    floor = [f"TRACK_DIR={td}", f"TRACK_ID={track_id}"]
    # Lazy import: verifier_profiles imports nothing from dispatch at module
    # load, but lazy keeps the boundary explicit and avoids any load-order
    # surprise in the hook-script import fan-out.
    from .verifier_profiles import field_set_for
    field_set = field_set_for(agent)
    if not field_set:
        return "\n".join(floor)
    lines, seen = [], set()
    for token in field_set:
        if token == "TRACK_DIR":
            value = f"TRACK_DIR={td}"
        elif token == "TRACK_ID":
            value = f"TRACK_ID={track_id}"
        elif token == "PHASE_INDEX":
            value = f"PHASE_INDEX={phase}"
        else:
            continue  # unknown token dropped (fail-open)
        if value not in seen:  # de-dup a registry row that lists a token twice
            seen.add(value)
            lines.append(value)
    # Guarantee the TRACK_DIR/TRACK_ID floor even if the registry row omitted it
    # (a verifier always needs to know where it runs).
    if not any(ln.startswith("TRACK_DIR=") for ln in lines):
        lines = [floor[0]] + lines
    if not any(ln.startswith("TRACK_ID=") for ln in lines):
        lines = [floor[1]] + lines
    return "\n".join(lines)


def _build_phase_checker(track_dir, state, phase, marker):
    """``dispatch_phase_checker`` envelope body (the synthesizer) from the fanned
    verifier verdicts stored in the marker.

    Emits ``AC_TRACE_GATE`` only when the ac-tracer FAILED, and
    ``AC_TRACE_N_UNGROUNDED`` only on warn (byte-for-byte the prose §3.2 Step-3
    it retires). Verdicts come from the marker (transcribed by the teleoperator
    from the two RESULT blocks), not re-derived — the read-only verifiers
    already ran.
    """
    td = str(track_dir)
    ac = marker.get("ac_verdict", "")
    lines = [
        f"TRACK_DIR={td}",
        f"TRACK_ID={state.get('track_id', '')}",
        f"PHASE_INDEX={phase}",
        f"EXECUTION_MODE={state.get('execution_mode', 'interactive')}",
        f"AC_TRACE_VERDICT={ac}",
    ]
    if ac == "FAILED" and marker.get("ac_gate"):
        lines.append(f"AC_TRACE_GATE={marker['ac_gate']}")
    if ac == "warn" and marker.get("ac_n_ungrounded") is not None:
        lines.append(f"AC_TRACE_N_UNGROUNDED={marker['ac_n_ungrounded']}")
    lines.append(f"L1_VERIFY_STATUS={marker.get('l1_status', '')}")
    if marker.get("l1_command"):
        lines.append(f"L1_VERIFY_COMMAND={marker['l1_command']}")
    return "\n".join(lines)


def build_dispatch_prompt(action, track_dir, *, pre=None, state=None, phase=None,
                          agent=None, attempt=0, marker=None):
    """The single pure entrypoint: ``(action, shared state) -> (agent, prompt)``.

    Resolves a dispatch action token to its pre-assembled ``(agent, prompt)``
    envelope. Every subagent prompt — explorer/task-executor serial dispatch,
    the ac-tracer/test-runner verifier fan-out, and the phase-checker synth —
    is built here, so both Rail A (prose) and Rail B (``step``/``wave`` JSON)
    resolve to one source. Keyword-only state args keep each action's required
    inputs explicit at the call site (``dispatch_executor`` needs ``pre``;
    ``dispatch_phase_checker`` needs ``marker``); missing inputs raise below.

    Returns ``(agent, prompt)`` for executor actions (the agent varies between
    explorer/task-executor) and ``(agent, prompt)`` for the rest where the agent
    is fixed by the action. Wrapping every return as a pair means the caller
    never has to special-case which actions know their agent up front.
    """
    if action in ("dispatch_executor", "dispatch_explorer"):
        if pre is None:
            raise ValueError("build_dispatch_prompt: 'pre' is required for "
                             f"action {action!r}")
        return _build_executor(track_dir, pre, attempt)
    if action == "dispatch_batch":
        # The agent must be a registered verifier. Widen to the resolved vocab
        # (ac-tracer/test-runner + any project-overlay verifier); a name outside
        # it is a programming error at the call site (a shape fanning out an
        # undeclared verifier), so raise rather than silently emit garbage.
        from .verifier_profiles import VERIFIER_VOCAB
        if agent not in VERIFIER_VOCAB():
            raise ValueError("build_dispatch_prompt: 'agent' must be a "
                             "registered verifier "
                             f"({', '.join(VERIFIER_VOCAB())}) for dispatch_batch")
        return agent, _build_verifier(track_dir, state, phase, agent)
    if action == "dispatch_phase_checker":
        if marker is None:
            raise ValueError("build_dispatch_prompt: 'marker' is required for "
                             "dispatch_phase_checker")
        return "phase-checker", _build_phase_checker(track_dir, state, phase, marker)
    raise ValueError(f"build_dispatch_prompt: unknown dispatch action {action!r}")


def _step_assemble_prompt(track_dir, pre, attempt):
    """Build the ready-to-paste subagent prompt for explorer/task-executor.

    Thin wrapper over :func:`build_dispatch_prompt` — kept so the serial-spine
    call sites and their tests stay byte-for-byte. Pre-assembled in code (not by
    the model) so a weak orchestrator can't fumble field interpolation — the
    step envelope's ``prompt`` is pasted verbatim into the Agent dispatch.
    """
    return build_dispatch_prompt("dispatch_executor", track_dir, pre=pre, attempt=attempt)


def _step_assemble_verifier_prompt(track_dir, state, phase, agent):
    """Build the ready-to-paste prompt for a read-only phase verifier
    (``ac-tracer`` / ``test-runner``).

    Thin wrapper over :func:`build_dispatch_prompt` (``dispatch_batch`` member).
    Read-only verifiers run on the main checkout (no worktree pinning, unlike
    wave members). Mirrors ``_wave_assemble_member_prompt`` (wave spine).
    """
    _, prompt = build_dispatch_prompt("dispatch_batch", track_dir, state=state,
                                     phase=phase, agent=agent)
    return prompt


# --------------------------------------------------------------------------- #
# Shared tolerant-JSON-marker core. ``phase-checkpoint``, ``skip-analysis``, and
# ``failure-analysis`` are all single-file-per-track verdict-on-disk markers with
# byte-identical read/write/clear semantics — only the filename differs. The core
# below is parameterized by the per-family ``_path(track_dir)`` function so each
# family keeps its named helpers (call sites + tests) while the bodies live once.
# Mirrors ``new_track.py``'s tolerant read/write helpers and lifecycle.
# --------------------------------------------------------------------------- #
def _json_marker_read(path):
    """Tolerant reader: the marker dict, or ``None`` on missing/corrupt.

    ``None`` always means "treat as absent" so the routing branches on the marker
    without existence checks and a half-written file never crashes the spine.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (ValueError, OSError):
        pass
    return None


def _json_marker_write(path, data):
    """Write the whole marker dict; ``parents=True`` ensures ``.conductor/`` exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def _json_marker_clear(path):
    """Delete the marker; idempotent (a missing file is a no-op success)."""
    if path.exists():
        path.unlink()


# --------------------------------------------------------------------------- #
# Phase-checkpoint handshake marker (WM2 verdict-on-disk, step 2).
#
# ``_any_phase_needs_checkpoint`` only sees "checkpoint absent in plan.md" — it
# can't tell *verifiers fanned, awaiting the synthesizer* from *nothing fanned
# yet*. This transient marker discriminates the sub-states. The teleoperator
# transcribes the fanned verifier verdicts to ``cmd_phase_verdict`` (writes the
# marker), then the spine emits the phase-checker synth dispatch; after the synth
# returns, ``cmd_phase_checkpoint_review`` stamps the checkpoint (PASSED) or
# clears it (FAILED → teleoperator halts) and deletes the marker either way.
# Mirrors ``new_track.py``'s tolerant read/write helpers and lifecycle. Single
# file per track — only one checkpoint is pending at a time (the first needing
# one); the phase lives inside the JSON so a stale post-crash file self-clears.
# --------------------------------------------------------------------------- #
_PHASE_CP_MARKER = "phase-checkpoint.json"


def _phase_cp_marker_path(track_dir):
    """Pure path to the marker — deliberately does NOT mkdir, so a read never
    creates directories as a side effect (mirrors ``_nt_marker_path``)."""
    return Path(track_dir) / ".conductor" / _PHASE_CP_MARKER


def _phase_cp_read_marker(track_dir):
    """Tolerant reader: ``None`` on missing/corrupt (see ``_json_marker_read``)."""
    return _json_marker_read(_phase_cp_marker_path(track_dir))


def _phase_cp_write_marker(track_dir, data):
    """Write the whole marker dict (see ``_json_marker_write``)."""
    _json_marker_write(_phase_cp_marker_path(track_dir), data)


def _phase_cp_clear_marker(track_dir):
    """Delete the marker; idempotent (see ``_json_marker_clear``)."""
    _json_marker_clear(_phase_cp_marker_path(track_dir))


def _step_assemble_phase_checker_prompt(track_dir, state, phase, marker):
    """Build the ready-to-paste prompt for ``conductor:phase-checker`` (the
    synthesizer) from the fanned verifier verdicts stored in the marker.

    Thin wrapper over :func:`build_dispatch_prompt` (``dispatch_phase_checker``).
    Adjacent to ``_step_assemble_verifier_prompt`` (the fan-out prompts). Verdicts
    come from the marker (transcribed by the teleoperator from the two RESULT
    blocks), not re-derived — the read-only verifiers already ran.
    """
    _, prompt = build_dispatch_prompt("dispatch_phase_checker", track_dir,
                                     state=state, phase=phase, marker=marker)
    return prompt


def shape_allows(track_dir, agent, state=None):
    """Resolve the active workflow-shape and decide if ``agent`` is in its nodes.

    The third axis made load-bearing: a track's ``workflow_shape`` (from
    ``track-state.json``, default ``"default"``) declares which dispatch agents
    its topology runs. This returns ``(allowed, shape_name)`` so the caller can
    emit a ``shape_violation`` disclosure when an agent is outside the shape —
    load-bearing AND visible without hard-blocking the existing rich flow
    (failure-analyst / refuter / skip-analyst are recovery leaves, not shape
    nodes, so callers gate the constraint on the core dispatch actions only).

    ``state`` is passed through (avoiding a re-load when the caller already has
    it); when ``None`` the track-state is loaded here.

    Note: this consults the shape's ``nodes`` (the SPINE topology) only, and is
    called solely from the ``dispatch`` leaf (executor/explorer). Checkpoint
    verifiers (``ac-tracer`` / ``test-runner``) never pass through here — they
    are checkpoint *children* governed by :func:`verifiers_for`, not spine nodes,
    so a phase-checker dispatching its verifiers never trips ``shape_violation``.
    """
    from .workflow_shapes import resolve_shape, nodes_for
    if state is None:
        state = load(track_dir)
    shape = resolve_shape(state.get("workflow_shape"))
    return agent in nodes_for(shape), shape


# --------------------------------------------------------------------------- #
# skip_analyze handshake (WM2 verdict-on-disk, step 3). Two agents — skip-analyst,
# then conditionally refuter — same marker shape as the phase-checkpoint handshake
# (the refuter prompt embeds skip-analyst's reasoning, so it can't be pre-assembled
# before that verdict is on disk). The spine routes between the two agents and owns
# the route judgment (skip / halt-for-human); the teleoperator only transcribes.
# Fires only in continuous mode (interactive uses the `ask` failed-task blob).
# --------------------------------------------------------------------------- #
_SKIP_ANALYSIS_MARKER = "skip-analysis.json"


def _skip_analysis_marker_path(track_dir):
    """Pure path — deliberately no mkdir (mirrors ``_phase_cp_marker_path``)."""
    return Path(track_dir) / ".conductor" / _SKIP_ANALYSIS_MARKER


def _skip_analysis_read_marker(track_dir):
    """Tolerant reader: ``None`` on missing/corrupt (see ``_json_marker_read``)."""
    return _json_marker_read(_skip_analysis_marker_path(track_dir))


def _skip_analysis_write_marker(track_dir, data):
    """Write the whole marker dict (see ``_json_marker_write``)."""
    _json_marker_write(_skip_analysis_marker_path(track_dir), data)


def _skip_analysis_clear_marker(track_dir):
    """Delete the marker; idempotent (see ``_json_marker_clear``)."""
    _json_marker_clear(_skip_analysis_marker_path(track_dir))


# --------------------------------------------------------------------------- #
# failure_analyze handshake — same marker shape as skip_analyze (WM2 verdict-on-
# disk). failure-analyst is a read-only diagnostic dispatched in continuous mode
# (before the final retry, and when skip-analyst returns retry_with_modification)
# to classify WHY a task keeps failing and pick a materially different next
# action. The spine owns the category→action route judgment in code
# (``_step_route_failure_analysis``); the teleoperator only transcribes the
# verdict. See agents/failure-analyst.md for the taxonomy.
# --------------------------------------------------------------------------- #
_FAILURE_ANALYSIS_MARKER = "failure-analysis.json"

# Verdict enums (mirrors _SKIP_RECOMMENDATIONS). The category taxonomy is the
# analyst's diagnostic classification; the recommendation is the action it asks
# the spine to take.
_FAILED_CATEGORIES = (
    "deterministic_bug", "spec_plan_defect", "context_budget",
    "environmental", "stuck",
)
_FAILED_RECOMMENDATIONS = ("retry_modified", "replan", "decompose", "escalate")


def _failure_analysis_marker_path(track_dir):
    """Pure path — deliberately no mkdir (mirrors ``_skip_analysis_marker_path``)."""
    return Path(track_dir) / ".conductor" / _FAILURE_ANALYSIS_MARKER


def _failure_analysis_read_marker(track_dir):
    """Tolerant reader: ``None`` on missing/corrupt (see ``_json_marker_read``)."""
    return _json_marker_read(_failure_analysis_marker_path(track_dir))


def _failure_analysis_write_marker(track_dir, data):
    """Write the whole marker dict (see ``_json_marker_write``)."""
    _json_marker_write(_failure_analysis_marker_path(track_dir), data)


def _failure_analysis_clear_marker(track_dir):
    """Delete the marker; idempotent (see ``_json_marker_clear``)."""
    _json_marker_clear(_failure_analysis_marker_path(track_dir))


# Modified-guidance marker — the B.5 bridge from failure-analyst's ``modification``
# verdict to the retrying task-executor. Written by ``_step_route_failure_analysis``
# on a ``retry_modified`` verdict, consumed + cleared by the SubagentStart hook
# (``on-subagent-start.py`` appends it as a ``[Conductor Modified Retry]`` block,
# mirroring the existing retry-context injection). Keyed by phase/task/subtask so a
# concurrent sibling task's retry can't read another's guidance.
def _modified_guidance_path(track_dir, pi, ti, si):
    sub = f"-{si}" if si is not None else ""
    return Path(track_dir) / ".conductor" / f".modified-guidance-{pi}-{ti}{sub}.md"


def _modified_guidance_write(track_dir, pi, ti, si, modification, root_cause=None):
    cdir = Path(track_dir) / ".conductor"
    cdir.mkdir(parents=True, exist_ok=True)
    payload = modification or ""
    if root_cause:
        payload = f"Root cause: {root_cause}\n\nModified approach:\n{payload}"
    _modified_guidance_path(track_dir, pi, ti, si).write_text(payload)


def _modified_guidance_read(track_dir, pi, ti, si):
    path = _modified_guidance_path(track_dir, pi, ti, si)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


def _modified_guidance_clear(track_dir, pi, ti, si):
    path = _modified_guidance_path(track_dir, pi, ti, si)
    if path.exists():
        path.unlink()


def _step_assemble_failure_analyst_prompt(track_dir, state, pi, ti, si, name):
    """Pre-assemble the ``conductor:failure-analyst`` prompt.

    Mirrors ``_step_assemble_skip_analyst_prompt`` (parent-scoped, no subtask
    line) and adds ``RETRY_COUNT`` + the per-task ``MAX_RETRIES`` ceiling so the
    analyst knows how much budget remains.
    """
    td = str(track_dir)
    # Resolve the failing task's per-task ceiling (A: max_retries override).
    ceiling = MAX_RETRIES
    try:
        tgt = target(state, int(pi), int(ti), int(si) if si is not None else None)
        ceiling = task_max_retries(tgt)
        retry_count = tgt.get("retry_count", 0)
    except (IndexError, KeyError, TypeError, ValueError):
        retry_count = 0
    return "\n".join([
        f"TRACK_DIR={td}",
        f"TRACK_ID={state.get('track_id', '')}",
        f"PHASE_INDEX={pi}",
        f"TASK_INDEX={ti}",
        f"TASK_NAME={name}",
        f"RETRY_COUNT={retry_count}",
        f"MAX_RETRIES={ceiling}",
    ])


def _step_assemble_skip_analyst_prompt(track_dir, state, pi, ti, si, name):
    """Pre-assemble the ``conductor:skip-analyst`` prompt (skills/implement §3.6).
    Parent-scoped (no subtask line), mirroring the prose."""
    td = str(track_dir)
    return "\n".join([
        f"TRACK_DIR={td}",
        f"TRACK_ID={state.get('track_id', '')}",
        f"PHASE_INDEX={pi}",
        f"TASK_INDEX={ti}",
        f"TASK_NAME={name}",
    ])


def _step_assemble_refuter_prompt(track_dir, marker):
    """Pre-assemble the ``conductor:refuter`` skip-refute prompt from the marker
    (skills/implement §3.6). The CLAIM embeds skip-analyst's reasoning verbatim —
    assembled in code (not by the model) so a weak orchestrator can't fumble it.
    ``PROJECT_DIR`` is the repo root (``_git_rev_parse_toplevel``); the
    SUSTAINED-when-uncertain default makes this refute block-when-uncertain (the
    conservative direction for a skip, since skipping is the riskier action)."""
    td = str(track_dir)
    pi, ti = marker.get("phase"), marker.get("task")
    name = marker.get("name", "?")
    reasoning = marker.get("reasoning") or "(none given)"
    project_dir = _git_rev_parse_toplevel(track_dir) or td
    claim = (f'Skip-analyst recommended skipping task P{pi}T{ti} ("{name}"), '
             f'reasoning: "{reasoning}". Challenge framing: this skip is UNSAFE '
             f"— a dependency marked completed is only superficially done (its own "
             f"ACs not actually met), or the failure handoff describes a fix cheap "
             f"relative to the cost of skipping.")
    return "\n".join([
        f"PROJECT_DIR={project_dir}",
        "DOMAIN=skip",
        f"CLAIM={claim}",
        f"CONTEXT_PATHS={td}/plan.md {td}/track-state.json "
        f"{td}/.conductor/handoff/P{pi}T{ti}.md",
    ])


def _step_emit_dispatch_batch(track_dir, state, phase, execution_mode, compact):
    """Emit ``dispatch_batch`` — the pre-assembled ac-tracer + test-runner
    fan-out that retires the serial spine's ``phase_checkpoint`` non-spine
    hand-off for the verifier prompts. The synthesizer (``phase-checker``)
    dispatch + verdict routing STAYS in prose §3.2, where the orchestrator holds
    the verifier RESULT blocks in context (verdicts don't flow through disk).
    Mirrors ``_wave_step_emit_batch`` (wave.py).

    Each member carries its own ``agent`` + ``prompt``; unlike wave members they
    need no worktree/branch — the verifiers are read-only and run on the main
    checkout. ``name`` mirrors the wave member shape (a display label).
    """
    wave = _build_verifier_wave(track_dir, state, phase)
    emit(dict(action="dispatch_batch", phase=phase, execution_mode=execution_mode,
              wave=wave),
         "step", compact)


def _manual_task_decision(track_dir, pi, ti, name):
    """Pre-computed Defer/Skip ``decision`` blob for an interactive [Manual] task.

    Same transducer shape as :func:`_failed_task_decision` — the orchestrator
    does ``AskUserQuestion(decision.question, …)`` → run
    ``decision.commands[choice]`` verbatim → re-call ``step`` (or HALT). Commands
    omit ``--subtask`` to mirror the failed-task blob (parent-scoped defer/skip).
    """
    td = str(track_dir)
    loc = f"P{pi}.T{ti}"
    defer_cmd = (f'track-state defer "{td}" --phase {pi} --task {ti} '
                 f'--reason {shlex.quote("Deferred: manual task requires human verification")}')
    skip_cmd = (f'track-state skip "{td}" --phase {pi} --task {ti} '
                f'--reason {shlex.quote("Skipped: manual task not required")}')
    sync_cmd = f'track-state sync-plan "{td}"'
    commit_defer = _bookkeeping_commit_line(
        f"chore(conductor): Defer manual task '{name}' [{loc}]")
    commit_skip = _bookkeeping_commit_line(
        f"chore(conductor): Skip manual task '{name}' [{loc}]")
    return dict(
        question=f"Manual task '{name}' ({loc}) needs human verification. Defer or skip?",
        header="Manual task",
        options=[
            {"label": "Defer", "description": "Mark deferred — revisit later"},
            {"label": "Skip", "description": "Mark skipped — not required"},
        ],
        commands={"Defer": [defer_cmd, sync_cmd, commit_defer],
                  "Skip": [skip_cmd, sync_cmd, commit_skip]},
        next={"Defer": "step", "Skip": "step"},
    )


def _step_emit_dispatch(track_dir, compact):
    """Run prepare (lock + start-commit) and emit the ``dispatch`` leaf with the
    pre-assembled subagent prompt. Falls back to leaf re-resolution if prepare
    surfaces a non-dispatch action (state moved under us — rare; bounded because
    the non-dispatch actions are themselves terminal/resolving).
    """
    pre = prepare_dispatch(track_dir)
    if pre.get("action") not in ("explore", "execute"):
        return _step_emit_next_leaf(track_dir, load(track_dir), compact)
    attempt = pre.get("retry_count", 0) + 1
    agent, prompt = _step_assemble_prompt(track_dir, pre, attempt)
    envelope = dict(action="dispatch", agent=agent, prompt=prompt,
              phase=pre["phase"], task=pre["task"], subtask=pre.get("subtask"),
              name=pre.get("name", "?"), attempt=attempt,
              max_retries=pre.get("max_retries", MAX_RETRIES),
              is_resume=pre.get("is_resume", False),
              execution_mode=pre.get("execution_mode", "interactive"))
    # Third-axis constraint (workflow-shapes): if the resolved shape's topology
    # does not include this dispatch agent, surface a shape_violation disclosure
    # (no-silent-caps) rather than silently dispatching off-topology. Advisory
    # today — the dispatch still proceeds so a shape misconfiguration never
    # deadlocks a track — but the violation is visible for the operator to act on.
    allowed, shape = shape_allows(track_dir, agent)
    if not allowed:
        envelope["shape_violation"] = (
            f"agent '{agent}' is not in workflow_shape '{shape}' nodes; "
            f"dispatching anyway (advisory) — fix track-state workflow_shape "
            f"or the shape's nodes if this is wrong")
        envelope["workflow_shape"] = shape
    emit(envelope, "step", compact)


def _step_route_skip_analysis(track_dir, marker, compact):
    """Route the skip_analyze handshake from the on-disk marker (WM2-3). The
    teleoperator transcribed a verdict (``skip-analyst-verdict`` → stage=analyzed,
    or ``skip-refute-review`` → stage=refuted); this decides + executes the next
    leaf, owning the §3.6 route judgment in code.

    stage=analyzed:
      skip → dispatch the refuter (prompt pre-assembled from skip-analyst's reasoning).
      pause_and_escalate / retry_with_modification → clear + ``halt`` (surface reasoning).
    stage=refuted:
      REFUTED / FAILURE → execute the skip in-spine (``_do_skip`` + sync + commit) + advance.
      SUSTAINED → clear + ``halt`` (override-to-block; surface refuter evidence).
    Unknown stage/recommendation/refute-status are defensive re-dispatches (never loop).
    """
    td = str(track_dir)
    stage = marker.get("stage")
    pi, ti = marker.get("phase"), marker.get("task")
    si = marker.get("subtask")
    name = marker.get("name", "?")

    def _redispatch_skip_analyst():
        state = load(track_dir)
        emit(dict(action="dispatch_skip_analyst", agent="skip-analyst",
                  phase=pi, task=ti, subtask=si, name=name,
                  execution_mode=state.get("execution_mode", "continuous"),
                  prompt=_step_assemble_skip_analyst_prompt(
                      track_dir, state, pi, ti, si, name)),
             "step", compact)

    def _redispatch_refuter():
        emit(dict(action="dispatch_refuter", agent="refuter",
                  phase=pi, task=ti, subtask=si, name=name,
                  prompt=_step_assemble_refuter_prompt(track_dir, marker)),
             "step", compact)

    if stage == "analyzed":
        rec = marker.get("recommendation")
        if rec == "skip":
            _redispatch_refuter()
            return
        if rec == "retry_with_modification":
            # B.6: skip-analyst says "this is fixable, just not by skipping" — hand
            # off to failure-analyst for a real diagnosis rather than halting. The
            # task is terminal failed here (post-exhaustion); the analyst's
            # retry_modified verdict will reactivate it. Clear the skip marker so
            # it doesn't re-route; the failure-analysis marker takes over.
            _skip_analysis_clear_marker(track_dir)
            state = load(track_dir)
            emit(dict(action="dispatch_failure_analyst", agent="failure-analyst",
                      phase=pi, task=ti, subtask=si, name=name,
                      execution_mode=state.get("execution_mode", "continuous"),
                      prompt=_step_assemble_failure_analyst_prompt(
                          track_dir, state, pi, ti, si, name)), "step", compact)
            return
        if rec == "pause_and_escalate":
            _skip_analysis_clear_marker(track_dir)
            emit(dict(action="halt", reason=rec, recommendation=rec,
                      reasoning=marker.get("reasoning"), impact=marker.get("impact"),
                      phase=pi, task=ti, subtask=si, name=name), "step", compact)
            return
        _skip_analysis_clear_marker(track_dir)  # unknown recommendation → re-analyze
        _redispatch_skip_analyst()
        return

    if stage == "refuted":
        rstatus = marker.get("refute_status")
        if rstatus in ("REFUTED", "FAILURE"):
            # Skip stands → execute in-spine (mirrors _do_complete in
            # _step_emit_next_leaf), then advance to the next leaf.
            _do_skip(track_dir, pi, ti, si,
                     reason=f"skip-analyst: skip; refute {rstatus}")
            _do_sync_plan(track_dir, load(track_dir))
            _git_commit_ensured(
                track_dir, f"chore(conductor): Skip '{name}' [P{pi}T{ti}] (skip-analyze)")
            _skip_analysis_clear_marker(track_dir)
            return _step_emit_next_leaf(track_dir, load(track_dir), compact)
        if rstatus == "SUSTAINED":
            _skip_analysis_clear_marker(track_dir)
            emit(dict(action="halt", reason="pause_and_escalate",
                      recommendation="pause_and_escalate",
                      reasoning=marker.get("reasoning"),
                      evidence=marker.get("refute_reasoning"),
                      phase=pi, task=ti, subtask=si, name=name), "step", compact)
            return
        _redispatch_refuter()  # unknown refute status → re-refute
        return

    _skip_analysis_clear_marker(track_dir)  # unknown stage → re-analyze
    _redispatch_skip_analyst()


def _step_route_failure_analysis(track_dir, marker, compact):
    """Route the failure_analyze handshake from the on-disk marker.

    The teleoperator transcribed failure-analyst's verdict (``failure-analyst-
    verdict`` → ``stage=analyzed``); this decides + executes the next leaf,
    owning the category→action route judgment in code.

    ``stage=analyzed`` routes by ``recommendation``:
      ``retry_modified`` → write the modification to the modified-guidance marker
        (B.5), reactivate the failed task (failed→pending, retry_count preserved
        so the attempt still counts against budget), clear the analysis marker,
        and re-dispatch task-executor. Bounded by ``analysis_rounds``: past
        ``MAX_ANALYSIS_ROUNDS`` the router falls through to ``escalate``→halt
        (a failure-analyst that triggers another failure-analyst is the loop this
        caps).
      ``replan`` / ``decompose`` / ``escalate`` → ``halt`` for a human, surfacing
        ``root_cause`` + ``modification``. (``decompose`` is the exception: it
        routes to an ``ask`` — ``_decompose_decision`` — whose Apply runs
        ``track-state split`` verbatim; see below. replan/escalate stay halts.)
    Unknown recommendation/stage are defensive re-dispatches (never loop).
    """
    td = str(track_dir)
    stage = marker.get("stage")
    pi, ti = marker.get("phase"), marker.get("task")
    si = marker.get("subtask")
    name = marker.get("name", "?")
    rec = marker.get("recommendation")
    rounds = int(marker.get("analysis_rounds", 1) or 1)

    # Recovery hints: the halts are intentional human gates (not gaps), but the
    # operator needs the SAFE manual path, not just a diagnosis. Each is the
    # minimal recipe that preserves the invariants the firewall depends on —
    # the original task's commit_sha is never destroyed, and replan never
    # silently rewrites an AC that downstream gates already measured against.
    _RECOVERY = {
        # Replan = the SPEC/PLAN is wrong. The analyst cannot decide which AC is
        # correct (that's intent, not diagnosis), so recovery is a human edit of
        # spec.md/plan.md followed by re-running the affected verification. The
        # halt is mandatory: silently rewriting an AC would retroactively falsify
        # every "verified against AC-2" stamp downstream.
        "replan": ("Edit spec.md / plan.md to resolve the conflict (see root_cause "
                   "+ modification), then decide whether completed tasks are still "
                   "valid (re-run ac-tracer / code-reviewer against the new ACs), "
                   "re-init or continue the track."),
        # Decompose = the task is too big, NOT wrong. The committed work is sound
        # and MUST be preserved: split in plan.md, skip the original (commit_sha
        # intact), insert the remainder as new pending tasks. No revert, no
        # re-do of the committed part.
        #
        # NOTE: decompose no longer reaches this halt — the route above emits an
        # ``ask`` (``_decompose_decision``) whose Apply runs ``track-state split``.
        # This entry is kept as the canonical statement of the invariant (and is
        # still surfaced if a future caller routes decompose through _halt).
        "decompose": ("Split the task in plan.md per the modification. Skip the "
                      "original task (its commit_sha is preserved — do NOT revert), "
                      "then insert the remainder as new pending tasks and re-run. "
                      "The committed work stays in git history."),
        # Escalate = analyst exhausted its options (or hit the analysis-round cap).
        # Pure hand-off; recovery is open-ended human judgment.
        "escalate": ("Manual escalation required — review the diagnosis and decide "
                     "the next step (the analyst exhausted its automated options)."),
    }

    def _halt(reason):
        _failure_analysis_clear_marker(track_dir)
        emit(dict(action="halt", reason=reason, recommendation=rec,
                  category=marker.get("category"),
                  reasoning=marker.get("root_cause"),
                  modification=marker.get("modification"),
                  recovery=_RECOVERY.get(reason),
                  what_was_done=marker.get("what_was_done"),
                  phase=pi, task=ti, subtask=si, name=name), "step", compact)

    def _redispatch_failure_analyst():
        state = load(track_dir)
        emit(dict(action="dispatch_failure_analyst", agent="failure-analyst",
                  phase=pi, task=ti, subtask=si, name=name,
                  execution_mode=state.get("execution_mode", "continuous"),
                  prompt=_step_assemble_failure_analyst_prompt(
                      track_dir, state, pi, ti, si, name)),
             "step", compact)

    if stage == "analyzed":
        if rec == "retry_modified":
            # Cap: a modified retry that fails again would re-trigger the
            # analyst — bound it so we don't loop analyze→retry→fail forever.
            if rounds > MAX_ANALYSIS_ROUNDS:
                _halt("escalate")
                return
            _modified_guidance_write(track_dir, pi, ti, si,
                                     marker.get("modification"),
                                     marker.get("root_cause"))
            reactivate_for_modified_retry(track_dir, pi, ti, si)
            _failure_analysis_clear_marker(track_dir)
            _do_sync_plan(track_dir, load(track_dir))
            _git_commit_ensured(
                track_dir,
                f"chore(conductor): Reactivate '{name}' [P{pi}T{ti}] "
                f"for modified retry (failure-analyst)")
            return _step_emit_dispatch(track_dir, compact)
        if rec == "decompose":
            # Decompose is mechanical enough to offer as a code-applied, human-
            # confirmed split — the recovery recipe used to be prose telling a
            # human to hand-edit plan.md; now ``_decompose_decision`` builds an
            # ``ask`` whose Apply runs ``track-state split`` verbatim. The marker
            # is cleared BEFORE the ask (leaving it would re-route to decompose on
            # the next ``step`` after the human picks Apply); mirrors retry_modified
            # clearing before re-dispatch. replan/escalate stay halts below.
            _failure_analysis_clear_marker(track_dir)
            emit(dict(action="ask", phase=pi, task=ti, subtask=si, name=name,
                      decision=_decompose_decision(track_dir, marker),
                      execution_mode=load(track_dir).get("execution_mode", "continuous")),
                 "step", compact)
            return
        if rec in ("replan", "escalate"):
            _halt(rec)
            return
        _failure_analysis_clear_marker(track_dir)  # unknown recommendation
        _redispatch_failure_analyst()
        return

    _failure_analysis_clear_marker(track_dir)  # unknown stage → re-analyze
    _redispatch_failure_analyst()


def _step_emit_exhausted(track_dir, outcome, execution_mode, retry_count, compact):
    """Surface a retries-exhausted failure: interactive → ``ask`` (Retry/Skip/Block
    via the shared failed-task decision blob), continuous → ``dispatch_skip_analyst``
    (the spine owns the §3.6 skip-analyst→refute→route handshake via the
    skip-analysis marker, WM2-3)."""
    state = load(track_dir)
    pi, ti, si = outcome.get("phase"), outcome.get("task"), outcome.get("subtask")
    name, rc = "?", retry_count
    try:
        tgt = target(state, int(pi), int(ti), int(si) if si is not None else None)
        name = tgt.get("name", "?")
        rc = tgt.get("retry_count", rc)
    except (IndexError, KeyError, TypeError, ValueError):
        pass
    if execution_mode == "interactive":
        decision = _failed_task_decision(
            track_dir, int(pi), int(ti), int(si) if si is not None else None, name, rc)
        emit(dict(action="ask", phase=pi, task=ti, subtask=si, name=name,
                  decision=decision, execution_mode=execution_mode), "step", compact)
    else:
        emit(dict(action="dispatch_skip_analyst", agent="skip-analyst",
                  phase=pi, task=ti, subtask=si, name=name,
                  execution_mode=execution_mode,
                  prompt=_step_assemble_skip_analyst_prompt(
                      track_dir, state, pi, ti, si, name)), "step", compact)


def _outcome_max_retries(track_dir, outcome):
    """Resolve the per-task ceiling for a finalize FAILURE outcome.

    Reads ``max_retries`` off the failing task (phase/task/subtask carried on the
    outcome) via :func:`task_max_retries`, so a task with a raised budget isn't
    declared exhausted at the global default. Falls back to the global
    ``MAX_RETRIES`` on any resolution error (fail-safe: over-retry is recoverable,
    a crash in the routing helper is not).
    """
    try:
        pi = int(outcome.get("phase"))
        ti = int(outcome.get("task"))
        si = outcome.get("subtask")
        si = int(si) if si is not None else None
        tgt = target(load(track_dir), pi, ti, si)
        return task_max_retries(tgt)
    except (IndexError, KeyError, TypeError, ValueError):
        return MAX_RETRIES


def _step_route_after_finalize(track_dir, outcome, compact):
    """Decide the next leaf from a finalize outcome (SUCCESS / FAILURE / error)."""
    if outcome.get("error"):
        emit(dict(action="error", error=outcome["error"]), "step", compact)
        return
    execution_mode = outcome.get("execution_mode", "interactive")
    cp = outcome.get("phase_checkpoint_pending")
    if cp is not None:
        _step_emit_dispatch_batch(track_dir, load(track_dir), cp, execution_mode, compact)
        return
    if outcome.get("status") == "SUCCESS":
        return _step_emit_next_leaf(track_dir, load(track_dir), compact)
    # FAILURE — retry (re-queued to pending by _do_fail) or surface exhaustion.
    # The ceiling is the failing task's own (max_retries override) — a task with a
    # higher per-task budget must not be declared exhausted at the global default.
    ceiling = _outcome_max_retries(track_dir, outcome)
    rc = outcome.get("retry_count", 0)
    if rc < ceiling:
        # Pre-exhaustion tier (B.6, continuous only): when exactly one attempt
        # remains, route through failure-analyst first so the final attempt is a
        # *modified* retry rather than a third identical one. Interactive mode
        # keeps a human in the loop and skips this. Lower retry_counts get the
        # ordinary identical retry — the analyst fires once, on the penultimate
        # failure, not on every failure.
        if (execution_mode == "continuous" and rc == ceiling - 1
                and ceiling >= 2):
            return _step_emit_dispatch_failure_analyst(track_dir, outcome, compact)
        return _step_emit_dispatch(track_dir, compact)
    _step_emit_exhausted(track_dir, outcome, execution_mode, rc, compact)


def _step_emit_dispatch_failure_analyst(track_dir, outcome, compact):
    """Emit the ``dispatch_failure_analyst`` leaf for a continuous-mode task one
    attempt from exhaustion (B.6 pre-exhaustion tier)."""
    state = load(track_dir)
    pi = int(outcome.get("phase"))
    ti = int(outcome.get("task"))
    si = outcome.get("subtask")
    si = int(si) if si is not None else None
    try:
        tgt = target(state, pi, ti, si)
        name = tgt.get("name", "?")
    except (IndexError, KeyError, TypeError, ValueError):
        name = "?"
    emit(dict(action="dispatch_failure_analyst", agent="failure-analyst",
              phase=pi, task=ti, subtask=si, name=name,
              execution_mode="continuous",
              prompt=_step_assemble_failure_analyst_prompt(
                  track_dir, state, pi, ti, si, name)),
         "step", compact)


def _emit_quiescent_leaf(track_dir, state, compact, command):
    """Shared terminal/quiescent routing for ``step`` and ``wave-step``.

    Resolves, in order: failed+exhausted → ``ask``/``skip_analyze``; pending phase
    checkpoint → ``phase_checkpoint``; no dispatchable work → ``done``. Returns the
    resolved next-task dict (with ``execution_mode`` set) when the caller should
    proceed with its own command-specific dispatchable-work branch (``step``:
    parent-complete/stuck/manual/dispatch; ``wave-step``: ``serial``); returns
    ``None`` once it has emitted a terminal/quiescent leaf.

    A failed+exhausted task surfaces its decision BEFORE a phase checkpoint —
    matching recover→dispatch-next ordering (Rail A §2.0 runs before §3.0), and
    only when no dispatchable work remains (pending work elsewhere proceeds
    first). ``command`` selects the emit allowlist (``"step"`` / ``"wave-step"``).
    """
    execution_mode = state.get("execution_mode", "interactive")
    nxt = _find_next_task(state)
    nxt["execution_mode"] = execution_mode
    has_dispatchable = nxt.get("phase", 0) >= 1

    if not has_dispatchable:
        found = _find_failed_exhausted(state)
        if found is not None:
            fpi, fti, fsi, ftgt, fname = found
            frc = ftgt.get("retry_count", 0)
            if execution_mode == "interactive":
                decision = _failed_task_decision(track_dir, fpi, fti, fsi, fname, frc)
                emit(dict(action="ask", phase=fpi, task=fti, subtask=fsi, name=fname,
                          decision=decision, execution_mode=execution_mode),
                     command, compact)
            else:
                if command == "step":
                    # Step spine owns the §3.6 handshake (WM2-3); wave-step keeps
                    # the non-spine skip_analyze leaf (parallel-step's contract).
                    emit(dict(action="dispatch_skip_analyst", agent="skip-analyst",
                              phase=fpi, task=fti, subtask=fsi, name=fname,
                              execution_mode=execution_mode,
                              prompt=_step_assemble_skip_analyst_prompt(
                                  track_dir, state, fpi, fti, fsi, fname)),
                         "step", compact)
                else:
                    emit(dict(action="skip_analyze", phase=fpi, task=fti, subtask=fsi,
                              name=fname, execution_mode=execution_mode),
                         command, compact)
            return None

    cp = _any_phase_needs_checkpoint(track_dir, state)
    if cp is not None:
        if command == "step":
            # Serial spine. If a synth_pending marker exists for THIS phase, the
            # verifiers already fanned and their verdicts are on disk (written by
            # ``cmd_phase_verdict``) → dispatch the synthesizer (phase-checker)
            # with a pre-assembled prompt. Otherwise (no marker, or a stale one
            # for another phase) fan the verifiers. The wave spine keeps the
            # non-spine ``phase_checkpoint`` leaf — its §3.2 hand-off is the
            # parallel-step skill's contract (command == "wave-step" arm below).
            marker = _phase_cp_read_marker(track_dir)
            if (marker and marker.get("stage") == "synth_pending"
                    and marker.get("phase") == cp):
                emit(dict(action="dispatch_phase_checker", agent="phase-checker",
                          phase=cp, execution_mode=execution_mode,
                          prompt=_step_assemble_phase_checker_prompt(
                              track_dir, state, cp, marker)),
                     "step", compact)
            else:
                if marker:  # stale (phase mismatch / unknown stage) → clear + fan
                    _phase_cp_clear_marker(track_dir)
                _step_emit_dispatch_batch(track_dir, state, cp, execution_mode, compact)
        else:
            emit(dict(action="phase_checkpoint", phase=cp, execution_mode=execution_mode),
                 command, compact)
        return None

    if not has_dispatchable:
        emit(dict(action="done"), command, compact)
        return None

    return nxt


def _step_emit_next_leaf(track_dir, state, compact):
    """Resolve the next leaf from a quiescent state (no in_progress task awaiting
    finalize). Resolves parent-complete / parent-stuck / continuous-[Manual]-defer
    internally before emitting; surfaces explore / execute / manual / phase_checkpoint
    / done / ask / skip_analyze."""
    nxt = _emit_quiescent_leaf(track_dir, state, compact, "step")
    if nxt is None:
        return

    execution_mode = nxt.get("execution_mode", "interactive")
    ntype = nxt["type"]
    pi, ti = nxt["phase"], nxt["task"]
    si = nxt.get("subtask")
    name = nxt.get("name", "?")

    if ntype == "parent-complete":
        sha = _last_subtask_sha_from_state(track_dir, pi, ti)
        _, pstate = _do_complete(track_dir, pi, ti, None, sha)
        _do_sync_plan(track_dir, pstate)
        _git_commit_ensured(track_dir, f"chore(conductor): Complete parent '{name}' [{sha}]")
        _finalize_parent(track_dir, pi, ti, sha)
        return _step_emit_next_leaf(track_dir, load(track_dir), compact)

    if ntype == "parent-stuck":
        sha = _last_subtask_sha_from_state(track_dir, pi, ti)
        _do_fail_parent(track_dir, pi, ti, "", sha)
        _do_sync_plan(track_dir, load(track_dir))
        _git_commit_ensured(
            track_dir, f"chore(conductor): Fail parent '{name}' (subtasks exhausted retries)")
        # The failed parent now surfaces as ask/skip_analyze via the no-active-task path.
        return _step_emit_next_leaf(track_dir, load(track_dir), compact)

    if _classify_task(nxt.get("tags", [])) == "manual":
        if execution_mode == "continuous":
            _do_defer(track_dir, pi, ti, si,
                      "Deferred: manual task requires human verification")
            _do_sync_plan(track_dir, load(track_dir))
            _git_commit_ensured(track_dir, f"chore(conductor): Defer manual task '{name}'")
            return _step_emit_next_leaf(track_dir, load(track_dir), compact)
        decision = _manual_task_decision(track_dir, pi, ti, name)
        emit(dict(action="ask", phase=pi, task=ti, subtask=si, name=name,
                  decision=decision, execution_mode=execution_mode), "step", compact)
        return

    # explore / execute → prepare + emit dispatch.
    return _step_emit_dispatch(track_dir, compact)


def cmd_step(track_dir, compact=True):
    """State-driven dispatch-loop step — the Rail B-min spine entry point.

    Composes recover + next + prepare + finalize into ONE leaf action per call,
    then returns. The orchestrator's entire job is to read ``action`` and do
    exactly that — dispatch the named agent with the pre-assembled ``prompt``,
    fan out a ``dispatch_batch`` of verifier prompts in one parallel message,
    relay an ``ask`` blob, hand off to the wave spine (``wave_active``),
    ``halt`` for human judgment, or enter the post-loop on ``done`` — then call
    ``step`` again.

    Action set:
      - ``dispatch``             : run one subagent (explorer/task-executor) with ``prompt``. [spine]
      - ``dispatch_batch``       : fan out the ac-tracer + test-runner verifier prompts in ONE parallel message; then ``phase-verdict`` (WM2-2). [spine]
      - ``dispatch_phase_checker``: dispatch the synthesizer; then ``phase-checkpoint-review`` (WM2-2). [spine]
      - ``dispatch_skip_analyst``: dispatch skip-analyst; then ``skip-analyst-verdict`` (WM2-3). [spine]
      - ``dispatch_refuter``     : dispatch the skip-refute; then ``skip-refute-review`` (WM2-3). [spine]
      - ``ask``                  : AskUserQuestion(decision…) → run decision.commands[choice] → step. [spine]
      - ``halt``                 : deliberate stop-for-human (skip-analyze pause/retry/refute-sustained); announce reasoning → STOP. [terminal]
      - ``wave_active``          : hand to the wave spine. [non-spine]
      - ``done``                 : track finalized → enter post-loop (skill §4.0). [terminal]
      - ``error``                : unrecoverable; HALT.

    Internal-only transitions (parent-complete/stuck auto-resolution, continuous
    [Manual] auto-defer) are fully resolved before emitting, so the model never
    sees them. An ``ask`` whose ``decision.next[choice] == "HALT"`` stops the
    loop; any other ``next`` means "re-call step".
    """
    # Wave in flight → the wave spine owns this track.
    wave = _active_wave(track_dir)
    if wave:
        emit(dict(action="wave_active", phase=wave.get("phase")), "step", compact)
        return

    state, fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        emit(dict(action="error", errors=verrors), "step", compact)
        return

    # skip_analyze handshake in progress? (WM2-3) A skip-analysis marker means the
    # teleoperator transcribed a skip-analyst/refuter verdict since the last dispatch;
    # route it before any other state-based logic (mirrors the checkpoint marker).
    sa = _skip_analysis_read_marker(track_dir)
    if sa is not None:
        return _step_route_skip_analysis(track_dir, sa, compact)

    # failure_analyze handshake in progress? A failure-analysis marker means the
    # teleoperator transcribed failure-analyst's verdict since the last dispatch;
    # route it (retry_modified → reactivate + re-dispatch; replan/decompose/
    # escalate → halt). Checked after skip-analysis so a skip hand-off wins if
    # both somehow exist.
    fa = _failure_analysis_read_marker(track_dir)
    if fa is not None:
        return _step_route_failure_analysis(track_dir, fa, compact)

    # If the current task is in_progress, the model just returned from a dispatch
    # (or was interrupted mid-dispatch). Decide finalize vs re-dispatch.
    pi = state.get("current_phase_index", 0)
    ti = state.get("current_task_index", 0)
    si = state.get("current_subtask_index")
    result_path = conductor_dir(track_dir) / "result.json"
    if pi >= 1 and ti >= 1:
        try:
            tgt = target(state, pi, ti, si)
        except IndexError:
            tgt = None
        if tgt is not None and tgt.get("status") == "in_progress":
            # The no-retry-burn contract (design: rail-b-step.md §"No-retry-burn"):
            # a dispatch that NEVER ran (killed session, context-budget yield
            # before any work) must not burn a retry. That state is *clean tree
            # + still-Start HEAD + no result.json*. Any of the other two signals
            # — a result.json, an impl commit past the Start commit, OR
            # uncommitted implementation work in the tree — means the agent DID
            # run and must finalize (synthesize SUCCESS from the commit, or
            # FAILURE-with-handoff from the partial work). Routing those to
            # re-dispatch would loop forever without incrementing retry_count.
            impl_work_done = (
                result_path.exists()
                or not _is_start_commit(track_dir)
                or bool(_git_uncommitted_files(track_dir))
            )
            if impl_work_done:
                # Agent returned (or committed/did code work without writing
                # result) → finalize. _synthesize_result_from_state resolves
                # SUCCESS vs FAILURE from git state and increments retry_count
                # on the FAILURE path.
                return _step_route_after_finalize(
                    track_dir, finalize_dispatch(track_dir), compact)
            # Interrupted before any work: HEAD still the Start commit, clean
            # tree, no result. Re-dispatch WITHOUT finalize so we don't burn a
            # retry on a dispatch that never ran. prepare's is_resume path
            # skips the start commit.
            _emit_redispatch_telemetry(track_dir, pi, ti, si)
            return _step_emit_dispatch(track_dir, compact)

    # No in_progress task awaiting finalize → resolve the next leaf.
    return _step_emit_next_leaf(track_dir, state, compact)


# --------------------------------------------------------------------------- #
# Phase-checkpoint handshake commands (WM2 verdict-on-disk, step 2).
#
# Stamp-only (return JSON, never emit a leaf) — the teleoperator transcribes a
# read-only agent's fixed-format RESULT line to one of these, then re-calls
# ``step``; the spine re-derives the next leaf from the marker it just wrote.
# Mirrors ``cmd_post_loop_review`` (WM2-1): the agent firewall stays intact (no
# read-only agent writes state); the judgment the prose §3.2/§3.7 hand-off asked
# the model to make now lives in code.
# --------------------------------------------------------------------------- #

# Verdict enums — the exact tokens the read-only agents emit (agents/ac-tracer.md
# §4.0, agents/test-runner.md §3.0). Mixed case is the agent contract: ac-tracer
# distinguishes passed/warn/skipped (ok-ish) from FAILED/ERROR (terminal).
_AC_VERDICTS = ("passed", "warn", "skipped", "FAILED", "ERROR")
_L1_STATUSES = ("passed", "failed", "error")


def cmd_phase_verdict(track_dir, ac_verdict, ac_gate, ac_n_ungrounded,
                      l1_status, l1_command):
    """Transcribe the fanned verifier verdicts to the checkpoint marker (WM2-2).

    After ``dispatch_batch`` fans ac-tracer + test-runner, the teleoperator parses
    both RESULT blocks and runs this with ``VERDICT``/``GATE``/``N_UNGROUNDED``
    (ac-tracer) and ``STATUS``/``COMMAND`` (test-runner). Writes
    ``stage=synth_pending`` so the next ``step`` emits the phase-checker synth
    dispatch (pre-assembled from the stored verdicts) instead of re-fanning.

    Validates the verdict enums (a code guard: a transcription typo HALTs with a
    clear error rather than handing the synthesizer garbage) and confirms a
    checkpoint is actually pending for this track. Idempotent — overwriting with
    fresh verdicts after a re-fan is harmless.
    """
    td = str(track_dir)
    if ac_verdict not in _AC_VERDICTS:
        out(dict(error=f"unrecognized ac-verdict: {ac_verdict!r}", track_dir=td,
                 hint=f"one of: {', '.join(_AC_VERDICTS)} (from ---AC TRACE RESULT--- VERDICT)"))
        return
    if l1_status not in _L1_STATUSES:
        out(dict(error=f"unrecognized l1-status: {l1_status!r}", track_dir=td,
                 hint=f"one of: {', '.join(_L1_STATUSES)} (from ---L1 VERIFY RESULT--- STATUS)"))
        return
    state, _fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        out(dict(error="track state unhealthy", errors=verrors, track_dir=td))
        return
    cp = _any_phase_needs_checkpoint(track_dir, state)
    if cp is None:
        out(dict(error="no pending phase checkpoint — nothing to synthesize",
                 track_dir=td,
                 hint="run `track-state step` to advance; phase-verdict follows a dispatch_batch"))
        return
    marker = {
        "phase": cp, "stage": "synth_pending",
        "ac_verdict": ac_verdict,
        "ac_gate": ac_gate or None,
        "ac_n_ungrounded": ac_n_ungrounded,
        "l1_status": l1_status,
        "l1_command": l1_command or None,
    }
    _phase_cp_write_marker(track_dir, marker)
    out(dict(ok=True, phase=cp, stage="synth_pending", track_dir=td))


def cmd_phase_checkpoint_review(track_dir, status, sha, reason):
    """Stamp or clear the checkpoint from phase-checker's STATUS (WM2-2).

    After the synthesizer (``dispatch_phase_checker``) returns, the teleoperator
    transcribes its ``---CHECKPOINT RESULT---`` ``STATUS`` to this command.
    ``PASSED`` stamps ``[checkpoint: <sha>]`` in plan.md (via
    ``_stamp_checkpoint_in_plan``) and deletes the marker → the next ``step``
    sees the checkpoint present and advances. ``FAILED`` deletes the marker → the
    teleoperator halts (an AC-trace authoring defect needs spec/plan edits, not a
    retry); re-invocation after the fix re-fans fresh, matching §3.7. Both
    outcomes delete the marker, so no terminal stage persists.
    """
    td = str(track_dir)
    verdict = (status or "").strip()
    state, _fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        out(dict(error="track state unhealthy", errors=verrors, track_dir=td))
        return
    cp = _any_phase_needs_checkpoint(track_dir, state)
    if cp is None:
        # Already stamped (e.g. a duplicate review call after a PASSED) — clean.
        _phase_cp_clear_marker(track_dir)
        out(dict(ok=True, stamped=False, reason="no_pending_checkpoint", phase=None,
                 track_dir=td,
                 hint="checkpoint already present — nothing to review"))
        return
    if verdict == "PASSED":
        if not sha or not re.match(r"^[0-9a-f]{7}$", sha):
            out(dict(error="PASSED requires a valid --sha (7 hex)",
                     track_dir=td, hint="CHECKPOINT_SHA from ---CHECKPOINT RESULT---"))
            return
        # Gate-group terminal gate (plan-format-contract.md §"Phase Gate Groups"):
        # if ``cp`` is the terminal member of a gate_group, its PASS resolves the
        # whole group's accumulated diff — so every member phase gets the real
        # SHA stamp (trading its ``[checkpoint: deferred <group>]`` marker for a
        # real one). Non-terminal members would otherwise carry a deferred marker
        # forever despite the group being green. ``cp`` can only be the terminal
        # member here: non-terminal members defer in _phase_needs_checkpoint and
        # never surface as the pending checkpoint.
        stamped_phases = [cp]
        group_members = _terminal_gate_group_members(track_dir, cp)
        for member in group_members:
            if member == cp:
                continue  # cp stamped below as the authoritative result
            mr = _stamp_checkpoint_in_plan(track_dir, member, sha)
            if "error" in mr:
                # Advisory — never block the terminal advance on a member stamp
                # failure (the terminal stamp is the load-bearing one). The
                # member keeps its deferred marker; operator can re-stamp.
                sys.stderr.write(
                    f"gate_group member Phase {member} stamp failed (advisory): "
                    f"{mr['error']}\n")
            else:
                stamped_phases.append(member)
        result = _stamp_checkpoint_in_plan(track_dir, cp, sha)
        if "error" in result:
            out(dict(error=result["error"], track_dir=td))
            return
        _phase_cp_clear_marker(track_dir)
        # Advisory: compile durable findings for later phases. Fail-open — a
        # compile error must never block the phase advance (the checkpoint is
        # already stamped). cross-phase findings live in .conductor/track-findings.md.
        try:
            compile_track_findings(track_dir, current_phase=cp)
        except Exception as exc:  # noqa: BLE001 — advisory, never fatal
            sys.stderr.write(f"track-findings compile skipped (advisory): {exc}\n")
        out(dict(ok=True, stamped=True, phase=cp, sha=sha, track_dir=td,
                 gate_group_members=(sorted(stamped_phases)
                                      if len(stamped_phases) > 1 else None)))
    elif verdict == "FAILED":
        _phase_cp_clear_marker(track_dir)
        out(dict(ok=True, stamped=False, phase=cp, track_dir=td,
                 reason=reason or "phase-checker FAILED",
                 hint="announce the reason and STOP; edit spec/plan then re-invoke to re-run the phase"))
    else:
        out(dict(error=f"unrecognized status: {verdict!r}", track_dir=td,
                 hint="PASSED | FAILED (from ---CHECKPOINT RESULT--- STATUS)"))


# skip_analyze handshake transcribe commands (WM2 verdict-on-disk, step 3).
_SKIP_RECOMMENDATIONS = ("skip", "pause_and_escalate", "retry_with_modification")
_REFUTE_STATUSES = ("SUSTAINED", "REFUTED", "FAILURE")


def cmd_skip_analyst_verdict(track_dir, recommendation, reasoning, impact, can_skip):
    """Transcribe skip-analyst's ``recommendation`` to the skip-analysis marker
    (WM2-3). After ``dispatch_skip_analyst`` returns, the teleoperator parses the
    ``---SKIP ANALYSIS---`` JSON and runs this. Writes ``stage=analyzed`` so the
    next ``step`` routes: ``skip`` → ``dispatch_refuter``; ``pause_and_escalate``
    / ``retry_with_modification`` → ``halt``.

    The failed+exhausted task (phase/task/subtask/name) is re-derived from state
    via ``_find_failed_exhausted`` — the spine owns the indices, so the
    teleoperator transcribes only the verdict fields (no index fumble surface).
    Validates the recommendation enum; idempotent overwrite on a re-analyze.
    """
    td = str(track_dir)
    if recommendation not in _SKIP_RECOMMENDATIONS:
        out(dict(error=f"unrecognized recommendation: {recommendation!r}", track_dir=td,
                 hint=f"one of: {', '.join(_SKIP_RECOMMENDATIONS)} "
                      "(from ---SKIP ANALYSIS--- recommendation)"))
        return
    state, _fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        out(dict(error="track state unhealthy", errors=verrors, track_dir=td))
        return
    found = _find_failed_exhausted(state)
    if found is None:
        _skip_analysis_clear_marker(track_dir)
        out(dict(error="no failed+exhausted task to skip-analyze", track_dir=td,
                 hint="run `track-state step` to advance"))
        return
    fpi, fti, fsi, _ftgt, fname = found
    marker = {
        "phase": fpi, "task": fti, "subtask": fsi, "name": fname,
        "stage": "analyzed", "recommendation": recommendation,
        "reasoning": reasoning, "impact": impact,
        "can_skip": _parse_bool(can_skip),
        "refute_status": None, "refute_reasoning": None,
    }
    _skip_analysis_write_marker(track_dir, marker)
    out(dict(ok=True, recommendation=recommendation, stage="analyzed",
             phase=fpi, task=fti, track_dir=td))


def cmd_skip_refute_review(track_dir, status, reasoning):
    """Transcribe the refuter's ``STATUS`` onto the skip-analysis marker (WM2-3).
    After ``dispatch_refuter`` returns, the teleoperator parses the
    ``---REFUTATION RESULT---`` ``STATUS`` and runs this. Writes ``stage=refuted``
    so the next ``step`` routes: ``REFUTED`` / ``FAILURE`` → execute the skip +
    advance; ``SUSTAINED`` → ``halt`` (override-to-block). Validates the enum;
    requires an ``analyzed`` marker (the refute only follows a skip recommendation).
    """
    td = str(track_dir)
    verdict = (status or "").strip()
    if verdict not in _REFUTE_STATUSES:
        out(dict(error=f"unrecognized refute status: {verdict!r}", track_dir=td,
                 hint=f"one of: {', '.join(_REFUTE_STATUSES)} "
                      "(from ---REFUTATION RESULT--- STATUS)"))
        return
    marker = _skip_analysis_read_marker(track_dir)
    if marker is None:
        out(dict(error="no skip-analysis marker — run skip-analyst-verdict first",
                 track_dir=td))
        return
    marker["stage"] = "refuted"
    marker["refute_status"] = verdict
    marker["refute_reasoning"] = reasoning
    _skip_analysis_write_marker(track_dir, marker)
    out(dict(ok=True, stage="refuted", refute_status=verdict, track_dir=td))


def cmd_failure_analyst_verdict(track_dir, category, recommendation, root_cause,
                                modification, what_was_done=None):
    """Transcribe failure-analyst's verdict to the failure-analysis marker.

    After ``dispatch_failure_analyst`` returns, the teleoperator parses the
    ``---FAILURE ANALYSIS---`` JSON and runs this. Writes ``stage=analyzed`` so
    the next ``step`` routes (``_step_route_failure_analysis``):
    ``retry_modified`` → inject modification + re-dispatch task-executor;
    ``replan`` / ``decompose`` / ``escalate`` → ``halt`` for a human.

    The failed task (phase/task/subtask/name) is re-derived from state via
    ``_find_failed_task`` (broader than skip-analyst's ``_find_failed_exhausted``
    — the analyst can fire before exhaustion). Validates the recommendation and
    category enums. Carries an ``analysis_rounds`` counter so the router can cap
    consecutive analysis→retry→fail cycles (``MAX_ANALYSIS_ROUNDS``).
    """
    td = str(track_dir)
    if recommendation not in _FAILED_RECOMMENDATIONS:
        out(dict(error=f"unrecognized recommendation: {recommendation!r}", track_dir=td,
                 hint=f"one of: {', '.join(_FAILED_RECOMMENDATIONS)} "
                      "(from ---FAILURE ANALYSIS--- recommendation)"))
        return
    if category not in _FAILED_CATEGORIES:
        out(dict(error=f"unrecognized category: {category!r}", track_dir=td,
                 hint=f"one of: {', '.join(_FAILED_CATEGORIES)} "
                      "(from ---FAILURE ANALYSIS--- category)"))
        return
    if recommendation == "retry_modified" and not (modification or "").strip():
        out(dict(error="retry_modified requires a non-empty modification "
                       "(a specific different approach)", track_dir=td,
                 hint="provide --modification, or choose escalate/replan/decompose"))
        return
    state, _fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        out(dict(error="track state unhealthy", errors=verrors, track_dir=td))
        return
    found = _find_failed_task(state)
    if found is None:
        _failure_analysis_clear_marker(track_dir)
        out(dict(error="no failed task to analyze", track_dir=td,
                 hint="run `track-state step` to advance"))
        return
    fpi, fti, fsi, _ftgt, fname = found
    # Increment the per-task analysis-rounds counter (B.7). A fresh marker starts
    # at 1; an existing marker (re-analyze) carries the prior count forward.
    prior = _failure_analysis_read_marker(track_dir) or {}
    rounds = int(prior.get("analysis_rounds", 0) or 0) + 1
    marker = {
        "phase": fpi, "task": fti, "subtask": fsi, "name": fname,
        "stage": "analyzed", "category": category,
        "recommendation": recommendation,
        "root_cause": root_cause,
        "modification": modification,
        "what_was_done": what_was_done,
        "analysis_rounds": rounds,
    }
    _failure_analysis_write_marker(track_dir, marker)
    out(dict(ok=True, recommendation=recommendation, category=category,
             stage="analyzed", analysis_rounds=rounds,
             phase=fpi, task=fti, track_dir=td))


def _parse_bool(val):
    """Lenient bool parse for transcribed ``--can-skip`` (``true``/``false``/``1``/``0``)."""
    return str(val).strip().lower() in ("true", "1", "yes", "y")


# --------------------------------------------------------------------------- #
# Rail B-min post-loop spine (skills/post-loop-step/SKILL.md). Collapses the
# prose post-loop (templates/post-loop.md §5.0–§8.0) into one leaf action per
# call, so the 185-line template is never resident on a small context window.
# Mirrors cmd_step: ordered gates, each short-circuits and emits ONE leaf.
# --------------------------------------------------------------------------- #

_POST_LOOP_SIDECAR = "post-loop.json"  # conductor-managed, committed (NOT gitignored)


def _post_loop_read_sidecar(track_dir):
    """Tolerant reader for ``.conductor/post-loop.json``.

    Returns a dict with defaults so callers can branch without existence checks.
    Schema 1 carried only ``reviewed_range``; schema 2 adds ``deferred_resolved``
    (§5.0 gate), ``advisory_diff_shown`` / ``lint_done`` / ``digest_shown``
    (§6.0 advisory / §6.5 / §7.5 gates — fired-markers: truthy ⇒ the gate ran,
    so the spine advances), and ``review_verdict`` / ``review_critical`` /
    ``review_high`` (the §7.0 review outcome, stamped by ``cmd_post_loop_review``
    alongside ``reviewed_range`` so a completed track's verdict + finding counts
    are auditable on the committed sidecar — non-blocking; the gate still
    advances on any non-FAILURE verdict). ``lint_status`` is reserved for a future
    richer model-written value; the gate keys on ``lint_done`` so the
    deterministic ``post`` (which can't read the agent's RESULT STATUS) only
    needs to stamp a boolean. All stamps MERGE (see ``_post_loop_stamp_line``)
    so a later gate never clobbers an earlier gate's marker — the
    lossless-resume invariant.
    """
    path = conductor_dir(track_dir) / _POST_LOOP_SIDECAR
    defaults = dict(reviewed_range=None, deferred_resolved=False,
                    advisory_diff_shown=None, lint_status=None, lint_done=False,
                    digest_shown=None,
                    review_verdict=None, review_critical=None, review_high=None)
    if not path.exists():
        return defaults
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            for k, v in data.items():
                defaults[k] = v
    except (ValueError, OSError):
        pass
    return defaults


def _post_loop_stamp_line(track_dir, updates):
    """Build a ``python3 -c`` one-liner that MERGES ``updates`` into the sidecar.

    Each post-loop gate that advances on a sidecar marker stamps its own field
    AFTER the gate passes. A heredoc OVERWRITE would clobber the prior gate's
    marker (e.g. an advisory stamp erasing ``reviewed_range`` → a re-review on
    the next resume); merging preserves every marker. ``updates`` is a dict of
    code-controlled bool/int/str values (no free text), rendered as a Python
    dict literal. The sidecar path rides in ``sys.argv[1]`` so no path text is
    embedded in the code (shlex-safe regardless of the track dir).
    """
    parts = []
    for k, v in updates.items():
        if isinstance(v, bool):
            parts.append(f"{json.dumps(k)}:{v}")  # Python True/False (not JSON true)
        elif isinstance(v, (int, float)):
            parts.append(f"{json.dumps(k)}:{v}")
        else:
            parts.append(f"{json.dumps(k)}:{json.dumps(v)}")  # quoted str literal
    updates_lit = "{" + ",".join(parts) + "}"
    sidecar = shlex.quote(str(Path(track_dir) / ".conductor" / _POST_LOOP_SIDECAR))
    code = (
        "import json,sys,pathlib;"
        "p=pathlib.Path(sys.argv[1]);"
        "d=json.loads(p.read_text()) if p.exists() else {};"
        f"d.update({updates_lit});"
        "p.write_text(json.dumps(d))"
    )
    return f"python3 -c {shlex.quote(code)} {sidecar}"


def _post_loop_merge_sidecar(track_dir, updates):
    """In-process read-modify-WRITE MERGE into the sidecar — the twin of
    ``_post_loop_stamp_line`` (which emits the same merge as bash for the
    teleoperator's ``post``). Used by commands that do a gate-advance IN CODE
    (e.g. ``cmd_post_loop_review``), so the stamp is never handed back to prose.
    Preserves every prior marker (lossless-resume invariant). Tolerant of a
    missing/corrupt file (starts from ``{}``)."""
    path = conductor_dir(track_dir) / _POST_LOOP_SIDECAR
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except (ValueError, OSError):
        data = {}
    data.update(updates)
    path.write_text(json.dumps(data, ensure_ascii=False))


def _post_loop_project_root(track_dir):
    """Best-effort project root for wiki-differ / doc-linter ``PROJECT_DIR``.

    The conductor root (dir holding ``tracks.md``) is ``<project>/conductor``;
    its parent is the project root. Falls back to the track dir resolved when no
    ``tracks.md`` ancestor is found — the agents re-resolve from there anyway.
    """
    cond = _resolve_conductor_root(track_dir)
    if cond is not None:
        return str(cond.parent)
    return str(Path(track_dir).resolve())


def _post_loop_counts(state):
    """Terminal-status counts across flat tasks + subtasks (done/skipped/deferred)."""
    done = skipped = deferred = 0
    for phase in state.get("phases", []):
        for task in phase.get("tasks", []):
            st = task.get("status")
            if st == "completed":
                done += 1
            elif st == "skipped":
                skipped += 1
            elif st == "deferred":
                deferred += 1
            for sub in task.get("subtasks", []):
                sst = sub.get("status")
                if sst == "completed":
                    done += 1
                elif sst == "skipped":
                    skipped += 1
                elif sst == "deferred":
                    deferred += 1
    return done, skipped, deferred


def _post_loop_read_findings(track_dir):
    """Best-effort read of ``review-result.json`` findings (defensive)."""
    path = conductor_dir(track_dir) / "review-result.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return []
    findings = data.get("findings") if isinstance(data, dict) else None
    return [f for f in (findings or []) if isinstance(f, dict)]


def _compose_digest(track_dir, state, desc, shas, range_str):
    """§7.5 comprehension digest — composed from data already in context (no dispatch).

    Mirrors templates/post-loop.md §7.5: what shipped / outcome / shape / the
    1–3 highest-risk diffs to read first. Informational, non-blocking.
    """
    done, skipped, deferred = _post_loop_counts(state)
    lines = [
        f"What shipped: {desc}",
        f"Outcome: {done} done · {skipped} skipped · {deferred} deferred",
    ]
    if shas and range_str:
        lines.append(f"Shape: {len(shas)} commit(s) over {range_str}")
    findings = _post_loop_read_findings(track_dir)
    if findings:
        sev_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        top = sorted(findings,
                     key=lambda f: sev_order.get(f.get("severity", "Low"), 4))[:3]
        lines.append(f"Review: {len(findings)} finding(s). 🔍 Read this first:")
        for f in top:
            sev = f.get("severity", "?")
            title = f.get("title", "?")
            file = f.get("file", "?")
            lines.append(f"  - [{sev}] {title} ({file})")
    else:
        lines.append("Review: none flagged")
    # The review VERDICT + at-review Critical/High counts (stamped by
    # cmd_post_loop_review) — makes the completed track's review judgment
    # auditable in the digest, not just the ephemeral review pass. Counts are
    # measured at review time (the §7.0 apply_fixes step runs AFTER, so some may
    # be resolved by the time the track archives).
    sidecar = _post_loop_read_sidecar(track_dir)
    rverdict = sidecar.get("review_verdict")
    if rverdict:
        parts = [f"Review verdict: {rverdict}"]
        if sidecar.get("review_critical") is not None:
            parts.append(f"Critical: {sidecar.get('review_critical')}")
        if sidecar.get("review_high") is not None:
            parts.append(f"High: {sidecar.get('review_high')}")
        if rverdict != "APPROVE":
            parts.append("counts at-review (apply_fixes ran after)")
        lines.append(" · ".join(parts))
    return "\n".join(lines)


def _post_loop_deferred_list(state):
    """All deferred tasks (flat + subtask) — mirrors cmd_deferred_report's loop."""
    out_list = []
    for pi, phase in enumerate(state.get("phases", []), 1):
        for ti, task in enumerate(phase.get("tasks", []), 1):
            if task.get("status") == "deferred":
                out_list.append(dict(phase=pi, task=ti, subtask=None,
                                     name=task.get("name", "?")))
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if sub.get("status") == "deferred":
                    out_list.append(dict(phase=pi, task=ti, subtask=si,
                                         name=sub.get("name", "?")))
    return out_list


def _post_loop_deferred_decision(track_dir, deferred):
    """§5.0 decision blob: resolve the deferred queue in one prompt.

    Verify-all / Skip-all mutate every deferred task to terminal (so re-entry
    finds an empty queue and the gate passes naturally); Keep-deferred stamps
    ``deferred_resolved`` in the sidecar (the tasks stay deferred for the final
    report, but the spine advances). Mirrors the ``_failed_task_decision`` shape.
    """
    loc = (f"P{deferred[0]['phase']}.T{deferred[0]['task']}"
           + (f".S{deferred[0]['subtask']}" if deferred[0].get("subtask") else ""))
    td = str(track_dir)
    verify_cmds, skip_cmds = [], []
    for d in deferred:
        sub_flag = f" --subtask {d['subtask']}" if d.get("subtask") else ""
        verify_cmds.append(
            f'track-state complete "{td}" --phase {d["phase"]} --task {d["task"]}'
            f'{sub_flag} --sha ""')
        skip_cmds.append(
            f'track-state skip "{td}" --phase {d["phase"]} --task {d["task"]}'
            f'{sub_flag} --reason {shlex.quote("Skipped: user verified not needed")}')
    sync_cmd = f'track-state sync-plan "{td}"'
    commit_cmd = _bookkeeping_commit_line(
        f"chore(conductor): Resolve deferred tasks [{loc}]")
    # Keep-deferred: stamp the sidecar (MERGE — preserves prior markers) so the
    # gate advances without mutating state.
    keep_cmd = _post_loop_stamp_line(td, {"schema": 2, "deferred_resolved": True})
    keep_commit = _bookkeeping_commit_line(
        f"chore(conductor): Keep deferred tasks (user-verified) [{loc}]")
    return dict(
        question=(f"{len(deferred)} deferred task(s) pending (first: "
                  f"'{deferred[0]['name']}' at {loc}). The quality score must "
                  f"reflect the verified state — resolve before finalize?"),
        header="Deferred tasks",
        options=[
            {"label": "Verify all", "description": "Mark every deferred task completed (user-verified)"},
            {"label": "Skip all", "description": "Mark every deferred task skipped — not required"},
            {"label": "Keep deferred", "description": "Leave deferred for the report; advance"},
        ],
        commands={
            "Verify all": verify_cmds + [sync_cmd, commit_cmd],
            "Skip all": skip_cmds + [sync_cmd, commit_cmd],
            "Keep deferred": [keep_cmd, keep_commit],
        },
        next={"Verify all": "post-loop-step", "Skip all": "post-loop-step",
              "Keep deferred": "post-loop-step"},
    )


def _post_loop_finalize_post(track_dir):
    """§5.5 ``post`` lines — bookkeeping the model runs after the in-code finalize."""
    td = str(track_dir)
    return [
        f'track-state sync-plan "{td}"',
        f'track-state registry-update "{td}" "conductor/tracks.md"',
        _bookkeeping_commit_line("chore(conductor): Complete track"),
    ]


def _post_loop_advisory_prompt(track_dir):
    """§6.0 advisory wiki-differ prompt — post-commit overview drift check."""
    return (f"PROJECT_DIR={_post_loop_project_root(track_dir)}\n"
            f"SCOPE=overview regen drift check\n")


def _post_loop_advisory_post(track_dir):
    """§6.0 advisory ``post`` — stamp ``advisory_diff_shown`` (non-blocking gate).

    ``post_on="always"``: advisory is non-blocking, so the spine advances on any
    return (including agent FAILURE → "announce and continue" per template §6.0).
    """
    td = str(track_dir)
    return [
        _post_loop_stamp_line(td, {"schema": 2, "advisory_diff_shown": True}),
        _bookkeeping_commit_line(
            "chore(conductor): Post-loop advisory diff checked"),
    ]


def _post_loop_lint_prompt(track_dir):
    """§6.5 doc-linter prompt — wiki health check on the synced docs."""
    return f"PROJECT_DIR={_post_loop_project_root(track_dir)}\n"


def _post_loop_lint_post(track_dir):
    """§6.5 lint ``post`` — stamp ``lint_done`` (non-blocking gate).

    ``post_on="always"``: lint is non-blocking; the actual PASS/WARN/FAIL is
    surfaced by the teleoperator from the ``---DOC LINT RESULT---`` block (the
    deterministic ``post`` can't read the agent's STATUS). The sidecar only
    records "the gate fired" so the spine advances.
    """
    td = str(track_dir)
    return [
        _post_loop_stamp_line(td, {"schema": 2, "lint_done": True}),
        _bookkeeping_commit_line("chore(conductor): Post-loop wiki lint run"),
    ]


def _post_loop_digest_post(track_dir):
    """§7.5 digest ``post`` — stamp ``digest_shown`` after announcing (no dispatch)."""
    td = str(track_dir)
    return [
        _post_loop_stamp_line(td, {"schema": 2, "digest_shown": True}),
        _bookkeeping_commit_line("chore(conductor): Post-loop digest shown"),
    ]


# §7.0 step 4 — chunked apply_fixes (P5). Only Critical/High findings with a
# suggestion + file are auto-fixable (Medium/Low → "approve with comments"); the
# spine chunks them per-file and drains one chunk per call so each bounded
# apply-fixes agent (maxTurns 20) finishes before overflow — the fix for the
# prior open-ended free-form patch agent (the "unguarded chimney").
_APPLY_FIXES_SEVERITIES = ("Critical", "High")
_APPLY_FIXES_DIR = "post-loop-fixes"  # conductor-managed sentinels (committed)


def _post_loop_fix_sentinel(track_dir, file_path):
    """Path to the per-chunk ``.done`` sentinel marking ``file_path``'s chunk drained."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file_path) + ".done"
    return Path(track_dir) / ".conductor" / _APPLY_FIXES_DIR / safe


def _post_loop_apply_fixes_post(track_dir, file_path):
    """§7.0 step 4 ``post`` — drop the chunk's ``.done`` sentinel + commit.

    A separate sentinel file per chunk (not a sidecar field) sidesteps the
    JSON-merge complexity of accumulating applied-finding lists, and existence
    is the durable marker (committed → survives a context-budget interruption).
    ``post_on`` defaults to non_failure — a failed chunk is NOT marked done, so
    re-entry re-dispatches it (fresh window) instead of skipping it.
    """
    td = str(track_dir)
    sentinel = _post_loop_fix_sentinel(td, file_path)
    return [
        f'mkdir -p {shlex.quote(str(sentinel.parent))}',
        f'touch {shlex.quote(str(sentinel))}',
        _bookkeeping_commit_line(
            f"chore(conductor): Post-loop fix chunk done [{file_path}]"),
    ]


def _post_loop_next_apply_fixes(track_dir, track_id):
    """Return the next ``apply_fixes`` leaf dict, or None when all chunks drained.

    Reads ``review-result.json`` (written by §7.0 code-reviewer), filters to
    Critical/High fixable findings, groups per-file, drops chunks whose sentinel
    already exists, and emits ONE chunk (deterministic file order). None when
    there is nothing fixable or every chunk is drained → caller advances to §7.5.
    """
    findings = _post_loop_read_findings(track_dir)
    fixable = [f for f in findings
               if f.get("severity") in _APPLY_FIXES_SEVERITIES
               and f.get("file") and str(f.get("suggestion", "")).strip()]
    if not fixable:
        return None
    chunks = {}
    for f in fixable:
        chunks.setdefault(f["file"], []).append(f)
    remaining = [fp for fp in sorted(chunks)
                 if not _post_loop_fix_sentinel(track_dir, fp).exists()]
    if not remaining:
        return None
    next_file = remaining[0]
    chunk = chunks[next_file]
    td = str(track_dir)
    return dict(
        action="apply_fixes", agent="apply-fixes", track_dir=td,
        prompt=(f"TRACK_DIR={td}\nTRACK_ID={track_id}\n"
                f"FILE={next_file}\nFINDINGS={json.dumps(chunk)}\n"),
        post=_post_loop_apply_fixes_post(td, next_file),
    )


def _post_loop_archive_decision(track_dir):
    """§8.0 Archive / Keep active / Delete decision blob."""
    td = str(track_dir)
    archive_cmd = f'track-state archive "{td}"'
    registry_cmd = f'track-state registry-update "<new_track_dir>" "conductor/tracks.md"'
    commit_cmd = _bookkeeping_commit_line("chore(conductor): Archive track")
    delete_cmd = f'rm -rf "{td}"'
    delete_commit = _bookkeeping_commit_line("chore(conductor): Delete track")
    return dict(
        question="Track finalized, doc-synced, and reviewed. Archive it now?",
        header="Archive",
        options=[
            {"label": "Archive", "description": "Relocate to archive/ + registry-update + commit"},
            {"label": "Keep active", "description": "Leave in the active set; stop here"},
            {"label": "Delete", "description": "Remove the track directory entirely (destructive)"},
        ],
        commands={
            "Archive": [archive_cmd, registry_cmd, commit_cmd],
            "Keep active": [],
            "Delete": [delete_cmd, delete_commit],
        },
        next={"Archive": "post-loop-step", "Keep active": "HALT", "Delete": "HALT"},
    )


def _post_loop_doc_sync_prompt(track_dir, track_id, desc, agent):
    """Pre-assembled prompt for corpus-writer (Phase 1) / wiki-synthesizer (Phase 2)."""
    return (
        f"TRACK_DIR={track_dir}\n"
        f"TRACK_ID={track_id}\n"
        f"TRACK_DESCRIPTION={desc}\n"
        f"_AGENT={agent}\n"
    )


def cmd_post_loop_step(track_dir, compact=True):
    """State-driven post-loop spine — Rail B-min, one leaf action per call.

    Replaces the prose ``templates/post-loop.md`` §5.0–§8.0 with ordered gates,
    each short-circuiting to ONE leaf, so the orchestrator's only job is to read
    ``action`` and relay it (dispatch an agent / run ``post`` / relay a decision
    / announce a digest / halt). Mirrors ``cmd_step``; lossless resume via the
    same durable markers ``cmd_post_loop_status`` reads (finalized, doc_synced,
    reviewed_range) plus the sidecar's ``deferred_resolved``.

    Action set:
      - ``deferred_ask``   : §5.0 — AskUserQuestion(decision) → run commands → loop. [spine]
      - ``finalize``       : §5.5 — finalize already ran in-code (ok); run ``post`` → loop. [spine]
      - ``dispatch``       : §6.0 Phase 1/2 — dispatch corpus-writer / wiki-synthesizer → loop. [spine]
      - ``dispatch_advisory``: §6.0 advisory — dispatch wiki-differ → loop. [spine]
      - ``dispatch`` (lint): §6.5 — dispatch doc-linter → loop. [spine]
      - ``dispatch_review``: §7.0 — dispatch code-reviewer; the skill then runs
                            ``post-loop-review --status`` (stamp) → loop. [spine]
      - ``apply_fixes``    : §7.0 step 4 — dispatch apply-fixes for one Critical/High
                             chunk, then run ``post`` (sentinel) → loop until drained. [spine]
      - ``digest``         : §7.5 — announce the pre-assembled digest, run ``post`` → loop. [spine]
      - ``archive_ask``    : §8.0 — AskUserQuestion(decision) → run commands → loop/HALT. [spine]
      - ``done``           : every gate satisfied. [terminal]
      - ``halt``           : finalize refused (incomplete) — surface + stop. [terminal]
      - ``error``          : unhealthy — HALT.

    Every dispatch/digest leaf that completes a gate carries a ``post`` (the
    deterministic bash that MERGES the gate's sidecar marker) and a ``post_on``
    rule: ``"non_failure"`` (default — a failed agent does not advance the gate)
    or ``"always"`` (advisory / lint / digest — non-blocking gates that advance
    on any return, including agent FAILURE). The §7.0 review leaf is the
    exception: it emits ``dispatch_review`` with NO ``post`` — the skill runs
    ``track-state post-loop-review --status`` instead, so the FAILURE→no-stamp
    judgment lives in code, not prose (the old ``post_on=non_failure`` rule
    relied on the teleoperator correctly detecting a failed review).
    """
    state, _fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        emit(dict(action="error", errors=verrors), "post-loop-step", compact)
        return

    td = str(track_dir)
    track_id = state.get("track_id") or Path(td).name
    desc = state.get("description") or state.get("name") or track_id
    sidecar = _post_loop_read_sidecar(track_dir)

    # An archived track has passed every post-loop gate (cmd_archive refuses
    # unless doc-synced, and the spine archives only after finalize + review) →
    # short-circuit to done. Idempotent re-entry after the archive commit lands here.
    if state.get("status") == "archived":
        emit(dict(action="done", track_dir=td), "post-loop-step", compact)
        return

    # §5.0 — deferred verification (skip if empty OR user already chose to keep).
    deferred = _post_loop_deferred_list(state)
    if deferred and not sidecar.get("deferred_resolved"):
        emit(dict(action="deferred_ask", track_dir=td,
                  decision=_post_loop_deferred_decision(td, deferred)),
             "post-loop-step", compact)
        return

    # §5.5 — finalization. finalized := a terminal status (completed/failed/
    # blocked — the set cmd_finalize leaves on ok:true — or archived, post-§8.0)
    # AND a numeric quality_score. An incomplete track stays in_progress with no
    # score, so it re-enters finalize (which halts on the still-incomplete set).
    # A failed/blocked track IS finalized (faithful A/B: it proceeds to archive,
    # where the human picks "Keep active") — without this, a terminal failed track
    # would loop the finalize leaf forever.
    status = state.get("status")
    finalized = (status in ("completed", "failed", "blocked", "archived")
                 and isinstance(state.get("quality_score"), (int, float)))
    if not finalized:
        result = _finalize_track(td)
        if not result.get("ok"):
            # Refused false completion — surface the unfinished units and stop.
            emit(dict(action="halt", reason=result.get("reason"),
                      incomplete=result.get("incomplete"), track_dir=td),
                 "post-loop-step", compact)
            return
        emit(dict(action="finalize", post=_post_loop_finalize_post(td),
                  track_dir=td), "post-loop-step", compact)
        return

    # §6.0 — doc sync (two-tier: Phase 1 corpus-writer, Phase 2 wiki-synthesizer).
    if not docs_synced_for_track(td):
        emit(dict(action="dispatch", agent="corpus-writer", track_dir=td,
                  prompt=_post_loop_doc_sync_prompt(td, track_id, desc, "corpus-writer")),
             "post-loop-step", compact)
        return
    if not wiki_phase2_committed_for_track(td):
        emit(dict(action="dispatch", agent="wiki-synthesizer", track_dir=td,
                  prompt=_post_loop_doc_sync_prompt(td, track_id, desc, "wiki-synthesizer")),
             "post-loop-step", compact)
        return

    # §6.0 advisory — wiki-differ post-commit drift check (non-blocking). Advances
    # on any return (post_on="always") — advisory FAILURE → "announce and continue".
    if not sidecar.get("advisory_diff_shown"):
        emit(dict(action="dispatch_advisory", agent="wiki-differ", track_dir=td,
                  prompt=_post_loop_advisory_prompt(td),
                  post=_post_loop_advisory_post(td), post_on="always"),
             "post-loop-step", compact)
        return

    # §6.5 — wiki lint (non-blocking). Advances on any return; the actual
    # PASS/WARN/FAIL is surfaced from the agent's RESULT block, not the sidecar.
    if not sidecar.get("lint_done"):
        emit(dict(action="dispatch", agent="doc-linter", track_dir=td,
                  prompt=_post_loop_lint_prompt(td),
                  post=_post_loop_lint_post(td), post_on="always"),
             "post-loop-step", compact)
        return

    # §7.0 — code review over the implementation SHA range. Skipped iff no SHAs.
    # range_str is hoisted so §7.5's digest (and §7.6 apply_fixes) can reuse it
    # without re-deriving the SHA range.
    shas = _get_all_shas(state)
    range_str = f"{shas[0]}~1..{shas[-1]}" if shas else None
    if shas:
        review_done = bool(sidecar.get("reviewed_range")
                           and sidecar["reviewed_range"] == range_str)
        if not review_done:
            # `dispatch_review` (not `dispatch`): the §7.0 gate-advance is owned
            # by `track-state post-loop-review --status`, NOT a teleoperator-
            # judged `post`. The skill transcribes the review's STATUS line to
            # that command, which stamps reviewed_range only on a real review (a
            # FAILURE does not stamp → next call re-reviews) — moving the failure
            # judgment out of prose (WM2 verdict-on-disk, step 1).
            emit(dict(action="dispatch_review", agent="code-reviewer", track_dir=td,
                      range=range_str, shas_count=len(shas),
                      prompt=(f"TRACK_DIR={td}\nTRACK_ID={track_id}\n"
                              f"REVISION_RANGE={range_str}\n")),
                 "post-loop-step", compact)
            return

    # §7.0 step 4 — chunked apply_fixes (P5). Drains one Critical/High fixable
    # chunk per call; None when nothing fixable or all chunks drained.
    apply_leaf = _post_loop_next_apply_fixes(td, track_id)
    if apply_leaf is not None:
        emit(apply_leaf, "post-loop-step", compact)
        return

    # §7.5 — comprehension digest (no dispatch; announce + stamp). Composed from
    # data already in context (SHAs + finalize + review-result.json findings).
    if not sidecar.get("digest_shown"):
        emit(dict(action="digest", track_dir=td,
                  digest=_compose_digest(td, state, desc, shas, range_str),
                  post=_post_loop_digest_post(td), post_on="always"),
             "post-loop-step", compact)
        return

    # §8.0 — archive gate. A failed/blocked track proceeds faithfully (the human
    # picks "Keep active"); cmd_finalize returned ok:true for terminal failed/blocked.
    if status != "archived":
        emit(dict(action="archive_ask", track_dir=td,
                  decision=_post_loop_archive_decision(td)),
             "post-loop-step", compact)
        return

    # Every gate satisfied.
    emit(dict(action="done", track_dir=td), "post-loop-step", compact)


def _to_int_or_none(v):
    """Parse a CLI flag value to int; None / unparsable → None (never fabricate 0)."""
    if v is None:
        return None
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def cmd_post_loop_review(track_dir, status, critical=None, high=None):
    """Stamp the §7.0 reviewed-range sidecar from the code-reviewer's STATUS —
    the FAILURE judgment moved OUT of the teleoperator's prose ``post`` rule into
    code (WM2 verdict-on-disk, step 1).

    The §7.0 leaf emits ``dispatch_review`` with NO ``post``: after the review
    returns, the teleoperator transcribes the ``STATUS:`` line from the
    ``---REVIEW RESULT---`` block to this command. A REAL review
    (APPROVE / APPROVE_WITH_COMMENTS / CHANGES_REQUESTED — the review ran,
    regardless of verdict) MERGE-stamps ``reviewed_range`` = the current SHA
    range (re-derived from state, identical to the spine's gate) and commits, so
    the next ``post-loop-step`` advances past §7.0. A FAILURE does NOT stamp →
    the next call re-emits the review (a crashed review is never silently treated
    as done — the silent-correctness bug this replaces). Re-deriving the range in
    code (not from a teleoperator-passed value) keeps the stamp byte-identical to
    the gate's equality check.

    The review VERDICT and Critical/High counts are also MERGE-stamped
    (``review_verdict`` / ``review_critical`` / ``review_high``), transcribed from
    the same RESULT block's ``STATUS:`` / ``CRITICAL:`` / ``HIGH:`` lines. This
    makes a completed track's review outcome **auditable on the committed sidecar**
    — "done is a claim": the verdict + counts the claim rests on now survive on
    disk (and surface in the §7.5 digest) — **without gating**. The gate still
    advances on any non-FAILURE verdict; the plugin's long-standing non-blocking
    review posture is unchanged (this is persistence-for-audit, NOT a blocking
    DONE gate). Counts are optional; an unparsed/absent count is not stamped
    (never fabricated as 0).
    """
    state, _fixes, verrors = ensure_healthy(track_dir)
    if state is None:
        out(dict(error="track state unhealthy", errors=verrors,
                 track_dir=str(track_dir)))
        return
    td = str(track_dir)
    shas = _get_all_shas(state)
    if not shas:
        out(dict(error="no implementation SHAs — nothing to review", track_dir=td))
        return
    range_str = f"{shas[0]}~1..{shas[-1]}"
    verdict = (status or "").strip().upper()
    if verdict == "FAILURE":
        out(dict(ok=True, stamped=False, reason="review_failure", track_dir=td,
                 hint="review did not complete — re-run post-loop-step to re-review"))
        return
    if verdict not in ("APPROVE", "APPROVE_WITH_COMMENTS", "CHANGES_REQUESTED"):
        out(dict(error=f"unrecognized review STATUS: {verdict!r}", track_dir=td,
                 hint="APPROVE | APPROVE_WITH_COMMENTS | CHANGES_REQUESTED | FAILURE "
                      "(from the ---REVIEW RESULT--- block)"))
        return
    c = _to_int_or_none(critical)
    h = _to_int_or_none(high)
    stamp = {"schema": 2, "reviewed_range": range_str, "review_verdict": verdict}
    if c is not None:
        stamp["review_critical"] = c
    if h is not None:
        stamp["review_high"] = h
    _post_loop_merge_sidecar(td, stamp)
    _git_commit(td, f"chore(conductor): Stamp post-loop reviewed range [{range_str}]")
    out(dict(ok=True, stamped=True, reviewed_range=range_str,
             status=verdict, review_verdict=verdict,
             review_critical=c, review_high=h, track_dir=td))

