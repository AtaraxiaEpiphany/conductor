"""Shared parser for the Conductor doc-map markdown tables.

Two maps classify docs with a Status vocabulary (``seeded`` / ``auto`` /
``on-demand``): ``templates/claude-md-toc.md`` (creation map) and
``templates/project-index.md`` (read-strategy map → ``conductor/index.md``).
Both encode the classification as markdown table rows, and both
``check-index-maps.py`` (template drift gate) and ``lint-conductor-index.py``
(live-tree seeded-file check) parse those rows with the same regexes. This
module owns that parsing so the two scripts can't silently diverge on the
table format.
"""
import re

# Recognized status vocabulary (order matches the maps' §Status legend).
STATUS_VOCAB = ("seeded", "auto", "on-demand")
VALID_STATUS = set(STATUS_VOCAB)

# Match a status tag at the start of a Creation Rule / Status cell. The maps use
# bold ``**seeded**`` (toc) and plain ``seeded`` (index); tolerate both.
STATUS_RE = re.compile(r"\*{0,2}(seeded|auto|on-demand)\*{0,2}")

# Extract a ``conductor/...`` path from a table cell (stops at whitespace or ``|``).
# Tolerates ``./`` prefixes (claude-md-toc) and ``<placeholder>``/``*glob`` segments.
PATH_RE = re.compile(r"\.?/?((?:conductor/)[^\s|`]+)")


def table_rows(text):
    """Yield each markdown table data row (list of cell strings).

    Skips non-table lines, the header row, and ``| :-- | :-: |`` separator rows.
    """
    for line in text.splitlines():
        s = line.strip()
        # Must be a table row (starts with ``|``). ``set(...) <= {'-'}`` already
        # excludes pure-dash separator rows; the post-split ``:`` check below
        # catches the ``|:-|`` colon-bearing variants in one combined filter.
        if not s.startswith("|") or set(s.replace("|", "").strip()) <= {"-"}:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(set(c) <= {"-", ":"} for c in cells):
            continue
        yield cells


def seeded_paths(text):
    """Return the set of ``conductor/...`` paths tagged ``seeded`` in a map.

    A row contributes its path iff its Status cell carries the ``seeded`` tag;
    rows tagged ``auto`` / ``on-demand`` (filled later by a skill or the user)
    are intentionally excluded — they are slots, not promises.
    """
    out = set()
    for cells in table_rows(text):
        joined = " | ".join(cells)
        m = STATUS_RE.search(joined)
        if not m or m.group(1) != "seeded":
            continue
        for pm in PATH_RE.finditer(joined):
            out.add(pm.group(1).rstrip("./").rstrip("/"))
    return out
