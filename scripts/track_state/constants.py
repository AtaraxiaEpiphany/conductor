"""Shared constants for track-state modules."""
import re

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

TERMINAL_STATUSES = {"completed", "skipped", "deferred", "blocked", "cancelled"}
TERMINAL_FOR_PARENT = TERMINAL_STATUSES | {"failed"}
AUTO_COMPLETE_OK = TERMINAL_STATUSES

_RE_TRAILING_MARKER = re.compile(
    r'\s*\[(?:N/A|verified|[0-9a-f]{7}(?:\s*,\s*[0-9a-f]{7})*)\]\s*$'
)

_RESET_FIELDS = [
    "commit_sha", "completed_at", "retry_count", "last_failure_summary",
    "skip_analysis", "defer_reason", "evidence",
]
