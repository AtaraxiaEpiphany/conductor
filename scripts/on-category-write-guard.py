#!/usr/bin/env python3
"""PreToolUse:Write|Edit|MultiEdit guard — deny a bare category-dir write that
skips seeding the category ``index.md``.

The problem this solves
-----------------------
``conductor/index.md`` advertises category entry points
(``conductor/design/api-specs/index.md``, ``.../database/index.md``, …) but those
directories and their ``index.md`` files are lazy — created on first seed by
``corpus-writer`` via ``scripts/seed-category-doc.py``. The seed step is otherwise a
prose contract in ``agents/corpus-writer.md`` §6.8 ("Write the doc, add a row to
``conductor/index.md``"), and a model under context pressure will skip the helper
and ``Write`` the doc directly — leaving an ad-hoc dir, no category index, and an
orphaned routing target. This is the same class of prose-invariant-a-model-ignores
gap that ``on-orchestrator-read-guard.py`` (thin-router) and ``on-dispatch-dedupe.py``
(second Agent spawn) close.

This hook makes the invariant deterministic: a ``Write``/``Edit``/``MultiEdit``
whose target sits under a category dir (``lib.category_dirs.CATEGORY_DIRS``) is
**denied** when that category's ``index.md`` does not yet exist. The deny reason
prescribes the exact owning command (``seed-category-doc.py``) — per the
dispatch-dedupe loop lesson, a deny reason that fails to name the command re-triggers
the same tool and loops. Once the helper has created the category index, subsequent
writes are allowed.

Scope / fail-open
-----------------
Only the ``Write`` / ``Edit`` / ``MultiEdit`` tools are gated, and only targets
under a known category dir. Non-category writes, and writes into a category dir
whose ``index.md`` already exists, pass through. ``agent_type`` absent → allow
outright (the orchestrator editing ad-hoc notes is out of scope; the guard
constrains agents that own corpus seeding). Any path-resolution, I/O, or parsing
error → allow + stderr warning (mirrors ``on-orchestrator-read-guard.py``'s
fail-open contract): a misbehaving guard is worse than none.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.hook_io import read_hook_input, write_hook_output  # noqa: E402
from lib.logging import init_logging, log_entry  # noqa: E402
from category_dirs import category_for  # noqa: E402


def _resolve_rel(file_path: str, cwd: str) -> str | None:
    """Reduce a Write target to a Conductor-root-relative path, or ``None``.

    A Write target may be absolute or relative to the hook ``cwd`` (the agent's
    working dir = repo root). We anchor it, normalise separators/redundant
    ``./``, and try to express it relative to ``cwd`` — which is the project
    root in normal operation. Returns ``None`` if it can't be made relative
    (e.g. a path outside the project); the caller treats that as "not a category
    path" and allows.
    """
    try:
        fp = str(file_path).replace("\\", "/")
    except TypeError:
        return None
    if not fp.startswith("/"):
        fp = f"{str(cwd).rstrip('/')}/{fp}"
    while "//" in fp:
        fp = fp.replace("//", "/")
    while "/./" in fp:
        fp = fp.replace("/./", "/")
    base = str(cwd).rstrip("/")
    if fp == base or fp.startswith(base + "/"):
        return fp[len(base) + 1:].lstrip("./")
    return None


def main():
    input_data = read_hook_input()

    tool = input_data.get("tool_name")
    if tool not in ("Write", "Edit", "MultiEdit"):
        write_hook_output(permission_decision="allow")
        return

    # Unlike on-orchestrator-read-guard (which protects against the orchestrator
    # and so allows subagents), here the protected writer IS the subagent
    # (corpus-writer). So we do NOT gate on agent_type presence — the guard applies
    # to every Write/Edit/MultiEdit regardless of who issues it. Only non-category
    # targets and already-seeded categories pass through (below).

    file_path = (input_data.get("tool_input") or {}).get("file_path", "")
    cwd = input_data.get("cwd") or str(Path.cwd())

    log_file = init_logging("on-category-write-guard")
    log_entry(log_file, f"event=write_probe tool={tool} path={file_path}")

    rel = _resolve_rel(file_path, cwd)
    if rel is None:
        write_hook_output(permission_decision="allow")
        return

    rel_cat = category_for(rel)
    if rel_cat is None:
        # Not under a category dir — allow (e.g. plan.md, spec.md, conductor/index.md).
        write_hook_output(permission_decision="allow")
        return

    # Writing the category index.md itself is the seeding act — always allow
    # (this is the helper / an agent legitimately creating the index).
    if rel == f"{rel_cat}/index.md" or rel.endswith(f"{rel_cat}/index.md"):
        write_hook_output(permission_decision="allow")
        return

    # Does the category index.md already exist? Resolve against cwd (project root).
    cat_index = Path(cwd) / rel_cat / "index.md"
    try:
        exists = cat_index.exists()
    except Exception:
        exists = False  # fail-open
    if exists:
        write_hook_output(permission_decision="allow")
        return

    # Infer the frontmatter type from the category so the prescribed command is
    # copy-paste-ready. Maps CATEGORY_DIRS → doc-conventions type.
    _CAT_TYPE = {
        "conductor/design/api-specs": "api",
        "conductor/design/database": "database",
        "conductor/design/architecture": "architecture",
        "conductor/requirement/ux-ui": "ux",
        "conductor/requirement/prd": "resource",
        "conductor/resource/references": "resource",
        "conductor/resource/faq": "resource",
    }
    doc_type = _CAT_TYPE.get(rel_cat, "concept")
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "${CLAUDE_PLUGIN_ROOT}")
    helper = f'python3 "{plugin_root}/scripts/seed-category-doc.py" "{rel}" {doc_type}'

    reason = (
        f"Conductor category-index invariant: you are writing `{rel}` into the "
        f"category dir `{rel_cat}/`, but its `index.md` does not exist yet — the "
        f"dir, the category index, and the routing row must all be wired together "
        f"or the doc is orphaned from the read-side routing map "
        f"(`runtime/contracts/doc-routing.md`). Run this first, then re-Write your "
        f"content:\n\n  {helper}\n\n"
        f"That creates `{rel_cat}/index.md` (status `auto`) and the parent dir. "
        f"The PreToolUse guard then allows the Write. (Per corpus-writer §6.8: "
        f"never Write a category-dir doc directly — always seed via the helper.)"
    )
    log_entry(log_file, f"event=deny category={rel_cat} path={rel}")
    print(
        f"⚠️  CONDUCTOR CATEGORY-WRITE GUARD: denied {tool} of `{rel}` — "
        f"category `{rel_cat}/` has no index.md yet. Run seed-category-doc.py first.",
        file=sys.stderr,
    )
    write_hook_output(permission_decision="deny", permission_decision_reason=reason)


if __name__ == "__main__":
    main()
