"""Tests for lib.result_probe — the shared result.json freshness probe."""
import importlib.util
import os
import sys
import time
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "result_probe", _scripts / "lib" / "result_probe.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
fresh_result_exists = _mod.fresh_result_exists
is_fresh = _mod.is_fresh


def _make_result(track_dir: Path, age_seconds: float = 0.0) -> Path:
    """Create a .conductor/result.json under track_dir, optionally aged."""
    p = track_dir / ".conductor" / "result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"status":"SUCCESS"}')
    if age_seconds > 0:
        ts = time.time() - age_seconds
        os.utime(p, (ts, ts))
    return p


class FreshResultExistsTests(TestCase):
    def test_fresh_direct_result_json(self):
        d = Path(__file__).resolve().parent / "_tmp_probe_fresh"
        try:
            _make_result(d, age_seconds=0)
            self.assertTrue(fresh_result_exists(str(d)))
        finally:
            _cleanup(d)

    def test_missing_result_json(self):
        d = Path(__file__).resolve().parent / "_tmp_probe_missing"
        try:
            d.mkdir(exist_ok=True)
            self.assertFalse(fresh_result_exists(str(d)))
        finally:
            _cleanup(d)

    def test_stale_result_json_rejected(self):
        d = Path(__file__).resolve().parent / "_tmp_probe_stale"
        try:
            # 10 minutes old → well outside the 180s window
            _make_result(d, age_seconds=600)
            self.assertFalse(fresh_result_exists(str(d)))
        finally:
            _cleanup(d)

    def test_fresh_under_tracks_glob(self):
        d = Path(__file__).resolve().parent / "_tmp_probe_glob"
        try:
            track = d / "conductor" / "tracks" / "feat-x"
            _make_result(track, age_seconds=0)
            self.assertTrue(fresh_result_exists(str(d)))
        finally:
            _cleanup(d)

    def test_nonexistent_cwd_no_crash(self):
        self.assertFalse(fresh_result_exists("/does/not/exist/at/all"))

    def test_custom_window(self):
        d = Path(__file__).resolve().parent / "_tmp_probe_window"
        try:
            # 50s old: stale under default 180s? no, 50 < 180 → fresh.
            # stale under a 30s window.
            _make_result(d, age_seconds=50)
            self.assertTrue(fresh_result_exists(str(d), seconds=180))
            self.assertFalse(fresh_result_exists(str(d), seconds=30))
        finally:
            _cleanup(d)


class IsFreshTests(TestCase):
    def test_missing_path(self):
        self.assertFalse(is_fresh(Path("/nope/zzz"), time.time()))

    def test_existing_path(self):
        p = Path(__file__)
        self.assertTrue(is_fresh(p, 0.0))


def _cleanup(d: Path):
    import shutil
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
