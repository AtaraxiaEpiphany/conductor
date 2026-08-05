"""Hook-target path normalization shared across the on-*-guard hooks.

A Write/Edit/Read target may arrive as an absolute path or one relative to the
hook ``cwd`` (the agent's working dir = repo root), and may carry redundant
``//`` or ``./`` segments. The guards all need the same reduction — anchor to
``cwd``, normalise separators, express relative to ``cwd`` — but must NOT use
``Path.resolve()``: guards frequently see paths the agent has not written yet
(no such file on disk), so a filesystem resolve is unreliable. This is the
string-only, filesystem-free equivalent.

Factored out of ``on-category-write-guard._resolve_rel`` so a normalization
edge case is fixed in one place.
"""

from __future__ import annotations

from pathlib import Path


def resolve_rel_target(file_path, cwd) -> str | None:
    """Reduce a tool target to a ``cwd``-relative path, or ``None``.

    Returns the normalized forward-slash path relative to ``cwd`` (the project
    root in normal operation), or ``None`` if the target can't be made relative
    to ``cwd`` (i.e. it lies outside the project). Callers treat ``None`` as
    "not a path this guard cares about" and allow.
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
