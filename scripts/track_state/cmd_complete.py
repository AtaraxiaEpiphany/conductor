"""cmd_complete: mutation that bridges to git/sync/notes."""
from .core import load, save
from .helpers import target, out, _any_phase_needs_checkpoint
from .mutations import _do_complete
from .sync import _do_sync_plan
from .git_ops import (
    _git_commit, _git_commit_ensured, _git_head_sha, _ensure_note,
    _has_sibling_sha, _update_task_sha,
)


def cmd_complete(track_dir, p, t, s=None, sha=None,
                 coverage=None, deviations=None):
    state = load(track_dir)
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None

    # Pre-check: if completing a subtask that would duplicate a sibling SHA,
    # create a conductor commit (allow-empty) to guarantee a unique SHA
    if si is not None and sha and _has_sibling_sha(state, pi, ti, si, sha):
        _do_sync_plan(track_dir, state)
        commit_msg = f"chore(conductor): Dedup SHA for P{pi}.T{ti}.S{si}"
        _git_commit(track_dir, commit_msg, allow_empty=True)
        new_sha = _git_head_sha(track_dir)
        if new_sha and new_sha != sha:
            sha = new_sha

    try:
        parent_completed = _do_complete(track_dir, p, t, s, sha)
    except IndexError as e:
        out(dict(error=str(e), hint="Run 'track-state validate --fix' to correct state"))
        return
    state = load(track_dir)

    # Resolve actual SHA from state (may differ from input sha when
    # parent-complete inherits from _last_subtask_sha)
    try:
        tgt = target(state, pi, ti, si)
    except IndexError as e:
        out(dict(error=str(e), hint="Run 'track-state validate --fix' to correct state"))
        return
    resolved_sha = tgt.get("commit_sha", sha or "")

    # Store evidence (from --coverage/--deviations flags or minimal default)
    if coverage is not None or deviations is not None:
        if "evidence" not in tgt:
            tgt["evidence"] = {"coverage_pct": None, "tc_coverage": "", "deviations": 0}
        if coverage is not None:
            tgt["evidence"]["coverage_pct"] = coverage
        if deviations is not None:
            tgt["evidence"]["deviations"] = deviations
        save(track_dir, state)
    elif "evidence" not in tgt:
        tgt["evidence"] = {"coverage_pct": None, "tc_coverage": "", "deviations": 0}
        save(track_dir, state)

    # Write git note if missing
    _ensure_note(track_dir, state, pi, ti, si, tgt)

    # Sync plan.md markers to reflect completion
    _do_sync_plan(track_dir, state)

    # Self-commit: cmd_complete is the recovery-route completion path (the in_progress
    # → git-log → `complete --sha` row in SKILL §2.0) and was the only completion
    # route that didn't commit, leaving plan.md + track-state.json dirty for the
    # orchestrator to manually "Fix state consistency." Mirror dispatch-finalize so
    # every completion is durable and the working tree is left clean.
    task_name = tgt.get("name", "unknown")
    commit_msg = f"chore(conductor): Complete '{task_name}' [{resolved_sha}]"
    committed = _git_commit_ensured(track_dir, commit_msg)
    if not committed:
        print(f"WARNING: conductor commit failed for '{task_name}'", file=sys.stderr)
    final_sha = resolved_sha
    if committed:
        final_sha = _git_head_sha(track_dir) or resolved_sha
    # Empty code-sha ([Explore]/[Docs] recovered completions) → store the conductor
    # commit SHA so the task carries a real SHA in state + plan. Input-sha sibling
    # dedup is already handled by the pre-check above; conductor commits are unique.
    if committed and not resolved_sha and final_sha:
        state = _update_task_sha(track_dir, pi, ti, si, final_sha)
        resolved_sha = final_sha

    # Reload so the checkpoint scan reflects persisted changes, then surface the
    # phase-checkpoint signal — uniform contract with cmd_defer / dispatch-finalize,
    # so the recovery path (which bypasses dispatch-next) still triggers the
    # phase-checker when this completion concludes an uncheckpointed phase.
    state = load(track_dir)
    result = dict(ok=True, parent_completed=parent_completed, sha=resolved_sha,
                 committed=committed)
    checkpoint_pending = _any_phase_needs_checkpoint(track_dir, state)
    if checkpoint_pending is not None:
        result["phase_checkpoint_pending"] = checkpoint_pending
        result["next_action"] = "dispatch_phase_checker"
    out(result)
