#!/usr/bin/env python3
"""seed-category-doc — wire a category dir + index.md before seeding a corpus doc.

The problem this solves
-----------------------
``conductor/index.md`` (scaffolded by ``setup`` from ``templates/project-index.md``)
declares category entry points like ``conductor/design/api-specs/index.md`` and
``conductor/design/database/index.md`` — the read-side routing targets in
``runtime/contracts/doc-routing.md``. But neither those directories nor their
``index.md`` files are ever created by ``setup`` or ``new-track``. They are lazy
slots, created on first seed. Historically that seed was a prose step in
``corpus-writer`` §6.8 ("Write the doc, add a row to ``conductor/index.md``") with
no owner for the *category* dir or its *category* ``index.md`` — so a doc dropped
into ``conductor/design/api-specs/foo.md`` left an ad-hoc dir, no category index,
and an orphaned routing target.

This script is the single deterministic owner of the mechanical part: ``mkdir``
the parent, create the category ``index.md`` from a minimal template if it is
missing, and report what happened. The agent then writes the doc's content + its
provenance frontmatter and appends the ``conductor/index.md`` Scoped Docs row
(unchanged prose). ``on-category-write-guard.py`` denies a bare ``Write`` into a
category dir whose ``index.md`` is absent and routes the agent here, so the wiring
cannot be skipped.

Idempotent: a second run for an existing dir/index is a no-op (returns
``category_index: "existing"``). Never edits the category index beyond seeding it —
appending child rows is the agent's Edit job, mirroring how
``conductor/index.md`` rows are maintained.

Exit discipline mirrors ``scaffold-strategy.py`` / ``check-index-maps.py``:
exit 0 + JSON summary on success; exit 1 + remediation hint on a bad path/type.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from category_dirs import CATEGORY_DIRS, category_for  # noqa: E402

# Frontmatter ``type`` vocabulary — must match ``runtime/contracts/doc-conventions.md``
# (Page Provenance Frontmatter). The category ``index.md`` is exempt from
# frontmatter (it is navigation), but the seeded doc is not, so validate the arg.
VALID_TYPES = frozenset(
    {"architecture", "api", "database", "ux", "resource", "entity", "concept", "source", "query"}
)

# One title per category for the seeded index.md. Keys are CATEGORY_DIRS entries.
_CATEGORY_TITLES = {
    "conductor/design/api-specs": "API Specifications",
    "conductor/design/database": "Database",
    "conductor/design/architecture": "Architecture",
    "conductor/requirement/ux-ui": "UX / UI Spec",
    "conductor/requirement/prd": "Product Requirements",
    "conductor/resource/references": "References",
    "conductor/resource/faq": "FAQ",
}


def _resolve_doc_path(raw: str) -> Path:
    """Resolve a Conductor-root-relative doc path against the project root.

    The hook/agent may pass a project-relative path (``conductor/...``) or an
    absolute one. Project root resolution prefers ``$CLAUDE_PROJECT_DIR`` (set by
    Claude Code for every project hook), then the process cwd — mirroring
    ``lib/env.get_data_dir``'s tiers without its fallback to the plugin dir (this
    script only ever runs inside a real project, so a plugin fallback would be a
    bug, not a convenience).
    """
    p = Path(raw)
    if p.is_absolute():
        return p
    project = os.environ.get("CLAUDE_PROJECT_DIR")
    base = Path(project) if project else Path.cwd()
    return (base / raw).resolve()


def _category_index_template(rel_category: str) -> str:
    title = _CATEGORY_TITLES.get(rel_category, Path(rel_category).name.replace("-", " ").title())
    return (
        f"# {title}\n\n"
        f"Category index for `{rel_category}/`. Auto-created by `seed-category-doc.py` on "
        f"first seed (status `auto`). Child docs are listed below as they are seeded by "
        f"`corpus-writer`; the full routing row lives in `conductor/index.md` (Scoped Docs).\n\n"
        f"| Name | Path | Summary |\n"
        f"|------|------|---------|\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "")
    ap.add_argument("doc_path", help="Conductor-root-relative path of the doc to seed "
                                     "(e.g. conductor/design/api-specs/auth-login.md)")
    ap.add_argument("type", help="Frontmatter type (doc-conventions.md vocabulary)")
    ap.add_argument("--title", default=None, help="Optional human title (reserved for future use)")
    ap.add_argument("--match", default=None, help="Optional routing match-strategy (reserved for future use)")
    args = ap.parse_args()

    if args.type not in VALID_TYPES:
        sys.stderr.write(
            f"HALT: unknown frontmatter type {args.type!r}. Valid: "
            f"{', '.join(sorted(VALID_TYPES))} (per doc-conventions.md).\n"
        )
        return 1

    doc_path = _resolve_doc_path(args.doc_path)

    # Derive the conductor-root-relative form for category lookup + the summary.
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd().resolve())
    try:
        rel = str(doc_path.relative_to(project)).replace("\\", "/")
    except ValueError:
        # Absolute path outside the project root — fall back to the raw arg form.
        rel = args.doc_path.replace("\\", "/").lstrip("./")

    rel_cat = category_for(rel)
    if rel_cat is None:
        # Not a category doc — still mkdir the parent so a non-category seed
        # (e.g. conductor/design/tech-stack.md edits) doesn't fail, and report
        # category_index: "none". The guard only denies category paths, so this
        # branch is reached only when the agent invokes the helper directly.
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        print(json.dumps({"doc": rel, "created_dir": True, "category_index": "none"}))
        return 0

    # Ensure the category dir exists, then the category index.md if missing.
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    cat_index = doc_path.parent / "index.md"
    if cat_index.exists():
        idx_status = "existing"
    else:
        cat_index.write_text(_category_index_template(rel_cat), encoding="utf-8")
        idx_status = "created"

    print(json.dumps({"doc": rel, "created_dir": True, "category_index": idx_status, "category": rel_cat}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
