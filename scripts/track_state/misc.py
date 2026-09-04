"""Miscellaneous track-state commands."""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from .core import load, save
from lib.markers import json_marker_read  # tolerant marker read, single-homed
from .helpers import (
    out, now_iso, target, extract_tags, _reset_task,
    _any_phase_needs_checkpoint, conductor_dir, _tag_exempt_from_coverage,
    _resolve_conductor_root, _find_registry,
)
from .mutations import _do_complete
from .sync import _do_sync_plan
from .git_ops import _git_commit, _git_head_sha, _ensure_note, docs_synced_for_track
from .constants import TERMINAL_FOR_PARENT
from .quality import _checklist_status, _to_number
from .spec_integrity import compute_ac_integrity
from .handoff import compile_track_findings


# Core conductor files every executable track must have. Single source for the
# setup check repeated (with drift) across skills — preflight centralizes it.
_TRACK_CORE_FILES = ("spec.md", "plan.md", "track-state.json")

# Project-level workflow index every /conductor:implement run depends on
# (implement §1.0 reads index.md). It lives at the conductor ROOT, not inside
# the track dir, so preflight resolves the root from the track path and checks
# it alongside the track-core files. Fail-open: when
# no conductor root is locatable (no tracks.md ancestor — e.g. a bare temp dir),
# the workflow check is skipped rather than failing ok, so a resolution miss can
# never HALT setup on a non-standard layout (and the existing preflight tests,
# which use temp dirs without a project layout, stay green).
_WORKFLOW_FILES = ("workflow/index.md",)

# Track-level non-terminal statuses eligible for auto-select — exactly the
# `[~]`/`[ ]` markers the skills auto-select on. (Task-level `pending` is NOT a
# track status; `TERMINAL_STATUSES` is task-level and omits `archived`/`failed`,
# so neither constant fits here.)
_TRACK_NON_TERMINAL = ("new", "in_progress")

# Inverse of the inline ``status_to_marker`` in ``cmd_registry_update`` (the
# 9-entry registry map — NOT ``constants.MARKER_MAP``, which is for plan.md and
# lacks ``new``/``archived``). Used to read a registry marker back to a status
# when ``track-state.json`` is unavailable.
_REGISTRY_MARKER_TO_STATUS = {
    " ": "new", "~": "in_progress", "x": "completed", "@": "archived",
    "#": "blocked", "-": "cancelled", "d": "deferred", ">": "skipped", "!": "failed",
}

# Inverse of ``_REGISTRY_MARKER_TO_STATUS`` — the single source for status ->
# marker, shared by ``cmd_registry_add`` (canonical write) and
# ``cmd_registry_update`` (marker sync). Hoisted from the inline dict that used
# to live in ``cmd_registry_update`` so the two registry writers cannot drift.
_STATUS_TO_MARKER = {
    "new": " ", "in_progress": "~", "completed": "x", "archived": "@",
    "blocked": "#", "cancelled": "-", "deferred": "d", "skipped": ">", "failed": "!",
}

# Read-side regexes mirroring the write-side matchers in ``cmd_registry_update``.
_RE_SECTION_HEAD = re.compile(r"^###\s+(\S+)")
_RE_SECTION_STATUS = re.compile(r"^\s*-\s+\*\*Status:\*\*\s+(\S+)")
# Checkbox: ``- [marker] description (link/)``. The link is the TRAILING
# parenthetical, so group 3 uses GREEDY ``.*\(`` to reach the LAST ``(`` on the
# line. A non-greedy ``.*?\(`` would stop at the first ``(`` — which is inside
# the description when it contains parens (``- [~] Add SSO (OAuth2) login
# (conductor/tracks/sso_20260706/)``) — and capture ``OAuth2`` as the link,
# silently resolving to a bogus track_dir. The write-side matcher in
# ``cmd_registry_update`` is identical and MUST stay in sync; reconstruction is
# unaffected (the groups still partition the whole line, only the marker swaps).
_RE_CHECKBOX = re.compile(r"^(\s*-\s+\[)([ x~!>#\-d@])(\]\s+.*\()([^)]*)(\).*)$")
_RE_SHORTNAME_DATE = re.compile(r"_\d{8}$")
_RE_TABLE_STATUS = re.compile(r"\b(new|in_progress|completed|archived|blocked|cancelled|deferred|skipped|failed)\b")
# Universal track_id token. ``cmd_derive_name`` always emits ``<slug>_<YYYYMMDD>``
# (slug = [a-z0-9_]; see cmd_derive_name), so EVERY real track_id matches this.
# Used as a backstop in ``_iter_registry_entries`` for lines the format-specific
# branches drop — the freeform entries ``new-track`` §2.6 historically wrote
# without a parseable trailing ``(link)``: plain bullets (``- auth_20260706``),
# bold ids (``**auth_20260706**``), checkbox-without-link (``- [~] auth_20260706``),
# inline mentions. High signal / low false positive: it requires a slug, an
# underscore, and exactly-eight trailing digits, so prose dates (``2026-07-06``)
# and SHAs don't match. Keep in sync with the twin in ``lib/path_utils.py``.
_RE_TRACK_ID_TOKEN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_]*_\d{8})")


def _roster_lint_findings():
    """Declared-names + validity lint over the resolved agent-roster (design D4).

    Runtime is fail-open by design — a dispatch hook never denies over a
    registry — so this is where a broken overlay gets LOUD: ``track-state
    check`` surfaces it before the first dispatch, not as a mysteriously
    unscaffolded agent mid-track. Two families:

    - **validity** — ``validate_merged_roster`` over the resolved document
      (unknown class, ``recovery_instruction`` without a recovery kind, …);
    - **declared-names-exist** — every roster row name and every merged
      shape's ``verifiers`` + ``nodes`` entry must resolve to a live
      agent-definition file in one of the three harness homes (plugin
      ``agents/`` ∪ project ``.claude/agents/`` ∪ user ``~/.claude/agents/``);
      a declared name with no file anywhere is a typo/dead name, never a
      dispatchable agent.

    Returns a list of human-readable finding strings; empty = clean.
    """
    from . import agent_roster as ar
    from . import workflow_shapes as ws
    from .registry_validate import validate_merged_roster

    findings = [f"agent-roster: {e}"
                for e in validate_merged_roster(ar._load())]  # noqa: SLF001 — registry-internal resolved-doc lookup

    live = set(ar.agent_file_names())
    dead_rows = sorted(n for n in ar.merged_agent_names() if n not in live)
    if dead_rows:
        findings.append(
            "agent-roster rows name agents with no definition file in any "
            "harness home (plugin agents/, project .claude/agents/, user "
            f"~/.claude/agents/): {', '.join(dead_rows)}")

    dead_shape_names = []
    for shape in ws.SHAPES_VOCAB():
        for field, names in (("verifiers", ws.verifiers_for(shape)),
                             ("nodes", ws.nodes_for(shape))):
            for name in names:
                if name not in live:
                    dead_shape_names.append(f"{shape}.{field}: {name}")
    if dead_shape_names:
        findings.append(
            "workflow-shape rows declare agents with no definition file in "
            "any harness home: " + ", ".join(dead_shape_names))
    return findings


def _probe_lint_findings():
    """Lint the resolved probe registry (baseline ⊕ overlay) for ``check``.

    Mirrors :func:`_roster_lint_findings` for the fourth registry:
    ``validate_probes_doc`` over the resolved document — an unimplemented
    builtin, an orphaned command, an unknown kind, or a missing description is
    loud at check (runtime stays fail-open: an invalid row degrades to the
    unknown-probe response, never a crash). Returns a list of human-readable
    finding strings; empty = clean.
    """
    from . import probes
    from .registry_validate import validate_probes_doc

    return [f"probes: {e}" for e in validate_probes_doc(probes._load())]  # noqa: SLF001 — registry-internal resolved-doc lookup


def _preflight_result(track_dir):
    """Compute the preflight envelope as a dict — factored body of
    ``cmd_preflight`` so ``cmd_setup`` can compose it without capturing stdout.

    Also carries the agent-roster lint (:func:`_roster_lint_findings`) as
    ``roster_errors`` and the probe-registry lint
    (:func:`_probe_lint_findings`) as ``probe_errors`` — non-empty makes
    ``ok`` false, so a broken overlay is loud at ``check`` (runtime stays
    fail-open by design).
    """
    td = Path(track_dir)
    missing = [f for f in _TRACK_CORE_FILES if not (td / f).exists()]
    invalid_state = False
    if not missing:
        try:
            load(track_dir)
        except Exception:
            invalid_state = True

    roster_errors = _roster_lint_findings()
    probe_errors = _probe_lint_findings()

    # Project-level workflow files. Skipped (empty) when no conductor root is
    # locatable — fail-open so this never blocks setup on an unusual layout. A
    # registry placed at the project root (``<root>/tracks.md``) makes
    # ``_resolve_conductor_root`` return the project root, but the workflow dir
    # still canonically lives at ``<root>/conductor/workflow/`` — so probe BOTH
    # ``<conductor_root>/<f>`` and ``<conductor_root>/conductor/<f>`` before
    # declaring a file missing (mirrors ``_resolve_track_dir``'s candidate probe).
    conductor_root = _resolve_conductor_root(track_dir)
    missing_workflow = []
    if conductor_root is not None:
        for f in _WORKFLOW_FILES:
            if (conductor_root / f).exists():
                continue
            if not (conductor_root / "conductor" / f).exists():
                missing_workflow.append(f)

    # Defense-in-depth: a mispassed track_dir (the registry file, or the
    # conductor root) yields the generic missing=[spec,plan,state] failure that
    # looks identical to a genuinely unbuilt track. Surface a targeted hint so
    # the skill's HALT message is actionable instead of mysterious. ``hint`` is
    # None for a normal (possibly incomplete) track dir.
    hint = None
    td_path = Path(track_dir)
    if td_path.is_file() or td_path.name == "tracks.md":
        hint = ("track_dir points to the registry file (or a file), not a track "
                "directory. Pass conductor/tracks/<track_id> "
                "(or run 'track-state resolve-track \"<query>\"').")
    elif td_path.is_dir() and (td_path / "tracks.md").exists() and \
            not any((td_path / f).exists() for f in _TRACK_CORE_FILES):
        hint = ("track_dir looks like the conductor root (contains tracks.md but "
                "no spec.md/plan.md/track-state.json). Pass conductor/tracks/<track_id>.")
    elif td_path.is_dir() and "track-state.json" in missing:
        # The dir exists (spec/plan may be there) but state was never written —
        # the track was scaffolded but not init'd. Name the recovery command so
        # the skill's HALT message is actionable, not "no track with status
        # new/in_progress".
        hint = ("track-state.json missing — the track dir exists but was never "
                "initialized. Run /conductor:new-track, or: "
                "track-state init-from-plan <td> --track-id <id> --type <type> "
                "--description '<text>'.")

    return dict(
        ok=not missing and not invalid_state and not missing_workflow
        and not roster_errors and not probe_errors,
        missing=missing,
        missing_workflow=missing_workflow,
        track_dir=str(td),
        invalid_state=invalid_state,
        roster_errors=roster_errors,
        probe_errors=probe_errors,
        hint=hint,
    )


def cmd_preflight(track_dir):
    """Verify a track's core conductor files exist and its state loads.

    Single machine-checkable entry point for skill setup checks, replacing the
    repeated "verify spec.md/plan.md/track-state.json" prose. Also gates the
    project-level workflow index (``conductor/workflow/index.md``) that
    implement depends on — fail-open per ``_resolve_conductor_root``. Outputs
    ``{ok, missing, missing_workflow, track_dir, invalid_state}`` and ALWAYS
    exits 0 — callers switch on ``ok`` and emit their own halt message (mirrors
    ``validate``). The check itself lives in ``_preflight_result``, shared with
    ``cmd_setup``.
    """
    out(_preflight_result(track_dir))


def cmd_quality_snapshot(track_dir):
    """Compute aggregate per-track quality metrics from state (read-only).

    GC-pillar building block realizing the doc's "quality grades per domain":
    completion breakdown + code-task coverage aggregate + evidence gaps +
    spec-deviation count, computed on demand. No persistence format is baked
    in — a future ledger can append this JSON, or skills/the orchestrator read
    it directly. Coverage is aggregated only over completed non-exempt tasks
    ([Docs]/[Config]/[Chore]/[Manual] are excluded), from each task's
    ``evidence.coverage_pct`` written by process-result/dispatch-finalize.
    """
    state = load(track_dir)
    units = []
    for phase in state.get("phases", []):
        for task in phase.get("tasks", []):
            units.append(task)
            units.extend(task.get("subtasks", []))

    total = len(units)
    by_status = {}
    coverage_vals = []
    coverage_pass = 0
    code_tasks = 0
    no_evidence = 0
    deviations = 0

    for u in units:
        st = u.get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1
        exempt = _tag_exempt_from_coverage(extract_tags(u.get("name", "")))
        ev = u.get("evidence") or {}
        deviations += ev.get("deviations", 0) or 0
        if st == "completed":
            if not exempt:
                code_tasks += 1
                cov = _to_number(ev.get("coverage_pct"))
                if cov is not None:
                    coverage_vals.append(cov)
                    if cov >= 80:
                        coverage_pass += 1
            if "evidence" not in u:
                no_evidence += 1

    completed = by_status.get("completed", 0)
    completion_pct = round(100 * completed / total, 1) if total else 0.0
    coverage_mean = (round(sum(coverage_vals) / len(coverage_vals), 1)
                     if coverage_vals else None)
    coverage_pass_pct = (round(100 * coverage_pass / code_tasks, 1)
                         if code_tasks else None)

    # AC integrity rates + advisory gate (None/"N/A" when no spec.md or no ACs).
    ac = compute_ac_integrity(track_dir)

    out(dict(
        track_id=state.get("track_id"),
        total_units=total,
        by_status=by_status,
        completion_pct=completion_pct,
        coverage_mean=coverage_mean,
        coverage_pass_pct=coverage_pass_pct,
        code_tasks_completed=code_tasks,
        tasks_missing_evidence=no_evidence,
        spec_deviations=deviations,
        ac_tc_coverage_rate=ac["ac_tc_coverage_rate"],
        ac_traceability_rate=ac["ac_traceability_rate"],
        ac_verification_rate=ac["ac_verification_rate"],
        ac_verification_measured_rate=ac["ac_verification_measured_rate"],
        ac_integrity_gate=ac["ac_integrity_gate"],
        ac_integrity_reason=ac["ac_integrity_reason"],
        ears_warnings=ac["ears_warnings"],
        ears_gate=ac["ears_gate"],
    ))


def cmd_spec_integrity(track_dir):
    """Compute AC coverage rates + advisory integrity gate (read-only).

    The measurable guarantee over Acceptance Criteria: cross-checks spec.md
    (the AC/TC inventory), plan.md (``<!-- AC-n -->`` task annotations), and
    track-state.json evidence (``tc_coverage``) into three rates — AC→TC
    coverage, AC→plan traceability, AC verification — plus diagnostic lists
    (orphan/untraced/dangling/unverified/partial ACs) and a WARN-only gate.
    FR/NFR are counts only (no traceability channel exists today, so no rate).
    Degrades to ``None`` rates / ``"N/A"`` gate when spec.md is absent or has
    no ACs — tracks without a formal spec are not penalized.
    """
    out(compute_ac_integrity(track_dir))


def cmd_reset(track_dir, scope, p=None, t=None):
    """Reset task(s) to pending, clearing all completion fields.

    Scopes:
      task  — reset a single task (and its subtasks) at phase p, task t
      phase — reset ALL tasks in phase p
      track — reset ALL tasks across ALL phases
    """
    state = load(track_dir)

    if scope == "task":
        if p is None or t is None:
            out(dict(error="task scope requires phase and task index"))
            sys.exit(1)
        pi, ti = int(p), int(t)
        if pi < 1 or ti < 1:
            out(dict(error=f"Indices must be >= 1: phase={pi}, task={ti}"))
            sys.exit(1)
        tgt = target(state, pi, ti)
        _reset_task(tgt)
        for sub in tgt.get("subtasks", []):
            _reset_task(sub)
        # If parent phase was terminal, bring it back to in_progress
        parent_phase = state["phases"][pi - 1]
        if parent_phase.get("status") in TERMINAL_FOR_PARENT:
            parent_phase["status"] = "in_progress"
        state["current_phase_index"] = pi
        state["current_task_index"] = ti
        state.pop("current_subtask_index", None)

    elif scope == "phase":
        if p is None:
            out(dict(error="phase scope requires phase index"))
            sys.exit(1)
        pi = int(p)
        if pi < 1:
            out(dict(error=f"Index must be >= 1: phase={pi}"))
            sys.exit(1)
        phases = state.get("phases", [])
        if pi > len(phases):
            out(dict(error=f"Phase index {pi} out of range (track has {len(phases)} phases)"))
            sys.exit(1)
        phase = phases[pi - 1]
        for task in phase.get("tasks", []):
            _reset_task(task)
            for sub in task.get("subtasks", []):
                _reset_task(sub)
        phase["status"] = "in_progress"
        state["current_phase_index"] = pi
        state["current_task_index"] = 1
        state.pop("current_subtask_index", None)

    elif scope == "track":
        for phase in state.get("phases", []):
            for task in phase.get("tasks", []):
                _reset_task(task)
                for sub in task.get("subtasks", []):
                    _reset_task(sub)
            phase["status"] = "in_progress"
        state["current_phase_index"] = 1
        state["current_task_index"] = 1
        state.pop("current_subtask_index", None)
        state["status"] = "in_progress"

    else:
        out(dict(error=f"Unknown scope: {scope}. Use task, phase, or track."))
        sys.exit(1)

    state["updated_at"] = now_iso()
    save(track_dir, state)
    _do_sync_plan(track_dir, state)

    out(dict(ok=True, scope=scope, phase=int(p) if p is not None else None,
             task=int(t) if t is not None else None))

def cmd_indices(track_dir):
    """Print phase/task/subtask index mapping for the track."""
    state = load(track_dir)
    phases = state.get("phases", [])
    if not phases:
        out(dict(indices=[]))
        return

    result = []
    for pi, ph in enumerate(phases, 1):
        phase_info = dict(
            index=pi, name=ph.get("name", "?"), status=ph.get("status", "?"),
            tasks=[])
        for ti, tk in enumerate(ph.get("tasks", []), 1):
            task_info = dict(
                index=ti, name=tk.get("name", "?"), status=tk.get("status", "?"),
                subtasks=[])
            for si, sub in enumerate(tk.get("subtasks", []), 1):
                task_info["subtasks"].append(dict(
                    index=si, name=sub.get("name", "?"),
                    status=sub.get("status", "?")))
            phase_info["tasks"].append(task_info)
        result.append(phase_info)

    out(dict(indices=result))


def _resolve_position(state):
    """Where the active task is — the ``►`` marker's source on the dashboard.

    Mirrors :func:`dispatch._find_next_task` Pass 1 (an ``in_progress`` task is
    a recovery / mid-dispatch continuation), then falls back to the cursor
    fields for a fresh or finalized track (no active task). Returns a small dict
    the renderer consumes — never persisted, never re-derives dispatch routing.
    """
    phases = state.get("phases") or []
    for pi, ph in enumerate(phases, 1):
        for ti, tk in enumerate(ph.get("tasks") or [], 1):
            if tk.get("status") == "in_progress":
                subs = tk.get("subtasks") or []
                for si, sub in enumerate(subs, 1):
                    if sub.get("status") in ("in_progress", "pending"):
                        return dict(phase=pi, task=ti, subtask=si,
                                    name=sub.get("name"), kind="subtask")
                return dict(phase=pi, task=ti, subtask=None,
                            name=tk.get("name"), kind="task")
    cpi = state.get("current_phase_index")
    cti = state.get("current_task_index")
    if isinstance(cpi, int) and isinstance(cti, int) and 1 <= cpi <= len(phases):
        tasks = phases[cpi - 1].get("tasks") or []
        if 1 <= cti <= len(tasks):
            tk = tasks[cti - 1]
            csi = state.get("current_subtask_index")
            subs = tk.get("subtasks") or []
            if isinstance(csi, int) and 1 <= csi <= len(subs):
                return dict(phase=cpi, task=cti, subtask=csi,
                            name=subs[csi - 1].get("name"), kind="cursor")
            return dict(phase=cpi, task=cti, subtask=None,
                        name=tk.get("name"), kind="cursor")
    return dict(phase=None, task=None, subtask=None, name=None, kind="empty")


def _view_unit(index, unit):
    """One task/subtask row for the dashboard task tree (read-only projection)."""
    ev = unit.get("evidence") or {}
    return dict(
        index=index,
        name=unit.get("name"),
        status=unit.get("status", "pending"),
        commit_sha=unit.get("commit_sha"),
        retry_count=unit.get("retry_count", 0),
        max_retries=unit.get("max_retries"),
        task_type=unit.get("task_type"),
        coverage_pct=ev.get("coverage_pct"),
    )


def _view_task_tree(state):
    """Phase → task → subtask walk for the dashboard (mirrors :func:`cmd_indices`
    with the extra per-unit fields the tree view annotates: SHA, retry budget,
    tag, coverage)."""
    result = []
    for pi, ph in enumerate(state.get("phases") or [], 1):
        phase_info = dict(index=pi, name=ph.get("name"),
                          status=ph.get("status"), tasks=[])
        for ti, tk in enumerate(ph.get("tasks") or [], 1):
            task_info = _view_unit(ti, tk)
            task_info["subtasks"] = [
                _view_unit(si, sub)
                for si, sub in enumerate(tk.get("subtasks") or [], 1)
            ]
            phase_info["tasks"].append(task_info)
        result.append(phase_info)
    return result


def _view_quality(state, track_dir):
    """The few quality metrics v1 surfaces (completion, mean coverage,
    AC-integrity gate). Same unit model + exemption predicate as
    :func:`cmd_quality_snapshot`, inlined rather than factored so the dashboard's
    minimal slice stays independent of the richer snapshot's shape."""
    units = []
    for ph in state.get("phases") or []:
        for tk in ph.get("tasks") or []:
            units.append(tk)
            units.extend(tk.get("subtasks") or [])
    total = len(units)
    completed = sum(1 for u in units if u.get("status") == "completed")
    completion_pct = round(100 * completed / total, 1) if total else 0.0
    cov_vals = []
    for u in units:
        if u.get("status") != "completed":
            continue
        if _tag_exempt_from_coverage(extract_tags(u.get("name") or "")):
            continue
        cov = _to_number((u.get("evidence") or {}).get("coverage_pct"))
        if cov is not None:
            cov_vals.append(cov)
    coverage_pct = round(sum(cov_vals) / len(cov_vals), 1) if cov_vals else None
    ac = compute_ac_integrity(track_dir)
    return dict(
        completion_pct=completion_pct,
        coverage_pct=coverage_pct,
        ac_integrity=ac.get("ac_integrity_gate"),
    )


def build_view_envelope(track_dir):
    """Compute the resolved-workflow + task-tree envelope — no emit.

    The ONE code-owned join a dashboard / status / studio renders from (never a
    second parser of ``track-state.json``). Extracted from :func:`cmd_view` so
    the shape-studio server's track-bound resolve endpoint reuses the exact same
    join — position tracking, phase-composition verifier narrowing, and quality
    gauges all flow through here, so a studio preview of a bound track is
    byte-identical to what ``/conductor:dashboard`` renders. Assembled from the
    EXISTING registry accessors (:mod:`workflow_shapes` + :mod:`task_profiles`),
    so a project overlay renders for free with zero Python edits.
    """
    from . import workflow_shapes as ws
    from . import task_profiles as tp
    from .registry_validate import CODE_TIERS

    state = load(track_dir)
    shape = ws.resolve_shape(state.get("workflow_shape"))
    verifiers = list(ws.verifiers_for(shape))
    current_phase = state.get("current_phase_index")
    if isinstance(current_phase, int) and tp.phase_is_code_free(state, current_phase):
        # Phase-composition narrowing — a code-free current phase drops the code
        # tiers (build-runner + test-runner) from the checkpoint fan-out (mirrors
        # dispatch._build_verifier_wave — both off the shared CODE_TIERS tuple);
        # ac-tracer stays (ACs are still traced).
        verifiers = [v for v in verifiers if v not in CODE_TIERS]

    return dict(
        track=dict(
            track_id=state.get("track_id"),
            type=state.get("type"),
            status=state.get("status"),
            execution_mode=state.get("execution_mode"),
            shape=shape,
            description=state.get("description"),
        ),
        resolved_workflow=dict(
            shape=shape,
            nodes=list(ws.nodes_for(shape)),
            verifiers=verifiers,
            gates=list(ws.gates_for(shape)),
            verify_policy=ws.verify_policy_for(shape),
            checkpoint_policy=ws.checkpoint_policy_for(shape),
            ac_grounding=ws.ac_grounding_for(shape),
            position=_resolve_position(state),
        ),
        task_tree=_view_task_tree(state),
        quality=_view_quality(state, track_dir),
    )


def cmd_view(track_dir, render=False):
    """Read-only resolved-workflow + task-tree snapshot — the dashboard backend.

    The ONE code-owned join a dashboard/status skill renders from (never a
    second parser of ``track-state.json``). Assembles the envelope from the
    EXISTING registry accessors — :mod:`workflow_shapes` for the node topology /
    gates / verifier fan-out, :mod:`task_profiles` for the phase-composition
    verifier narrowing — so a project overlay (new shape, new gate set, new
    code-free phase) renders for free with zero Python edits. ``render=True``
    prints a Unicode dashboard (:func:`dashboard_render.render`); the default
    prints the JSON envelope. Read-only — no firewall exposure.

    The envelope itself is built by :func:`build_view_envelope` (shared with the
    shape-studio server's track-bound resolve); this command adds only the
    emit/render choice.
    """
    envelope = build_view_envelope(track_dir)
    if render:
        from . import dashboard_render
        print(dashboard_render.render(envelope))
        return
    out(envelope)


def _status_unit(index, unit):
    """:func:`_view_unit` plus the reason fields the Issues/Deferred sections
    surface — ``last_failure_summary``, ``skip_analysis``, ``defer_reason``
    (all strings on the task per the schema; the structured analyst verdicts
    live in transient sidecars, not on the task, so this is the real data)."""
    u = _view_unit(index, unit)
    u["last_failure_summary"] = unit.get("last_failure_summary")
    u["skip_analysis"] = unit.get("skip_analysis")
    u["defer_reason"] = unit.get("defer_reason")
    return u


def _status_phases(state):
    """Phase → task → subtask walk for the status report. Carries the STORED
    phase/task statuses — the conductor writes them authoritatively (and the
    validator autofixes phase status); this does NOT re-derive them."""
    result = []
    for pi, ph in enumerate(state.get("phases") or [], 1):
        phase_info = dict(index=pi, name=ph.get("name"),
                          status=ph.get("status"), tasks=[])
        for ti, tk in enumerate(ph.get("tasks") or [], 1):
            task_info = _status_unit(ti, tk)
            task_info["subtasks"] = [
                _status_unit(si, sub)
                for si, sub in enumerate(tk.get("subtasks") or [], 1)
            ]
            phase_info["tasks"].append(task_info)
        result.append(phase_info)
    return result


def _collect_status_unit(u, pi, ti, si, issues, deferred):
    st = u.get("status")
    if st in ("failed", "blocked"):
        issues.append(dict(phase=pi, task=ti, subtask=si, name=u.get("name"),
                           kind=st, retry_count=u.get("retry_count", 0),
                           max_retries=u.get("max_retries"),
                           last_failure_summary=u.get("last_failure_summary"),
                           skip_analysis=u.get("skip_analysis")))
    elif st == "deferred":
        deferred.append(dict(phase=pi, task=ti, subtask=si, name=u.get("name"),
                             reason=u.get("defer_reason")))


def _status_issues_deferred(state):
    """Collect failed/blocked (issues) and deferred units with their reason
    fields. Walks tasks + subtasks — the aggregation the skill used to do in
    prose, now in code."""
    issues, deferred = [], []
    for pi, ph in enumerate(state.get("phases") or [], 1):
        for ti, tk in enumerate(ph.get("tasks") or [], 1):
            _collect_status_unit(tk, pi, ti, None, issues, deferred)
            for si, sub in enumerate(tk.get("subtasks") or [], 1):
                _collect_status_unit(sub, pi, ti, si, issues, deferred)
    return issues, deferred


def _status_progress(state):
    """Completed/total unit count (tasks + subtasks) for overall progress."""
    units = []
    for ph in state.get("phases") or []:
        for tk in ph.get("tasks") or []:
            units.append(tk)
            units.extend(tk.get("subtasks") or [])
    total = len(units)
    completed = sum(1 for u in units if u.get("status") == "completed")
    return dict(completed=completed, total=total)


def _status_track(entry):
    """One track's status envelope from a classified registry entry.

    ``status`` is the authoritative STORED value (loadable) or the registry-
    marker projection (uninit/missing/ghost) — NEVER re-derived from tasks.
    That first-match-wins re-derivation was the /conductor:status skill's drift;
    retiring it means trusting the status the conductor's lifecycle commands
    (start / finalize / archive) maintain, consistent with every other
    diagnostic (``view``, ``post-loop-status``, ``quality-snapshot``).
    """
    from . import workflow_shapes as ws
    td = entry.get("track_dir")
    state_val = entry.get("state")
    base = dict(track_id=entry.get("track_id"), track_dir=td, state=state_val,
                status=entry.get("status"), marker=entry.get("marker"))
    if state_val != "loadable" or not td:
        return dict(base, type=None, description=None, shape=None,
                    execution_mode=None,
                    position=dict(phase=None, task=None, subtask=None,
                                  name=None, kind="empty"),
                    phases=[], issues=[], deferred=[],
                    progress=dict(completed=0, total=0))
    s = load(td)
    issues, deferred = _status_issues_deferred(s)
    base.update(type=s.get("type"), description=s.get("description"),
                shape=ws.resolve_shape(s.get("workflow_shape")),
                execution_mode=s.get("execution_mode"),
                position=_resolve_position(s), phases=_status_phases(s),
                issues=issues, deferred=deferred, progress=_status_progress(s))
    return base


def _status_summary(tracks):
    """All-tracks summary computed in code (the skill used to count this in prose)."""
    by_status = {}
    for t in tracks:
        st = t.get("status") or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
    tot_completed = sum(t["progress"]["completed"] for t in tracks)
    tot_total = sum(t["progress"]["total"] for t in tracks)
    return dict(
        total_tracks=len(tracks),
        by_status=by_status,
        overall_progress=dict(
            completed=tot_completed, total=tot_total,
            pct=round(100 * tot_completed / tot_total, 1) if tot_total else 0.0),
        deferred_count=sum(len(t["deferred"]) for t in tracks),
    )


def cmd_status(query=None, registry_path=None):
    """Read-only status-report envelope — the code-owned backend
    /conductor:status renders from.

    Retires the skill's hand-parse + prose-aggregation drift: track/phase status
    are the authoritative STORED values (never re-derived — that derivation WAS
    the drift), and summary counts / issues / deferred / position are computed
    here, never by the model. With ``query``: one track (resolved through the
    same :func:`_resolve_core` machinery as ``check``; a direct dir path is
    accepted too). Without: every registry entry, classified
    loadable/uninit/missing/ghost. ALWAYS exits 0 — the outcome is in the JSON
    (``ok``/``reason``), never the exit code, mirroring ``resolve-track``/``check``.
    """
    reg = _resolve_registry(registry_path)
    resolved = _classify_registry(reg)
    if resolved is None:
        out(dict(ok=False, reason="no_registry",
                 hint="Run /conductor:setup (no conductor/tracks.md found)."))
        return

    q = (query or "").strip()
    if q and Path(q).is_dir():
        # Direct track-dir path (e.g. handed from `check`'s td) — report it even
        # if it isn't a registry entry. Determine loadability so _status_track
        # knows whether to build the rich envelope.
        state_val, status = "loadable", None
        try:
            status = load(q).get("status")
        except Exception:
            state_val = "uninit"
        entry = dict(track_id=Path(q).name, track_dir=q, state=state_val,
                     status=status, marker=None)
        tracks = [_status_track(entry)]
    elif q:
        core = _resolve_core(reg, query)
        if not core.get("ok"):
            out(dict(ok=False, reason=core.get("reason", "no_match"), query=query,
                     hint=core.get("hint"),
                     candidates=core.get("candidates", [])))
            return
        tid = core.get("track_id")
        tracks = [_status_track(r) for r in resolved if r.get("track_id") == tid]
    else:
        tracks = [_status_track(r) for r in resolved]

    out(dict(ok=True, tracks=tracks, summary=_status_summary(tracks)))


def cmd_derive_name(shortname):
    """Derive the canonical track_id and track_dir for a shortname, today.

    Stateless name resolver — the single source of truth for the
    ``shortname_YYYYMMDD`` convention (schemas/track-state.schema.json). The
    skills call this instead of hand-formatting the id, so the date always comes
    from the clock rather than the model's recall.

    Normalizes the shortname (lowercase, non ``[a-z0-9]`` runs → ``_``,
    collapsed + trimmed), strips any pre-existing trailing date, and appends
    today's date. Idempotent: re-running on the same day yields the same id.
    Collision detection is intentionally NOT done here — the skill owns
    uniqueness (new-track §2.6) — which keeps this trivially testable (no fs).
    """
    # Local date, not UTC: a track name is a human-facing label (ls, commit
    # messages, registry), unlike now_iso()'s UTC which is for ordering-
    # sensitive timestamps. Wall-clock "today" is what the user expects.
    raw = shortname or ""
    slug = re.sub(r"[^a-z0-9]+", "_", raw.lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    # Drop a pre-existing _YYYYMMDD so a re-stamp never double-appends.
    slug = re.sub(r"_\d{8}$", "", slug)
    if not slug:
        slug = "track"  # matches the cli.py default fallback
    date = datetime.now().strftime("%Y%m%d")
    track_id = f"{slug}_{date}"
    out(dict(
        ok=True,
        track_id=track_id,
        track_dir=f"conductor/tracks/{track_id}",
        shortname=slug,
        date=date,
    ))


def _propose_rationale(shape):
    """Deterministic one-line consequence statement for ``shape`` vs ``default``.

    Composed purely from resolved registry fields (never hand-authored per
    shape — a second prose home would drift the moment a registry row changes):
    dropped gates, dropped checkpoint verifiers, shifted AC grounding, or the
    explicit "keeps the full default" when nothing differs.
    """
    from . import workflow_shapes as ws

    parts = []
    dropped_gates = [g for g in ws.gates_for("default")
                     if g not in ws.gates_for(shape)]
    if dropped_gates:
        parts.append("drops the " + "/".join(dropped_gates)
                     + " gate(s) track-wide")
    dropped_verifiers = [v for v in ws.verifiers_for("default")
                         if v not in ws.verifiers_for(shape)]
    if dropped_verifiers:
        parts.append("drops " + "/".join(dropped_verifiers)
                     + " from the checkpoint fan-out")
    grounding = ws.ac_grounding_for(shape)
    if grounding != ws.ac_grounding_for("default"):
        parts.append(f"grounds ACs by {grounding} instead of tests")
    if not parts:
        parts.append("keeps the full default gates and verifier fan-out")
    return "; ".join(parts)


def cmd_propose_shape(description, brief_path=None):
    """Propose the workflow shape for a track description — planning-as-data D2/D3.

    The planning front door's selection step: pure signal-matching over
    (description ⊕ brief when readable) via :func:`workflow_shapes.rank_shapes`
    — deterministic, no model call, no filesystem writes. Mirrors
    ``derive_task_tag`` one layer down (task tags) with the safety inverted: a
    tag silently exempts gates (hence its >=2-hit bar), while a proposed shape
    is always confirmed by the user before it takes effect — ``confirm_required``
    is the guard, so a single distinct hit suffices to SURFACE a candidate.

    Output contract (the new-track skill consumes this, never re-derives):

    - ``proposed`` — ``default``, or the strict-plurality winner. A top score
      tied with the runner-up is ambiguity, not a proposal: default wins
      silently (``set-workflow-shape`` remains the override).
    - ``confirm_required`` — ``(proposed != "default") or brief_used``. A brief
      is consequential planning input: on the brief path the shape is ALWAYS
      user-confirmed, even when the keyword signals miss and the proposal is
      ``default`` — non-English wording ranks zero hits and a silent default
      then runs default's full tdd/coverage gates on migration-shaped work
      (the reported bug). No brief → ``proposed != "default"``: ``false`` →
      the skill proceeds silently; ``true`` → ONE ``AskUserQuestion``
      (recommended = ``proposed``, alternative = ``default``) — the generic
      D3 confirm.
    - ``chosen`` — the full entry for the proposed shape (gates / verifiers /
      ac_grounding / planning_doc + resolved path / rationale), so the skill
      records ``$WORKFLOW_SHAPE``/``$AC_GROUNDING``/``$PLAY_PATH`` from ONE
      object whichever branch runs. ``candidates`` carries every scored shape
      (score + hits for transparency); ``default`` carries the fallback entry
      for the confirm's alternative option.

    ``--brief`` is fail-open: an absent/unreadable brief never blocks the
    proposal (``brief_used`` reports whether it landed).
    """
    from . import workflow_shapes as ws

    desc = (description or "").strip()
    if not desc:
        out(dict(ok=False, error="missing description",
                 hint='track-state propose-shape "<description>" '
                      "[--brief <track_dir>/brief.md]"))
        return

    text = desc
    brief_used = False
    if brief_path:
        try:
            text = desc + "\n" + Path(brief_path).read_text(encoding="utf-8")
            brief_used = True
        except OSError:
            # Fail-open: a missing/unreadable brief ranks on the description
            # alone — selection must never halt on a stale pointer.
            pass

    def _entry(shape):
        return dict(
            shape=shape,
            gates=list(ws.gates_for(shape)),
            verifiers=list(ws.verifiers_for(shape)),
            ac_grounding=ws.ac_grounding_for(shape),
            planning_doc=ws.planning_doc_for(shape) or ws.DEFAULT_PLANNING_DOC,
            planning_doc_path=str(ws.resolve_planning_doc(shape)),
            rationale=_propose_rationale(shape),
        )

    default_entry = _entry("default")
    candidates = []
    for cand in ws.rank_shapes(text):
        entry = _entry(cand["shape"])
        entry["score"] = cand["score"]
        entry["hits"] = cand["hits"]
        candidates.append(entry)

    proposed = "default"
    if candidates and (len(candidates) == 1
                       or candidates[0]["score"] > candidates[1]["score"]):
        proposed = candidates[0]["shape"]

    out(dict(
        ok=True,
        proposed=proposed,
        confirm_required=(proposed != "default") or brief_used,
        brief_used=brief_used,
        chosen=_entry(proposed),
        candidates=candidates,
        default=default_entry,
        hint="confirm_required=false → proceed silently with `proposed`; "
             "true → ONE AskUserQuestion (recommended=proposed, "
             "alternative=default; brief path → judgment ask with candidates "
             "when proposed==default). set-workflow-shape overrides later.",
    ))


def _brief_section_items(brief_text, heading):
    """Non-empty list-item count under a ``## <heading>`` brief section.

    The brief's machine anchors are the English ``##`` headings (the same
    ASCII-anchor convention spec.md uses); items are ``-``/``*``/``+`` bullets.
    Returns ``None`` when the section is absent (no signal), ``0`` when present
    but empty. Section body ends at the next ``## `` heading.
    """
    lines = brief_text.splitlines()
    in_section = False
    count = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_section:
                break
            in_section = stripped[3:].strip().lower() == heading.lower()
            continue
        if in_section and stripped[:2] in ("- ", "* ", "+ "):
            if stripped[2:].strip():
                count += 1
    return count if in_section else None


def cmd_propose_grounding(description, brief_path=None):
    """The fog gate — should this track run a grounding fan-out before planning?

    The grounding-fanout front door (new-track §2.2.5): a PURE signal match
    over (description ⊕ brief when readable), the ``propose-shape`` precedent
    exactly — deterministic, no model call, no filesystem writes. Two signal
    families compose into ``score``:

    - **Keyword hits** — the registry's top-level ``grounding_signals``
      (complexity/cross-module wording) matched via
      :func:`workflow_shapes.grounding_hits` (the shared word-boundary
      matcher).
    - **Brief structural signals** — ``## Open Questions`` with >= 2 non-empty
      items (the planner will inherit unresolved fog) and ``## References``
      present-but-empty (the planner will have no doc anchors). Plain section
      parses, no judgment.

    Output contract (new-track §2.2.5 consumes this, never re-derives):
    ``foggy = score >= 1`` — one distinct signal is enough to ASK (the ask is
    the anchor; over-firing costs one question, under-firing loses the
    grounding). ``confirm_required`` mirrors ``foggy`` verbatim so the skill
    switches on one field. ``--brief`` is fail-open: an absent/unreadable
    brief never blocks the gate (``brief_used`` reports whether it landed).
    Skip the whole step when the track's shape is ``research-first`` — its
    Prelude already explores.
    """
    from . import workflow_shapes as ws

    desc = (description or "").strip()
    if not desc:
        out(dict(ok=False, error="missing description",
                 hint='track-state propose-grounding "<description>" '
                      "[--brief <track_dir>/brief.md]"))
        return

    brief_text = None
    brief_used = False
    if brief_path:
        try:
            brief_text = Path(brief_path).read_text(encoding="utf-8")
            brief_used = True
        except OSError:
            # Fail-open: a missing/unreadable brief gates on the description
            # alone — the fog check must never halt on a stale pointer.
            pass

    hits = ws.grounding_hits(desc)
    if brief_text is not None:
        hits = hits + ws.grounding_hits(brief_text)
    # Dedupe keyword hits across description/brief (a signal landing in both
    # is one fog point, not two).
    seen: list[str] = []
    for h in hits:
        if h not in seen:
            seen.append(h)
    hits = seen

    brief_hits: list[str] = []
    if brief_text is not None:
        open_q = _brief_section_items(brief_text, "Open Questions")
        if open_q is not None and open_q >= 2:
            brief_hits.append(f"open questions in brief ({open_q})")
        refs = _brief_section_items(brief_text, "References")
        if refs == 0:
            brief_hits.append("references section empty")

    score = len(hits) + len(brief_hits)
    parts = []
    if hits:
        parts.append(f"{len(hits)} complexity signal"
                     f"{'s' if len(hits) != 1 else ''} ({', '.join(hits)})")
    if brief_hits:
        parts.append(f"{len(brief_hits)} brief signal"
                     f"{'s' if len(brief_hits) != 1 else ''} "
                     f"({'; '.join(brief_hits)})")
    rationale = (" + ".join(parts) + " — the ground the planner needs may "
                 "not be mapped yet") if parts else \
        "no fog signals — quiet track, plan directly"

    out(dict(
        ok=True,
        foggy=score >= 1,
        score=score,
        hits=hits + brief_hits,
        confirm_required=score >= 1,
        brief_used=brief_used,
        rationale=rationale,
        hint="foggy=false → proceed to §2.3, no ask; foggy=true → ONE "
             "AskUserQuestion (recommended = run the 3-explorer fan-out). "
             "Skip the whole gate when $WORKFLOW_SHAPE is research-first.",
    ))


def _candidate_roots(conductor_root):
    """Base dirs to probe when resolving a track dir, given the conductor root
    (the directory holding ``tracks.md`` = ``reg.parent``).

    The canonical registry ``<proj>/conductor/tracks.md`` has tracks at
    ``<conductor_root>/tracks/<id>``. But a registry placed at the project root
    (``<proj>/tracks.md`` — which ``_find_registry`` deliberately accepts) makes
    ``conductor_root`` BE the project root, so the tracks still actually live at
    ``<proj>/conductor/tracks/<id>`` = ``<conductor_root>/conductor/tracks/<id>``.
    Probing ``<conductor_root>``, its parent, and ``<conductor_root>/conductor``
    lets a track resolve regardless of where the registry file was placed,
    instead of assuming ``conductor_root`` is always the real conductor root.
    """
    try:
        p = Path(conductor_root).resolve(strict=False)
    except OSError:
        return [Path(conductor_root)]
    seen, roots = set(), []
    for cand in (p, p.parent, p / "conductor"):
        try:
            cc = cand.resolve(strict=False)
        except OSError:
            continue
        if cc not in seen:
            seen.add(cc)
            roots.append(cc)
    return roots or [p]


def _resolve_track_dir(rel_or_id, roots):
    """First existing track dir among probed candidates; else canonical fallback.

    ``rel_or_id`` is a registry link (relative or absolute) or a bare track_id.
    Probes the link as-is against each candidate root, plus the canonical
    ``<root>/tracks/<id>`` form for bare ids. Returns the first dir that exists;
    if none (stale entry / dir-name mismatch), returns ``roots[0]/tracks/<id>``
    so the caller still has a deterministic path to surface in
    ``track_dir_missing`` rather than crashing or silently dropping the entry.
    """
    link = Path(rel_or_id)
    if link.is_absolute():
        return str(link.resolve(strict=False))
    rel = str(link).replace("\\", "/")
    bare = "/" not in rel
    tid = link.name
    for root in roots:
        if (root / rel).is_dir():
            return str((root / rel).resolve(strict=False))
        if bare and (root / "tracks" / tid).is_dir():
            return str((root / "tracks" / tid).resolve(strict=False))
    first = roots[0] if roots else Path.cwd()
    return str((first / "tracks" / tid).resolve(strict=False))


def _iter_registry_entries(text, conductor_root):
    """Parse registry text into a list of ``{track_id, track_dir, marker, status_str}``.

    Read-only mirror of the write-side parsing in ``cmd_registry_update`` (the
    single source of truth for the registry file's three formats). Handles:

    - **checkbox**: ``- [marker] desc (path/)`` — ``track_dir`` from the link
      path (probed against candidate roots, so a registry at the project root
      still resolves); ``marker`` captured directly.
    - **section**: ``### <id>`` + ``- **Status:** <status>`` — ``track_dir``
      derived as ``<conductor_root>/tracks/<id>`` (sections carry no path).
    - **table**: ``| id | type | status | desc |`` — ``track_dir`` derived.

    Only checkbox entries carry a path; section/table entries derive it from
    ``track_id`` via the canonical ``conductor/tracks/<track_id>`` layout.
    Directory resolution goes through :func:`_resolve_track_dir`, which probes
    :func:`_candidate_roots` so a registry placed anywhere (project root vs
    ``conductor/``) resolves the same track. Returns entries in document order;
    malformed lines are silently skipped.
    """
    entries = []
    roots = _candidate_roots(conductor_root)
    in_section_id = None
    section_status = None

    def _derived_dir(track_id):
        return _resolve_track_dir(track_id, roots)

    def _flush(tid, status):
        if tid:
            entries.append(dict(track_id=tid, track_dir=_derived_dir(tid),
                                marker=None, status_str=status))

    for line in text.splitlines():
        # Section heading: ### <id>  (id is first token; heading may have prose)
        m = _RE_SECTION_HEAD.match(line)
        if m:
            _flush(in_section_id, section_status)  # prior section w/o Status line
            in_section_id = m.group(1).strip()
            section_status = None
            continue
        if in_section_id is not None:
            sm = _RE_SECTION_STATUS.match(line)
            if sm:
                section_status = sm.group(1).strip()
                _flush(in_section_id, section_status)
                in_section_id = None
                continue
        # Checkbox: - [marker] desc (path/)
        cm = _RE_CHECKBOX.match(line)
        if cm:
            _prefix, marker, _mid, link_path, _suffix = cm.groups()
            lp = link_path.strip()
            if not lp:
                # An empty link ``()`` carries no track identity — no path to
                # resolve and no basename to derive a track_id from. Skip it:
                # emitting ``{track_dir: None}`` here would let ``_resolve_core``
                # auto-select a null track_dir and crash ``_preflight_result``
                # (``Path(None)`` -> TypeError), or pollute ``ambiguous``
                # candidates with nulls the skill can't render as labels.
                continue
            # Resolve the link via ``_resolve_track_dir``, which probes the
            # candidate roots (conductor root, its parent, and ``<root>/conductor``)
            # and returns the first dir that exists. This is robust to BOTH link
            # forms (project-root-relative ``conductor/tracks/<id>/`` and
            # conductor-root-relative ``tracks/<id>/``) AND to the registry being
            # placed at the project root instead of ``conductor/`` — the old
            # form-based pick resolved against a single guessed base and silently
            # produced non-existent paths when the registry location didn't match
            # the assumption. Falls back to the canonical derivation when no
            # candidate exists (stale entry), so the entry is still surfaced for
            # ``track_dir_missing`` diagnosis instead of being dropped.
            track_dir = _resolve_track_dir(lp, roots)
            track_id = Path(lp).name  # full id incl. _YYYYMMDD; shortname derived at match time
            entries.append(dict(track_id=track_id, track_dir=track_dir,
                                marker=marker,
                                status_str=_REGISTRY_MARKER_TO_STATUS.get(marker)))
            continue
        # Table row: | id | type | status | desc |
        if line.lstrip().startswith("|"):
            sm = _RE_TABLE_STATUS.search(line)
            if sm:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                tid = cells[0] if cells else ""
                if tid and tid.lower() not in ("id", "track id", "track"):
                    entries.append(dict(track_id=tid, track_dir=_derived_dir(tid),
                                        marker=None, status_str=sm.group(1)))
            continue  # a table line is fully owned by this branch (status or not)
        # Universal fallback: a dated track_id token anywhere on the line.
        # Catches the freeform entries new-track's model wrote without a
        # parseable ``(link)`` — plain bullet ``- auth_20260706``, bold
        # ``**auth_20260706**``, checkbox-without-link ``- [~] auth_20260706``,
        # inline mentions. Status stays authoritative (``_resolve_core`` reads
        # each entry's ``track-state.json``), so the fallback only needs to
        # supply ``track_id`` + a derived dir. Re-emits are deduped below.
        tok = _RE_TRACK_ID_TOKEN.search(line)
        if tok:
            tid = tok.group(1)
            entries.append(dict(track_id=tid, track_dir=_derived_dir(tid),
                                marker=None, status_str=None))
    _flush(in_section_id, section_status)  # trailing section w/o Status line
    # Dedup by track_id, FIRST occurrence wins. The universal fallback can
    # re-emit an id a section/checkbox branch already captured (e.g. a
    # section's ``- **Path:** ...(<id>/)`` body line also contains the id);
    # track_ids are unique, so first-wins is safe and keeps the link-resolved
    # ``track_dir`` from a canonical branch over the fallback's derived dir.
    seen, deduped = set(), []
    for e in entries:
        tid = e.get("track_id")
        if tid in seen:
            continue
        seen.add(tid)
        deduped.append(e)
    return deduped


def _classify_registry(reg):
    """Read the tracks registry and classify every entry by liveness state.

    The single source for the entry → ``{track_id, track_dir, status, marker,
    state}`` classification, shared by :func:`_resolve_core` (single-track
    selection) and :func:`cmd_status` (all-tracks enumeration). ``state`` is the
    liveness key — ``loadable`` (state read OK, ``status`` authoritative),
    ``uninit`` (dir exists but state won't load), ``missing`` (dir absent),
    ``ghost`` (no dir resolved). Returns ``None`` when there is no registry, so
    each caller surfaces its own ``no_registry`` envelope.
    """
    if reg is None or not reg.is_file():
        return None
    conductor_root = reg.parent
    entries = _iter_registry_entries(reg.read_text(), conductor_root)
    resolved = []
    for e in entries:
        td = e.get("track_dir")
        status = e.get("status_str")
        state = "ghost"
        if td:
            if Path(td).is_dir():
                try:
                    status = load(td).get("status") or status
                    state = "loadable"
                except Exception:
                    state = "uninit"  # dir exists, state unreadable -> needs init
            else:
                state = "missing"  # derived/linked dir doesn't exist
        resolved.append(dict(track_id=e.get("track_id"), track_dir=td,
                             status=status, marker=e.get("marker"), state=state))
    return resolved


def _resolve_core(reg, query):
    """Resolve ``query -> track_dir`` and return the outcome dict (no ``out()``).

    Factored body of ``cmd_resolve_track`` so ``cmd_setup`` can compose it without
    capturing stdout. Hosts two small-window-model defenses before tier matching:

    - **Literal placeholder:** a model that emits ``$ARGUMENTS`` unsubstituted
      (the reported empty-``<td>`` failure) collapses to auto-select instead of
      ``no_match``.
    - **Full track_dir path:** the ``done`` -> post-loop-step hand-off passes
      ``<td>`` (the resolved dir) verbatim; a path's basename is the track_id, so
      reduce it and fall through to the exact-id tier instead of ``no_match``.

    Each entry is classified by ``state`` (not just status), so a track that
    exists on disk but won't load is diagnosed precisely instead of silently
    dropping out and collapsing to the useless "no track with status
    new/in_progress":

    - ``loadable`` — ``track-state.json`` read OK; ``status`` is authoritative
      (the registry marker is the fallback projection only).
    - ``uninit`` — dir EXISTS but state won't load (no/unreadable
      ``track-state.json``). Selectable by identity so preflight can report
      ``track_not_initialized``.
    - ``missing`` — dir does NOT exist (stale entry / dir-name mismatch).
    - ``ghost`` — no ``track_dir`` (empty link). Unselectable.
    """
    if reg is None or not reg.is_file():
        return dict(ok=False, reason="no_registry",
                    hint="Run /conductor:setup (no conductor/tracks.md found).")
    resolved = _classify_registry(reg)
    if resolved is None:
        return dict(ok=False, reason="no_registry",
                    hint="Run /conductor:setup (no conductor/tracks.md found).")

    q = (query or "").strip()
    if q.lower() in ("$arguments", "${arguments}"):
        q = None  # literal placeholder emitted unsubstituted -> auto-select
    elif "/" in q or "\\" in q:
        # full track_dir path (done hand-off) -> basename is the track_id
        q = Path(q).name.lower() or None
    else:
        q = q.lower() or None

    if q:
        # Tier 1: exact track_id (short-circuits even if shortname collides).
        hits = [r for r in resolved if r["track_id"] and r["track_id"].lower() == q]
        # Tier 2: shortname prefix (track_id minus trailing _YYYYMMDD).
        if not hits:
            hits = [r for r in resolved if r["track_id"]
                    and _RE_SHORTNAME_DATE.sub("", r["track_id"]).lower().startswith(q)]
        # Tier 3: path-basename substring.
        if not hits:
            hits = [r for r in resolved if r["track_dir"]
                    and q in Path(r["track_dir"]).name.lower()]
        if len(hits) == 1:
            return dict(ok=True, track_id=hits[0]["track_id"], track_dir=hits[0]["track_dir"],
                        status=hits[0]["status"], via="arg")
        elif len(hits) > 1:
            return dict(ok=False, reason="ambiguous", candidates=hits)
        else:
            return dict(ok=False, reason="no_match", query=query,
                        hint=f"No registry entry matches '{query}'. "
                             "List tracks with 'cat conductor/tracks.md'.")

    # Auto-select (no query). Priority: a live (loadable + non-terminal) track
    # first; then a single on-disk-but-uninitialized track (selected by identity
    # so preflight can report ``track_not_initialized`` instead of the useless
    # "no track with status new/in_progress"); then precise diagnosis.
    live = [r for r in resolved if r["state"] == "loadable"
            and r["status"] in _TRACK_NON_TERMINAL]
    if live:
        if len(live) == 1:
            r = live[0]
            return dict(ok=True, track_id=r["track_id"], track_dir=r["track_dir"],
                        status=r["status"], via="auto_single")
        # >1 live: prefer resuming a SINGLE in_progress track over starting one
        # of several new ones — work-in-flight beats fresh work, and this
        # dissolves the common "one track running + a freshly-created new track"
        # ambiguous prompt. Only ask on a genuine tie (multiple in_progress, or
        # several new with none in flight).
        in_prog = [r for r in live if r["status"] == "in_progress"]
        if len(in_prog) == 1:
            r = in_prog[0]
            return dict(ok=True, track_id=r["track_id"], track_dir=r["track_dir"],
                        status=r["status"], via="auto_prefer_in_progress")
        return dict(ok=False, reason="ambiguous", candidates=live)

    # No live track. A single uninitialized-but-on-disk track is the obvious
    # target — resolve it so ``cmd_check``'s preflight reports the missing state
    # precisely. >1 uninit is a genuine "which do you want to init?" ambiguity.
    uninit = [r for r in resolved if r["state"] == "uninit"]
    if len(uninit) == 1:
        r = uninit[0]
        return dict(ok=True, track_id=r["track_id"], track_dir=r["track_dir"],
                    status=r["status"], via="auto_uninit")
    if len(uninit) > 1:
        return dict(ok=False, reason="ambiguous", candidates=uninit)

    # No live, no uninit. Distinguish a registry of only-done tracks from a
    # stale registry whose every dir is missing (the dir-name-mismatch / orphan
    # case). An empty registry (no entries parsed — e.g. a lone ``- [~] x ()``
    # ghost) stays ``no_non_terminal`` to preserve the contract callers test.
    if any(r["state"] == "loadable" for r in resolved):
        return dict(ok=False, reason="no_non_terminal",
                    hint="No track with status new/in_progress — all registered "
                         "tracks are completed/archived. Start a new track with "
                         "/conductor:new-track, or pass a track_id to re-open one.")
    if resolved:
        missing_ids = [r["track_id"] for r in resolved if r["track_id"]]
        return dict(ok=False, reason="track_dir_missing", track_ids=missing_ids,
                    hint="Registry lists track(s) but their directories don't "
                         "exist (" + ", ".join(missing_ids) + "). Check the link "
                         "or dir names in conductor/tracks.md.")
    return dict(ok=False, reason="no_non_terminal",
                hint="No track with status new/in_progress. Pass a track_id, or "
                     "run /conductor:new-track.")


def _resolve_registry(registry_path):
    """The registry path: an explicit ``--registry`` arg, else auto-located."""
    return Path(registry_path) if registry_path else _find_registry()


def _resolve_track_dir_or_halt(track_dir, command=None):
    """Resolve the ``<track-dir>`` positional for the Rail B-min spine commands.

    ``check`` returns BOTH ``td`` (an absolute path) and ``track_id``; a
    small-window teleoperator sometimes hands the bare ``track_id`` to the next
    command (``step`` / ``wave-step`` / ``recover`` / ...), which then crashes
    inside ``conductor_dir().mkdir(exist_ok=True)`` with a confusing
    ``FileNotFoundError: <track_id>/.conductor`` — the literal id is treated as
    a relative path whose parent doesn't exist. "but actually the file exists"
    is the symptom: the track dir is fine, the *argument* was wrong.

    Accept BOTH forms so the model's fumble is harmless:

      - an existing path (the normal ``td`` fast path — a single ``is_dir()``
        check, no registry walk), or
      - a bare track_id / shortname / relative link, resolved through the SAME
        registry machinery ``check`` uses. ``_resolve_core`` already collapses
        the literal-placeholder and full-path-arg defenses, so this extends the
        small-window robustness ``check`` established to the downstream step.

    Returns the resolved path on success. On a bare id that can't be resolved,
    emits a structured ``{error}`` and exits 1 (matching the CLI's existing
    spine-error contract) instead of the raw ``mkdir`` traceback.
    """
    if track_dir is None:
        return None
    if Path(track_dir).is_dir():
        return track_dir
    core = _resolve_core(_resolve_registry(None), track_dir)
    if core.get("ok"):
        td = core.get("track_dir")
        if td and Path(td).is_dir():
            return td
        reason = "track_dir_missing"
    else:
        reason = core.get("reason", "no_match")
    if reason == "no_registry":
        msg = (f"'{track_dir}' is not an existing directory and no "
               "conductor/tracks.md was found to resolve it from. Re-run the "
               "skill ('track-state check' locates the track), or pass the "
               "track directory path.")
    elif reason == "ambiguous":
        msg = (f"'{track_dir}' is not an existing directory and matches "
               "multiple registry entries. Pass the full track directory "
               "path, or run 'track-state check' to disambiguate.")
    elif reason == "track_dir_missing":
        msg = (f"'{track_dir}' resolved to a registry entry whose directory "
               f"doesn't exist ({core.get('track_dir')}). Check the link/dir "
               "names in conductor/tracks.md.")
    else:  # no_match / no_non_terminal / etc.
        msg = (f"'{track_dir}' is not an existing track directory and no "
               "registry entry matches it. Re-run the skill — "
               f"'track-state check \"{track_dir}\"' resolves the track — or "
               "pass the track directory path (the 'td' field check returns).")
    out(dict(error=msg, command=command, reason=reason, hint=core.get("hint")))
    sys.exit(1)


def cmd_resolve_track(query=None, registry_path=None):
    """Resolve a ``track_dir`` from the Tracks Registry; ALWAYS exits 0.

    A machine-checkable entry point for the skill §1.0 "locate track" step —
    kills the bug class where a model hand-constructs the path (and passes
    ``conductor/tracks.md``, the registry file, instead of the track directory).
    Mirrors ``cmd_preflight``'s contract: outcome is in the JSON, never the exit
    code, because ambiguity is a normal skill-handled branch (surface via
    ``AskUserQuestion``), not an error. The resolve logic (and the placeholder /
    full-path defenses) live in ``_resolve_core``, shared with ``cmd_setup``.
    """
    reg = _resolve_registry(registry_path)
    out(_resolve_core(reg, query))


def cmd_check(query=None, registry_path=None):
    """Resolve + preflight a track in one call; ALWAYS exits 0.

    Collapses the skill §1.0 ``resolve-track`` + ``preflight`` pair into a single
    read-only query so the model never hand-carries ``<td>`` between them — the
    last path-handoff in setup, and the exact mishandling class that motivated
    ``resolve-track``. Mirrors ``cmd_preflight`` / ``cmd_resolve_track``: outcome
    in JSON, never the exit code. Composes ``_resolve_core`` (resolve + the
    placeholder / full-path defenses + the per-entry ``state`` classification)
    with ``_preflight_result`` (the readiness check). Read-only — the ``new``
    -> ``start`` transition stays with ``recover``, where the status comes from.

    The skill switches on ``action`` (a ready-to-execute directive, not a status
    to re-interpret) — this is what keeps the 5 skills' §1.0 a 3-arm switch
    instead of a run-on branch sentence:

      - ``action:"proceed"`` — resolved + ready. ``{ok:true, td, track_id,
        status, via, announce}``. Print ``announce`` (the transparent
        "Auto-selected '<id>' (<status>)" / "Resolved track '<id>'" line), then
        continue to ``recover``.
      - ``action:"ask"`` — ambiguous. ``{ok:false, reason:"ambiguous",
        candidates, announce}``. ``AskUserQuestion`` over ``candidates``.
      - ``action:"halt"`` — anything that stops the skill.
        ``{ok:false, reason, message, hint?, recover?, missing?,
        missing_workflow?, roster_errors?}``. Print ``message``; HALT (if
        ``recover`` is present it is a suggested command the user may run, not
        something the skill auto-executes). ``reason`` is one of
        ``track_not_initialized`` / ``track_dir_missing`` / ``roster`` /
        ``preflight`` / ``no_registry`` / ``no_match`` / ``no_non_terminal``.
        (``roster`` = the resolved agent-roster failed validation or declares
        dead agent names — design D4's lint-loud surface; runtime stays
        fail-open.)

    The diagnostic reasons matter: a track that exists on disk but lacks state,
    or whose registry dir doesn't exist, used to collapse to the useless "no
    track with status new/in_progress". They now surface as
    ``track_not_initialized`` (with a ready ``recover`` command) and
    ``track_dir_missing`` respectively.

    The legacy ``ok`` / ``reason`` / ``td`` / ``candidates`` / ``missing`` /
    ``missing_workflow`` / ``hint`` fields are all preserved alongside ``action``
    so existing readers keep working — ``action`` is the preferred switch.
    """
    reg = _resolve_registry(registry_path)
    core = _resolve_core(reg, query)
    reason = core.get("reason") if not core.get("ok") else None

    if reason == "ambiguous":
        out(dict(action="ask", ok=False, reason="ambiguous",
                 candidates=core.get("candidates", []),
                 announce="Multiple active tracks — choose one."))
        return
    if reason == "no_registry":
        out(dict(action="halt", ok=False, reason="no_registry",
                 message="Conductor environment incomplete. Run /conductor:setup.",
                 hint=core.get("hint")))
        return
    if reason == "track_dir_missing":
        out(dict(action="halt", ok=False, reason="track_dir_missing",
                 track_ids=core.get("track_ids", []),
                 message=core.get("hint")
                 or "Registry lists track(s) whose directories don't exist.",
                 hint=core.get("hint")))
        return
    if reason in ("no_match", "no_non_terminal"):
        out(dict(action="halt", ok=False, reason=reason,
                 message=core.get("hint")
                 or "No track selected. Pass a track_id, or see conductor/tracks.md.",
                 hint=core.get("hint")))
        return

    td = core.get("track_dir")
    track_id = core.get("track_id")
    if not td:
        # Defense-in-depth: ``_resolve_core`` should never return ok:true
        # without a track_dir (empty-link registry entries are skipped in
        # ``_iter_registry_entries``), but this command's contract is "ALWAYS
        # exits 0" — a stray null must surface as a HALT reason, not a
        # ``Path(None)`` TypeError crash.
        out(dict(action="halt", ok=False, reason="no_match",
                 message="Resolved track has no usable directory — check the "
                         "link paths in conductor/tracks.md."))
        return

    # A resolved-but-nonexistent dir (query path: the registry entry's derived
    # dir doesn't exist on disk). Diagnose distinctly from a real preflight gap.
    if not Path(td).is_dir():
        out(dict(action="halt", ok=False, reason="track_dir_missing", td=td,
                 track_id=track_id,
                 message=(f"Track '{track_id}' is registered but its directory "
                          f"doesn't exist: {td}. Check the link or dir name in "
                          "conductor/tracks.md.")))
        return

    pf = _preflight_result(td)
    if not pf["ok"]:
        roster_errors = pf.get("roster_errors", [])
        if roster_errors:
            # Distinct from a missing-file preflight: the track is fine, the
            # agent-roster registry is not. Name the fix (the overlay is the
            # likely author; the baseline ships valid) — runtime stays
            # fail-open, so this HALT is the only loud surface.
            out(dict(
                action="halt", ok=False, reason="roster", td=td,
                track_id=track_id, roster_errors=roster_errors,
                message=("Agent-roster registry invalid:\n  - "
                         + "\n  - ".join(roster_errors)
                         + "\nFix conductor/workflow/agent-roster.json (or "
                           "the plugin baseline); 'track-state registry-doc "
                           "--roster' renders validation errors.")))
            return
        missing = pf.get("missing", [])
        # The on-disk-but-uninitialized case: the track was scaffolded but never
        # had init-from-plan run. Hand back a ready ``recover`` command rather
        # than the generic "environment incomplete" message.
        if "track-state.json" in missing:
            recover = (f'track-state init-from-plan "{td}" --track-id {track_id} '
                       f'--type feature --description "<short description>"')
            out(dict(action="halt", ok=False, reason="track_not_initialized",
                     td=td, track_id=track_id, recover=recover,
                     message=(f"Track '{track_id}' directory exists but "
                              "track-state.json is missing — it was scaffolded "
                              "but never initialized. Run /conductor:new-track, "
                              f"or:\n  {recover}"),
                     missing=missing))
            return
        out(dict(action="halt", ok=False, reason="preflight", td=td,
                 message="Conductor environment incomplete. Run /conductor:setup.",
                 hint=pf.get("hint")
                 or "Conductor environment incomplete. Run /conductor:setup.",
                 missing=missing,
                 missing_workflow=pf.get("missing_workflow")))
        return

    # Resolved + ready.
    status = core["status"]
    via = core.get("via", "arg")
    how = "auto-selected" if str(via).startswith("auto") else "resolved"
    announce = f"Track '{core['track_id']}' ({status}) — {how}."
    out(dict(action="proceed", ok=True, td=td, track_id=core["track_id"],
             status=status, via=via, announce=announce))


# Backward-compat alias: the command was renamed ``setup`` -> ``check`` (it is
# read-only; "setup" implied mutation). The ``setup`` CLI string and any internal
# refs keep resolving through this alias so existing skills/tests don't break
# during the transition.
cmd_setup = cmd_check


def _get_all_shas(state):
    """Extract all commit SHAs from state. Returns list."""
    shas = []
    for phase in state["phases"]:
        for task in phase["tasks"]:
            sha = task.get("commit_sha", "")
            if sha and task["status"] in ("completed", "skipped", "failed", "blocked", "deferred", "cancelled"):
                shas.append(sha)
            for sub in task.get("subtasks", []):
                sha = sub.get("commit_sha", "")
                if sha and sub["status"] in ("completed", "skipped", "failed", "blocked", "deferred", "cancelled"):
                    shas.append(sha)
    return shas


def cmd_shas(track_dir):
    """Extract all commit SHAs from completed tasks. Returns first/last + a review range.

    `range` is `{first}~1..{last}` — the parent of the first commit through the last,
    so `git diff {range}` includes the first task's own changes. `first..last` alone
    masks the first commit's exclusive diff (git compares the two endpoint trees)."""
    state = load(track_dir)
    shas = _get_all_shas(state)
    first = shas[0] if shas else None
    last = shas[-1] if shas else None
    out(dict(
        shas=shas,
        first=first,
        last=last,
        count=len(shas),
        range=f"{first}~1..{last}" if shas else None,
    ))


def cmd_post_loop_status(track_dir):
    """Read-only post-loop resumability gates (Strategy 1).

    Emits the durable/cheap signals each post-loop phase gates on, so a
    re-invoked ``/conductor:implement`` can skip phases already completed across
    a context-budget interruption. No new ``track-state.json`` field; the gates
    reuse existing markers:

    * ``finalized`` — finalize state (``status == completed`` AND a numeric
      ``quality_score``).
    * ``doc_synced`` — the ``docs(conductor): ...[{track_id}]`` commit, via
      :func:`docs_synced_for_track` (same marker ``cmd_archive`` trusts).
    * ``review.done`` — the conductor-managed ``.conductor/post-loop.json``
      sidecar's ``reviewed_range`` equals the current ``{first}~1..{last}``.

    Read-only, calls ``out()`` directly (no ``COMPACT_FIELDS`` entry), mirroring
    ``cmd_shas``. The review range is the SAME ``{first}~1..{last}`` string
    ``cmd_shas`` emits, so the equality check preserves first-commit inclusion
    semantics — resolving a deferred task whose SHA was ``first`` changes the
    range and correctly forces a re-review.
    """
    state = load(track_dir)
    shas = _get_all_shas(state)
    first = shas[0] if shas else None
    last = shas[-1] if shas else None
    current_range = f"{first}~1..{last}" if shas else None

    # Review-range sidecar (conductor-managed, committed — NOT gitignored).
    # Written by the orchestrator immediately after code-reviewer returns.
    reviewed_range = None
    pl_path = Path(track_dir) / ".conductor" / "post-loop.json"
    data = json_marker_read(pl_path)
    if data:
        reviewed_range = data.get("reviewed_range")

    review_done = bool(reviewed_range and current_range
                       and reviewed_range == current_range)

    # Rail A paste-verbatim (design D3): when the §7.0 review is owed, attach
    # the deterministic prompt core (same builder the post-loop-step spine
    # emits) so templates/post-loop.md §7.0 pastes it instead of re-building
    # the first-commit-inclusive range in prose. Lazy import (misc→dispatch
    # would cycle at module level).
    review_prompt = None
    if shas and not review_done:
        from .dispatch import build_review_prompt
        review_prompt = build_review_prompt(
            str(track_dir), state.get("track_id", ""), current_range)

    status = state.get("status")
    finalized = (status == "completed"
                 and isinstance(state.get("quality_score"), (int, float)))

    out(dict(
        track_id=state.get("track_id"),
        status=status,
        finalized=finalized,
        doc_synced=docs_synced_for_track(track_dir),
        review=dict(done=review_done, range=current_range,
                    reviewed_range=reviewed_range, prompt=review_prompt),
        shas_count=len(shas),
    ))


def _stamp_checkpoint_in_plan(track_dir, p, sha):
    """Add or update the ``[checkpoint: <sha>]`` marker on Phase <p>'s heading in
    plan.md. Returns a result dict (no printing) so both the ``add-checkpoint``
    CLI command and the phase-checkpoint handshake (``cmd_phase_checkpoint_review``)
    can stamp without double-printing. ``ok`` on success; ``error`` on a missing
    plan.md, malformed SHA, or phase heading not found.

    A successful stamp also compiles ``.conductor/track-findings.md`` for this
    phase (advisory, fail-open) — the single home for that trigger; see the
    comment at the tail of this function."""
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        return dict(error="plan.md not found")
    if not re.match(r"^[0-9a-f]{7}$", sha):
        return dict(error="Invalid SHA format: must be 7 hex characters")

    with open(plan_path) as f:
        lines = f.readlines()

    result = []
    phase_num = int(p)  # 1-based phase number, matches "## Phase N" in plan.md
    found = False
    for line in lines:
        stripped = line.rstrip("\n")
        if re.match(rf"^##\s+Phase\s+{phase_num}\b", stripped):
            # Remove an existing checkpoint SHA before re-stamping. Inner space
            # is \s* (not \s+) to match plan_parse._CHECKPOINT / validate — all
            # four checkpoint detectors must agree, or a hand-authored/legacy
            # no-space stamp ([checkpoint:abcdef1]) is stripped by the parser but
            # not here → a duplicate stamp on re-stamp. The runtime always writes
            # with a space (the f-string below), so \s* still matches it.
            base = re.sub(
                r"\s+\[checkpoint:\s*[0-9a-f]+\]$", "", stripped)
            result.append(f"{base} [checkpoint: {sha}]")
            found = True
        else:
            result.append(stripped)
    if not found:
        return dict(error=f"Phase {phase_num} heading not found in plan.md")

    with open(plan_path, "w") as f:
        f.write("\n".join(result))
        if result and not result[-1].endswith("\n"):
            f.write("\n")

    # Single-homed track-findings compile: BOTH stamp paths funnel through this
    # helper (cmd_add_checkpoint — Rail A / the phase-checker agent — and
    # cmd_phase_checkpoint_review's PASSED arm — Rail B), so the compile lives
    # here rather than at either call site. A PASSED checkpoint means the
    # phase's durable findings are settled; later phases' consumers read
    # .conductor/track-findings.md. FAILED never stamps but DOES compile (via
    # compile_track_findings_fail_open at the review command's FAILED arm) —
    # failed phases are often where the learning is. Advisory + fail-open: a
    # compile error must never block the advance (the checkpoint is already
    # stamped).
    compile_track_findings_fail_open(track_dir, phase_num)
    return dict(ok=True, phase=p, sha=sha)


def compile_track_findings_fail_open(track_dir, phase_num=None):
    """Advisory track-findings compile that can never raise (single behavior
    home). Called from ``_stamp_checkpoint_in_plan`` (the PASSED stamp path,
    same module) and from ``cmd_phase_checkpoint_review``'s FAILED arm — one
    fail-open posture, two call sites. Calls the module-level
    ``compile_track_findings`` binding (the monkeypatch target the fail-open
    test patches), so keep the indirection — do not import it locally.
    """
    try:
        compile_track_findings(track_dir, current_phase=phase_num)
    except Exception as exc:  # noqa: BLE001 — advisory, never fatal
        sys.stderr.write(f"track-findings compile skipped (advisory): {exc}\n")


def cmd_add_checkpoint(track_dir, p, sha):
    """Add or update checkpoint SHA for a phase in plan.md (CLI wrapper)."""
    out(_stamp_checkpoint_in_plan(track_dir, p, sha))


def _amend_plan_task_tag(track_dir, p, t, tag):
    """Prepend a dispatch ``[Tag]`` onto a top-level task line in plan.md.

    The misroute-recovery write (decision: task-type ownership): a wrong label
    is fixed by amending the plan — the name is authoritative and ``task_type``
    is re-derived from it — never by a dispatch-time override. Returns
    ``dict(ok=True, name=<new cleaned name>)``; ``name`` is the OLD name when
    the tag was already present (idempotent no-op), and ``dict(error=...)`` on
    a missing plan/phase/task. The caller mirrors ``name`` into state and
    re-dispatches; routing then derives through the normal classification
    path. The edit is position-keyed (phase heading + nth top-level task
    line, the same anchors ``parse_plan`` walks), so it never guesses at
    name-matching.
    """
    from .plan_parse import _TASK_LINE, _clean_name

    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        return dict(error="plan.md not found")

    phase_num, task_num = int(p), int(t)
    with open(plan_path) as f:
        lines = f.readlines()

    result = []
    cur_phase = None
    task_seen = 0
    edited_name = None
    for line in lines:
        stripped = line.rstrip("\n")
        pm = re.match(r"^##\s+Phase\s+(\d+)\b", stripped)
        if pm:
            cur_phase = int(pm.group(1))
            result.append(stripped)
            continue
        tm = _TASK_LINE.match(stripped)
        # Top-level only (no indent) — subtasks never carry their own tag.
        if (cur_phase == phase_num and tm and not tm.group(1).strip()):
            task_seen += 1
            if task_seen == task_num:
                indent, status, name = tm.group(1), tm.group(2), tm.group(3)
                existing = extract_tags(name)
                if tag in existing:
                    edited_name = name  # idempotent: already tagged
                else:
                    edited_name = f"[{tag}] {name}".strip()
                    stripped = f"{indent}- [{status}] {edited_name}"
        result.append(stripped)
    if task_seen < task_num or (cur_phase or 0) < phase_num:
        return dict(error=f"task {task_num} not found under Phase {phase_num} "
                          "in plan.md")

    with open(plan_path, "w") as f:
        f.write("\n".join(result))
        if result and not result[-1].endswith("\n"):
            f.write("\n")
    # The returned name mirrors what parse_plan would store for the edited
    # line (comments stripped, sha/verified markers stripped, dispatch tags
    # preserved) so the caller's state mirror never drifts from a re-parse.
    return dict(ok=True, name=_clean_name(edited_name))



def cmd_deferred_report(track_dir):
    """List all deferred tasks with their context for final verification."""
    state = load(track_dir)
    deferred = []
    for pi, phase in enumerate(state["phases"], 1):
        for ti, task in enumerate(phase["tasks"], 1):
            if task["status"] == "deferred":
                deferred.append(dict(
                    phase=pi, task=ti, subtask=None,
                    name=task["name"], reason=task.get("defer_reason", ""),
                    phase_name=phase["name"],
                ))
            for si, sub in enumerate(task.get("subtasks", []), 1):
                if sub["status"] == "deferred":
                    deferred.append(dict(
                        phase=pi, task=ti, subtask=si,
                        name=sub["name"], reason=sub.get("defer_reason", ""),
                        phase_name=phase["name"],
                    ))
    out(dict(deferred=deferred, count=len(deferred)))

def cmd_phase_done(track_dir, p):
    state = load(track_dir)
    phase = state["phases"][int(p) - 1]
    terminal = TERMINAL_FOR_PARENT
    total = 0
    done = 0
    for task in phase["tasks"]:
        total += 1
        if task["status"] in terminal:
            done += 1
        for sub in task.get("subtasks", []):
            total += 1
            if sub["status"] in terminal:
                done += 1
    result = dict(complete=done == total, terminal=done, total=total)
    if result["complete"]:
        # Rail A paste-verbatim (design D3): when THIS phase's checkpoint is
        # due, attach the same pre-assembled verifier fan-out `dispatch-next`
        # and `step` emit (single source: _build_verifier_wave) so §3.7 → §3.2
        # Step 1 pastes each member's `prompt` verbatim — the code-free
        # narrowing (which verifiers fan) is resolved in code, not re-derived
        # in prose. Lazy import: dispatch imports misc (cycle at module level).
        from .dispatch import _build_verifier_wave, resolve_phase_gate
        from .helpers import _phase_needs_checkpoint
        cp = _phase_needs_checkpoint(track_dir, state, int(p))
        if cp is not None:
            gate_plan = resolve_phase_gate(track_dir, state, cp)
            result["checkpoint_due"] = True
            result["verifier_wave"] = _build_verifier_wave(
                track_dir, state, cp, verifiers=gate_plan["verifiers"])
    out(result)

def cmd_registry_update(track_dir, tracks_md_path):
    """Update a track's entry in the Tracks Registry (tracks.md) based on track-state.json status.

    Handles two formats:
    1. Section-based: ### TrackID ... - **Status:** value ... - **Path:** [link](dir/)
    2. Checkbox: - [marker] description (path/)
    """
    state = load(track_dir)
    track_dir_path = Path(track_dir).resolve()
    track_status = state.get("status", "new")
    track_id = state.get("track_id", "")

    registry_path = Path(tracks_md_path)
    if not registry_path.exists():
        out(dict(error=f"Tracks registry not found: {tracks_md_path}"))
        return

    content = registry_path.read_text()
    track_dir_name = track_dir_path.name

    new_marker = _STATUS_TO_MARKER.get(track_status, " ")

    lines = content.split("\n")
    updated = False
    in_track_section = False

    # NOTE: this is the WRITE side (mutates lines by index). The READ side —
    # enumerating every registry entry — lives in ``_iter_registry_entries``
    # (used by ``cmd_resolve_track``). Intentional duplication: a read-only
    # yielder does not fit this index-mutating write loop.
    for i, line in enumerate(lines):
        # Detect track section start: ### heading containing track dir name or track_id
        if re.match(r"^###\s+", line):
            in_track_section = track_dir_name in line or track_id in line

        # Format 1: Section-based — **Status:** value
        if in_track_section and re.match(r"^\s*-\s+\*\*Status:\*\*\s+", line):
            old_status = re.sub(r"^\s*-\s+\*\*Status:\*\*\s+", "", line).strip()
            if old_status != track_status:
                lines[i] = f"- **Status:** {track_status}"
                updated = True
            continue

        # Format 2: Checkbox — [marker] ... (path/). Greedy ``.*\(`` reaches the
        # LAST ``(`` (the link), so a description containing parens doesn't
        # capture the wrong text as the link path. Mirrors ``_RE_CHECKBOX``
        # (read side) — keep the two identical.
        m = re.match(r"^(\s*-\s+\[)([ x~!>#\-d@])(\]\s+.*\()([^)]*)(\).*)$", line)
        if m:
            prefix, old_marker, mid, link_path, suffix = m.groups()
            if track_dir_name in link_path or str(track_dir_path) in link_path:
                if old_marker != new_marker:
                    lines[i] = f"{prefix}{new_marker}{mid}{link_path}{suffix}"
                    updated = True
                break

    # Also update table row if present: | id | type | status | desc |
    for i, line in enumerate(lines):
        if re.match(r"^\|", line) and (track_id in line or track_dir_name in line):
            parts = [p.strip() for p in line.split("|")]
            # Find the status column (typically 3rd, index 3 after split)
            if len(parts) >= 4:
                new_line = line
                # Replace status in table — status is 3rd data column
                new_line = re.sub(
                    r"\|\s*(new|in_progress|completed|blocked|cancelled|deferred|skipped|failed|archived)\s*\|",
                    f"| {track_status} |",
                    new_line,
                    count=1,
                )
                if new_line != line:
                    lines[i] = new_line
                    updated = True

    if updated:
        registry_path.write_text("\n".join(lines))
        out(dict(updated=True, marker=new_marker, status=track_status))
    else:
        out(dict(updated=False, status=track_status))


def cmd_registry_add(track_dir, tracks_md_path=None):
    """Append a CANONICAL checkbox entry for ``track_dir`` to the Tracks Registry.

    Single source of truth for the registry line format — kills the drift class
    where ``new-track`` §2.6's model hand-wrote entries the reader couldn't parse
    (no ``(link)``, plain bullet, bold id, ...), which silently broke auto-select
    AND explicit ``setup <track>`` (entries dropped -> ``no_match``). Reads
    ``track_id`` / ``status`` / ``description`` from ``track-state.json`` and
    writes exactly::

        - [<marker>] <description> (conductor/tracks/<track_id>/)

    Idempotent: if an entry for ``track_id`` already parses (any format), it's a
    no-op (``already_present: true``) — safe to re-run, and it never duplicates.
    The registry is auto-located when ``tracks_md_path`` is omitted (walk-up via
    ``_find_registry``, then alongside the track dir), so the skill never has to
    hand-compute the path.
    """
    state = load(track_dir)
    track_id = state.get("track_id") or Path(track_dir).resolve().name
    status = state.get("status", "new")
    desc = (state.get("description") or track_id).strip() or track_id
    marker = _STATUS_TO_MARKER.get(status, " ")

    if tracks_md_path:
        reg = Path(tracks_md_path)
    else:
        # Walk UP from the track dir (CWD-independent): finds
        # ``<root>/conductor/tracks.md`` whether the skill ran from the project
        # root, ``conductor/``, or elsewhere. (``conductor_dir()`` is the
        # per-track ``.conductor/`` metadata dir, NOT the project conductor
        # root — not what we want here.)
        reg = _find_registry(track_dir)
    if not reg or not reg.is_file():
        out(dict(ok=False, reason="no_registry",
                 hint="No conductor/tracks.md found. Run /conductor:setup."))
        return

    existing = _iter_registry_entries(reg.read_text(), reg.parent)
    if any(e.get("track_id") == track_id for e in existing):
        out(dict(ok=True, already_present=True, track_id=track_id,
                 registry=str(reg)))
        return

    link = f"conductor/tracks/{track_id}/"
    line = f"- [{marker}] {desc} ({link})"
    content = reg.read_text()
    if content and not content.endswith("\n"):
        content += "\n"
    reg.write_text(content + line + "\n")
    out(dict(ok=True, appended=True, track_id=track_id, marker=marker,
             line=line, registry=str(reg)))


def cmd_registry_doc(tag=None, shape=None, roster=None):
    """Render the RESOLVED task-type + workflow-shape + agent-roster registries
    (baseline ⊕ overlay) — the live-data view complementing the grammar-only
    contract. Three on-demand filters: ``--tag`` / ``--shape`` / ``--roster``
    render one entity's row plus its prompt-shaping prose verbatim. Strictly
    read-only; fail-open everywhere.
    """
    # Local import: these modules are read by the phase-checker/dispatch paths and
    # resolve the overlay via the project root; importing here (not at module top)
    # keeps the render self-contained and avoids any import-order coupling.
    from . import task_profiles as tp
    from . import workflow_shapes as ws
    from . import agent_roster as ar
    from .registry_validate import validate_merged_roster

    def _yesno(b):
        return "yes" if b else "no"

    def _tag_row(tag):
        """One registry-derived table row for a tag (reused by full + filtered)."""
        route = tp.route_for([tag])
        gates = "+".join(tp.gates_of(tag)) or "(none)"
        grounding = tp.grounding_of(tag)
        when = tp.when_to_use_for(tag).strip().replace("\n", " ")
        markers = []
        doc = tp.workflow_doc_for(tag)
        if tp.workflow_for(tag) or doc:
            markers.append(f"workflow: {doc}" if doc else "workflow")
        if tp.refactor_for(tag):
            markers.append("refactor")
        marker = f" *({', '.join(markers)})*" if markers else ""
        return f"| `{tag}` | `{route}` | `{gates}` | `{grounding}` | {when}{marker} |"

    def _explicit_signals(tag):
        """The tag's EXPLICIT ``signals`` keyword list, or ``None``.

        Only a registry row that declares ``signals`` (a list) returns a value.
        Tags like ``[Refactor]`` deliberately omit it — they are opt-in, not
        goal-detected — so this returns ``None`` for them (never the weaker
        tokens ``_signals_for`` would *derive* from ``when_to_use``, which exist
        only for ``derive_task_tag``'s coarse fallback). Mirrors
        ``on-subagent-start._tag_summary_rows``: the planner matches a task
        description against these same keywords.
        """
        sig = tp._profile(tag).get("signals")  # noqa: SLF001 — registry-internal profile lookup
        return sig if isinstance(sig, list) and sig else None

    def _shape_row(shape):
        """One registry-derived table row for a workflow-shape (reused by full + filtered)."""
        nodes = " → ".join(ws.nodes_for(shape))
        verifiers = ", ".join(ws.verifiers_for(shape))
        policy = ws.verify_policy_for(shape)
        stop = ws.stop_condition_for(shape)
        return f"| `{shape}` | {nodes} | {verifiers} | `{policy}` | `{stop}` |"

    def _roster_row(agent):
        """One registry-derived table row for a rostered agent (full + filtered)."""
        row = ar.row_for(agent) or {}
        return (
            f"| `{agent}` | `{row.get('class', '?')}` | "
            f"{_yesno(ar.is_single_writer(agent))} | "
            f"{_yesno(row.get('registry_injection') is True)} | "
            f"{_yesno(row.get('retry') is True)} | "
            f"`{ar.recovery_kind_for(agent)}` |"
        )

    # --- filtered payloads ---------------------------------------------------
    # The on-demand path: one entity's row + its prompt-shaping prose verbatim.
    # This is the large-and-conditional payload the executor fetches instead of
    # having it injected into every dispatch.
    if tag is not None:
        if tag in tp.TAG_VOCAB():
            print(f"# Task Type `{tag}` (resolved: plugin baseline ⊕ project overlay)")
            print()
            print("| Tag | Route | Gates | Grounding | When to use |")
            print("|---|---|---|---|---|")
            print(_tag_row(tag))
            print()
            wf = tp.workflow_for(tag)
            doc = tp.workflow_doc_for(tag)
            if doc:
                # The docfile form (preferred for bespoke workflows): render
                # the resolved steps-library file verbatim. Project steps dir
                # wins the plugin one (resolve_workflow_doc), so an overlay
                # docfile renders here with zero plugin edits.
                path = tp.resolve_workflow_doc(tag)
                print(f"## Workflow docfile for `{tag}`: `{doc}` "
                      f"(follow it instead of default TDD)")
                print()
                try:
                    print(path.read_text(encoding="utf-8").rstrip("\n"))
                except OSError as exc:  # fail-open render, never a crash
                    print(f"_(docfile unreadable at {path}: {exc} — "
                          f"fall back to default TDD: "
                          f"`templates/workflow/steps/default-tdd.md`)_")
            elif wf:
                # The inline `workflow` form (small overlays): render the
                # registry prose verbatim, unchanged.
                print(f"## `workflow` for `{tag}` (follow this prose instead of default TDD)")
                print()
                print(wf)
            elif tp.is_tdd_exempt([tag]) and tp.is_coverage_exempt([tag]):
                print(f"_(no bespoke workflow for `{tag}` → both-exempt fast "
                      f"path: go straight to Step 8 of "
                      f"`templates/workflow/steps/default-tdd.md`)_")
            else:
                # The default arm renders the default docfile verbatim so a
                # single `--tag` fetch always returns the tag's complete
                # actionable step prose (Tier B fetch, context-model).
                path = tp.resolve_workflow_doc(tag)
                print(f"## Workflow docfile for `{tag}`: "
                      f"`{tp.DEFAULT_WORKFLOW_DOC}` (default TDD, Steps 3-8)")
                print()
                try:
                    print(path.read_text(encoding="utf-8").rstrip("\n"))
                except OSError as exc:  # fail-open render, never a crash
                    print(f"_(default docfile unreadable at {path}: {exc})_")
            if tp.refactor_for(tag):
                print()
                print(f"## `refactor` for `{tag}`: **true**")
                print()
                print(f"A task with leading tag `[{tag}]` opts into the tactical refactorer "
                      f"(§3.6c): the orchestrator dispatches `conductor:refactorer` once "
                      f"after the task succeeds — no `[Refactor]` name marker or "
                      f"`CONDUCTOR_TASK_REFACTOR=1` env required (those remain as escape "
                      f"hatches). The `[Conductor Registry]` block the executor receives "
                      f"carries this flag as `refactor: true`.")
            sig = _explicit_signals(tag)
            if sig:
                print()
                print(f"## `signals` for `{tag}` (description-matching keywords)")
                print()
                print(f"Match a task to this tag by these keywords: "
                      f"{', '.join(str(k) for k in sig)}")
            else:
                print()
                print(f"_(no explicit `signals` for `{tag}` → opt-in; match "
                      f"deliberately, never auto-propose)_")
        else:
            # Fail-open, mirroring the no-filter posture: an unknown tag is
            # surfaced, not raised (the *validator* hard-errors on unknown tags;
            # the renderer never does).
            print(f"# Task Type `{tag}` — UNKNOWN to the resolved registry")
            print()
            print(f"`{tag}` is not in the resolved tag vocabulary "
                  f"({', '.join(tp.TAG_VOCAB())}). `init-from-plan` would reject "
                  f"it as a hard error; register it in "
                  f"`conductor/workflow/task-type-profiles.json` to add it.")
        return

    if shape is not None:
        if shape in ws.SHAPES_VOCAB():
            print(f"# Workflow Shape `{shape}` "
                  f"(resolved: plugin baseline ⊕ project overlay)")
            print()
            print("| Shape | Nodes | Verifiers | Verify policy | Stop condition |")
            print("|---|---|---|---|---|")
            print(_shape_row(shape))
            print()
            # The planning docfile (preferred form): render the resolved
            # planning-library file verbatim — the shape's planning procedure
            # (orchestrator-facing Prelude + planner-facing body). Project
            # planning dir wins the plugin one (resolve_planning_doc), so an
            # overlay docfile renders here with zero plugin edits. The legacy
            # inline `instruction` prose renders only when a row carries it
            # (none shipped does).
            doc = ws.planning_doc_for(shape) or ws.DEFAULT_PLANNING_DOC
            path = ws.resolve_planning_doc(shape)
            print(f"## Planning docfile for `{shape}`: `{doc}` "
                  f"(the shape's planning procedure — Prelude + body)")
            print()
            try:
                print(path.read_text(encoding="utf-8").rstrip("\n"))
            except OSError as exc:  # fail-open render, never a crash
                print(f"_(docfile unreadable at {path}: {exc} — fall back: "
                      f"`templates/planning/{ws.DEFAULT_PLANNING_DOC}`)_")
            legacy = ws.instruction_for(shape)
            if legacy:
                print()
                print(f"## `instruction` for `{shape}` (LEGACY inline prose)")
                print()
                print(legacy)
            # when_to_use is the human-facing rationale — the gloss for the
            # machine `signals` propose-shape matches (mirrors the tag arm's
            # when_to_use column).
            when = ws._shape(shape).get("when_to_use", "")  # noqa: SLF001 — registry-internal shape lookup
            if when:
                print()
                print(f"## `when_to_use` for `{shape}` "
                      f"(rationale — the gloss for the machine `signals`)")
                print()
                print(when)
            # The shape-controlled paradigm (the portability axis): which gates
            # the shape enforces, the executor's default workflow, and how ACs
            # are grounded. `registry-doc --shape <name>` shows the full shape
            # contract — topology above, paradigm here.
            gates = ", ".join(ws.gates_for(shape)) or "(none)"
            print()
            print(f"## Shape-controlled paradigm for `{shape}`")
            print()
            print(f"- **gates**: {gates} — the track-level ON/OFF each quality "
                  f"gate composes with the per-task exemption (a gate fires for "
                  f"a task iff listed here AND the task's tag is not exempt). "
                  f"`tdd`=F2, `coverage`=F3, `checkpoint`=F5.")
            print(f"- **ac_grounding**: `{ws.ac_grounding_for(shape)}` — how "
                  f"acceptance criteria are grounded (`test` = by `test_TC_*` "
                  f"functions; `review` = by an artifact anchor + a review "
                  f"attestation).")
            print(f"- **checkpoint_policy**: `{ws.checkpoint_policy_for(shape)}` — "
                  f"whether the checkpoint phase runs (`run` = the phase-checker "
                  f"checkpoint fans out; `skip-if-declared` = short-circuited, "
                  f"only when the shape declares a substitute — see "
                  f"`ac_grounding`).")
        else:
            print(f"# Workflow Shape `{shape}` — UNKNOWN to the resolved registry")
            print()
            print(f"`{shape}` is not in the resolved shape vocabulary "
                  f"({', '.join(ws.SHAPES_VOCAB())}). A track-state.json carrying "
                  f"it resolves to `default` (fail-open); register it in "
                  f"`conductor/workflow/workflow-shapes.json` to add it.")
        return

    if roster is not None:
        if ar.row_for(roster) is not None:
            print(f"# Agent Roster: `{roster}` "
                  f"(resolved: plugin baseline ⊕ project overlay)")
            print()
            print("| Agent | Class | Single-writer | Registry-injection | Retry | Recovery |")
            print("|---|---|---|---|---|---|")
            print(_roster_row(roster))
            print()
            # The fence verbatim: this is the exact string composed into the
            # SubagentStart reminder (REMINDER_LEAD + fence) — the one facet
            # every dispatch depends on and the reason a row exists.
            print(f"## Result-format fence for `{roster}` "
                  f"(SubagentStart injects it verbatim)")
            print()
            print(f"`{ar.reminder_for(roster)}`")
            rec = ar.recovery_instruction_for(roster)
            if rec:
                print()
                print(f"## Recovery instruction for `{roster}` "
                      f"(appended after the [Conductor Recovery] lead)")
                print()
                print(rec)
            errs = validate_merged_roster(ar._load())  # noqa: SLF001 — registry-internal resolved-doc lookup
            if errs:
                print()
                print("## WARNING: resolved roster validation errors")
                print()
                for e in errs:
                    print(f"- {e}")
        else:
            # Fail-open, mirroring the tag/shape arms: an unknown agent is
            # surfaced, never raised (the *lint* hard-errors on dead names).
            print(f"# Agent Roster: `{roster}` — UNKNOWN to the resolved roster")
            print()
            print(f"`{roster}` is unrostered: it dispatches fine (the harness "
                  f"resolves the three name homes) but receives no scaffold — "
                  f"no reminder, no recovery contract, fail-open. To scaffold "
                  f"it, add one row in `conductor/workflow/agent-roster.json`.")
        return

    # --- full overview (no filter) -------------------------------------------
    print("# Conductor Registry (resolved: plugin baseline ⊕ project overlay)")
    print()
    print("Source: conductor/workflow/{task-type,workflow-shape}-profiles.json "
          "(project overlay) over the plugin baseline.")
    print()

    # --- Task types -----------------------------------------------------------
    tags = tp.TAG_VOCAB()
    print(f"## Task Types ({len(tags)})")
    print()
    print("| Tag | Route | Gates | Grounding | When to use |")
    print("|---|---|---|---|---|")
    for tag in tags:
        print(_tag_row(tag))
    print()
    print("Rows carrying a `workflow` diverge from default TDD — the executor "
          "fetches that prose on demand (`track-state registry-doc --tag <Name>`); "
          "the rest use default TDD (Steps 3-8). Rows carrying `refactor` opt into "
          "the tactical refactorer at the §3.6c seam after success. `[Explore]` "
          "routes to explorer; task-executor REFUSES it.")
    print()

    # Tag signals: the description-matching keywords a planner/deriver match
    # against. Only tags that explicitly declare `signals` appear (opt-in tags
    # like [Refactor] omit it). This is the matcher DATA spec-planner fetches
    # here on demand (tier B) instead of receiving it injected — so the planner
    # matches a task description against the same inputs derive_task_tag uses.
    sig_rows = [(t, _explicit_signals(t)) for t in tags]
    sig_rows = [(t, s) for t, s in sig_rows if s]
    if sig_rows:
        print("## Tag Signals (description-matching keywords)")
        print()
        print("Match a task description to a tag by these keywords — GUIDANCE "
              "for your labeling judgment, not a command to run: classify by "
              "the task's deliverable, use these as tiebreakers. Only tags "
              "that explicitly declare `signals` appear; a tag with no row "
              "here (e.g. `[Refactor]`) is opt-in — match it deliberately, "
              "never auto-propose it. (`init-from-plan --check` prints an "
              "advisory when your declared tag disagrees with these signals.)")
        print()
        for sig_tag, sig in sig_rows:
            print(f"- `[{sig_tag}]`: {', '.join(str(k) for k in sig)}")
            # Few-shot exemplars (Finding-1 method 4): judgment transfers
            # through examples better than keyword lists — render each
            # declared example verbatim under its tag's keyword line.
            ex = tp._profile(sig_tag).get("examples")  # noqa: SLF001 — registry-internal profile lookup
            if isinstance(ex, list) and ex:
                for e in ex:
                    print(f"    e.g. {e}")
        print()

    # --- Workflow shapes (the node sequence) ---------------------------------
    shapes = ws.SHAPES_VOCAB()
    print(f"## Workflow Shapes ({len(shapes)})")
    print()
    print("| Shape | Nodes | Verifiers | Verify policy | Stop condition |")
    print("|---|---|---|---|---|")
    for shape in shapes:
        print(_shape_row(shape))
    print()
    print("What each node *says* lives in Task Types above; the **node sequence** "
          "lives here. A track-state.json `workflow_shape` selects the topology; "
          "an off-topology dispatch surfaces a `shape_violation` (advisory, "
          "no-silent-caps). The **Verifiers** column is load-bearing — it names "
          "which checkpoint verifiers a shape fans out (a project shape omitting "
          "`test-runner` simply doesn't fan it out). `default` is the loop the "
          "conductor has always run, now declared rather than hardcoded.")
    print()

    # --- Agent roster (the dispatch scaffold) --------------------------------
    agents = ar.merged_agent_names()
    print(f"## Agent Roster ({len(agents)})")
    print()
    print("| Agent | Class | Single-writer | Registry-injection | Retry | Recovery |")
    print("|---|---|---|---|---|---|")
    for agent in agents:
        print(_roster_row(agent))
    print()
    print("The dispatch SCAFFOLD each agent receives (result fence, registry "
          "injection, retry context, single-writer guard, stop-hook recovery). "
          "The fence body and recovery instruction render verbatim on demand "
          "(`track-state registry-doc --roster <agent>`). An agent absent from "
          "this table is *unrostered*: dispatchable but fail-open with no "
          "scaffold — the pre-registry behavior for built-in agents. A project "
          "scaffolds its own agent with one row in "
          "`conductor/workflow/agent-roster.json`.")
    print()
    roster_errs = validate_merged_roster(ar._load())  # noqa: SLF001 — registry-internal resolved-doc lookup
    if roster_errs:
        print("**WARNING — resolved roster validation errors** (the read is "
              "fail-open; `track-state check` fails on these):")
        print()
        for e in roster_errs:
            print(f"- {e}")
        print()


def cmd_record_summary(track_dir):
    """Record a compact task summary for context recovery after compaction."""
    summaries_path = conductor_dir(track_dir) / "task-summaries.json"
    # Read from stdin: JSON with {phase, task, sha, status, summary}
    data = json.loads(sys.stdin.read() if not sys.stdin.isatty() else "{}")
    p, t = data.get("phase", "?"), data.get("task", "?")
    sha = data.get("sha", "")
    status = data.get("status", "?")
    summary = data.get("summary", "")

    summaries = {}
    if summaries_path.exists():
        try:
            summaries = json.loads(summaries_path.read_text())
        except (json.JSONDecodeError, ValueError):
            pass

    try:
        key = f"P{int(p)}.T{int(t)}"
    except (ValueError, TypeError):
        key = f"P{p}.T{t}"
    summaries[key] = {"sha": sha, "status": status, "summary": summary}
    summaries_path.write_text(json.dumps(summaries, indent=2))
    out(dict(ok=True, recorded=key))
