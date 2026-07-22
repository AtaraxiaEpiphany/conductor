#!/usr/bin/env python3
"""check-index-maps — drift gate for the two Conductor doc maps.

Two maps govern doc placement/reading:
- ``templates/claude-md-toc.md``  → creation map (pasted into project CLAUDE.md)
- ``templates/project-index.md``  → read-strategy map (→ ``conductor/index.md``)

They group docs differently but MUST agree on the first-class spine, and both
must classify every row with the same **Status** vocabulary — ``seeded``
(setup creates it), ``auto`` (a skill creates it on first use), or ``on-demand``
(user/agent creates it when needed). This script promotes those invariants into
code so the maps cannot silently drift (harness-engineering §4.4 promote-into-code).

Checks (any failure → exit 1 with a remediation message; mirrors
``scaffold-strategy.py``'s exit discipline):

1. **Vocabulary** — every row's status tag is one of ``{seeded, auto, on-demand}``.
2. **Spine agreement** — every path listed in one map appears in the other
   (derived data-driven from the maps themselves, replacing the hand-maintained
   ``SPINE`` list that previously lived in ``test_toc_completeness.py``).
3. **Seeded-really-created** — every path tagged ``seeded`` is actually written by
   ``conductor:setup`` (cross-checked against ``SEED_PATHS`` below, sourced from
   ``skills/setup/SKILL.md``'s writes). This is the precise guard for the bug
   where ``index.md`` advertised a doc setup never created: a ``seeded`` row whose
   path isn't in ``SEED_PATHS`` is a lie that will surface as a missing file in
   every fresh project.

Exit 0 + OK line on success; exit 1 + remediation message on any failure (§4.3).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from env import get_plugin_root
from index_map import STATUS_RE as _STATUS_RE
from index_map import PATH_RE as _PATH_RE
from index_map import table_rows as _table_rows
from index_map import VALID_STATUS
from category_dirs import category_for


# Paths ``conductor:setup`` actually writes (§2.1–§2.5 of skills/setup/SKILL.md).
# A row tagged ``seeded`` in either map MUST resolve to one of these (a path is
# considered covered if it lives under a seeded directory, e.g. a specific
# styleguide file under conductor/workflow/code-styleguides/). Keep this in sync
# with setup's writes — the spine-agreement check surfaces drift, this list
# surfaces a seeded-tag lie.
SEED_PATHS = [
    "conductor/product/product.md",
    "conductor/product/product-guidelines.md",
    "conductor/design/tech-stack.md",
    "conductor/overview.md",
    "conductor/purpose.md",
    "conductor/log.md",
    "conductor/workflow/index.md",
    "conductor/workflow/code-styleguides",  # dir; setup copies general + detected langs
    "conductor/workflow/testing/strategy.md",
    "conductor/tracks.md",
    "conductor/index.md",
    # claude-md-toc.md surfaces plugin-provided authoring rules as seeded, but
    # doc-conventions.md is NOT a project path (it lives in the plugin) — exempt.
]

# claude-md-toc.md uses ``*(plugin-provided)*`` as its path for non-project docs.
# Those rows carry a status but no project path; exclude them from path checks
# (detected inline via a substring test in _parse_map, not this constant).
# The shared table parser + status/path regexes live in ``lib/index_map.py``.

# Strip ``<placeholder>`` and ``*glob`` segments so a creation-pattern path
# (``conductor/design/api-specs/<endpoint>.md``) normalizes to the same routing
# prefix as its index entry (``conductor/design/api-specs/index.md``).
_PLACEHOLDER_RE = re.compile(r"<[^>]+>|\*[^/]*")


def _normalize_prefix(path):
    """Reduce a path to its leading ``conductor/<area>/<sub>`` directory prefix.

    The two maps intentionally express the same category at different granularities
    (the index lists a routing entry point like ``api-specs/index.md``; the toc
    lists the per-file creation pattern ``api-specs/<endpoint>.md``). Comparing at
    a fixed 2-segments-under-``conductor/`` depth makes ``index ⊆ toc`` the right
    invariant instead of brittle string equality. Placeholders and globs are
    stripped first so they don't form their own prefix, and a trailing file-like
    segment under a placeholder dir is dropped (``queries/<slug>.md`` → ``queries``).
    """
    clean = _PLACEHOLDER_RE.sub("", path).rstrip("./").rstrip("/")
    parts = [p for p in clean.split("/") if p and p != "."]
    # If the final segment looks like a filename (has a dot) sitting under a
    # placeholder-bearing dir, drop it — we want the directory, not the file.
    if len(parts) > 2 and "." in parts[-1]:
        parts = parts[:-1]
    return "/".join(parts[:3])


def _parse_map(text):
    """Return ``(statused, untagged)``.

    ``statused``: ``{path: status}`` for rows carrying a recognized status tag.
    ``untagged``: paths from rows that carry a ``conductor/`` path but NO
    recognized ``seeded``/``auto``/``on-demand`` tag — these are vocabulary
    drift (a typo or a free-text status) and must be reported, not silently
    dropped. Plugin-provided non-project rows are excluded from both.
    """
    statused = {}
    untagged = set()
    for cells in _table_rows(text):
        joined = " | ".join(cells)
        is_plugin_row = "plugin-provided" in joined
        status_m = _STATUS_RE.search(joined)
        paths = [m.group(1).rstrip("./").rstrip("/") for m in _PATH_RE.finditer(joined)]
        paths = [p for p in paths if p]
        if is_plugin_row:
            continue
        if not paths:
            continue
        if not status_m:
            untagged.update(paths)
            continue
        status = status_m.group(1)
        for path in paths:
            statused[path] = status
    return statused, untagged


def _is_seeded_path(path, seeded):
    """A seeded row's path is covered if it equals a seed path or sits under one."""
    for sp in seeded:
        if path == sp or path.startswith(sp.rstrip("/") + "/"):
            return True
    return False


def main():
    plugin_root = get_plugin_root()
    index_path = plugin_root / "templates" / "project-index.md"
    toc_path = plugin_root / "templates" / "claude-md-toc.md"
    for p in (index_path, toc_path):
        if not p.exists():
            sys.exit(f"HALT: map missing: {p} (is CLAUDE_PLUGIN_ROOT set correctly?)")

    index_map, index_untagged = _parse_map(index_path.read_text(encoding="utf-8"))
    toc_map, toc_untagged = _parse_map(toc_path.read_text(encoding="utf-8"))

    errors = []

    # 1. Vocabulary — every table row with a conductor/ path must carry a
    #    recognized status tag. An unrecognized tag (typo / free text) means the
    #    row is invisible to the status checks and must be reported explicitly.
    for label, untagged in (("project-index.md", index_untagged),
                            ("claude-md-toc.md", toc_untagged)):
        if untagged:
            errors.append(
                f"{label}: rows missing a seeded/auto/on-demand status tag: "
                + ", ".join(sorted(untagged))
            )
    for label, m in (("project-index.md", index_map), ("claude-md-toc.md", toc_map)):
        if not m:
            errors.append(f"{label}: parsed zero status-tagged rows — table format broken?")

    # 2. Spine agreement — the creation map (toc) is an intentional superset
    #    (it carries creation patterns the read-map groups under one entry, plus
    #    globs like decision-*.md). So the invariant is index ⊆ toc at the
    #    directory-prefix level, NOT symmetric equality.
    index_prefixes = {_normalize_prefix(p) for p in index_map}
    toc_prefixes = {_normalize_prefix(p) for p in toc_map}
    only_index = index_prefixes - toc_prefixes
    if only_index:
        errors.append(
            "project-index.md lists categories absent from claude-md-toc.md (spine drift): "
            + ", ".join(sorted(only_index))
        )

    # 3. Seeded-really-created — every ``seeded`` row's path must be written by setup.
    for label, m in (("project-index.md", index_map), ("claude-md-toc.md", toc_map)):
        liars = sorted(p for p, st in m.items() if st == "seeded" and not _is_seeded_path(p, SEED_PATHS))
        if liars:
            errors.append(
                f"{label}: rows tagged 'seeded' but NOT written by setup (fix the tag or "
                f"add the path to SEED_PATHS): " + ", ".join(liars)
            )

    # 4. Category-index is lazy — no row tagged ``seeded`` may point at a category
    #    ``index.md``. Category indices (``conductor/design/api-specs/index.md``,
    #    ``.../database/index.md``, …) are created on FIRST SEED by corpus-writer
    #    (``seed-category-doc.py``), never pre-created by setup. A ``seeded`` tag on
    #    one is the precise lie that would create an empty stub and let it rot —
    #    the opposite of the lazy-by-design contract. Paths are repo-relative here
    #    (they come from the templates), so ``category_for`` matches them directly.
    for label, m in (("project-index.md", index_map), ("claude-md-toc.md", toc_map)):
        bad = sorted(
            p for p, st in m.items()
            if st == "seeded"
            and p.endswith("index.md")
            and category_for(p) is not None
        )
        if bad:
            errors.append(
                f"{label}: category index.md rows tagged 'seeded' but category indices "
                f"are lazy (created on first seed by corpus-writer, never by setup). "
                f"Re-tag as 'auto': " + ", ".join(bad)
            )

    if errors:
        sys.stderr.write("index-map drift detected:\n")
        for e in errors:
            sys.stderr.write(f"  - {e}\n")
        sys.stderr.write(
            "Remediation: align both maps on the same spine, ensure every row carries a "
            "seeded/auto/on-demand status, and tag as 'seeded' only paths setup actually writes.\n"
        )
        sys.exit(1)

    print(
        f"OK: index maps agree — {len(index_prefixes)} read-map categories covered by the "
        f"creation map, vocabulary valid, all 'seeded' rows created by setup."
    )


if __name__ == "__main__":
    main()
