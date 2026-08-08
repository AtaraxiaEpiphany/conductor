"""Additive spec.md amendment splice (Track A3 — replan-as-amendment).

When failure-analyst returns ``replan`` WITH the AC details (``ac_superseded`` +
``ac_prime_text``), the spine stages an in-place amendment instead of halting
(dispatch.py ``_step_route_failure_analysis`` → ``_amendment_decision`` ask →
``cmd_amend_apply``). This helper appends a ``## Amendment N`` section to spec.md
declaring which AC is superseded and what replaces it.

CRITICAL — additive only (the governing invariant: a "verified against AC-N" stamp
must stay sound forever):

  * The original ``- AC-N:`` line is NEVER touched. ``parse_spec``
    (spec_parse.py) collects ACs only while inside the ``## Acceptance Criteria``
    section; a ``## Amendment N`` heading ends that section, so the amendment
    prose is NOT parsed as a duplicate AC. ``cmd_spec_anchors`` /
    ``compute_ac_integrity`` stay sound — they read the original ACs unchanged.
  * The amendment is APPENDED (end of spec.md) — no insertion-point parse, no
    reordering of existing sections. The supersede is recorded, not executed, so
    every downstream stamp that measured against the original AC stays truthful.

Modeled on ``sync.insert_subtask_lines`` (line walk + atomic ``os.replace``).
"""
import os
import re
import sys
import tempfile
from pathlib import Path

# Existing amendment headings (to compute the next number). Case-insensitive.
_AMENDMENT_HEAD = re.compile(r"^##\s+Amendment\s+(\d+)\b", re.IGNORECASE)

# Prime mark (U+2032) — the superseding criterion is AC-N′.
_PRIME = "′"


def next_amendment_number(spec_path):
    """The next amendment number: max existing ``## Amendment N`` + 1, or 1."""
    n = 0
    if spec_path.exists():
        for line in spec_path.read_text().splitlines():
            m = _AMENDMENT_HEAD.match(line.rstrip())
            if m:
                n = max(n, int(m.group(1)))
    return n + 1


def render_amendment(number, ac_superseded, ac_prime_text, root_cause=None,
                     affected_tasks=None):
    """Render the ``## Amendment N`` markdown block.

    Deliberately avoids the ``- AC-N`` bullet shape (uses ``**Label:**`` prose) so
    it cannot be misread as an AC even out of section context — belt AND braces
    with parse_spec's section scoping.
    """
    affects = ", ".join(affected_tasks) if affected_tasks else "(none named)"
    lines = [
        "",
        f"## Amendment {number}",
        "",
        f"- **Supersedes:** {ac_superseded} (the original line is preserved "
        "verbatim above; this amendment narrows or corrects it).",
        f"- **Adds:** {ac_superseded}{_PRIME} — {ac_prime_text}",
    ]
    if root_cause:
        lines.append(f"- **Reason:** {root_cause}")
    lines.append(f"- **Affected tasks:** {affects}")
    lines.extend([
        "",
        (f"> Staged by conductor:failure-analyst (replan verdict). Additive: the "
         "original acceptance criteria are untouched; re-verify affected tasks "
         f"against {ac_superseded}{_PRIME}."),
        "",
    ])
    return "\n".join(lines)


def splice_amendment(track_dir, ac_superseded, ac_prime_text, root_cause=None,
                     affected_tasks=None):
    """Append a ``## Amendment N`` section to spec.md. Returns the number used.

    Atomic (temp + ``os.replace``). Returns ``None`` and emits a WARNING to stderr
    if spec.md is missing or the splice fails — the rest of the amend-apply flow
    still runs (the amendment marker + injection carry the intent; the human can
    splice manually). Mirrors ``insert_subtask_lines``'s fail-soft contract.
    """
    spec_path = Path(track_dir) / "spec.md"
    if not spec_path.exists():
        print("WARNING: spec.md missing — amendment not spliced "
              "(marker + injection carry intent)", file=sys.stderr)
        return None
    number = next_amendment_number(spec_path)
    block = render_amendment(number, ac_superseded, ac_prime_text, root_cause,
                             affected_tasks)
    text = spec_path.read_text()
    if not text.endswith("\n"):
        text += "\n"
    fd, tmp = tempfile.mkstemp(dir=str(spec_path.parent), prefix=".spec-amend-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text + block)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, spec_path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print("WARNING: spec.md amendment splice failed — spec.md unchanged",
              file=sys.stderr)
        return None
    return number
