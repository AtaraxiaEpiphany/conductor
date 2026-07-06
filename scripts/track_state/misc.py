"""Miscellaneous track-state commands."""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from .core import load, save
from .helpers import (
    out, now_iso, target, extract_tags, _reset_task,
    _any_phase_needs_checkpoint, conductor_dir, _tag_exempt_from_coverage,
    _resolve_conductor_root, _find_registry,
)
from .mutations import _do_complete
from .sync import _do_sync_plan
from .git_ops import _git_commit, _git_head_sha, _ensure_note, docs_synced_for_track
from .constants import TERMINAL_FOR_PARENT
from .quality import _checklist_status
from .spec_integrity import compute_ac_integrity


# Core conductor files every executable track must have. Single source for the
# setup check repeated (with drift) across skills — preflight centralizes it.
_TRACK_CORE_FILES = ("spec.md", "plan.md", "track-state.json")

# Project-level workflow files every /conductor:implement run depends on
# (implement §1.0 reads index.md; §4.0 reads post-loop.md). They live at the
# conductor ROOT, not inside the track dir, so preflight resolves the root from
# the track path and checks them alongside the track-core files. Fail-open: when
# no conductor root is locatable (no tracks.md ancestor — e.g. a bare temp dir),
# the workflow check is skipped rather than failing ok, so a resolution miss can
# never HALT setup on a non-standard layout (and the existing preflight tests,
# which use temp dirs without a project layout, stay green).
_WORKFLOW_FILES = ("workflow/index.md", "workflow/post-loop.md")

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


def _preflight_result(track_dir):
    """Compute the preflight envelope as a dict — factored body of
    ``cmd_preflight`` so ``cmd_setup`` can compose it without capturing stdout.
    """
    td = Path(track_dir)
    missing = [f for f in _TRACK_CORE_FILES if not (td / f).exists()]
    invalid_state = False
    if not missing:
        try:
            load(track_dir)
        except Exception:
            invalid_state = True

    # Project-level workflow files. Skipped (empty) when no conductor root is
    # locatable — fail-open so this never blocks setup on an unusual layout.
    conductor_root = _resolve_conductor_root(track_dir)
    missing_workflow = []
    if conductor_root is not None:
        missing_workflow = [f for f in _WORKFLOW_FILES
                            if not (conductor_root / f).exists()]

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

    return dict(
        ok=not missing and not invalid_state and not missing_workflow,
        missing=missing,
        missing_workflow=missing_workflow,
        track_dir=str(td),
        invalid_state=invalid_state,
        hint=hint,
    )


def cmd_preflight(track_dir):
    """Verify a track's core conductor files exist and its state loads.

    Single machine-checkable entry point for skill setup checks, replacing the
    repeated "verify spec.md/plan.md/track-state.json" prose. Also gates the two
    project-level workflow files (``conductor/workflow/index.md`` and
    ``post-loop.md``) that implement depends on — fail-open per
    ``_resolve_conductor_root``. Outputs
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
                cov = ev.get("coverage_pct")
                if isinstance(cov, (int, float)):
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


def _iter_registry_entries(text, conductor_root):
    """Parse registry text into a list of ``{track_id, track_dir, marker, status_str}``.

    Read-only mirror of the write-side parsing in ``cmd_registry_update`` (the
    single source of truth for the registry file's three formats). Handles:

    - **checkbox**: ``- [marker] desc (path/)`` — ``track_dir`` from the link
      path (resolved against ``conductor_root``); ``marker`` captured directly.
    - **section**: ``### <id>`` + ``- **Status:** <status>`` — ``track_dir``
      derived as ``<conductor_root>/tracks/<id>`` (sections carry no path).
    - **table**: ``| id | type | status | desc |`` — ``track_dir`` derived.

    Only checkbox entries carry a path; section/table entries derive it from
    ``track_id`` via the canonical ``conductor/tracks/<track_id>`` layout.
    Returns entries in document order; malformed lines are silently skipped.
    """
    entries = []
    root = Path(conductor_root)
    in_section_id = None
    section_status = None

    def _derived_dir(track_id):
        return str(root / "tracks" / track_id)

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
            # Registry checkbox links are written either project-root-relative
            # ("conductor/tracks/<id>/") — the canonical form ``cmd_derive_name``
            # emits — or conductor-root-relative ("tracks/<id>/"). ``root`` is
            # the conductor root (parent of tracks.md = <project>/conductor);
            # resolving a project-root-relative link against it doubles
            # "conductor/" and yields a non-existent path. Pick the base by
            # form: absolute as-is, "conductor/"-prefixed against the project
            # root (root.parent), else against the conductor root.
            link = Path(lp)
            lp_norm = lp.replace("\\", "/").lower()
            if link.is_absolute():
                track_dir = str(link.resolve())
            elif lp_norm.startswith("conductor/"):
                track_dir = str((root.parent / lp).resolve())
            else:
                track_dir = str((root / lp).resolve())
            track_id = link.name  # full id incl. _YYYYMMDD; shortname derived at match time
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

    Status is authoritative — read from each entry's ``track-state.json`` via
    ``load()``; the registry marker is the fallback when state is unreadable.
    """
    if reg is None or not reg.is_file():
        return dict(ok=False, reason="no_registry",
                    hint="Run /conductor:setup (no conductor/tracks.md found).")
    conductor_root = reg.parent
    entries = _iter_registry_entries(reg.read_text(), conductor_root)

    # Authoritative status per entry (registry projection is the fallback).
    resolved = []
    for e in entries:
        status = e.get("status_str")
        td = e.get("track_dir")
        if td:
            try:
                status = load(td).get("status") or status
            except Exception:
                pass  # keep the registry projection
        resolved.append(dict(track_id=e.get("track_id"), track_dir=td,
                             status=status, marker=e.get("marker")))

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

    # Auto-select: the active (non-terminal) tracks.
    live = [r for r in resolved if r["status"] in _TRACK_NON_TERMINAL]
    if not live:
        return dict(ok=False, reason="no_non_terminal",
                    hint="No track with status new/in_progress. Pass a track_id query.")
    if len(live) == 1:
        r = live[0]
        return dict(ok=True, track_id=r["track_id"], track_dir=r["track_dir"],
                    status=r["status"], via="auto_single")
    # >1 live: prefer resuming a SINGLE in_progress track over starting one of
    # several new ones — work-in-flight beats fresh work, and this dissolves the
    # common "one track running + a freshly-created new track" ambiguous prompt.
    # Only ask on a genuine tie (multiple in_progress, or several new with none
    # in flight).
    in_prog = [r for r in live if r["status"] == "in_progress"]
    if len(in_prog) == 1:
        r = in_prog[0]
        return dict(ok=True, track_id=r["track_id"], track_dir=r["track_dir"],
                    status=r["status"], via="auto_prefer_in_progress")
    return dict(ok=False, reason="ambiguous", candidates=live)


def _resolve_registry(registry_path):
    """The registry path: an explicit ``--registry`` arg, else auto-located."""
    return Path(registry_path) if registry_path else _find_registry()


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


def cmd_setup(query=None, registry_path=None):
    """Resolve + preflight a track in one call; ALWAYS exits 0.

    Collapses the skill §1.0 ``resolve-track`` + ``preflight`` pair into a single
    read-only query so the model never hand-carries ``<td>`` between them — the
    last path-handoff in setup, and the exact mishandling class that motivated
    ``resolve-track``. Mirrors ``cmd_preflight`` / ``cmd_resolve_track``: outcome
    in JSON, never the exit code. Composes ``_resolve_core`` (resolve + the
    placeholder / full-path defenses) with ``_preflight_result`` (the readiness
    check). Read-only — the ``new`` -> ``start`` transition stays with
    ``recover``, where the status comes from.

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
        ``{ok:false, reason, message, hint?, missing?, missing_workflow?}``.
        Print ``message``; HALT. ``reason`` is one of ``preflight`` /
        ``no_registry`` / ``no_match`` / ``no_non_terminal``.

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
    if reason in ("no_match", "no_non_terminal"):
        out(dict(action="halt", ok=False, reason=reason,
                 message=core.get("hint")
                 or "No track selected. Pass a track_id, or see conductor/tracks.md.",
                 hint=core.get("hint")))
        return

    td = core.get("track_dir")
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

    pf = _preflight_result(td)
    if not pf["ok"]:
        out(dict(action="halt", ok=False, reason="preflight", td=td,
                 message="Conductor environment incomplete. Run /conductor:setup.",
                 hint=pf.get("hint")
                 or "Conductor environment incomplete. Run /conductor:setup.",
                 missing=pf.get("missing"),
                 missing_workflow=pf.get("missing_workflow")))
        return

    # Resolved + ready.
    status = core["status"]
    via = core.get("via", "arg")
    how = "auto-selected" if str(via).startswith("auto") else "resolved"
    announce = f"Track '{core['track_id']}' ({status}) — {how}."
    out(dict(action="proceed", ok=True, td=td, track_id=core["track_id"],
             status=status, via=via, announce=announce))


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
    if pl_path.exists():
        try:
            data = json.loads(pl_path.read_text())
            if isinstance(data, dict):
                reviewed_range = data.get("reviewed_range")
        except (ValueError, OSError):
            reviewed_range = None

    review_done = bool(reviewed_range and current_range
                       and reviewed_range == current_range)

    status = state.get("status")
    finalized = (status == "completed"
                 and isinstance(state.get("quality_score"), (int, float)))

    out(dict(
        track_id=state.get("track_id"),
        status=status,
        finalized=finalized,
        doc_synced=docs_synced_for_track(track_dir),
        review=dict(done=review_done, range=current_range,
                    reviewed_range=reviewed_range),
        shas_count=len(shas),
    ))


def cmd_add_checkpoint(track_dir, p, sha):
    """Add or update checkpoint SHA for a phase in plan.md."""
    plan_path = Path(track_dir) / "plan.md"

    if not plan_path.exists():
        out(dict(error="plan.md not found"))
        return

    # Validate sha format
    if not re.match(r"^[0-9a-f]{7}$", sha):
        out(dict(error="Invalid SHA format: must be 7 hex characters"))
        return

    with open(plan_path) as f:
        lines = f.readlines()

    result = []
    phase_num = int(p)  # Already 1-based
    found = False

    for line in lines:
        stripped = line.rstrip("\n")
        # Match phase heading: ## Phase 1: ... or ## Phase 1
        pm = re.match(rf"^##\s+Phase\s+{phase_num}\b", stripped)
        if pm:
            # Remove existing checkpoint if present
            base = re.sub(r"\s+\[checkpoint:\s*[0-9a-f]+\]$", "", stripped)
            # Add new checkpoint
            updated = f"{base} [checkpoint: {sha}]"
            result.append(updated)
            found = True
        else:
            result.append(stripped)

    if not found:
        out(dict(error=f"Phase {int(p)} heading not found in plan.md"))
        return

    with open(plan_path, "w") as f:
        f.write("\n".join(result))
        if result and not result[-1].endswith("\n"):
            f.write("\n")

    out(dict(ok=True, phase=p, sha=sha))


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
    out(dict(complete=done == total, terminal=done, total=total))

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
