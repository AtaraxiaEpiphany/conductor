"""Core state I/O: load/save track-state.json with file locking.

Locking model
-------------
Every state mutation is a read-modify-write (RMW). To prevent lost updates
under concurrent writers, the flock on ``.track-state.lock`` must be held for
the *entire* RMW — not released between ``load()`` and ``save()``. The atomic
RMW primitive is :func:`update`: it holds ``LOCK_EX`` across load→fn→save.

Leaf mutation commands should use ``update`` rather than the ``load()`` /
``save()`` pair, whose individual locks do not span the gap between them.
"""
import json
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path


def _lock_file_path(track_dir):
    """Path to the separate lock file used for coordinated file access."""
    return Path(track_dir) / ".track-state.lock"


def _backup_path(track_dir):
    """Path to the backup file used for corruption recovery."""
    return Path(track_dir) / "track-state.json.bak"


@contextmanager
def locked(track_dir, exclusive=True):
    """Hold the flock on ``.track-state.lock`` for a critical section.

    Exclusive by default (use for read-modify-write); pass ``exclusive=False``
    for a brief shared read lock. Uses a separate lock file (not the data file)
    so the lock survives the inode swap of ``os.replace()`` in
    :func:`_save_unlocked`.

    Lock-acquisition failure is surfaced on stderr but does not raise: on the
    supported Linux/macOS runtime ``fcntl`` is always available, and failing
    open preserves prior behavior. The guarantee is only meaningful when the
    lock is actually held — :func:`update` performs the whole RMW under one
    held lock.
    """
    import fcntl

    lock_fd = open(_lock_file_path(track_dir), "w")
    acquired = False
    try:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            acquired = True
        except (ImportError, AttributeError, OSError) as e:
            # Fail open, but loudly: silent loss of mutual exclusion must never
            # be invisible.
            print(f"WARNING: track-state lock unavailable ({e}); proceeding without",
                  file=sys.stderr)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            except (ImportError, AttributeError, OSError):
                pass
        lock_fd.close()


def _read_json(path):
    with open(path, "r") as f:
        return json.load(f)


def _load_unlocked(track_dir):
    """Read track-state.json without locking. Falls back to .bak on corruption."""
    state_file = Path(track_dir) / "track-state.json"
    try:
        return _read_json(state_file)
    except json.JSONDecodeError:
        bak = _backup_path(track_dir)
        if bak.exists():
            try:
                data = _read_json(bak)
                # Restore backup to main file so subsequent loads succeed
                shutil.copy2(str(bak), str(state_file))
                return data
            except (json.JSONDecodeError, OSError):
                pass
        raise


def _save_unlocked(track_dir, state):
    """Atomic write of track-state.json without locking.

    Backs up the current file after a successful replace. Uses a temp file +
    fsync + os.replace so readers never observe a partially written file.
    """
    import os
    import tempfile

    state_file = Path(track_dir) / "track-state.json"

    temp_file = tempfile.NamedTemporaryFile(
        mode='w',
        dir=state_file.parent,
        prefix=f'.{state_file.name}.tmp',
        delete=False
    )
    temp_file_name = None
    try:
        json.dump(state, temp_file, indent=2, ensure_ascii=False)
        temp_file.write("\n")
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file_name = temp_file.name
    except (OSError, IOError):
        temp_file.close()
        if temp_file_name is None:
            temp_file_name = temp_file.name
        try:
            os.unlink(temp_file_name)
        except OSError:
            pass
        raise
    finally:
        temp_file.close()

    os.replace(temp_file_name, str(state_file))

    # Create backup after successful write so next corruption has a fallback
    try:
        shutil.copy2(str(state_file), str(_backup_path(track_dir)))
    except OSError:
        pass


def load(track_dir):
    """Read track-state.json under a brief shared lock.

    For mutations prefer :func:`update`, which holds an exclusive lock across
    the whole read-modify-write (load()/save() do not span the gap).
    """
    with locked(track_dir, exclusive=False):
        return _load_unlocked(track_dir)


def save(track_dir, state):
    """Write track-state.json under a brief exclusive lock (full overwrite).

    Atomic, but does NOT close the lost-update window for callers that do
    ``load()`` → mutate → ``save()`` as separate calls — use :func:`update`.
    """
    with locked(track_dir, exclusive=True):
        _save_unlocked(track_dir, state)


def update(track_dir, fn, *, exclusive=True):
    """Atomic read-modify-write: the race-free mutation primitive.

    Holds ``LOCK_EX`` across load→fn→save, so two concurrent updates cannot
    lose each other's changes (the TOCTOU gap that plain load()/save() leaves
    open). ``fn`` receives the freshly-loaded state, mutates it **in place**,
    and may return a value; ``update`` persists the mutated state and returns
    whatever ``fn`` returned.

        retry_count = update(track_dir, lambda s: _bump_retry(s, ...))

    ``exclusive=False`` is available for read-then-conditionally-write flows
    that only need to coordinate with writers; the default ``exclusive=True``
    is correct for all mutations.
    """
    with locked(track_dir, exclusive=exclusive):
        state = _load_unlocked(track_dir)
        result = fn(state)
        _save_unlocked(track_dir, state)
        return result
