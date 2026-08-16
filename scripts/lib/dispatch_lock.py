"""Atomic occupancy lock for the dispatch critical section.

The problem this solves
-----------------------
The inflight dispatch marker (``lib/dispatch_inflight``) is stamped by
``prepare_dispatch`` via a read-modify-write: it reads the prior ``gen``, bumps
it, and writes ``(gen+1, start_sha, …)``. Under the conductor's normal
synchronous model that is uncontended — one dispatch at a time — so the
non-atomic bump is harmless.

The hole opens if the ``Agent`` tool ever returns early (background mode:
``CLAUDE_AUTO_BACKGROUND_TASKS`` auto-backgrounds after ~2 min;
``CLAUDE_CODE_FORK_SUBAGENT`` forces all spawns to background). Two
``prepare_dispatch`` calls can then race on the same ``(phase, task, subtask)``:
both read ``gen=N``, both stamp ``gen=N+1`` — and the dedupe hook's
same-``gen``-means-one-dispatch-twice disambiguation breaks, because two
*fresh* dispatches now look like a single one spawned twice.

This module closes the read-modify-write window with an exclusive
``fcntl.flock`` held for the duration of the critical section. It does NOT
hold the lock for the agent's whole lifetime — hooks are separate processes
that cannot share a file descriptor, and the marker's ``HEAD == start_sha``
encoding already stands in for "still working". It serializes only the
*decision* (gen bump + stamp), which is the part that must be atomic under
concurrency.

Fail-open
---------
A lock failure must NEVER block a dispatch — that would strand a task, which
is strictly worse than the race it guards. On any ``OSError``/``IOError`` the
contextmanager yields a no-op: the caller proceeds without holding the lock,
and the existing marker + git-HEAD predicate remains the safety net. This is
the same fail-open principle as ``lib/dispatch_inflight`` (readers return
``None``, writers swallow ``OSError``) and the dedupe hook.
"""
import contextlib
import fcntl
import os
from pathlib import Path

from .constants import DISPATCH_LOCK_NAME


def _lock_path(track_dir):
    """Path to the per-track dispatch lock file.

    A sibling of the inflight markers under ``<track_dir>/.conductor/``, covered
    by the per-track gitignore rule (``.dispatch.lock``, written by
    ``track_state.quality._ensure_conductor_gitignore``). Transient lock state —
    never handed to the model or staged.
    """
    return Path(track_dir) / ".conductor" / DISPATCH_LOCK_NAME


@contextlib.contextmanager
def acquire(track_dir):
    """Hold an exclusive lock across the dispatch critical section (fail-open).

    Usage::

        with dispatch_lock.acquire(track_dir):
            prev_gen = inflight.read_gen(...)
            inflight.write(..., gen=prev_gen + 1)

    The lock is process-wide exclusive (``LOCK_EX``) on
    ``<track_dir>/.conductor/.dispatch.lock``. Two concurrent
    ``prepare_dispatch`` calls for the same track serialize here, so the
    read-modify-write of the inflight ``gen`` is atomic.

    Fail-open: on any ``OSError`` (cannot create/open the lock file, cannot
    flock — e.g. on a filesystem that doesn't support advisory locks, or a
    read-only tree), the contextmanager yields ``None`` instead of raising.
    The caller's work proceeds unguarded; the marker + git-HEAD predicate
    remains the safety net. A misbehaving lock is worse than none.
    """
    try:
        path = _lock_path(track_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+")
    except OSError:
        # Cannot even open the lock file → fail open (no-op context).
        yield None
        return

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError:
        # Filesystem doesn't support advisory locks, or flock failed → fail
        # open. Close the handle we opened and proceed unguarded.
        try:
            fh.close()
        except OSError:
            pass
        yield None
        return

    try:
        yield fh
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass
