"""Core state I/O: load/save track-state.json, plus a transaction context.

Locking uses a separate ``.track-state.lock`` file (not ``track-state.json``
itself) so the lock survives the ``os.replace()`` inode swap in the atomic
write. ``load`` takes a shared lock; ``save`` and ``transaction`` take an
exclusive lock.

Closes the read-modify-write race: previously every mutator did
``state = load()`` … ``save(state)`` with the lock released in between, so two
concurrent commands could both read the same version and the second save
clobbered the first. ``transaction()`` holds the exclusive lock across the
whole load→mutate→save window.
"""
import json
import shutil
from contextlib import contextmanager
from pathlib import Path


def _lock_file_path(track_dir):
    """Path to the separate lock file used for coordinated file access."""
    return Path(track_dir) / ".track-state.lock"


def _backup_path(track_dir):
    """Path to the backup file used for corruption recovery."""
    return Path(track_dir) / "track-state.json.bak"


def _acquire(lock_path, exclusive):
    """Acquire an flock on ``lock_path``, returning the held fd (or None).

    ``exclusive`` selects LOCK_EX (write/transaction) vs LOCK_SH (read).
    flock is advisory and absent on Windows; failures are non-fatal — the lock
    is best-effort protection against concurrent corruption, not a hard gate.
    """
    try:
        import fcntl
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd.fileno(),
                    fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        return lock_fd
    except (ImportError, AttributeError, OSError):
        return None


def _release(lock_fd):
    """Release a lock fd acquired via :func:`_acquire` (no-op for None)."""
    if lock_fd is None:
        return
    try:
        import fcntl
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
    except (ImportError, AttributeError, OSError):
        pass


def _read_state(state_file, track_dir):
    """Read+parse ``state_file``, falling back to the ``.bak`` on corruption.

    Lock-free — the caller (``load``/``transaction``) already holds the lock.
    On successful .bak recovery the main file is restored from the backup so
    subsequent loads succeed without re-falling-back.
    """
    def _read(path):
        with open(path, "r") as f:
            return json.load(f)

    try:
        return _read(state_file)
    except json.JSONDecodeError:
        bak = _backup_path(track_dir)
        if bak.exists():
            try:
                data = _read(bak)
                shutil.copy2(str(bak), str(state_file))
                return data
            except (json.JSONDecodeError, OSError):
                pass
        raise


def _write_state(track_dir, state):
    """Atomic write of ``state`` to track-state.json + .bak backup.

    Lock-free — the caller (``save``/``transaction``) already holds the lock.
    Writes to a temp file, fsyncs, then ``os.replace`` (atomic inode swap), and
    refreshes the .bak afterward so the next corruption has a fallback.
    """
    import tempfile
    import os

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
    except (OSError, IOError) as e:
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

    try:
        shutil.copy2(str(state_file), str(_backup_path(track_dir)))
    except OSError:
        pass


def load(track_dir):
    """Load track-state.json under a shared lock (read-only).

    Falls back to ``.bak`` on JSON corruption. For read-modify-write sequences
    prefer :func:`transaction` — it holds an exclusive lock across load+save,
    closing the race this separate-acquire pattern leaves open.
    """
    state_file = Path(track_dir) / "track-state.json"
    lock_fd = _acquire(_lock_file_path(track_dir), exclusive=False)
    try:
        return _read_state(state_file, track_dir)
    finally:
        _release(lock_fd)


def save(track_dir, state):
    """Save track-state.json under an exclusive lock (standalone write).

    Prefer :func:`transaction` for read-modify-write sequences.
    """
    lock_fd = _acquire(_lock_file_path(track_dir), exclusive=True)
    try:
        _write_state(track_dir, state)
    finally:
        _release(lock_fd)


@contextmanager
def transaction(track_dir):
    """Hold LOCK_EX across a load→mutate→save transaction.

    Yields the loaded state; on clean exit the (mutated) state is written back
    under the same held lock. On an exception the in-memory mutation is
    discarded — nothing is saved — and the lock is released, leaving the
    on-disk state consistent.

    Closes the read-modify-write race: every mutator previously did
    ``state = load()`` … ``save(state)`` with the lock released between the two,
    so concurrent commands could both read v1 and the second save clobbered the
    first. Within this block the exclusive lock is held continuously.

    Usage::

        with transaction(track_dir) as state:
            state["phases"][0]["tasks"][0]["status"] = "completed"

    Do NOT nest transactions on the same ``track_dir`` in one call stack — a
    second LOCK_EX on the (separately-opened) lock file would self-deadlock.
    Mutators are independent single transactions; orchestration that chains
    several mutators calls them sequentially, not nested.
    """
    state_file = Path(track_dir) / "track-state.json"
    lock_fd = _acquire(_lock_file_path(track_dir), exclusive=True)
    state = _read_state(state_file, track_dir)
    try:
        yield state
    except Exception:
        # Discard the in-memory mutation; do NOT save a partial/failed update.
        raise
    else:
        _write_state(track_dir, state)
    finally:
        _release(lock_fd)
