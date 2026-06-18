"""Git subprocess operations: commit, SHA, notes."""
import json
import re
import sys
from pathlib import Path

from .core import update
from .helpers import target, now_iso, _store_evidence, conductor_dir, _normalize_sha
from .sync import _do_sync_plan


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

    try:
        import subprocess
        # Resolve full SHA (result may have 7-char short form)
        full_sha = subprocess.run(
            ["git", "rev-parse", sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        ).stdout.strip()
        if not full_sha:
            print(f"WARNING: git note skipped — cannot resolve SHA '{sha}'", file=sys.stderr)
            return
        result = subprocess.run(
            ["git", "notes", "add", "-f", "-m", note, full_sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        if result.returncode != 0:
            print(f"WARNING: git notes add failed for {sha}: {result.stderr.strip()}", file=sys.stderr)
    except (ImportError, subprocess.SubprocessError, FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as e:
        print(f"WARNING: git note write error for {sha}: {e}", file=sys.stderr)


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
        import subprocess
        diff_out = subprocess.run(
            ["git", "diff", "--name-only", f"{sha}~1", sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        if diff_out.returncode == 0 and diff_out.stdout.strip():
            files = ", ".join(diff_out.stdout.strip().split("\n"))
    except (ImportError, subprocess.SubprocessError, FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        pass  # Git unavailable - no file info available

    lines = [f"[Conductor] {task_name} ({loc})"]
    lines.append("Summary: (recovered — no runtime data available)")
    if files:
        lines.append(f"Files: {files}")
    lines.append("Coverage: (not recovered)")
    lines.append("Spec deviations: (not recovered)")

    note = "\n".join(lines)

    try:
        import subprocess
        full_sha = subprocess.run(
            ["git", "rev-parse", sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        ).stdout.strip()
        if not full_sha:
            print(f"WARNING: git note skipped — cannot resolve SHA '{sha}'", file=sys.stderr)
            return
        # Only write if no note exists
        existing = subprocess.run(
            ["git", "notes", "show", full_sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        if existing.returncode != 0:
            result = subprocess.run(
                ["git", "notes", "add", "-f", "-m", note, full_sha],
                capture_output=True, text=True, cwd=track_dir, timeout=5
            )
            if result.returncode != 0:
                print(f"WARNING: git notes add failed for {sha}: {result.stderr.strip()}", file=sys.stderr)
    except (ImportError, subprocess.SubprocessError, FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as e:
        print(f"WARNING: git note write error for {sha}: {e}", file=sys.stderr)


def _git_commit(track_dir, message, allow_empty=False):
    """Stage conductor state files and create a git commit. Returns True if committed.

    Only stages files that dispatch-finalize modifies: track-state.json,
    plan.md, .conductor/, and issues.md — never arbitrary untracked files.
    When allow_empty is True, creates a commit even with nothing staged (for SHA dedup).
    """
    try:
        import subprocess
        # Stage only conductor-managed files (not arbitrary untracked files)
        paths = ["track-state.json", "plan.md", ".conductor/", "issues.md"]
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
    pi, ti = int(p), int(t)
    si = int(s) if s is not None else None

    def mutate(state):
        tgt = state["phases"][pi - 1]["tasks"][ti - 1]
        if si is not None:
            tgt["subtasks"][si - 1]["commit_sha"] = sha
        else:
            tgt["commit_sha"] = sha
        return state

    state = update(track_dir, mutate)
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
    files (track-state.json, plan.md, .conductor/, issues.md, handoff.md).
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
                          "issues.md", "handoff.md")
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
        import subprocess
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
    """Check if a commit has a git note. If not, try to write one."""
    sha = tgt.get("commit_sha", "")
    if not sha:
        return

    try:
        import subprocess
        full_sha = subprocess.run(
            ["git", "rev-parse", sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        ).stdout.strip()
        if not full_sha:
            return

        # Check if note already exists
        existing = subprocess.run(
            ["git", "notes", "show", full_sha],
            capture_output=True, text=True, cwd=track_dir, timeout=5
        )
        if existing.returncode == 0 and existing.stdout.strip():
            return  # Note exists, nothing to do

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
    except (ImportError, subprocess.SubprocessError, FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as e:
        print(f"WARNING: _ensure_note failed for {sha}: {e}", file=sys.stderr)

