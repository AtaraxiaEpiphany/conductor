"""Shared library for JSON processing

Provides unified JSON loading, saving and manipulation utilities.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Smart double-quotes → ASCII, for tolerant parsing of agent-emitted JSON.
# Only the *double*-quote forms are mapped: JSON requires double-quoted
# strings, so a value wrapped in “ ” becomes parseable, while single quotes
# (which JSON never accepts as delimiters) are left untouched — they only
# legitimately appear *inside* a string value, where they need no repair.
_SMART_QUOTE_MAP = {
    "“": '"', "”": '"',   # “ ”
    "«": '"', "»": '"',   # « »
}


def parse_tolerant_json(text: Any) -> Any:
    """Parse agent-emitted JSON tolerantly, or return ``None`` on total failure.

    Subagents return ``---RESULT---`` JSON blocks, and weak models routinely
    degrade them in ways a strict ``json.loads`` rejects:

    - wrapped in ``` ```json / ``` ``` fences;
    - surrounded by leading/trailing prose ("Here is the result: {...} Done.");
    - trailing commas before ``}`` / ``]``;
    - smart quotes (" " ' ' « ») instead of ASCII;
    - en/em dashes or other punctuation in string values.

    Each is repaired in turn, then ``json.loads`` is attempted. Returns the
    parsed object on success, ``None`` if every attempt fails (callers branch
    on ``None`` — typically re-dispatch the agent or surface a clear error
    rather than persisting garbage). Mirrors ``load_json_safe``'s default-on-
    failure contract but *repairs* first where that helper does not.

    ``None`` input, non-string input, or an empty string → ``None``.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    cleaned = text

    # 1. Strip ```json / ``` code fences.
    cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)

    # 2. Normalize smart quotes → ASCII (so quote-delimited strings parse).
    for smart, ascii_ch in _SMART_QUOTE_MAP.items():
        cleaned = cleaned.replace(smart, ascii_ch)

    # 3. If the (now de-fenced) text is not pure JSON, extract the first
    #    balanced {...} or [...] block — defends against surrounding prose.
    parsed = _try_loads(cleaned)
    if parsed is not None:
        return parsed
    extracted = _extract_balanced(cleaned)
    if extracted is not None:
        parsed = _try_loads(extracted)
        if parsed is not None:
            return parsed
    return None


def _try_loads(s: str) -> Any:
    """``json.loads`` after stripping trailing commas; ``None`` on failure."""
    try:
        # Trailing commas before } or ] are invalid JSON but common from models.
        no_trailing = re.sub(r",\s*([}\]])", r"\1", s)
        return json.loads(no_trailing)
    except (ValueError, RecursionError):
        return None


def _extract_balanced(s: str) -> Optional[str]:
    """Return the first balanced ``{...}`` or ``[...]`` substring, or ``None``.

    Walks the string tracking depth and string-literal state so braces inside
    strings/escapes don't fool the matcher. Starts at the first ``{`` or ``[``.
    """
    open_ch = None
    start = -1
    for i, ch in enumerate(s):
        if ch in "{[":
            open_ch = ch
            start = i
            break
    if start < 0:
        return None
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for j in range(start, len(s)):
        ch = s[j]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return s[start:j + 1]
    return None  # unbalanced


def extract_result_block(text: str, marker: str) -> Optional[str]:
    """Pull the content between ``---<marker>---`` and ``---END <marker>---``.

    The recurring agent result-block shape (``---ANALYSIS RESULT---`` ...
    ``---END ANALYSIS RESULT---``, ``---REVIEW RESULT---`` ... etc.). Returns
    the inner text (untrimmed of internal newlines) or ``None`` if either
    delimiter is absent. Whitespace inside ``END <marker>`` is tolerated so a
    model that writes ``---END  REVIEW RESULT---`` still matches.
    """
    if not text or not marker:
        return None
    start_re = re.compile(r"---\s*" + re.escape(marker) + r"\s*---", re.IGNORECASE)
    end_re = re.compile(r"---\s*END\s+" + re.escape(marker) + r"\s*---", re.IGNORECASE)
    start_m = start_re.search(text)
    if not start_m:
        return None
    end_m = end_re.search(text, start_m.end())
    if not end_m:
        return None
    return text[start_m.end():end_m.start()]


def load_json(file_path: Path) -> Union[Dict, List]:
    """Load JSON file

    Args:
        file_path: JSON file path

    Returns:
        Parsed JSON data

    Raises:
        FileNotFoundError: File not found
        json.JSONDecodeError: JSON format error
    """
    if not file_path.exists():
        raise FileNotFoundError(f"JSON file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(
    file_path: Path,
    data: Union[Dict, List],
    indent: Optional[int] = None,
    ensure_ascii: bool = False
) -> None:
    """Save JSON file

    Args:
        file_path: Output file path
        data: Data to save
        indent: Indentation spaces (None for compact format)
        ensure_ascii: Whether to ensure ASCII encoding
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
        f.write("\n")


def load_json_safe(file_path: Path, default: Any = None) -> Any:
    """Safely load JSON file, return default on error

    Args:
        file_path: JSON file path
        default: Default value on error

    Returns:
        Parsed JSON data or default
    """
    try:
        return load_json(file_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json_pretty(file_path: Path, data: Union[Dict, List]) -> None:
    """Save formatted JSON file (with indentation)

    Args:
        file_path: Output file path
        data: Data to save
    """
    save_json(file_path, data, indent=2)