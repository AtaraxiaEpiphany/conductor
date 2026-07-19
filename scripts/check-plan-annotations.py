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
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output
from track_state.plan_parse import _extract_refs

# Valid plan.md checkbox marker chars (constants.MARKER_MAP values), copied from
# check-plan-checkboxes.py.
_VALID_MARKER_CLASS = r"[ x~!>#\-d]"

# A top-level task bullet at column 0 with a real checkbox marker — mirrors
# plan_parse._TASK_LINE's structure (no keyword required). Used with .match, so
# the implicit start-anchor excludes indented (2-space) subtask lines.
_TOP_TASK = re.compile(rf"^-\s+\[{_VALID_MARKER_CLASS}\]")

# Dispatch tag that exempts a task from the annotation requirement (non-
# implementation work). Boundary-anchored like helpers.extract_tags, so the
# orchestrator's tag-based TDD gating and this hook agree on what's exempt.
_EXEMPT_TAG = re.compile(r"(?<!\S)\[(?:Explore|Docs|Config|Chore|Manual|Migrate)\](?!\S)")


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
        if _EXEMPT_TAG.search(raw):
            continue  # tagged → non-implementation → exempt
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
    for _label, text in _text_to_scan(tool_input):
        all_hits.extend(_scan(text))

    if not all_hits:
        write_hook_output(hook_event_name="PreToolUse")
        return

    lines = ["plan.md implementation tasks are missing their "
             "<!-- AC-n, TC-n.n --> annotation — the task's traceability would "
             "be silently lost (plan-format-contract.md §6):"]
    for lineno, raw, fix in all_hits:
        lines.append(f"  line {lineno}:  {raw.strip()}")
        lines.append(f"    → fix: {fix}")
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
        ),
    )


if __name__ == "__main__":
    main()
