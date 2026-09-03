#!/usr/bin/env python3
"""PreToolUse hook: enforce ``<!-- AC-n, TC-n.n -->`` annotations on plan.md
implementation task lines.

plan-format-contract.md §6 says each implementation task MUST carry an HTML
comment linking its ACs and test scenarios. ``plan_parse._extract_refs`` never
raises — a missing or malformed comment silently yields empty
``ac_refs``/``tc_refs``, so the task loses all traceability and downstream
``result._tc_consistency_gate`` returns ``N/A`` (the #2/#3 declared→claimed→
grounded chain goes silent on that task). This is the same silent-data-loss
class the sibling ``check-plan-checkboxes.py`` prevents for ``[ ]`` markers.

This hook blocks the Write/Edit (basename ``plan.md``) before it lands when a
top-level, untagged (implementation) task line lacks a well-formed comment with
≥1 ``AC-\\d+`` AND ≥1 ``TC-\\d+\\.\\d+`` (aggregated across ALL comments on the
line, exactly as ``_extract_refs`` does). Dispatch-tagged
(``[Explore]``/``[Docs]``/``[Config]``/``[Chore]``/``[Manual]``) and indented
subtask lines are exempt (subtasks inherit AC context from their parent).

Task detection mirrors ``plan_parse._TASK_LINE`` — structure only (a column-0
``- [<marker>]`` bullet), NOT the literal ``Task:``/``Subtask:`` keyword, because
the parser itself treats every checkbox bullet as a task. Requiring the keyword
here would miss ``- [ ] Implement login`` (no annotation) — the very silent-loss
bug this hook exists to catch.

Unlike the checkbox hook there is NO defense-in-depth: ``_extract_refs`` never
validates comments, and ``parse_plan`` / ``init-from-plan --check`` don't check
them, so this hook is the only enforcement point for §6. An Edit whose
``new_string`` is only a description fragment (no ``- [`` anchor) is not scanned
— a residual false-negative documented as a follow-on (a full-file PostToolUse
re-scan), not a safety net that exists today.

Task-artifact edges (plan-format-contract.md rule 9, findings/artifact edge)
share this hook: ``_scan_artifact_edges`` DENIES a malformed ``<!-- produces:
-->`` / ``<!-- uses: -->`` comment (present, zero path tokens — the same
silent-loss class as a missing AC annotation), and surfaces advisory dangling /
orphan edges as ``[Conductor]`` context on an ALLOW (deliver + surface, never
deny on an unconsumed artifact). The advisory is graph-level (uses with no
produces anywhere, produces with no uses anywhere) and computed on FULL-CONTENT
writes only — an Edit fragment cannot see the whole plan, so a fragmentary
write skips it (documented residual, same class as the AC fragment-blindness
above); ``parse_plan.validate_uses`` covers the full plan at init time.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from track_state.plan_parse import _extract_refs
from track_state.helpers import extract_tags
from track_state.task_profiles import is_tdd_exempt

# Valid plan.md checkbox marker chars (constants.MARKER_MAP values), copied from
# check-plan-checkboxes.py.
_VALID_MARKER_CLASS = r"[ x~!>#\-d]"

# A top-level task bullet at column 0 with a real checkbox marker — mirrors
# plan_parse._TASK_LINE's structure (no keyword required). Used with .match, so
# the implicit start-anchor excludes indented (2-space) subtask lines.
_TOP_TASK = re.compile(rf"^-\s+\[{_VALID_MARKER_CLASS}\]")

# A top-level task is exempt from the annotation requirement when it is
# TDD-exempt (non-implementation work: [Explore]/[Docs]/[Config]/[Chore]/
# [Manual]). Detected via the SAME predicates the orchestrator's TDD gating uses
# (helpers.extract_tags + task_profiles.is_tdd_exempt) so this hook and the gate
# agree on what's exempt — and [Refactor] (tdd_exempt=False, real implementation
# work that owes a working test) is correctly NOT exempt, so a refactor task
# still must carry its AC/TC annotation. NB: an earlier revision matched the
# whole registry vocab (TAG_VOCAB), which exempted [Refactor] and silently
# dropped its traceability. extract_tags strips HTML comments, so a tag named
# only inside a <!-- --> note does NOT exempt the line (a real tag would lead
# the task name, not sit in a comment).


def _comment_refs(line: str):
    """``(has_ac, has_tc)`` aggregated across ALL ``<!-- -->`` comments on the
    line — a bool view of ``plan_parse._extract_refs`` (the parser's own scan),
    so the hook and parser agree by construction instead of by copied regex.
    """
    ac_refs, tc_refs = _extract_refs(line)
    return bool(ac_refs), bool(tc_refs)


def _fix_for(has_ac: bool, has_tc: bool) -> str:
    """Actionable remediation for an untagged task missing AC and/or TC refs."""
    contract = "(plan-format-contract.md §6)"
    if not has_ac and not has_tc:
        return f"add a <!-- AC-n, TC-n.n --> annotation {contract}"
    if not has_ac:
        return f"add an AC-n ref to the <!-- --> annotation {contract}"
    return f"add a TC-n.n ref to the <!-- --> annotation {contract}"


def _scan(text: str, max_hits: int = 8):
    """Return up to ``max_hits`` ``(lineno, raw, fix)`` tuples for top-level
    implementation task lines whose annotation is missing or incomplete."""
    hits = []
    for idx, raw in enumerate(text.splitlines()):
        if not _TOP_TASK.match(raw):
            continue  # not a top-level task line (subtask/heading/prose/blank)
        if is_tdd_exempt(extract_tags(raw)):
            continue  # TDD-exempt → non-implementation → exempt from §6
        has_ac, has_tc = _comment_refs(raw)
        if has_ac and has_tc:
            continue  # well-formed annotation present
        hits.append((idx + 1, raw, _fix_for(has_ac, has_tc)))
        if len(hits) >= max_hits:
            break
    return hits


def _text_to_scan(tool_input: dict):
    """Yield ``(label, text)`` chunks to scan — mirrors check-plan-checkboxes.

    Write → its full content. Edit → its new_string. MultiEdit → each edit's
    new_string. Nothing plan.md-relevant yields nothing.
    """
    if "content" in tool_input and isinstance(tool_input["content"], str):
        yield ("content", tool_input["content"])
    if "new_string" in tool_input and isinstance(tool_input["new_string"], str):
        yield ("new_string", tool_input["new_string"])
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for i, e in enumerate(edits):
            if isinstance(e, dict) and isinstance(e.get("new_string"), str):
                yield (f"edits[{i}].new_string", e["new_string"])


def _scan_artifact_edges(text: str, max_hits: int = 8):
    """Scan full plan content for malformed task-artifact edge comments.

    Returns ``(deny_hits, advisory_lines)``:
    - deny_hits: ``(lineno, raw)`` for a ``<!-- produces: -->`` or
      ``<!-- uses: -->`` comment whose payload yields zero path tokens — the
      malformed deny class (mirrors the AC-annotation silent-loss deny).
    - advisory_lines: dangling/orphan notes (uses with no produces anywhere;
      produces with no uses anywhere), formatted for additional_context. Empty
      when ``_parse_plan_text``-level extraction finds a clean edge graph.

    Computed via the parser's own extractor (``_extract_artifact_refs``) so the
    hook and parser agree by construction.
    """
    from track_state.plan_parse import _extract_artifact_refs
    deny_hits = []
    produced = set()   # (path) declared by any produces comment
    used = set()       # (path) declared by any uses comment
    per_line = []      # (lineno, produces_paths, uses_paths)
    for idx, raw in enumerate(text.splitlines()):
        p_refs, p_has = _extract_artifact_refs(raw, "produces")
        u_refs, u_has = _extract_artifact_refs(raw, "uses")
        if p_has and not p_refs:
            deny_hits.append((idx + 1, raw))
            if len(deny_hits) >= max_hits:
                break
        if u_has and not u_refs:
            deny_hits.append((idx + 1, raw))
            if len(deny_hits) >= max_hits:
                break
        if p_refs or u_refs:
            per_line.append((idx + 1, p_refs, u_refs))
            produced.update(p_refs)
            used.update(u_refs)
    advisory_lines = []
    for lineno, p_refs, u_refs in per_line:
        for p in p_refs:
            if p not in used:
                advisory_lines.append(
                    f"  line {lineno}: produces {p} but no task declares "
                    f"`uses: {p}` — dead-edge candidate; give it a consumer "
                    f"before the final phase")
        for u in u_refs:
            if u not in produced:
                advisory_lines.append(
                    f"  line {lineno}: uses {u} but no task declares "
                    f"`produces: {u}` — check the path or declare the producer")
    return deny_hits, advisory_lines[:max_hits]


def main():
    input_data = read_hook_input()
    tool_input = input_data.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "") or ""

    # Only inspect writes to a file named plan.md (spec-planner writes
    # {TRACK_DIR}/plan.md). Other files pass through untouched.
    if Path(file_path).name != "plan.md":
        write_hook_output(hook_event_name="PreToolUse")
        return

    all_hits = []
    has_full_content = False
    for label, text in _text_to_scan(tool_input):
        if label == "content":
            has_full_content = True
        all_hits.extend(_scan(text))

    # Task-artifact edge scan (rule 9): malformed comments deny on any chunk;
    # the dangling/orphan advisory needs the whole plan, so full content only.
    edge_denies = []
    edge_advisories = []
    if has_full_content:
        edge_denies, edge_advisories = _scan_artifact_edges(
            tool_input["content"])

    if not all_hits and not edge_denies:
        if edge_advisories:
            detail = "\n".join(
                ["plan.md task-artifact edges (produces/uses) — advisory, "
                 "the write lands; fix before the final phase:"] + edge_advisories)
            write_hook_output(
                hook_event_name="PreToolUse",
                additional_context=f"[Conductor] {detail}",
                permission_decision="allow",
                permission_decision_reason=(
                    "allowed; task-artifact edge advisory attached "
                    "(plan-format-contract.md rule 9)"))
        else:
            write_hook_output(hook_event_name="PreToolUse")
        return

    lines = ["plan.md implementation tasks are missing their "
             "<!-- AC-n, TC-n.n --> annotation — the task's traceability would "
             "be silently lost (plan-format-contract.md §6):"]
    for lineno, raw, fix in all_hits:
        lines.append(f"  line {lineno}:  {raw.strip()}")
        lines.append(f"    → fix: {fix}")
    if edge_denies:
        lines.append("plan.md carries a malformed task-artifact edge comment "
                     "— the edge would be silently lost "
                     "(plan-format-contract.md rule 9):")
        for lineno, raw in edge_denies:
            lines.append(f"  line {lineno}:  {raw.strip()}")
            lines.append("    → fix: add at least one repo-relative path, "
                         "comma-separated: <!-- produces: reports/a.md -->")
    detail = "\n".join(lines)

    write_hook_output(
        hook_event_name="PreToolUse",
        additional_context=f"[Conductor] {detail}",
        permission_decision="deny",
        permission_decision_reason=(
            "plan.md implementation tasks must carry an HTML comment "
            "<!-- AC-n, TC-n.n --> linking their ACs and test scenarios "
            "(plan-format-contract.md §6) — otherwise the task's traceability "
            "is silently lost. Add the comment and retry."
            + (" Malformed <!-- produces:/uses: --> comments (no path tokens) "
               "are denied too (rule 9) — add a repo-relative path."
               if edge_denies else "")
        ),
    )


if __name__ == "__main__":
    main()
