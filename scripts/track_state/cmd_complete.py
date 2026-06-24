"""cmd_complete: mutation that bridges to git/sync/notes."""
from .core import load, save
from .helpers import target, out
from .mutations import _do_complete
from .sync import _do_sync_plan
from .git_ops import _git_commit, _git_head_sha, _ensure_note, _has_sibling_sha


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
        parent_completed, state = _do_complete(track_dir, p, t, s, sha)
    except IndexError as e:
        out(dict(error=str(e), hint="Run 'track-state validate --fix' to correct state"))
        return

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

    out(dict(ok=True, parent_completed=parent_completed, sha=resolved_sha))
