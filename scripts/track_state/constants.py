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
# The DEFAULT ceiling — per-task overrides via ``max_retries`` (see
# :func:`task_max_retries`) are honored at every enforcement site. Single source
# of truth for the default; never re-literal "3":
#  - Enforced by mutations._do_fail (re-queues as "pending" while retry_count <
#    task_max_retries(tgt); flips to "failed" at the threshold) and _do_fail_parent
#    (pins retry_count = task_max_retries(tgt) so recover surfaces the parent as
#    failed+max).
#  - Emitted in track-state output (recover, dispatch-prepare) as `max_retries`
#    so the implement skill routes on it without hardcoding the number, and
#    rendered by handoff as "{retry_count}/{max_retries}". Bump it here and
#    every consumer follows.
MAX_RETRIES = 3


def task_max_retries(task):
    """Per-task retry ceiling, falling back to the global ``MAX_RETRIES``.

    Single resolver for "how many attempts does this task get" — every enforcement
    site (``mutations._do_fail`` requeue decision, ``_do_fail_parent`` pin,
    ``dispatch._find_failed_exhausted``) reads the ceiling through here rather than
    the bare ``MAX_RETRIES``, so a task carrying ``max_retries`` is honored. Absent
    / invalid (non-int or < 1) → global default; defensive so a corrupt value can't
    zero out a task's retry budget.
    """
    mr = task.get("max_retries") if isinstance(task, dict) else None
    return mr if isinstance(mr, int) and mr >= 1 else MAX_RETRIES


# Bound on consecutive failure-analyst rounds for one task (dispatch.py
# ``_step_route_failure_analysis``). A failure-analyst whose ``retry_modified``
# verdict fails again and re-triggers another failure-analyst is the loop this
# caps: past the limit the router falls through to ``escalate``→halt instead of
# re-analyzing. Mirrors ``MAX_RECOVERY_TURNS`` (lib/recovery.py) in spirit.
#
# 2 = the analyst gets ONE refinement round on a failed modified-retry (round 1
# prescribes the modification; if it fails, round 2 can prescribe a different one)
# before escalating. Raising it further risks burning budget on a stuck task;
# lowering to 1 gives no refinement at all.
MAX_ANALYSIS_ROUNDS = 2

# Stuck-lock heartbeat. ``_do_lock`` stamps ``locked_at`` (epoch seconds) on the
# task; a task still ``in_progress`` past this threshold is treated as a
# killed-session orphan and reaped to ``pending`` by
# ``validate._fix_stale_lock`` (surfaced via ``recover``'s ``fixes_applied``).
# Shorter than the legacy 24h ``updated_at`` reaper so a killed session unblocks
# in minutes, not hours; tasks missing ``locked_at`` (pre-change state) are left
# to the 24h reaper, which can still determine their age via ``updated_at``.
STALE_LOCK_SECONDS = 1800  # 30 min
LOCKED_AT_FIELD = "locked_at"  # epoch-seconds key on the task object

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
