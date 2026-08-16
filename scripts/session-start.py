#!/usr/bin/env python3
"""SessionStart hook: inject runtime/core-contract.md + session handoff into session context.

On compact events, inject a compact summary to reduce context pressure.
"""

import os
import re
import sys
import time
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_simple_output
from lib.env import get_data_dir
from lib.json_utils import load_json_safe
from lib.path_utils import get_file_age_hours
from lib.frontmatter import check_corpus_frontmatter
from lib.atomic_io import atomic_write_text
from lib.logging import init_logging, log_entry

# The compact reminder's volatile lines are DERIVED, never re-typed: the Task
# State legend renders from track_state.constants.MARKER_MAP (the single source
# for status↔checkbox markers) and the commit line's type list renders from
# lib.constants.VALID_COMMIT_TYPES (V10's single source — same rendering as the
# pre-command-check deny message). F1-F6/V1-V11 stay static (names, not values).
sys.path.insert(0, str(Path(__file__).parent))
from track_state.constants import MARKER_MAP, SHA_MARKERS  # noqa: E402
from lib.constants import VALID_COMMIT_TYPES  # noqa: E402


def _task_state_line() -> str:
    parts = [
        f"[{marker}] {status}{' [sha]' if marker in SHA_MARKERS else ''}"
        for status, marker in MARKER_MAP.items()
    ]
    return "Task State: | " + " | ".join(parts) + " |"


COMPACT_CONTENT = f"""## Conductor Core (compact)

Post-compaction step 1: run `track-state recover <td>`, switch on status; never rely on memory.

{_task_state_line()}

Commit: <{VALID_COMMIT_TYPES.strip("()")}>(<scope>): <description>

Firewall: F1(state lock) F2(TDD) F3(coverage) F4(SHA) F5(checkpoint) F6(context guard)
Anti-patterns: V1-V11. Violation -> STOP -> WORKFLOW VIOLATION: <code> -> revert."""


def get_session_handoff(data_dir: Path) -> str:
    """Get session handoff content from previous session

    Args:
        data_dir: Data directory path

    Returns:
        Handoff content or empty string
    """
    handoff_file = data_dir / "session-handoff.md"
    if handoff_file.exists():
        try:
            return f"\n\n--- Previous Session Handoff ---\n{handoff_file.read_text(encoding='utf-8')}"
        except Exception:
            pass
    return ""


def get_conductor_content(plugin_root: Path, source: str) -> str:
    """Get conductor content based on source type

    Args:
        plugin_root: Plugin root directory
        source: Source type (startup or compact)

    Returns:
        Content to inject
    """
    if source == "compact":
        return COMPACT_CONTENT

    # Load full runtime/core-contract.md
    instructions_file = plugin_root / "runtime" / "core-contract.md"
    if instructions_file.exists():
        try:
            return instructions_file.read_text(encoding="utf-8")
        except Exception as e:
            # A broken/missing contract would silently degrade the whole session
            # to the 7-line compact stub — surface it so it isn't invisible.
            print(f"[conductor session-start] WARNING: runtime/core-contract.md "
                  f"unreadable ({e}); falling back to compact stub.", file=sys.stderr)
    else:
        print(f"[conductor session-start] WARNING: runtime/core-contract.md missing; "
              f"falling back to compact stub.", file=sys.stderr)

    return COMPACT_CONTENT


_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def _count_broken_wikilinks(file_path: Path, project_root: Path) -> int:
    """Count [[wikilinks]] in a single file that don't resolve (append .md, project-relative)."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception:
        return 0
    broken = 0
    for target in _WIKILINK.findall(text):
        # Resolution rule (core contract): path relative to project root, append .md.
        candidate = project_root / (target + ".md")
        if not candidate.exists():
            # Also try the raw target (some links already include an extension/anchor).
            if not (project_root / target).exists():
                broken += 1
    return broken


def get_wiki_drift_warnings(project_root: Path) -> str:
    """Cheap, high-signal wiki-drift scan run at session start (GC pillar).

    Advisory only — surfaced as additional_context, never blocks. Three signals:
    overview.md staleness (>30d), scoped docs missing provenance frontmatter,
    and broken [[wikilinks]] in overview.md (the auto-owned synthesis that must
    never reference a non-existent doc). Returns '' when the wiki is absent or clean.
    """
    conductor = project_root / "conductor"
    if not conductor.is_dir():
        return ""

    warnings = []

    # 1. overview.md staleness (regenerated each track via wiki-synthesizer Phase 2).
    overview = conductor / "overview.md"
    age_h = get_file_age_hours(overview)
    if age_h is not None and age_h > 30 * 24:
        warnings.append(
            f"overview.md is {int(age_h // 24)}d stale — run /conductor:implement on a "
            f"track (or /conductor:wiki ingest) to regenerate it."
        )

    # 2. scoped docs missing provenance frontmatter (evidence-based drift, O4).
    fm_findings = check_corpus_frontmatter(conductor)
    if fm_findings:
        warnings.append(
            f"{len(fm_findings)} scoped doc(s) missing provenance frontmatter "
            f"(type/sources/last_verified) — run /conductor:wiki-doctor lint."
        )

    # 3. broken [[wikilinks]] in overview.md (auto-owned; must never dangle).
    # The scan reads only overview.md (cost ∝ its wikilink count, not corpus
    # size), so it needs no corpus-size guard — counting the whole tree just to
    # decide whether to read one file is wasted work on a session-start hot path.
    if overview.exists():
        broken = _count_broken_wikilinks(overview, project_root)
        if broken:
            warnings.append(
                f"{broken} broken [[wikilink]](s) in overview.md — run "
                f"/conductor:wiki-doctor lint."
            )

    if not warnings:
        return ""
    body = "\n".join(f"- {w}" for w in warnings)
    return f"\n\n--- Wiki drift (advisory GC) ---\n{body}\n"


def get_loop_digest(project_root: Path) -> str:
    """Comprehension-debt nudge: re-surface the latest active track's high-risk
    review findings so the operator reads what the loop shipped.

    Countermeasure to Osmani's comprehension debt — "the faster the loop ships
    code you didn't write… unless you read what the loop made." The post-loop
    §7.5 digest fires once per track before archive; this re-surfaces those same
    Critical/High findings on every non-compact SessionStart, the deterministic
    event the loop-heartbeat ADR sanctions (no wall-clock cron, no watermark to
    GC — the nudge self-retires once the track archives). Advisory only: returns
    '' when there is nothing worth surfacing, and the whole body is wrapped so a
    malformed review/state file can never break session bootstrap.

    Resolution mirrors ``scripts/lib/result_probe.py``: tracks live at
    ``{project_root}/conductor/tracks/<id>/`` with review files at
    ``{TRACK_DIR}/.conductor/review-result.json``. Terminal-status tracks
    (``archived``/``cancelled``) are filtered out *before* picking the newest by
    mtime, so a freshly-archived track can't shadow an active one. ``completed``
    is NOT terminal — a finalized-but-unarchived track still nudges.

    Limitation: if ``project_root`` isn't the real project root (e.g. resumed
    from a subdir) the glob silently misses, same tolerance as the wiki-drift
    scan. We deliberately do not walk upward (monorepo wrong-project risk).
    """
    try:
        base = Path(project_root)
        candidates = []
        for p in base.glob("conductor/tracks/*/.conductor/review-result.json"):
            track_dir = p.parents[1]  # conductor/tracks/<id>/
            state = load_json_safe(track_dir / "track-state.json", default={})
            if not isinstance(state, dict):
                state = {}
            if state.get("status") in ("archived", "cancelled"):
                continue  # terminal — neither nag forever nor shadow an active track
            candidates.append(p)
        if not candidates:
            return ""
        chosen = max(candidates, key=lambda p: p.stat().st_mtime)
        data = load_json_safe(chosen, default=None)
        if not isinstance(data, dict):
            return ""
        findings = data.get("findings")
        if not isinstance(findings, list):
            return ""
        # .capitalize() tolerates LLM severity-casing drift (critical / HIGH).
        hits = [
            f for f in findings
            if isinstance(f, dict)
            and (f.get("severity", "") or "").capitalize() in ("Critical", "High")
        ]
        if not hits:
            return ""

        track_id = chosen.parents[1].name
        lines = [
            f"Track `{track_id}` — {len(hits)} Critical/High review finding(s) "
            f"you haven't re-read:"
        ]
        for f in hits[:3]:
            title = f.get("title") or "(untitled)"
            file_ = f.get("file") or ""
            loc = f.get("lines") or ""
            where = f"{file_}:{loc}" if (file_ and loc) else (file_ or loc)
            lines.append(f"- {title}" + (f" ({where})" if where else ""))
        if len(hits) > 3:
            lines.append(f"(+{len(hits) - 3} more)")
        lines.append(
            "Re-read these before starting new work — this is how comprehension "
            "debt accrues."
        )
        lines.append(f"Full review: {chosen}")
        body = "\n".join(lines)
        return f"\n\n--- Loop digest (advisory) ---\n{body}\n"
    except Exception:
        # Advisory context must never break session bootstrap.
        return ""


def _write_session_start(data_dir: Path, session_id: str) -> None:
    """Stamp the session start time so the SessionEnd hook can log duration.

    Writes ``int(time.time())`` atomically to ``<data_dir>/logs/.session-{id}.start``;
    ``data_dir`` is the project-scoped resolver output (``CLAUDE_PROJECT_DIR/.conductor``
    by default — see ``lib.env.get_data_dir``).
    ``session-end.py::log_session_duration`` reads it, appends a
    ``duration_seconds=`` line to ``session-metrics.log``, and unlinks it. This
    closes the loop that was dead (session-end read a file nothing wrote).
    Missing ``session_id`` or a write failure is non-fatal — metrics are
    best-effort and must never break session bootstrap.
    """
    if not session_id:
        return
    try:
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(log_dir / f".session-{session_id}.start",
                          str(int(time.time())))
    except OSError:
        pass


def _warn_if_backgrounding_enabled(data_dir: Path) -> None:
    """Log a warning if background subagents are not disabled at session start.

    The conductor is a serial spine: one agent owns a locked task at a time,
    enforced by the PreToolUse:Agent dedupe hook. Background subagents
    (``CLAUDE_AUTO_BACKGROUND_TASKS`` auto-backgrounds after ~2 min;
    ``CLAUDE_CODE_FORK_SUBAGENT`` forces all spawns to background) reorder
    ``SubagentStop`` and break that invariant — the exact "subagent re-runs
    while the previous is still running" relapse. The plugin's settings.json
    sets ``CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`` (the hard off-switch for
    the whole subsystem), but a user override or a future update that clears
    it would reopen the window invisibly. This makes the regression noisy in
    the session log instead of silent. Best-effort; never breaks bootstrap.
    """
    flag = os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "")
    if flag == "1":
        return
    try:
        log_file = init_logging("session-start")
        log_entry(
            log_file,
            "WARNING CLAUDE_CODE_DISABLE_BACKGROUND_TASKS is not '1' — background "
            "subagents may be active, which breaks the conductor's single-writer "
            "invariant (a backgrounded agent reorders SubagentStop and can cause "
            "duplicate dispatches). Set CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1.",
            level="WARNING",
        )
    except Exception:
        pass


def main():
    """Main hook function"""
    # Read hook input
    input_data = read_hook_input()
    source = input_data.get("source", "startup")
    session_id = input_data.get("session_id", "")

    # Get paths
    plugin_root = Path(__file__).parent.parent
    data_dir = get_data_dir()

    # Stamp session start for duration metrics. Skipped on compact — compaction
    # is a mid-session event and would reset the timer (startup's stamp must
    # survive so end-of-session measures the full duration).
    if source != "compact":
        _write_session_start(data_dir, session_id)
        _warn_if_backgrounding_enabled(data_dir)

    # Get content
    content = get_conductor_content(plugin_root, source)

    # Add session handoff if exists
    handoff = get_session_handoff(data_dir)
    full_content = content + handoff

    # Advisory scans — skip on compact (keep that context minimal).
    if source != "compact":
        cwd_str = input_data.get("cwd", "")
        project_root = Path(cwd_str) if cwd_str else Path.cwd()
        full_content += get_wiki_drift_warnings(project_root)
        full_content += get_loop_digest(project_root)

    # Output
    write_simple_output(additional_context=full_content)


if __name__ == "__main__":
    main()