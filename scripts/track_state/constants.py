"""Shared constants for track-state modules.

The status *terminal* / *auto-complete* sets are defined once in the shared
``lib.constants`` layer (they're read by hooks and linters too) and re-exported
here so track_state's internal ``from .constants import …`` keeps working.
Rendering-specific constants (MARKER_MAP, SHA_MARKERS) stay local — they're
only used by the markdown plan-sync path.
"""
import re

from lib.constants import (
    TERMINAL_STATUSES,
    TERMINAL_FOR_PARENT,
    AUTO_COMPLETE_OK,
)

MARKER_MAP = {
    "pending": " ",
    "in_progress": "~",
    "completed": "x",
    "failed": "!",
    "skipped": ">",
    "deferred": "d",
    "blocked": "#",
    "cancelled": "-",
}

SHA_MARKERS = {"x", "!", ">", "#", "-", "d"}

# Maximum retry attempts for a failed task before marking it permanently failed.
#
# Single source of truth for the retry threshold — never re-literal "3":
#  - Enforced by mutations._do_fail (re-queues as "pending" while retry_count <
#    MAX_RETRIES; flips to "failed" at the threshold) and _do_fail_parent (pins
#    retry_count = MAX_RETRIES so recover surfaces the parent as failed+max).
#  - Emitted in track-state output (recover, dispatch-prepare) as `max_retries`
#    so the implement skill routes on it without hardcoding the number, and
#    rendered by handoff as "{retry_count}/{max_retries}". Bump it here and
#    every consumer follows.
MAX_RETRIES = 3

# Execution modes for the implement skill (schema: track-state.schema.json).
# interactive: pauses for user confirmation at each phase checkpoint.
# continuous: auto-proceeds through all phases without pausing (phase-checker
#             skips its confirmation gate; [Manual] tasks auto-defer).
EXECUTION_MODES = ("interactive", "continuous")

_RE_TRAILING_MARKER = re.compile(
    r'\s*\[(?:N/A|verified|[0-9a-f]{7}(?:\s*,\s*[0-9a-f]{7})*)\]\s*$'
)

_RESET_FIELDS = [
    "commit_sha", "completed_at", "retry_count", "last_failure_summary",
    "skip_analysis", "defer_reason", "evidence",
]
