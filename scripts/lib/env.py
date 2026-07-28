"""Shared library for environment variable handling

Provides unified environment variable retrieval and validation functionality.
"""

import os
import sys
from pathlib import Path
from typing import Optional


def get_plugin_root() -> Path:
    """Get plugin root directory

    Resolution priority (ground-truth first):
      1. ``__file__``-based root — env.py lives at ``<plugin>/scripts/lib/env.py``,
         so its parent.parent.parent is ALWAYS the true plugin root and is always
         available (we are executing from inside the plugin tree).
      2. ``$CLAUDE_PLUGIN_ROOT`` — trusted ONLY when it resolves to the same path
         as the ``__file__`` root. A stale/wrong env var (a different plugin's
         install, a subshell, a wrapper dir) used to win unconditionally and make
         scripts like scaffold-strategy.py HALT on a ``templates/`` path it
         computed wrong. Never let a hint override ground truth without checking.

    Returns:
        Plugin root directory path
    """
    # Ground truth: env.py is at <plugin>/scripts/lib/env.py.
    file_root = Path(__file__).resolve().parent.parent.parent

    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        env_root_resolved = Path(env_root).resolve()
        if env_root_resolved == file_root:
            return env_root_resolved
        # Mismatch → the env var is stale or points elsewhere. Prefer the
        # file-derived root (always correct) and surface the discrepancy so a
        # genuinely broken install is still diagnosable rather than a silent
        # HALT deep inside a downstream script.
        print(
            f"WARNING: CLAUDE_PLUGIN_ROOT={env_root!r} does not match the "
            f"plugin location derived from this file ({file_root}); using "
            f"{file_root}. Fix the env var if this plugin was relocated.",
            file=sys.stderr,
        )
    return file_root


def resolve_data_dir() -> tuple[Path, str]:
    """Resolve the runtime data dir AND report which tier fired.

    Returns ``(data_dir, tier_label)``. The path follows the resolution
    priority documented in ``get_data_dir`` (explicit override → project → cwd
    heuristic → plugin fallback). ``tier_label`` is a short human-readable name
    of the tier that fired, so callers that surface "where did logs land?"
    (e.g. ``track-state log-path``) report the tier that *actually* fired
    instead of re-deriving it and risking drift.

    This is the single source for the tier ladder — ``get_data_dir`` and
    ``track_state.logs_read._resolve_tier`` both delegate here so the order and
    labels can't diverge.
    """
    explicit = os.environ.get("CLAUDE_PLUGIN_DATA")
    if explicit:
        return Path(explicit), "CLAUDE_PLUGIN_DATA (explicit override)"
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return Path(project_dir) / ".conductor", "CLAUDE_PROJECT_DIR (project)"
    # cwd heuristic: Claude Code doesn't always inject ``CLAUDE_PROJECT_DIR``
    # (seen empty even in live session shells), so without this tier the
    # resolver would fall straight through to ``<plugin>/.data`` and land every
    # log/failure event in the *plugin* tree instead of the project being run.
    # The plugin only runs where a conductor track tree is present, so a
    # ``conductor/tracks/`` dir in ``cwd`` is a reliable "we're in a real
    # project" signal. The plugin repo itself has ``conductor/design/`` but no
    # ``conductor/tracks/``, so this never false-positives on the plugin dir.
    cwd = Path.cwd()
    if (cwd / "conductor" / "tracks").is_dir():
        return cwd / ".conductor", "cwd heuristic (conductor/tracks/ present)"
    # Fail-safe: plugin-anchored (always writable, always exists). This is the
    # LAST resort — logs written here COLLIDE across concurrent projects (a
    # user running Conductor in two projects at once gets one merged,
    # unreadable file). Emit a one-line stderr warning so the trap is visible
    # and the user knows to set CLAUDE_PROJECT_DIR or run from the project root.
    # One-shot per process: the warning is advisory; don't spam a long-lived
    # caller that resolves repeatedly.
    _warn_plugin_fallback_once()
    return get_plugin_root() / ".data", "PLUGIN FALLBACK (shared — collides across projects!)"


def get_data_dir() -> Path:
    """Get the runtime data directory for project-scoped telemetry.

    Thin wrapper over ``resolve_data_dir`` (which owns the full tier ladder).
    Resolution priority (explicit override first, project next, plugin last):

      1. ``$CLAUDE_PLUGIN_DATA`` — explicit override (tests, sandboxes, custom
         layouts). Returned verbatim.
      2. ``$CLAUDE_PROJECT_DIR/.conductor`` — the project's runtime dir.
         Conductor's logs/failures/recovery events are *project-scoped* (they
         describe a specific project's tracks), so they belong beside the
         project's ``conductor/`` tree — where you look when debugging — not
         under the shared plugin dir (which collides across projects).
         ``CLAUDE_PROJECT_DIR`` is set by Claude Code for every project hook,
         so this is the common path with zero config. The ``/.conductor/``
         root-anchored gitignore rule (setup §2.5) already covers it.
      3. ``$cwd/.conductor`` when ``$cwd/conductor/tracks/`` exists — a cwd
         heuristic that catches the common case where ``CLAUDE_PROJECT_DIR`` is
         not injected (it has been observed empty even in live session shells).
         Without this tier the resolver silently falls through to the plugin dir
         and every log/failure event lands in the plugin tree instead of the
         project being run. Gated on ``conductor/tracks/`` so it never matches
         the plugin repo itself (which has ``conductor/design/`` but no tracks).
      4. ``<plugin>/.data`` — fail-safe. Hooks that fire outside any project
         (e.g. session-start before a track exists, or a non-project cwd) still
         need a writable home; the plugin dir always exists.

    Returns:
        Data directory path (not yet created; callers mkdir as needed).
    """
    return resolve_data_dir()[0]


# Process-local guard so the plugin-fallback warning fires at most once per
# interpreter. Hooks are short-lived (one process per fire), but tests and any
# future long-lived caller resolve repeatedly — repeat warnings would be noise.
_PLUGIN_FALLBACK_WARNED = False


def _warn_plugin_fallback_once() -> None:
    """Emit a one-shot stderr warning that logs are landing in the plugin dir.

    The plugin fallback collides across concurrent multi-project use (two
    projects → one merged log file), which is the root cause of "I can't see
    my subagent events." Surfacing it loudly — rather than silently writing —
    is the difference between a fixable misconfiguration and an invisible one.
    Never raises.
    """
    global _PLUGIN_FALLBACK_WARNED
    if _PLUGIN_FALLBACK_WARNED:
        return
    _PLUGIN_FALLBACK_WARNED = True
    try:
        print(
            "[conductor] WARNING: no project context resolved (CLAUDE_PROJECT_DIR "
            "unset and cwd has no conductor/tracks/). Writing logs to the SHARED "
            "plugin dir, which collides across concurrent projects. Run from your "
            "project root or set CLAUDE_PROJECT_DIR to keep logs project-scoped.",
            file=sys.stderr,
        )
    except Exception:
        pass


def infer_project_dir_from_payload(input_data) -> Optional[str]:
    """Derive ``CLAUDE_PROJECT_DIR`` from a hook payload's ``cwd``, or ``None``.

    The problem this solves
    -----------------------
    ``get_data_dir`` resolves the cwd-heuristic tier from the *process* cwd
    (``Path.cwd()``). But a hook's process cwd is frequently the plugin dir or
    some wrapper — not the project the hook is actually operating on. The
    hook's logical project is in ``input_data["cwd"]`` (the payload). When
    ``$CLAUDE_PROJECT_DIR`` is unset AND the process cwd isn't a track root,
    the resolver silently falls through to the shared ``<plugin>/.data`` log —
    which collides across concurrent projects and is why users "can't see"
    their subagent events.

    This helper is called once per hook (from ``lib.hook_io.read_hook_input``)
    to promote the payload cwd into the env, so the rest of the process
    resolves the *project* correctly without each of the ~10 ``get_data_dir``
    call sites having to thread a new argument.

    Rules (first non-empty wins; never raises):
      - If ``$CLAUDE_PROJECT_DIR`` is already set, respect it — do nothing.
      - If the payload ``cwd`` (or its ancestors) contains
        ``conductor/tracks/``, that ancestor IS the project root.
      - Otherwise: ``None`` (leave the resolver to its tiers, including the
        loud plugin fallback).

    Accepts a dict (``{"cwd": ...}``) or ``None``.
    """
    try:
        if os.environ.get("CLAUDE_PROJECT_DIR"):
            return None
        if not isinstance(input_data, dict):
            return None
        raw = (input_data.get("cwd") or "").strip()
        if not raw:
            return None
        p = Path(raw)
        # Walk up looking for conductor/tracks — that ancestor is the project.
        for cand in (p, *p.parents):
            try:
                if (cand / "conductor" / "tracks").is_dir():
                    os.environ["CLAUDE_PROJECT_DIR"] = str(cand)
                    return str(cand)
            except OSError:
                continue
    except Exception:
        pass
    return None


def get_logs_dir() -> Path:
    """Get logs directory

    Returns:
        Logs directory path
    """
    data_dir = get_data_dir()
    return data_dir / "logs"


def get_session_id() -> Optional[str]:
    """Get current session ID

    Returns:
        Session ID or None
    """
    return os.environ.get("CLAUDE_SESSION_ID")


def get_permission_mode() -> Optional[str]:
    """Get current permission mode

    Returns:
        Permission mode or None
    """
    return os.environ.get("CLAUDE_PERMISSION_MODE")


def get_cwd() -> Optional[str]:
    """Get current working directory

    Returns:
        Working directory path or None
    """
    return os.environ.get("CLAUDE_CWD")


def ensure_env_vars(required_vars: list[str]) -> None:
    """Ensure required environment variables exist

    Args:
        required_vars: List of required environment variable names

    Raises:
        ValueError: When required environment variables are missing
    """
    missing_vars = []
    for var in required_vars:
        if not os.environ.get(var):
            missing_vars.append(var)

    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")


def is_remote_env() -> bool:
    """Check if running in remote environment

    Returns:
        True if running in remote environment
    """
    return os.environ.get("CLAUDE_CODE_REMOTE") == "true"


def is_compact_mode() -> bool:
    """Check if running in compact mode

    Returns:
        True if running in compact mode
    """
    return os.environ.get("CLAUDE_EFFORT") == "low"


# Common path quick accessors
def get_track_state_json(track_dir: Path) -> Path:
    """Get track-state.json file path

    Args:
        track_dir: Track directory

    Returns:
        track-state.json file path
    """
    return track_dir / "track-state.json"


def get_plan_md_path(track_dir: Path) -> Path:
    """Get plan.md file path

    Args:
        track_dir: Track directory

    Returns:
        plan.md file path
    """
    return track_dir / "plan.md"