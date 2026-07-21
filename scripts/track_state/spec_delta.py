"""``track-state spec-delta`` — what does a spec.md edit put at risk?

Fills the spec half of the post-``git reset`` recovery story. The plan half
(``reconcile-plan``, shipped ``7b00224``) reconciles an edited ``plan.md`` into
``track-state.json`` **by name**, preserving ``commit_sha``. But an edit to
``spec.md`` — a reworded Acceptance Criterion, a tightened NFR, a new Constraint
— is a different hazard: a completed task whose ``<!-- AC-n -->`` annotation
claimed the *old* AC text now carries a ``commit_sha`` whose work may no longer
satisfy the new criterion. Nothing flagged this before; ``reconcile-plan``
reconciles *structure*, not *meaning*.

This module is the read-only engine behind ``/conductor:re-spec`` (and usable
standalone): diff two versions of ``spec.md`` (AC/FR/NFR inventory + body text),
then join every **changed** AC to the plan tasks that claim it via
``plan_parse._extract_refs``, then keep only the join hits that are **terminal
in state with a ``commit_sha``** — those are the SHAs a human must decide
whether to keep or reset. Surface only; never mutate.

Modelled on ``spec-anchors`` (``spec_integrity.py:481``): a pure diff function
plus a read-only CLI that prints JSON via ``helpers.out`` and exits 0. All
intelligence is composition of existing parsers (``spec_parse.parse_spec``,
``plan_parse.parse_plan``); no new parsing of the Constraints section (the
closed 4-key ``_SECTION_HEADINGS`` stays — constraints ride the directive
channel, ``.conductor/track-directives.md``, not the parser).
"""
import subprocess
from pathlib import Path

from . import helpers
from . import spec_parse
from .plan_parse import parse_plan
from .core import load

# Terminal statuses that carry a commit_sha worth flagging as at-risk.
# (Mirrors constants.SHA_MARKERS conceptually, without importing it — a node is
# "at risk" iff it claims done-ness AND has a SHA to lose.)
_TERMINAL_WITH_SHA = ("completed", "skipped")


def _ac_body_map(spec):
    """``{AC-N: body_text}`` from a ``parse_spec_text`` result's ``ac_items``."""
    return {item["id"]: item["text"] for item in spec.get("ac_items", [])}


def _id_set(parsed, key):
    """De-duped ID list from a ``parse_spec`` result (``acs``/``frs``/``nfrs``)."""
    return list(dict.fromkeys(parsed.get(key, [])))


def compute_spec_delta(before_text, after_text, plan_tasks):
    """Diff two spec.md versions; join changed ACs → plan tasks → at-risk SHAs.

    Pure: callers supply the before/after spec text and the parsed plan tasks
    (each task is a dict from ``parse_plan`` augmented with ``status`` /
    ``commit_sha`` / a ``coord`` like ``"P2.T1"`` — see ``_enrich_tasks`` which
    the CLI runs before calling this). Writes nothing.

    Returns::

        {
          "changed_acs": [{"id": "AC-3", "before": "...", "after": "..."}],
          "added_acs":   ["AC-4"],
          "removed_acs": ["AC-2"],
          "changed_frs": [...], "added_frs": [...], "removed_frs": [...],
          "changed_nfrs":[...], "added_nfrs":[...], "removed_nfrs":[...],
          "at_risk_tasks": [{"coord": "P2.T1", "name": "...",
                             "commit_sha": "abc1234", "acs": ["AC-3"]}],
          "errors": []
        }

    ``at_risk_tasks`` is the headline: only tasks that (a) claim a **changed**
    AC (body text differs — added/removed ACs are structural, handled by
    reconcile-plan, not here) AND (b) are terminal in state with a
    ``commit_sha``. Added/removed ACs don't put *existing* SHAs at risk (a new
    AC has no prior implementer; a removed AC's claimers are simply no longer
    traced). Surface only — the caller decides reset-vs-keep.
    """
    errors = []
    try:
        before_spec = spec_parse.parse_spec_text(before_text)
        after_spec = spec_parse.parse_spec_text(after_text)
    except Exception as exc:  # best-effort, like spec_parse itself
        errors.append(f"spec parse failed: {exc}")
        before_spec = after_spec = {"frs": [], "nfrs": [], "acs": [], "ac_items": []}

    before_ac = _ac_body_map(before_spec)
    after_ac = _ac_body_map(after_spec)

    def _delta_body(before_map, after_map):
        changed, added, removed = [], [], []
        for aid, body in after_map.items():
            if aid not in before_map:
                added.append(aid)
            elif before_map[aid] != body:
                changed.append({"id": aid, "before": before_map[aid],
                                "after": body})
        for aid in before_map:
            if aid not in after_map:
                removed.append(aid)
        return changed, added, removed

    changed_acs, added_acs, removed_acs = _delta_body(before_ac, after_ac)

    # FR/NFR are inventory-only (no traceability channel — no plan annotation
    # points at them). Report set deltas only.
    def _id_delta(key):
        b = _id_set(before_spec, key)
        a = _id_set(after_spec, key)
        added = [x for x in a if x not in b]
        removed = [x for x in b if x not in a]
        return added, removed

    added_frs, removed_frs = _id_delta("frs")
    added_nfrs, removed_nfrs = _id_delta("nfrs")

    # Join: changed ACs → tasks claiming them (via ac_refs) → terminal+SHA only.
    changed_ids = {c["id"] for c in changed_acs}
    at_risk = []
    if changed_ids:
        for task in plan_tasks:
            claimed = [r for r in task.get("ac_refs", []) if r in changed_ids]
            if not claimed:
                continue
            if (task.get("status") in _TERMINAL_WITH_SHA
                    and task.get("commit_sha")):
                at_risk.append({
                    "coord": task.get("coord", "?"),
                    "name": task.get("name", "?"),
                    "commit_sha": task["commit_sha"],
                    "acs": claimed,
                })

    return {
        "changed_acs": changed_acs,
        "added_acs": added_acs,
        "removed_acs": removed_acs,
        "added_frs": added_frs,
        "removed_frs": removed_frs,
        "added_nfrs": added_nfrs,
        "removed_nfrs": removed_nfrs,
        "at_risk_tasks": at_risk,
        "errors": errors,
    }


def _enrich_tasks(track_dir, state):
    """Parse plan.md and graft state status/commit_sha + a ``P{n}.T{n}`` coord
    onto each top-level task. Subtasks are ignored (they inherit AC context
    from the parent and are never independent claimers — rule 6).
    """
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        return []
    parsed = parse_plan(plan_path)
    enriched = []
    phases = state.get("phases", [])
    for phase in parsed.get("phases", []):
        pnum = phase["number"]
        # State phases are keyed by POSITION (they carry no "number" field),
        # same assumption reconcile-plan makes (``_state_index_maps`` enumerate).
        state_phase = phases[pnum - 1] if 0 <= pnum - 1 < len(phases) else None
        state_tasks = (state_phase or {}).get("tasks", [])
        for ti, task in enumerate(phase.get("tasks", []), 1):
            # Match by cleaned name (state names are already clean).
            st = next((t for t in state_tasks
                       if helpers._clean_trailing_markers(t.get("name", "")).strip().lower()
                       == helpers._clean_trailing_markers(task.get("name", "")).strip().lower()),
                      None)
            enriched.append({
                "coord": f"P{pnum}.T{ti}",
                "name": task.get("name", ""),
                "ac_refs": task.get("ac_refs", []),
                "status": (st or {}).get("status"),
                "commit_sha": (st or {}).get("commit_sha"),
            })
    return enriched


def _git_show_spec(track_dir, rev):
    """``git show <rev>:spec.md`` → text, or None on failure (not a git track,
    shallow history, file absent at that rev). Fail-open like reconcile's
    ``_is_sha_live`` probe."""
    try:
        result = subprocess.run(
            ["git", "show", f"{rev}:spec.md"],
            capture_output=True, text=True, cwd=str(track_dir), timeout=10)
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    return None


def cmd_spec_delta(track_dir, before=None):
    """Read-only CLI: diff the current ``spec.md`` against a prior version and
    report which completed SHAs are now at risk.

    ``after`` is always the current ``{TRACK_DIR}/spec.md``.
    ``before`` is, in priority order: the ``--before <path>`` argument, else
    ``git show HEAD~1:spec.md`` (the last committed spec — the common case right
    after ``/conductor:re-spec`` commits an edit). If neither resolves (no git
    history / not a git track), reports an error in ``errors[]`` and still
    exits 0 — failure is surfaced via the payload, never a non-zero exit
    (mirrors ``spec-anchors``).
    """
    spec_path = Path(track_dir) / "spec.md"
    errors = []
    if not spec_path.exists():
        helpers.out({"ok": False, "errors": [f"spec.md not found at {spec_path}"]})
        return
    after_text = spec_path.read_text()

    if before:
        before_text = Path(before).read_text() if Path(before).exists() else None
        if before_text is None:
            errors.append(f"--before path not found: {before}")
    else:
        before_text = _git_show_spec(track_dir, "HEAD~1")
        if before_text is None:
            errors.append(
                "could not resolve prior spec.md (no --before given and "
                "`git show HEAD~1:spec.md` failed — not a git track, shallow "
                "history, or spec.md untracked). Pass --before <path> to a "
                "saved baseline.")

    if before_text is None:
        helpers.out({"ok": False, "errors": errors, "source": str(spec_path)})
        return

    state = load(track_dir)
    plan_tasks = _enrich_tasks(track_dir, state)
    delta = compute_spec_delta(before_text, after_text, plan_tasks)
    helpers.out({
        "ok": not delta["errors"],
        "source": str(spec_path),
        "before": before or "HEAD~1:spec.md",
        **delta,
        "errors": errors + delta["errors"],
    })
