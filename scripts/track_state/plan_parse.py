"""Parse and validate plan.md for init-from-plan.

plan.md grammar (see agents/spec-planner.md §4.2):

    # Implementation Plan: <title>                 (ignored)

    ## Phase <N>: <Phase Name>                      (N starts at 1, contiguous)
    - [ ] Task: <desc> <!-- AC-1, TC-1.1 -->         (top-level task)
      - [ ] Subtask: <desc>                          (indented = subtask)
    - [ ] [Manual] Task: ...                         (each phase ends with one)

Checkbox marker chars are the values of constants.MARKER_MAP:
    ' ' pending   x completed   ~ in_progress   ! failed
    > skipped     # blocked      - cancelled     d deferred

This module produces the PLAN_STRUCTURE shape that quality._init_core expects,
plus structural diagnostics. Name cleaning mirrors validate._parse_plan_structure
so a structure derived here round-trips cleanly with sync-plan.
"""
import re
from pathlib import Path

from .constants import MARKER_MAP
from .helpers import _clean_trailing_markers

# "## Phase 1", "## Phase 1: Build", "## Phase 1 - Build", "## Phase 1 — Build".
# group(1)=number, group(2)=name (may be empty / None).
_PHASE_HEADING = re.compile(r"^##\s+Phase\s+(\d+)\b\s*[:：\-—]?\s*(.*)$")

# Well-formed checkbox line. group(1)=indent, group(2)=marker char, group(3)=rest.
# Trailing content optional so an empty `- [ ]` is caught as an empty name.
_TASK_LINE = re.compile(r"^(\s*)-\s+\[([ x~!>#\-d])\]\s*(.*)$")

# A bracket with exactly one char that is NOT a valid marker, e.g. "- [X] ...".
# Multi-char brackets ([Manual], [checkpoint:abc1234], [N/A]) never match because
# they lack a ']' immediately after the first char — so they are left untouched.
_BAD_MARKER_LINE = re.compile(r"^(\s*)-\s+\[(.)\]\s*.*$")

# A task/subtask bullet MISSING its checkbox entirely, e.g. "- Subtask: x" or
# "- [Explore] Task: x" (tag present but no [ ] status marker). Without this, the
# fall-through branch silently drops the line and the subtask vanishes from state.
# Mirrors scripts/check-plan-checkboxes.py (the Write/Edit hook) — defense in
# depth for direct edits the hook cannot see.
_MISSING_CHECKBOX_LINE = re.compile(
    r"^(\s*)-\s+(?!\[[ x~!>#\-d]\])(?:\[[A-Za-z]+\]\s*)?(task|subtask)\b",
    re.IGNORECASE,
)

# Trailing checkpoint marker on a phase heading: [checkpoint:abcdef1]
_CHECKPOINT = re.compile(r"\[checkpoint:\s*[0-9a-f]+\]", re.IGNORECASE)

# AC/TC traceability refs live inside ``<!-- AC-1, TC-1.1 -->`` comments on the
# parent task line (plan-format-contract.md rule 6). Captured BEFORE _clean_name
# strips the comment, so ac_integrity can trace ACs → tasks without changing the
# stored task name or the PLAN_STRUCTURE shape.
_HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)
_AC_REF = re.compile(r"AC-\d+")
_TC_REF = re.compile(r"TC-\d+\.\d+")

_VALID_MARKERS = set(MARKER_MAP.values())


def _clean_name(rest):
    """Strip HTML comments then trailing SHA/[N/A]/[verified] markers.

    Dispatch tags ([Manual], [Explore], ...) are preserved — the dispatch
    router reads them from the stored task name.
    """
    # re.DOTALL: strip multi-line <!-- ... --> comments whole so tag-like or
    # marker text inside them can't leak into the cleaned name.
    rest = re.sub(r"<!--.*?-->", "", rest, flags=re.DOTALL).strip()
    rest = _clean_trailing_markers(rest)
    return rest.strip()


def _extract_refs(rest):
    """Pull AC-n / TC-n.m IDs from ``<!-- ... -->`` comments on a task line.

    Returns ``(ac_refs, tc_refs)`` as de-duped lists in first-seen order. Only
    refs inside HTML comments count — a stray ``AC-1`` in prose is ignored, so
    the annotation contract (rule 6) is what's measured.
    """
    ac_refs, tc_refs = [], []
    for m in _HTML_COMMENT.finditer(rest):
        body = m.group(1)
        for ref in _AC_REF.findall(body):
            if ref not in ac_refs:
                ac_refs.append(ref)
        for ref in _TC_REF.findall(body):
            if ref not in tc_refs:
                tc_refs.append(ref)
    return ac_refs, tc_refs


def parse_plan(plan_path):
    """Parse plan.md → {"phases": [...], "errors": [...], "warnings": [...]}.

    phases is an ordered list of:
        {"name": str, "number": int, "line": int,
         "tasks": [{"name": str, "subtasks": [str, ...], "line": int,
                    "ac_refs": [str, ...], "tc_refs": [str, ...]}, ...]}

    Names are cleaned; dispatch tags are preserved. ``ac_refs``/``tc_refs`` carry
    the AC/TC IDs from each parent task's ``<!-- ... -->`` annotation (captured
    before the comment is stripped); subtasks have neither (they inherit).
    ``to_plan_structure`` drops both, so they never reach track-state.json.
    errors block initialization; warnings are advisory.
    """
    errors = []
    warnings = []
    phases = []
    current_phase = None
    current_task = None
    seen_phase_numbers = set()
    expected_next = 1

    text = Path(plan_path).read_text()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()

        pm = _PHASE_HEADING.match(line)
        if pm:
            num = int(pm.group(1))
            name = _CHECKPOINT.sub("", pm.group(2) or "").strip()
            if num in seen_phase_numbers:
                errors.append(f"line {lineno}: duplicate phase number {num}")
            if num != expected_next:
                errors.append(
                    f"line {lineno}: phase {num} out of order "
                    f"(expected Phase {expected_next})")
            seen_phase_numbers.add(num)
            expected_next = num + 1
            current_phase = {"name": name, "number": num, "line": lineno, "tasks": []}
            phases.append(current_phase)
            current_task = None
            if not name:
                warnings.append(f"line {lineno}: Phase {num} heading has no name")
            continue

        tm = _TASK_LINE.match(line)
        if tm:
            indent, rest = tm.group(1), tm.group(3)
            is_subtask = len(indent) > 0
            if current_phase is None:
                errors.append(
                    f"line {lineno}: task/subtask appears before any Phase heading")
                continue
            ac_refs, tc_refs = _extract_refs(rest)
            name = _clean_name(rest)
            if not name:
                errors.append(f"line {lineno}: empty task/subtask name")
                continue
            if is_subtask:
                if current_task is None:
                    errors.append(
                        f"line {lineno}: subtask '{name}' has no parent task "
                        f"(Phase {current_phase['number']})")
                    continue
                # Subtasks inherit AC context from their parent (rule 6) and are
                # stored as plain strings, so no ac_refs/tc_refs here.
                current_task["subtasks"].append(name)
            else:
                current_task = {"name": name, "subtasks": [], "line": lineno,
                                "ac_refs": ac_refs, "tc_refs": tc_refs}
                current_phase["tasks"].append(current_task)
            continue

        # Not a valid task line — but is it a malformed checkbox we should flag?
        bad = _BAD_MARKER_LINE.match(line)
        if bad:
            indent, marker = bad.group(1), bad.group(2)
            where = (f"Phase {current_phase['number']}" if current_phase
                     else "before any phase")
            kind = "subtask" if indent else "task"
            errors.append(
                f"line {lineno}: invalid {kind} marker '[{marker}]' ({where}) "
                f"— valid: [ ] [x] [~] [!] [>] [#] [-] [d]")
            continue
        missing = _MISSING_CHECKBOX_LINE.match(line)
        if missing:
            indent, kw = missing.group(1), missing.group(2).lower()
            where = (f"Phase {current_phase['number']}" if current_phase
                     else "before any phase")
            kind = "subtask" if indent else "task"
            errors.append(
                f"line {lineno}: {kind} '{kw}' line is missing its '[ ]' checkbox "
                f"({where}) — write '- [ ] {kw.capitalize()}: ...' so it is not "
                f"silently dropped from track-state.json")
            continue
        # Everything else (title, prose, blank lines, non-checkbox bullets) ignored.

    if not phases:
        errors.append("plan.md has no '## Phase N' headings")

    # Per-phase structural checks
    for ph in phases:
        label = f"Phase {ph['number']}" + (f" '{ph['name']}'" if ph["name"] else "")
        if not ph["tasks"]:
            errors.append(f"{label}: has no tasks")
            continue
        if "[Manual]" not in ph["tasks"][-1]["name"]:
            warnings.append(
                f"{label}: last task is not a [Manual] verification task "
                f"(expected at the end of every phase)")
        for t in ph["tasks"]:
            if len(t["subtasks"]) == 1:
                warnings.append(
                    f"{label} task '{t['name']}': has 1 subtask "
                    f"(convention is 0 or ≥2)")

    return {"phases": phases, "errors": errors, "warnings": warnings}


def to_plan_structure(parsed):
    """Convert parse_plan() output → PLAN_STRUCTURE dict for _init_core.

    Drops phase 'number'/'line' and task 'line' metadata. Tasks with subtasks
    carry a 'subtasks' list; tasks without omit the key — matching the contract
    that quality._init_core consumes.
    """
    out_phases = []
    for ph in parsed["phases"]:
        tasks = []
        for t in ph["tasks"]:
            entry = {"name": t["name"]}
            if t["subtasks"]:
                entry["subtasks"] = t["subtasks"]
            tasks.append(entry)
        out_phases.append({"name": ph["name"], "tasks": tasks})
    return {"phases": out_phases}


def collect_ac_refs(parsed):
    """Aggregate every AC-n referenced by a task's ``<!-- AC-n -->`` annotation.

    Returns a de-duped list (first-seen order) across all phases/tasks. Used by
    ac_integrity to compute traceability (spec ACs ∩ plan ACs) and to flag
    dangling refs (plan ACs not present in spec.md).
    """
    refs, seen = [], set()
    for ph in parsed.get("phases", []):
        for t in ph.get("tasks", []):
            for ref in t.get("ac_refs", []):
                if ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
    return refs
