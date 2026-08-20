"""Shared constants for conductor hook scripts and the track-state machine.

This is the single source of truth for constants that cross the hook↔state-
machine boundary. track_state.constants re-exports the status sets below so its
internal ``from .constants import …`` keeps working; hooks and linters import
them directly from here (lightweight — lib has no package-init cost, unlike
importing the track_state package which pulls in the whole state machine).
"""

# --- Task status semantics --------------------------------------------------
# The status lifecycle is owned by the state machine, but the *terminal* /
# *auto-complete* sets are read by hooks and linters too (on-batch-complete,
# lint-track-state), so they live in this shared layer.
#
# TERMINAL_STATUSES: a task is done (no further work). "failed" is NOT terminal
#   here — a failed task can still be retried.
# TERMINAL_FOR_PARENT: statuses that release the parent for auto-completion.
#   Adds "failed": once a child has failed-and-exhausted-retries the parent can
#   still progress (see mutations._do_fail / MAX_RETRIES).
# AUTO_COMPLETE_OK: statuses that count toward a parent's auto-complete.
TERMINAL_STATUSES = {"completed", "skipped", "deferred", "blocked", "cancelled"}
TERMINAL_FOR_PARENT = TERMINAL_STATUSES | {"failed"}
AUTO_COMPLETE_OK = TERMINAL_STATUSES

# --- Recovery success indicators (only meaningful after [Conductor Recovery] marker) ---
RECOVERY_SUCCESS_PATTERNS = [
    r"status.*SUCCESS",
    r"All tests passed",
    r"Coverage:",
]

# --- Conventional commit format (V10 enforcement) ---
# Matches: type(scope): description — e.g. "feat(api): add user endpoint"
VALID_COMMIT_TYPES = r"(feat|fix|docs|style|refactor|test|chore)"
COMMIT_MSG_PATTERN = rf"^{VALID_COMMIT_TYPES}\([^)]+\):\s*.+"

# --- Build-artifact paths that must never land in an implementation commit ---
# task-executor's Step 8 ``git add -A`` sweeps the whole working tree; on a
# brownfield project with no/weak ``.gitignore`` that pulls in node_modules/,
# build outputs, caches, etc. These are also mirrored into the setup
# ``.gitignore`` template (templates/conductor-gitignore.md) as the primary
# architectural guardrail — this list is the hook backstop for projects whose
# ``.gitignore`` predates conductor. Matched as repo-relative path prefixes OR
# exact dir/file names anywhere in the path (e.g. ``src/__pycache__`` too).
BUILD_ARTIFACT_NAMES = {
    "node_modules", "dist", "build", "__pycache__", ".venv", "venv",
    "coverage", ".cache", ".next", ".nuxt", ".turbo", "out", "target",
}
BUILD_ARTIFACT_EXACT = {
    ".DS_Store", "Thumbs.db", "yarn-error.log", "npm-debug.log",
}
# A path is an artifact if any segment is in BUILD_ARTIFACT_NAMES, or its
# basename is in BUILD_ARTIFACT_EXACT. Kept as functions (not a regex) so the
# match is segment-aware (``dist`` matches ``dist/x`` and ``a/dist/x`` but not
# ``distribute.py``).
def is_build_artifact_path(path: str) -> bool:
    """True if a repo-relative path is a build artifact / cache that must never
    be committed by an implementation commit. Segment-aware."""
    if not path:
        return False
    parts = path.replace("\\", "/").split("/")
    if any(seg in BUILD_ARTIFACT_NAMES for seg in parts):
        return True
    return parts[-1] in BUILD_ARTIFACT_EXACT


# --- Transient runtime marker filenames (.conductor/ under a track) ---------
# Single home for every transient marker FILENAME / path TEMPLATE so the
# writer modules, the hooks that probe them, and quality.py's normative
# gitignore tuple (``_TRANSIENT_MARKERS``) cannot drift: derive = import the
# constant, never re-type it. Owner modules alias these under their historical
# private names (e.g. dispatch's ``_PHASE_CP_MARKER``); quality builds its
# gitignore patterns from the exact same constants.
#
# Concrete filenames:
RESULT_MARKER = "result.json"                      # task result; deleted by dispatch-finalize
NT_PROGRESS_MARKER = "new-track-progress.json"     # new-track resume marker (new_track.py)
PHASE_CHECKPOINT_MARKER = "phase-checkpoint.json"  # phase-checkpoint handshake (dispatch.py)
SKIP_ANALYSIS_MARKER = "skip-analysis.json"        # skip-analyze handshake (dispatch.py)
REVIEW_RESULT_MARKER = "review-result.json"        # post-loop review findings (dispatch.py)
REVIEW_SEEN_MARKER = "review-seen.json"            # self-review loop state (implement skill §3.6b prose)
WAVE_LEDGER_NAME = "parallel.json"                 # wave sidecar ledger (wave.py)
WAVE_MARKER_NAME = "wave-agent.marker"             # per-worktree SubagentStop short-circuit (wave.py)
WAVE_DRAIN_MARKER_NAME = ".wave-drain-processed"   # wave-step drain marker (wave.py)
DISPATCH_LOCK_NAME = ".dispatch.lock"              # flock target (lib/dispatch_lock.py)
BRIEF_PROGRESS_MARKER = "brief-progress.json"      # brief resume marker (brief.py)
FAILURE_ANALYSIS_MARKER = "failure-analysis.json"  # failure-analyst handshake (dispatch.py)
PHASE_RECOVERY_MARKER = "phase-recovery.json"      # phase-checkpoint recovery (dispatch.py)
AMENDMENT_STAGED_MARKER = "amendment-staged.json"  # spec-amendment staging (dispatch.py)
DISPATCH_MANIFEST_MARKER = "dispatch-manifest.md"  # per-dispatch workflow manifest (dispatch_manifest.py)

# Path templates. ``{sub}`` interpolates to ``-N`` or ``""``; the other fields
# are indices. quality.py derives each family's gitignore glob from the
# template (every field → ``*``, adjacent stars collapsed), so renaming or
# re-shaping a marker updates the gitignore automatically.
DISPATCH_INFLIGHT_TMPL = ".dispatch-inflight-{phase}-{task}{sub}.json"
TRIPWIRE_COUNT_TMPL = ".tripwire-{phase}-{task}{sub}.count"
MODIFIED_GUIDANCE_TMPL = ".modified-guidance-{pi}-{ti}{sub}.md"
AMENDMENT_GUIDANCE_TMPL = ".amendment-guidance-{pi}-{ti}{sub}.md"
