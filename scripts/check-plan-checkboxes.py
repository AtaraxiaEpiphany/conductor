#!/usr/bin/env python3
"""PreToolUse hook: enforce well-formed ``[ ]`` checkboxes on plan.md task/subtask lines.

Two classes of defect are blocked, both of which plan_parse would otherwise
silently drop (the line vanishes from track-state.json — a data-loss defect):

  * MISSING checkbox — spec-planner emits ``- Subtask: x`` with no bracket, or
    ``- [Explore] Task: x`` (a dispatch tag is NOT a checkbox).
  * MALFORMED checkbox — the bracket is present but wrong: ``- [] x`` (empty —
    the modal LLM typo for ``- [ ]``), ``- [  ] x`` (whitespace), ``- [xy] x``
    (wrong width). These sit in the gap between plan_parse._TASK_LINE (one
    valid char) and _BAD_MARKER_LINE (exactly one char): zero/2+ chars match
    neither, so the line is silently dropped.

This hook blocks the Write/Edit (basename ``plan.md``) before it lands and tells
the model the corrected form so it self-corrects and retries.

Defense in depth: ``plan_parse.parse_plan`` errors on the same patterns, so a
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

# A complete, well-formed checkbox: exactly one valid marker char inside [].
# Used to exclude valid ``[ ]``/``[x]`` lines from the malformed-bracket check.
_VALID_CHECKBOX = re.compile(rf"^\[{_VALID_MARKER_CLASS}\]$")

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

# A non-checkbox dash bullet carrying an HTML comment (``<!-- AC/TC/deps -->``)
# but NO Task:/Subtask: keyword — an author who dropped the keyword yet kept the
# annotation. Prose bullets don't carry AC/TC/deps comments, so a comment is a
# strong "this was meant to be a task" signal; without this catch the line is
# silently dropped by plan_parse (data loss). Keyword-independent safety net so
# that softening the Task:/Subtask: *convention* does not widen the silent-drop
# hole. Mirrors plan_parse._MISSING_CHECKBOX_ANNOTATED.
_MISSING_CHECKBOX_ANNOTATED = re.compile(
    rf'^(\s*)-\s+(?!\[{_VALID_MARKER_CLASS}\])(?:\[[A-Za-z]+\]\s*)?.*<!--',
    re.MULTILINE,
)

# A dash-bullet whose first token is a bracket group [...] of ANY width — used
# to catch the bracket-MALFORMED case: a writer who intended a task but botched
# the checkbox. group(1)=indent, group(2)=the bracket token incl. brackets.
# Mirrors plan_parse._BRACKET_TOKEN so the hook and parser agree on detection.
_BRACKET_TOKEN = re.compile(r"^(\s*)-\s+(\[[^\]]*\])")

# Bracket tokens that are NOT checkboxes but are legitimate first tokens of a
# dash-bullet (dispatch tags + trailing status markers). These route through the
# _MISSING_CHECKBOX path (tag-but-no-checkbox) below for a more accurate message
# rather than being flagged as malformed. Mirrors plan_parse._KNOWN_BRACKET_TOKEN.
_KNOWN_BRACKET_TOKEN = re.compile(
    r"^\[(?:Manual|Explore|Docs|Config|Chore|Migrate|N/A|verified|[0-9a-fA-F]{7,})\]$",
    re.IGNORECASE,
)


def _suggest(raw_line: str) -> str:
    """Insert ``[ ] `` right after the leading ``<indent>- `` of a bullet."""
    m = re.match(r'^(\s*-\s+)(.*)$', raw_line)
    if not m:
        return raw_line
    return f"{m.group(1)}[ ] {m.group(2)}"


def _suggest_malformed(raw_line: str) -> str:
    """Replace the first bracket token ``[...]`` with a pending ``[ ]``.

    For a malformed checkbox (empty ``[]``, whitespace ``[  ]``, wrong-width
    ``[xy]``) the bracket is present but wrong, so the fix is to rewrite its
    content — NOT to insert a second ``[ ]`` (which ``_suggest`` would do).
    """
    return re.sub(r"\[[^\]]*\]", "[ ]", raw_line, count=1)


def _scan(text: str, max_hits: int = 8):
    """Return up to ``max_hits`` (lineno, raw_line, suggested) tuples.

    Catches both classes of silent-drop defect on a task/subtask bullet:
      * missing checkbox  — ``- Subtask: x`` / ``- [Explore] Task: x``
      * malformed bracket — ``- [] x`` / ``- [  ] x`` / ``- [xy] x`` (a bracket
        IS present but is not a valid single-marker checkbox)
    A line is flagged at most once; malformed takes priority (the bracket is
    the closer-to-correct form, so its fix is more specific). Valid checkboxes
    (``[ ]``/``[x]``/…) and known non-checkbox tags (``[Manual]``, ``[N/A]``…)
    are never flagged.
    """
    hits = []
    for idx, raw in enumerate(text.splitlines()):
        if len(hits) >= max_hits:
            break
        mb = _BRACKET_TOKEN.match(raw)
        if mb:
            bracket = mb.group(2)
            if not _VALID_CHECKBOX.match(bracket) and not _KNOWN_BRACKET_TOKEN.match(bracket):
                hits.append((idx + 1, raw, _suggest_malformed(raw)))
                continue
        if _MISSING_CHECKBOX.search(raw):
            hits.append((idx + 1, raw, _suggest(raw)))
            continue
        if _MISSING_CHECKBOX_ANNOTATED.search(raw):
            hits.append((idx + 1, raw, _suggest(raw)))
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

    lines = ["plan.md task/subtask lines have a missing or malformed [ ] checkbox — "
             "the line would be silently dropped from track-state.json:"]
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
            "(e.g. '- [ ] ...'). Rewrite each flagged line and retry."
        ),
    )


if __name__ == "__main__":
    main()
