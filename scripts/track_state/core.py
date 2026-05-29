"""Core state I/O: load/save track-state.json."""
import json
import shutil
from pathlib import Path


def _lock_file_path(track_dir):
    """Path to the separate lock file used for coordinated file access."""
    return Path(track_dir) / ".track-state.lock"


def _backup_path(track_dir):
    """Path to the backup file used for corruption recovery."""
    return Path(track_dir) / "track-state.json.bak"


def load(track_dir):
    """Load track-state.json with shared lock for concurrent access safety.

    Falls back to .bak on JSON corruption.
    """
    state_file = Path(track_dir) / "track-state.json"
    lock_path = _lock_file_path(track_dir)
    lock_fd = None
    try:
        import fcntl
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_SH)
    except (ImportError, AttributeError, OSError):
        pass

    def _read(path):
        with open(path, "r") as f:
            return json.load(f)

    try:
        try:
            return _read(state_file)
        except json.JSONDecodeError:
            bak = _backup_path(track_dir)
            if bak.exists():
                try:
                    data = _read(bak)
                    # Restore backup to main file so subsequent loads succeed
                    shutil.copy2(str(bak), str(state_file))
                    return data
                except (json.JSONDecodeError, OSError):
                    pass
            raise
    finally:
        if lock_fd is not None:
            try:
                import fcntl
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
            except (ImportError, AttributeError, OSError):
                pass


def save(track_dir, state):
    """Save track-state.json with exclusive lock using atomic write pattern.

    Creates a .bak backup of the current file before overwriting.
    Uses a separate lock file (.track-state.lock) so that the lock persists
    across the os.replace() call (which swaps the inode of track-state.json,
    rendering locks on that file's fd ineffective).
    """
    import tempfile
    import os

    state_file = Path(track_dir) / "track-state.json"
    lock_path = _lock_file_path(track_dir)

    lock_fd = None
    try:
        import fcntl
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
    except (ImportError, AttributeError, OSError):
        pass

    try:
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

        # Create backup after successful write so next corruption has a fallback
        try:
            shutil.copy2(str(state_file), str(_backup_path(track_dir)))
        except OSError:
            pass
    finally:
        if lock_fd is not None:
            try:
                import fcntl
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
            except (ImportError, AttributeError, OSError):
                pass
