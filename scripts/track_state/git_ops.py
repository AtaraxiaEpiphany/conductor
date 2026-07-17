"""Git subprocess operations: commit, SHA, notes."""
import json
import re
import subprocess
import sys
from pathlib import Path

from .core import load, save
from .helpers import target, now_iso, _store_evidence, conductor_dir, _normalize_sha
from .sync import _do_sync_plan
from lib.git_utils import docs_synced_for_track  # noqa: F401 — re-exported; single source shared with lint-track-state
from lib.git_utils import wiki_phase2_committed_for_track  # noqa: F401 — re-exported; post-loop spine Phase-2 discriminator

# Best-effort git ops never crash the finalize/recover path: a git failure
# (missing binary, timeout, bad SHA) degrades to a warning + skip, not a raise.
_GIT_OP_ERRORS = (
    subprocess.SubprocessError, FileNotFoundError, PermissionError,
    subprocess.TimeoutExpired,
)


def _git_note_exists(track_dir, sha):
    """True if ``sha`` resolves and already has a git note attached.

    Best-effort: any git error, a missing binary, an unresolvable SHA, or no
    note all return ``False`` — ``_ensure_note`` treats that as "write one".
    """
    try:
        full_sha = subprocess.run(
            ["git", "rev-parse", sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        ).stdout.strip()
        if not full_sha:
            return False
        existing = subprocess.run(
            ["git", "notes", "show", full_sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        return existing.returncode == 0 and bool(existing.stdout.strip())
    except _GIT_OP_ERRORS:
        return False


def _add_git_note(track_dir, sha, note, *, skip_if_exists=False):
    """Resolve ``sha`` to a full SHA and attach ``note`` to it. Best-effort:
    returns ``True`` on success or (with ``skip_if_exists``) when a note is
    already present; ``False`` on any failure, with a warning. Never raises.

    Shared resolve/show/add mechanics for every git-note writer here so the
    never-crash exception net and the SHA-resolution dance live in one place.
    """
    try:
        full_sha = subprocess.run(
            ["git", "rev-parse", sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        ).stdout.strip()
        if not full_sha:
            print(f"WARNING: git note skipped — cannot resolve SHA '{sha}'",
                  file=sys.stderr)
            return False
        if skip_if_exists:
            existing = subprocess.run(
                ["git", "notes", "show", full_sha],
                capture_output=True, text=True, cwd=track_dir, timeout=5
            )
            if existing.returncode == 0 and existing.stdout.strip():
                return True  # already attached
        result = subprocess.run(
            ["git", "notes", "add", "-f", "-m", note, full_sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        if result.returncode != 0:
            print(f"WARNING: git notes add failed for {sha}: "
                  f"{result.stderr.strip()}", file=sys.stderr)
            return False
        return True
    except _GIT_OP_ERRORS as e:
        print(f"WARNING: git note write error for {sha}: {e}", file=sys.stderr)
        return False


def _write_git_note(track_dir, result_data, state):
    """Write human-readable git note for the task's commit.
    Best-effort: logs warnings on failure, silently skips if SHA missing."""
    sha = result_data.get("commit_sha", "")
    if not sha or sha == "N/A":
        return

    task_name = result_data.get("task_name", "unknown")
    phase = result_data.get("phase", "?")
    task = result_data.get("task", "?")
    subtask = result_data.get("subtask")
    summary = result_data.get("summary", "")
    files = result_data.get("files_changed", "")
    tc_ids = result_data.get("tc_coverage", "")
    cov_pct = result_data.get("coverage_pct")
    cov_tool = result_data.get("coverage_tool", "")
    deviations = result_data.get("spec_deviation_detail", [])

    try:
        p_val, t_val = int(phase), int(task)
        loc = f"P{p_val}.T{t_val}"
        if subtask is not None:
            loc += f".S{int(subtask)}"
    except (ValueError, TypeError):
        # Non-numeric values (e.g. "?") — show as-is for degraded output
        loc = f"P{phase}.T{task}"
        if subtask is not None:
            loc += f".S{subtask}"

    lines = [f"[Conductor] {task_name} ({loc})"]
    lines.append(f"Summary: {summary}")
    if files:
        lines.append(f"Files: {files}")
    if cov_pct is not None:
        lines.append(f"Coverage: {cov_pct}%{' — ' + cov_tool if cov_tool else ''}")
    if tc_ids:
        lines.append(f"TCs: {tc_ids}")
    if deviations:
        dev_texts = [d.get("description", str(d)) if isinstance(d, dict) else str(d) for d in deviations]
        lines.append(f"Spec deviations: {'; '.join(dev_texts)}")
    else:
        lines.append("Spec deviations: NONE")

    note = "\n".join(lines)

    _add_git_note(track_dir, sha, note)


def _write_git_note_basic(track_dir, sha, state, pi, ti, si=None):
    """Write a basic git note from track-state.json + git when result.json is unavailable.
    Used during recovery. Best-effort: logs warnings on failure."""
    if not sha:
        return

    try:
        tgt = state["phases"][pi - 1]["tasks"][ti - 1]
        if si is not None:
            tgt = tgt["subtasks"][si - 1]
        task_name = tgt.get("name", "unknown")
    except (IndexError, KeyError):
        task_name = "unknown"

    loc = f"P{pi}.T{ti}"
    if si is not None:
        loc += f".S{si}"

    # Get files from git diff
    files = ""
    try:
        diff_out = subprocess.run(
            ["git", "diff", "--name-only", f"{sha}~1", sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        if diff_out.returncode == 0 and diff_out.stdout.strip():
            files = ", ".join(diff_out.stdout.strip().split("\n"))
    except _GIT_OP_ERRORS:
        pass  # Git unavailable - no file info available

    lines = [f"[Conductor] {task_name} ({loc})"]
    lines.append("Summary: (recovered — no runtime data available)")
    if files:
        lines.append(f"Files: {files}")
    lines.append("Coverage: (not recovered)")
    lines.append("Spec deviations: (not recovered)")

    note = "\n".join(lines)

    # Only write if no note exists (recovery must never overwrite a real note).
    _add_git_note(track_dir, sha, note, skip_if_exists=True)


def _git_commit(track_dir, message, allow_empty=False):
    """Stage conductor state files and create a git commit. Returns True if committed.

    Only stages files that dispatch-finalize modifies: track-state.json,
    plan.md, .conductor/ — never arbitrary untracked files.
    When allow_empty is True, creates a commit even with nothing staged (for SHA dedup).
    """
    try:
        import subprocess
        # Stage only conductor-managed files (not arbitrary untracked files)
        paths = ["track-state.json", "plan.md", ".conductor/"]
        subprocess.run(
            ["git", "add", "--"] + [p for p in paths if Path(track_dir, p).exists()],
            capture_output=True, text=True, cwd=track_dir, timeout=10
        )
        # Verify there is something staged
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True, text=True, cwd=track_dir, timeout=10
        )
        if diff.returncode == 0 and not allow_empty:
            return False  # Nothing staged
        cmd = ["git", "commit", "-m", message]
        if allow_empty:
            cmd.append("--allow-empty")
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=track_dir, timeout=10)
        if result.returncode != 0:
            print(f"WARNING: git commit failed: {result.stderr.strip()}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"WARNING: git commit error: {e}", file=sys.stderr)
        return False


def _git_commit_ensured(track_dir, message):
    """Commit with allow-empty fallback for guaranteed SHA creation.

    Tries a normal commit first (staged changes only). If nothing is staged,
    retries with --allow-empty so every caller gets a unique SHA.
    Returns True if a commit was created.
    """
    committed = _git_commit(track_dir, message)
    if not committed:
        committed = _git_commit(track_dir, message, allow_empty=True)
    return committed


def _git_head_sha(track_dir):
    """Get 7-char short SHA of current HEAD. Returns None on failure."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        sha = result.stdout.strip()
        return sha if re.match(r"^[0-9a-f]{7}$", sha) else None
    except Exception as e:
        print(f"WARNING: git rev-parse error: {e}", file=sys.stderr)
        return None


def _has_sibling_sha(state, pi, ti, si, sha):
    """Check if a sibling subtask already uses the given SHA."""
    if si is None or not sha:
        return False
    try:
        task = state["phases"][int(pi) - 1]["tasks"][int(ti) - 1]
        for i, sub in enumerate(task.get("subtasks", []), 1):
            if i != int(si) and sub.get("commit_sha") == sha:
                return True
    except (IndexError, KeyError, ValueError):
        pass
    return False


def _update_task_sha(track_dir, p, t, s, sha):
    """Update commit_sha for a specific task/subtask and re-sync plan.
    Returns the updated state so callers avoid a redundant load()."""
    state = load(track_dir)
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None
    tgt = state["phases"][pi - 1]["tasks"][ti - 1]
    if si is not None:
        tgt["subtasks"][si - 1]["commit_sha"] = sha
    else:
        tgt["commit_sha"] = sha
    save(track_dir, state)
    _do_sync_plan(track_dir, state)
    return state


def _is_start_commit(track_dir):
    """Check if HEAD commit message matches a conductor 'Start task' pattern.

    Returns True when HEAD is a Start commit, meaning the task-executor
    produced no implementation commits of its own."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        msg = result.stdout.strip()
        return bool(re.match(r"^chore\(conductor\): Start task ", msg))
    except Exception:
        return False


def _git_uncommitted_files(track_dir):
    """Get list of unstaged, staged, and untracked files in the working tree.

    Returns sorted list of repo-relative file paths, excluding conductor-managed
    files (track-state.json, plan.md, .conductor/, handoff.md).
    Uses git status --porcelain to cover all three categories in one call."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        # --porcelain: "XY PATH" per line; Y=space→unstaged, M/A→staged, ?→untracked
        files = {line[3:] for line in result.stdout.splitlines() if len(line) > 3}
    except Exception:
        files = set()
    # Exclude conductor-managed files — only report implementation files
    conductor_prefixes = ("track-state", "plan.md", ".conductor/",
                          "handoff.md")
    return sorted(f for f in files
                  if not any(f.startswith(p) or f == p for p in conductor_prefixes))


def _find_conductor_shas(track_dir):
    """Build a mapping of task_name -> SHA from recent conductor completion commits.

    Single git-log invocation fetches all recent conductor commits;
    Python-side parsing avoids regex issues with special chars in task names.
    Returns dict {task_name: 7-char SHA}. First (most recent) match wins.
    """
    shas = {}
    try:
        import subprocess
        result = subprocess.run(
            ["git", "log", "--all", "--format=%h %s", "--grep",
             "chore(conductor): Complete", "-50"],
            capture_output=True, text=True, cwd=track_dir, timeout=10
        )
        if result.returncode != 0 or not result.stdout.strip():
            return shas
        for line in result.stdout.strip().split('\n'):
            parts = line.split(' ', 1)
            if len(parts) < 2:
                continue
            sha, msg = parts
            # Extract task name from "chore(conductor): Complete 'name' [...]"
            # or "chore(conductor): Complete parent 'name' [...]"
            # or "chore(conductor): Complete stuck parent 'name' [...]"
            m = re.search(r"Complete(?:\s+\w+)*\s+'([^']+)'", msg)
            if m:
                name = m.group(1)
                norm = _normalize_sha(sha)
                if norm and name not in shas:
                    shas[name] = norm
    except Exception:
        pass
    return shas


def _recover_git_notes(track_dir, state):
    """Best-effort: write git notes for completed tasks that are missing them.
    Scans track-state.json for completed tasks with commit SHAs,
    checks if each commit has a git note, writes one if missing."""
    try:
        for pi, phase in enumerate(state.get("phases", []), 1):
            for ti, task in enumerate(phase.get("tasks", []), 1):
                # Check flat task
                if task.get("status") == "completed" and task.get("commit_sha"):
                    _ensure_note(track_dir, state, pi, ti, None, task)

                # Check subtasks
                for si, sub in enumerate(task.get("subtasks", []), 1):
                    if sub.get("status") == "completed" and sub.get("commit_sha"):
                        _ensure_note(track_dir, state, pi, ti, si, sub)
    except (ImportError, FileNotFoundError):
        pass  # Git unavailable or missing state file


def _ensure_note(track_dir, state, pi, ti, si, tgt):
    """Attach a git note to ``tgt``'s commit if none is present yet.

    Existence is checked first (cheap resolve+show via ``_git_note_exists``) so
    we skip content synthesis entirely when a note is already attached. Content
    comes from ``result.json`` when it matches this task (richest), else a basic
    note is synthesized from track-state.json + git. The writers' resolve/show/
    add mechanics + never-crash net are shared via ``_add_git_note``.
    """
    sha = tgt.get("commit_sha", "")
    if not sha or _git_note_exists(track_dir, sha):
        return  # unresolvable, or a note is already attached — nothing to do

    # No note — try full recovery from result.json, then basic from state
    result_path = Path(track_dir) / ".conductor" / "result.json"
    if result_path.exists():
        try:
            with open(result_path) as f:
                r = json.load(f)
            rp, rt = r.get("phase"), r.get("task")
            rs = r.get("subtask")
            # Match by indices (1-based)
            if str(rp) == str(pi) and str(rt) == str(ti) and ((rs is None and si is None) or str(rs) == str(si)):
                _write_git_note(track_dir, r, state)
                return
            # Fallback: match by task_name
            r_name = r.get("task_name", "")
            if r_name == tgt.get("name", ""):
                # Pass corrected indices so _write_git_note computes correct P/T location
                _write_git_note(track_dir, {**r, "phase": pi, "task": ti, "subtask": si}, state)
                return
        except (FileNotFoundError, json.JSONDecodeError):
            pass  # Result file missing or invalid

    # Fall back to basic note from track-state.json + git
    _write_git_note_basic(track_dir, sha, state, pi, ti, si)


def _finalize_parent(track_dir, p, t, sha, *, ensure_evidence=True):
    """Finalize a parent task's audit trail after its conductor commit.

    Shared post-commit sequence for parent-complete / parent-stuck in
    cmd_dispatch_next and the legacy parent-complete in cmd_process_result,
    which previously hand-rolled three drifting copies:
      * resolve final_sha from HEAD — the conductor commit may have advanced it;
      * if final_sha differs from the subtask-derived ``sha``, persist the
        parent's commit_sha and re-sync plan.md so the marker carries the SHA;
      * write the conductor git note targeting that SHA;
      * (unless ``ensure_evidence=False``) seed minimal evidence if none exists.

    ``p``/``t`` are 1-based phase/task indices. ``sha`` is the subtask-derived
    SHA the caller committed under. Returns final_sha.
    """
    final_sha = _git_head_sha(track_dir) or sha
    if final_sha != sha:
        state = load(track_dir)
        state["phases"][p - 1]["tasks"][t - 1]["commit_sha"] = final_sha
        save(track_dir, state)
        _do_sync_plan(track_dir, state)
    state = load(track_dir)
    parent_tgt = state["phases"][p - 1]["tasks"][t - 1]
    _ensure_note(track_dir, state, p, t, None, parent_tgt)
    if ensure_evidence and "evidence" not in parent_tgt:
        parent_tgt["evidence"] = {"coverage_pct": None, "tc_coverage": "", "deviations": 0}
        save(track_dir, state)
    return final_sha


# --- Wave parallelism: worktree + cherry-pick integration --------------------
#
# The serial conductor runs every git op with ``cwd=track_dir`` (git walks up to
# find the repo). Wave parallelism adds a second axis: each wave member executes
# in its OWN ``git worktree`` (isolated branch + working tree), so N agents never
# share an index. These helpers manage that lifecycle. Cherry-pick MUST run with
# ``cwd=repo_root`` (the main worktree, on the track branch) — never ``track_dir``
# — because the integration target is the track branch, not the worktree's branch.


def _git_rev_parse_toplevel(track_dir):
    """Absolute path to the working-tree top level (the main worktree root).

    ``track_dir`` is a subdir of the repo (``conductor/tracks/<id>/``); git walks
    up to the worktree root. Wave helpers run cherry-pick / worktree commands with
    ``cwd=<this path>`` because those operate on the main worktree (the track
    branch), not on ``track_dir``. Returns ``None`` on failure.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        top = result.stdout.strip()
        return top if result.returncode == 0 and top else None
    except Exception as e:
        print(f"WARNING: git rev-parse --show-toplevel error: {e}", file=sys.stderr)
        return None


def _git_worktree_add(repo_root, worktree_path, branch, base_sha):
    """Create a worktree at ``worktree_path`` on NEW branch ``branch`` at ``base_sha``.

    ``git worktree add -b <branch> <path> <base>`` atomically creates the branch
    and checks it out in an isolated working tree sharing the repo's object
    store. The agent runs there; its commits land on ``branch``, leaving the main
    worktree (track branch) untouched. Returns True/False.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["git", "worktree", "add", "-b", branch, worktree_path, base_sha],
            capture_output=True, text=True, cwd=repo_root, timeout=30
        )
        if result.returncode != 0:
            print(f"WARNING: git worktree add failed: {result.stderr.strip()}",
                  file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"WARNING: git worktree add error: {e}", file=sys.stderr)
        return False


def _git_branch_tip(repo_root, ref):
    """7-char short SHA of ``ref`` (a branch or commit-ish). None on failure."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", ref],
            capture_output=True, text=True, cwd=repo_root, timeout=5
        )
        sha = result.stdout.strip()
        return sha if result.returncode == 0 and re.match(r"^[0-9a-f]{7}$", sha) else None
    except Exception:
        return None


def _git_range_commit_count(repo_root, base_sha, tip_sha):
    """Number of commits in (base, tip]. 0 means the agent committed nothing."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{base_sha}..{tip_sha}"],
            capture_output=True, text=True, cwd=repo_root, timeout=5
        )
        return int(result.stdout.strip()) if result.returncode == 0 else 0
    except (Exception, ValueError):
        return 0


def _git_merge_squash(repo_root, ref, message):
    """Squash-merge ``ref`` (a wave member's branch) onto the current branch.

    Runs in the MAIN worktree (``cwd=repo_root``, on the track branch). ``git
    merge --squash <ref>`` stages all of the member branch's changes since the
    merge-base (the wave's ``base_sha``) WITHOUT updating HEAD or recording a
    merge — then we commit them as ONE squashed commit. This preserves the
    "one conductor commit per task" linear audit trail and drops the agent's
    intermediate red/green WIP commits that would muddy the F2 story. (``git
    cherry-pick`` has no ``--squash``; ``merge --squash`` is the canonical tool.)

    The caller MUST guard the empty case first (``_git_range_commit_count == 0``
    → agent committed nothing → FAILURE); a squash-merge of an up-to-date branch
    stages nothing and the commit would fail. Returns the new HEAD SHA (str) on
    success, or ``None`` on conflict/failure (the merge is aborted and the index
    left clean so the track branch is unchanged — the caller fails the member).
    """
    try:
        import subprocess
        merge = subprocess.run(
            ["git", "merge", "--squash", ref],
            capture_output=True, text=True, cwd=repo_root, timeout=30
        )
        if merge.returncode != 0:
            # Conflict (or dirty tree) — abort and leave the index clean so the
            # track branch is unchanged. Caller marks the member conflict→fail.
            subprocess.run(
                ["git", "merge", "--abort"],
                capture_output=True, text=True, cwd=repo_root, timeout=10
            )
            # Defensive: clear any residual staged squash state.
            subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                capture_output=True, text=True, cwd=repo_root, timeout=10
            )
            print(f"WARNING: merge --squash conflict for '{ref}': "
                  f"{merge.stderr.strip() or merge.stdout.strip()}", file=sys.stderr)
            return None
        commit = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True, text=True, cwd=repo_root, timeout=10
        )
        if commit.returncode != 0:
            # Nothing staged (empty merge) or commit refused — reset and signal.
            subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                capture_output=True, text=True, cwd=repo_root, timeout=10
            )
            print(f"WARNING: squash commit failed: {commit.stderr.strip()}",
                  file=sys.stderr)
            return None
        return _git_head_sha(repo_root)
    except Exception as e:
        print(f"WARNING: merge --squash error: {e}", file=sys.stderr)
        return None


def _git_worktree_remove(repo_root, worktree_path):
    """Remove a worktree (``git worktree remove --force``). Best-effort."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            capture_output=True, text=True, cwd=repo_root, timeout=15
        )
        if result.returncode != 0:
            print(f"WARNING: git worktree remove failed: {result.stderr.strip()}",
                  file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"WARNING: git worktree remove error: {e}", file=sys.stderr)
        return False


def _git_branch_delete(repo_root, branch):
    """Delete a branch (``git branch -D``). Best-effort; never raises."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "branch", "-D", branch],
            capture_output=True, text=True, cwd=repo_root, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False

