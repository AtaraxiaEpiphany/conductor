r"""Tests for the track-scoped result freshness probe (Gap #4).

``fresh_result_exists`` gained a ``track_dir`` param: when given, it checks ONLY
that track's ``.conductor/result.json`` instead of globbing every track. This
kills the cross-track false positive where a fresh result.json in track B
satisfied a SubagentStop probe running for track A (suppressing a needed
recovery turn, or masking a missing result). ``track_dir=None`` preserves the
prior cwd-relative + glob behavior.
"""
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.result_probe import fresh_result_exists


def _seed_result(track_dir, age_seconds=0):
    p = Path(track_dir) / ".conductor" / "result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"status":"SUCCESS"}')
    if age_seconds:
        ts = time.time() - age_seconds
        import os
        os.utime(p, (ts, ts))
    return p


class TrackScopedProbeTests(TestCase):
    def test_ignores_other_tracks_results(self):
        """A fresh result in B must NOT satisfy a probe scoped to A."""
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "conductor" / "tracks" / "a_20260628"
            b = Path(d) / "conductor" / "tracks" / "b_20260628"
            _seed_result(b)  # fresh, but in the WRONG track
            self.assertFalse(fresh_result_exists(d, track_dir=str(a)))
            self.assertTrue(fresh_result_exists(d, track_dir=str(b)))

    def test_stale_result_in_scope_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "conductor" / "tracks" / "a_20260628"
            _seed_result(a, age_seconds=600)  # stale
            self.assertFalse(fresh_result_exists(d, track_dir=str(a)))

    def test_no_result_in_scope_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "conductor" / "tracks" / "a_20260628"
            a.mkdir(parents=True)
            self.assertFalse(fresh_result_exists(d, track_dir=str(a)))

    def test_none_track_dir_falls_back_to_glob(self):
        """Without track_dir the prior behavior is preserved (glob finds any track)."""
        with tempfile.TemporaryDirectory() as d:
            b = Path(d) / "conductor" / "tracks" / "b_20260628"
            _seed_result(b)
            self.assertTrue(fresh_result_exists(d))  # glob hits B


if __name__ == "__main__":
    main()
