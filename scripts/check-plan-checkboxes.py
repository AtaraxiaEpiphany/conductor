#!/usr/bin/env python3
"""PreToolUse hook: enforce ``[ ]`` checkboxes on plan.md task/subtask lines.

spec-planner (sonnet) occasionally emits ``- Subtask: x`` without the ``[ ]``
status marker. plan_parse._TASK_LINE requires the marker, so a bracket-less
line is silently dropped and the subtask vanishes from track-state.json — a
silent data-loss defect.

This hook blocks the Write/Edit (on a path whose basename is ``plan.md``) before
it lands, and tells the model the corrected form so it self-corrects and retries.

Defense in depth: ``plan_parse.parse_plan`` also errors on the same pattern, so a
direct edit the hook cannot see (e.g. an Edit whose ``new_string`` is only the
fixed fragment, or an external editor) is still caught at ``init-from-plan``.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output

# Valid plan.md checkbox marker chars (constants.MARKER_MAP values).
_VALID_MARKER_CLASS = r"[ x~!>#\-d]"

# A task/subtask bullet that does NOT start with a valid checkbox.
#   ^(\s*)-             — optional indent + bullet dash
#   (?!\[<marker>\])    — NOT immediately followed by a valid ``[x]`` checkbox
#   (?:[Tag]\s*)?       — optional dispatch tag like [Explore] / [Manual]
#   (task|subtask)\b    — the task/subtask keyword (the spec-planner convention)
# Matches ``- Subtask: x``, ``  - subtask: x``, ``- [Explore] Task: x`` (tag but
# no checkbox), but NOT ``- [ ] Subtask: x`` / ``- [x] Task: x``. The lookahead
# must include the opening ``[`` — without it ``[x]`` / ``[d]`` slip through and
# the tag branch ``\[[A-Za-z]+\]`` then matches them as a dispatch tag, flagging
# valid completed/deferred task lines (a false positive on any Edit to plan.md).
_MISSING_CHECKBOX = re.compile(
    rf'^(\s*)-\s+(?!\[{_VALID_MARKER_CLASS}\])(?:\[[A-Za-z]+\]\s*)?(task|subtask)\b',
    re.IGNORECASE | re.MULTILINE,
)


def _suggest(raw_line: str) -> str:
    """Insert ``[ ] `` right after the leading ``<indent>- `` of a bullet."""
    m = re.match(r'^(\s*-\s+)(.*)$', raw_line)
    if not m:
        return raw_line
    return f"{m.group(1)}[ ] {m.group(2)}"


def _scan(text: str, max_hits: int = 8):
    """Return up to ``max_hits`` (lineno, raw_line, suggested) tuples."""
    hits = []
    lines = text.splitlines()
    for m in _MISSING_CHECKBOX.finditer(text):
        lineno = text.count("\n", 0, m.start()) + 1
        raw = lines[lineno - 1] if 0 < lineno <= len(lines) else m.group(0)
        hits.append((lineno, raw, _suggest(raw)))
        if len(hits) >= max_hits:
            break
    return hits


def _text_to_scan(tool_input: dict):
    """Yield (label, text) chunks to scan for the given tool input.

    Write → its full content. Edit → its new_string. MultiEdit → each edit's
    new_string. Returns None if there is nothing plan.md-relevant to scan.
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
    # {TRACK_DIR}/plan.md). Other files are passed through untouched.
    if Path(file_path).name != "plan.md":
        write_hook_output(hook_event_name="PreToolUse")
        return

    all_hits = []
    for _label, text in _text_to_scan(tool_input):
        all_hits.extend(_scan(text))

    if not all_hits:
        write_hook_output(hook_event_name="PreToolUse")
        return

    lines = ["plan.md task/subtask lines are missing their [ ] checkbox — "
             "the subtask would be silently dropped from track-state.json:"]
    for lineno, raw, suggested in all_hits:
        lines.append(f"  line {lineno}:  {raw.strip()}")
        lines.append(f"    → fix: {suggested.strip()}")
    detail = "\n".join(lines)

    write_hook_output(
        hook_event_name="PreToolUse",
        additional_context=f"[Conductor] {detail}",
        permission_decision="deny",
        permission_decision_reason=(
            "plan.md task/subtask bullets must start with a [ ] checkbox marker "
            "(e.g. '- [ ] Subtask: ...'). Rewrite each flagged line and retry."
        ),
    )


if __name__ == "__main__":
    main()
