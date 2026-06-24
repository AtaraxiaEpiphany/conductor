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
