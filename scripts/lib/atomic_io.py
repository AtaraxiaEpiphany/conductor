"""Atomic file writes: temp file + fsync + os.replace (inode-swap atomicity).

The shared substrate for every place conductor writes a JSON file that must
survive a crash mid-write. Previously the temp-file / fsync / ``os.replace``
dance was hand-rolled in two places — ``track_state/core._write_state`` and
``track_state/result.cmd_write_result`` — and had started to drift. It lives
here once now.

The temp file is created in the target file's own directory so ``os.replace``
stays within one filesystem (and is therefore atomic on POSIX), and is removed
on any write error. This helper is **lock-free**: the caller owns any
higher-level coordination (see ``track_state.core.transaction`` for the
read-modify-write lock).
"""
import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path, data, *, indent=2, ensure_ascii=False):
    """Atomically write ``data`` as JSON to ``path``.

    Writes to a sibling temp file, fsyncs it, then ``os.replace``-swaps it into
    place (atomic on POSIX — readers see either the old or the new file, never a
    partial). A trailing newline is appended for POSIX text-file friendliness;
    ``json.load`` tolerates it. Returns the resolved ``path``.

    On any write error the temp file is removed and the exception propagates;
    the original file is left untouched.
    """
    path = Path(path)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(path.parent),
        prefix=f".{path.name}.tmp.",
        delete=False,
    )
    try:
        json.dump(data, tmp, indent=indent, ensure_ascii=ensure_ascii)
        tmp.write("\n")
        tmp.flush()
        os.fsync(tmp.fileno())
    except (OSError, IOError):
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    finally:
        tmp.close()

    os.replace(tmp.name, str(path))
    return path
