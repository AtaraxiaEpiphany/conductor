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


def task_max_retries(task, shape=None):
    """Per-task retry ceiling: task → shape → global ``MAX_RETRIES``.

    Single resolver for "how many attempts does this task get" — every enforcement
    site (``mutations._do_fail`` requeue decision, ``_do_fail_parent`` pin,
    ``dispatch._find_failed_exhausted``) reads the ceiling through here rather than
    the bare ``MAX_RETRIES``, so a task carrying ``max_retries`` is honored. The
    chain: a task-level ``max_retries`` (int ≥ 1) wins; else the track's workflow
    shape's ``max_retries`` (``workflow_shapes.max_retries_for`` — a shape-level
    default for its job family); else the global. ``shape`` accepts the resolved
    shape NAME (string) callers already hold from
    ``state["workflow_shape"]`` — import kept lazy so ``constants`` stays
    import-light for the hooks. Absent/invalid at any tier → next tier;
    defensive so a corrupt value can't zero out a task's retry budget.
    """
    mr = task.get("max_retries") if isinstance(task, dict) else None
    if isinstance(mr, int) and mr >= 1:
        return mr
    if shape:
        from . import workflow_shapes as ws
        smr = ws.max_retries_for(shape)
        if smr >= 1:
            return smr
    return MAX_RETRIES


# Loop-until-dry recovery backstops for the failure-analyze retry arm
# (dispatch.py ``_step_route_failure_analysis``). The retry arm keeps
# re-analyzing + retrying while the failure-analyst produces NOVEL root causes;
# TWO independent backstops stop it (the "twin backstop"):
#
#  RECOVERY_DRY_K — the "converged" signal: after this many CONSECUTIVE rounds
#    whose root_cause was already seen (no new diagnosis), the router halts
#    (escalate). The analyst has nothing new to offer — looping further would
#    just repeat a known-bad modification. Novelty is computed in
#    cmd_failure_analyst_verdict (which sees the root_cause) and stamped on the
#    failure-analysis marker as ``seen_root_causes`` + ``consecutive_empty_rounds``.
#  MAX_RECOVERY_ROUNDS — the hard budget: a per-track ceiling on TOTAL analysis
#    rounds regardless of novelty, so a run of distinct-but-wrong diagnoses can't
#    burn budget forever. Read fail-open; a future per-shape ``max_recovery_rounds``
#    field will tune this per workflow.
#
# Both → ``_halt("escalate")``. The retry/decompose/skip/escalate arms are
# otherwise fully automated; single-homed in runtime/contracts/recovery-policy.md.
RECOVERY_DRY_K = 2
MAX_RECOVERY_ROUNDS = 4

# Per-PASE hard budget (the phase-recovery twin backstop's ceiling; the dry arm
# reuses the shared ``RECOVERY_DRY_K`` above). A phase checkpoint that FAILS on an
# auto-routing track routes through the phase-level failure-analyst; the retry arm
# reactivates the phase's tasks and re-fans the checkpoint. This caps how many
# such analyze→retry→re-fail rounds a single phase may burn (distinct-but-wrong
# diagnoses can't loop forever) before ``escalate``→halt. Lower than the task-level
# ceiling because a phase round re-runs the whole phase (heavier than one task).
# Single-homed in runtime/contracts/recovery-policy.md (§ "Phase-level recovery").
MAX_PHASE_RECOVERY_ROUNDS = 3

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

# Recovery policies (schema: track-state.schema.json). DECOUPLES the failed-task
# recovery decision from ``execution_mode`` — the two read independently. Read at
# every failed+exhausted decision site via ``dispatch._auto_route_failure``.
#  ask  (legacy): an interactive track surfaces a Retry/Skip/Block ``ask`` on a
#                 failed+exhausted task (continuous still auto-routes). Existing
#                 tracks without the field read as ``ask`` (state.get default).
#  auto         : routes straight to the skip-analyst handshake REGARDLESS of
#                 execution_mode — opt an interactive track into auto-recovery
#                 without giving up checkpoint pausing. New tracks default to it
#                 (set by ``quality._init_core``).
RECOVERY_POLICIES = ("ask", "auto")

_RE_TRAILING_MARKER = re.compile(
    r'\s*\[(?:N/A|verified|[0-9a-f]{7}(?:\s*,\s*[0-9a-f]{7})*)\]\s*$'
)

_RESET_FIELDS = [
    "commit_sha", "completed_at", "retry_count", "last_failure_summary",
    "skip_analysis", "defer_reason", "evidence",
]
