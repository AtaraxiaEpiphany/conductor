"""task_context — the per-task plan↔spec join, owned as one read-only CLI.

task-executor's Layer 1+2 used to hand-roll this join (read plan.md, find the
task, read its ``<!-- AC-n, TC-n.m -->`` annotation, open spec.md, extract each
AC's text + the TC rows that trace to those ACs). ``plan_parse`` and
``spec_parse`` already compute both halves internally; this module composes them
into one deterministic join so the extraction cannot drift from the parsers'
grammar. Read-only and fail-open everywhere.

Output shape::

    {"phase", "task", "name", "tags", "tag_profile",
     "ac_refs", "tc_refs", "acs": [{"id","text"}], "tcs": [{"id","ac"}],
     "artifacts": {"uses": [...], "produced": [...]},
     "errors", "warnings"}

``tag_profile`` is ``None`` for an untagged (default) task; otherwise it carries
the leading tag's resolved profile (route / tdd_exempt / coverage_exempt) plus
whether the tag carries a ``workflow`` (the prose itself is fetched separately
via ``registry-doc --tag``) and ``refactor``. The (phase, task) address is the
top-level task; subtasks inherit their parent's refs, so no subtask dimension is
needed here (mirrors ``result._declared_tcs_for_task``).

``artifacts`` (findings/artifact edge) delivers the dataflow: ``uses`` carries
this task's ``<!-- uses: ... -->`` refs resolved against the project root, and
``produced`` carries every earlier task's declared artifacts harvested from the
handoff ledger (strictly-earlier ``(phase, task)`` by handoff stem — same-phase
serial included; self/future/same-phase-higher excluded). Fail-open: no plan,
no handoffs, or malformed stems yield empty lists, never an error.
"""
import os
import re
from pathlib import Path

from .plan_parse import parse_plan
from .spec_parse import parse_spec
from .helpers import out, extract_tags
from . import task_profiles as tp

_HANDOFF_STEM = re.compile(r"^P(\d+)T(\d+)$")


def _task_refs(track_dir, phase, task):
    """The ``(name, ac_refs, tc_refs)`` for one task, via ``parse_plan``.

    State tasks don't carry ``ac_refs``/``tc_refs`` (``to_plan_structure`` drops
    them), so this re-parses ``plan.md`` — the same trick
    :func:`result._declared_tcs_for_task` uses. Returns ``(None, [], [])`` when
    plan.md, the phase, or the task index is absent.
    """
    plan_path = Path(track_dir) / "plan.md"
    if not plan_path.exists():
        return None, [], []
    try:
        pi, ti = int(phase) - 1, int(task) - 1
    except (TypeError, ValueError):
        return None, [], []
    phases = parse_plan(plan_path).get("phases", [])
    if not (0 <= pi < len(phases)):
        return None, [], []
    tasks = phases[pi].get("tasks", [])
    if not (0 <= ti < len(tasks)):
        return None, [], []
    t = tasks[ti]
    return t.get("name"), t.get("ac_refs", []), t.get("tc_refs", [])


def _resolve_project_root(track_dir):
    """Best-effort project root for resolving repo-relative artifact paths.

    ``{root}/conductor/tracks/{id}`` is the canonical track layout → the root
    is three levels up. Otherwise defer to ``$CLAUDE_PROJECT_DIR``; a bare
    track dir in a test resolves to ``None`` (paths stay repo-relative, with
    a warning) rather than guessing.
    """
    p = Path(track_dir).resolve()
    if len(p.parts) >= 3 and p.parts[-3:-1] == ("conductor", "tracks"):
        return p.parents[2]
    env_proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_proj:
        return Path(env_proj).resolve()
    return None


def _artifact_edges(track_dir, phase, task):
    """The artifacts delivery join (findings/artifact edge). Fail-open.

    Returns ``(uses, produced)``:
    - uses: ``[{"path", "resolved", "exists"}]`` for THIS task's ``uses:``
      refs, resolved against the project root (``resolved``/``exists`` are
      ``None`` when no root could be determined — the repo-relative path is
      still delivered).
    - produced: ``[{"path", "role", "source"}]`` for every artifact an
      EARLIER task declared (handoff-stem ``(phase, task)`` strictly less than
      this task's lexicographic coordinate; same-phase serial included —
      P2T1 reaches P2T3). Self, future, and same-phase-higher excluded.
    """
    from .handoff import _extract_candidates
    uses, produced = [], []
    try:
        cur = (int(phase), int(task))
    except (TypeError, ValueError):
        return uses, produced

    plan_path = Path(track_dir) / "plan.md"
    uses_refs = []
    if plan_path.exists():
        try:
            phases = parse_plan(plan_path).get("phases", [])
            pi, ti = cur[0] - 1, cur[1] - 1
            if 0 <= pi < len(phases):
                tasks = phases[pi].get("tasks", [])
                if 0 <= ti < len(tasks):
                    uses_refs = tasks[ti].get("uses_refs", []) or []
        except Exception:  # noqa: BLE001 — advisory join, never fatal
            uses_refs = []

    root = _resolve_project_root(track_dir)
    for ref in uses_refs:
        entry = {"path": ref, "resolved": None, "exists": None}
        if root is not None:
            resolved = (root / ref).resolve()
            entry["resolved"] = str(resolved)
            try:
                entry["exists"] = resolved.exists()
            except OSError:
                entry["exists"] = None
        uses.append(entry)

    handoff_dir = Path(track_dir) / ".conductor" / "handoff"
    try:
        for a in _extract_candidates(handoff_dir).get(
                "artifacts_produced", []) or []:
            m = _HANDOFF_STEM.fullmatch(str(a.get("source", "")))
            if not m:
                continue
            if (int(m.group(1)), int(m.group(2))) >= cur:
                continue  # self, future, or same-phase-higher: not yet owed
            produced.append({"path": a.get("path", ""), "role": a.get("role", ""),
                             "source": a.get("source", "")})
    except Exception:  # noqa: BLE001 — advisory join, never fatal
        pass
    return uses, produced


def compute_task_context(track_dir, phase, task):
    """Join one plan task to its spec ACs/TCs (read-only).

    Composes :func:`plan_parse.parse_plan` (the task's ``ac_refs``/``tc_refs``)
    with :func:`spec_parse.parse_spec` (AC text + TC rows) so task-executor
    fetches one deterministic join instead of hand-extracting across both
    files. Never raises: a missing plan/spec or out-of-range index yields empty
    lists plus a diagnostic (mirroring the parsers' best-effort posture).
    """
    name, ac_refs, tc_refs = _task_refs(track_dir, phase, task)
    uses, produced = _artifact_edges(track_dir, phase, task)
    if name is None:
        return {
            "phase": phase, "task": task, "name": None,
            "tags": [], "tag_profile": None,
            "ac_refs": [], "tc_refs": [], "acs": [], "tcs": [],
            "artifacts": {"uses": uses, "produced": produced},
            "errors": [f"task not found at phase {phase} task {task} "
                       f"(re-check the indices against plan.md)"],
            "warnings": [],
        }

    tags = extract_tags(name)
    tag_profile = None
    if tags:
        leading = tags[0]
        prof = tp._profile(leading)  # noqa: SLF001 — registry-internal profile lookup
        tag_profile = {
            "tag": leading,
            "route": prof.get("route", "executor"),
            "tdd_exempt": bool(prof.get("tdd_exempt")),
            "coverage_exempt": bool(prof.get("coverage_exempt")),
            # The workflow prose/docfile is large + conditional → fetched on
            # demand (the dispatch manifest / registry-doc --tag, tier B), not
            # inlined here. "present"/"absent" is the pointer, mirroring
            # task-executor's injected block; workflow_doc names the docfile
            # when the tag declares one (else None — inline prose or absent).
            "workflow": ("present" if (tp.workflow_for(leading)
                                       or tp.workflow_doc_for(leading))
                         else "absent"),
            "workflow_doc": tp.workflow_doc_for(leading) or None,
            "refactor": bool(tp.refactor_for(leading)),
        }

    errors, warnings = [], []
    acs, tcs = [], []
    spec_path = Path(track_dir) / "spec.md"
    ac_ref_set = set(ac_refs)
    if not spec_path.exists():
        if ac_refs or tc_refs:
            warnings.append("spec.md absent — cannot resolve AC text / TC rows")
    else:
        spec = parse_spec(spec_path)
        ac_text = {it["id"]: it["text"] for it in spec.get("ac_items", [])}
        for ref in ac_refs:
            if ref in ac_text:
                acs.append({"id": ref, "text": ac_text[ref]})
            else:
                warnings.append(
                    f"{ref} referenced by this task is not in spec.md "
                    f"(dangling AC ref)")
        # TCs tracing to any of this task's ACs, ordered by the task's declared
        # tc_refs first (the task's own view), then any extras the spec lists.
        spec_tcs = [tc for tc in spec.get("tcs", [])
                    if tc.get("ac") in ac_ref_set]
        if tc_refs:
            tc_by_id = {tc["id"]: tc for tc in spec_tcs}
            seen, ordered = set(), []
            for ref in tc_refs:
                if ref in tc_by_id and ref not in seen:
                    ordered.append(tc_by_id[ref])
                    seen.add(ref)
            for tc in spec_tcs:  # spec-listed TCs not named in the annotation
                if tc["id"] not in seen:
                    ordered.append(tc)
            tcs = ordered
        else:
            tcs = spec_tcs

    return {
        "phase": phase, "task": task, "name": name,
        "tags": tags, "tag_profile": tag_profile,
        "ac_refs": ac_refs, "tc_refs": tc_refs,
        "acs": acs, "tcs": tcs,
        "artifacts": {"uses": uses, "produced": produced},
        "errors": errors, "warnings": warnings,
    }


def cmd_task_context(track_dir, phase, task):
    """``track-state task-context`` — print the joined task-context JSON."""
    out(compute_task_context(track_dir, phase, task))
