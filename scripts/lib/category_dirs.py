"""The canonical set of Conductor category directories.

Each category directory carries an ``index.md`` (the read-side entry point the
``conductor/index.md`` Scoped Docs table and ``runtime/contracts/doc-routing.md``
route into). Those directories and their ``index.md`` files are **lazy**: created on
first seed by ``corpus-writer`` (via ``scripts/seed-category-doc.py``), never
pre-created by ``setup``. A bare agent ``Write`` into a category directory whose
``index.md`` does not yet exist is denied by ``scripts/on-category-write-guard.py``
and routed through the helper — so the dir, the category index, and the
``conductor/index.md`` routing row all get wired in one step (no orphaned docs).

This constant is the single source of truth shared by the helper and the guard so
the two cannot disagree about which directories count as "categories" — the exact
two-files-must-agree drift ``check-index-maps.py`` exists to prevent. Keep it in
sync with the ``auto``/``on-demand`` category rows of
``templates/project-index.md`` (that drift is caught by the gate's spine check).

Conductor root-relative, POSIX separators (``/``), no leading ``./`` and no
trailing ``/`` — normalised so prefix-matching against a resolved path is a plain
string test.
"""

CATEGORY_DIRS = frozenset(
    {
        "conductor/design/api-specs",
        "conductor/design/database",
        "conductor/design/architecture",
        "conductor/requirement/ux-ui",
        "conductor/requirement/prd",
        "conductor/resource/references",
        "conductor/resource/faq",
    }
)


def category_for(rel_path: str) -> str | None:
    """Return the category dir ``rel_path`` sits under, or ``None``.

    ``rel_path`` is a Conductor-root-relative path (POSIX separators, optional
    leading ``./``). A path is "in a category" iff one of :data:`CATEGORY_DIRS` is
    a prefix of it at a directory boundary (``conductor/design/api-specs`` matches
    ``conductor/design/api-specs/foo.md`` but not ``conductor/design/api-specs-old``).
    """
    p = rel_path.replace("\\", "/").lstrip("./").rstrip("/")
    for d in CATEGORY_DIRS:
        if p == d or p.startswith(d.rstrip("/") + "/"):
            return d
    return None
