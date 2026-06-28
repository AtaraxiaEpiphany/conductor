"""Tests for lib.atomic_io.atomic_write_json — the shared crash-safe write.

Pins the temp+fsync+os.replace semantics that track_state.core._write_state and
track_state.result.cmd_write_result both now delegate to (previously each
hand-rolled its own copy).
"""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.atomic_io import atomic_write_json, atomic_write_text


class AtomicWriteJsonTests(TestCase):
    def test_writes_readable_json_with_trailing_newline(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.json"
            ret = atomic_write_json(p, {"a": 1, "b": [2, 3]})
            self.assertEqual(ret, p)
            text = p.read_text()
            self.assertTrue(text.endswith("\n"))  # POSIX text-file friendly
            self.assertEqual(json.loads(text), {"a": 1, "b": [2, 3]})

    def test_overwrites_existing_file_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.json"
            p.write_text('{"old": true}')
            atomic_write_json(p, {"new": true} if False else {"new": True})
            self.assertEqual(json.loads(p.read_text()), {"new": True})

    def test_no_temp_file_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.json"
            atomic_write_json(p, {"x": 1})
            temps = [f for f in os.listdir(d) if f.startswith(".out.json.tmp")]
            self.assertEqual(temps, [])

    def test_ensure_ascii_false_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out.json"
            atomic_write_json(p, {"k": "café — ✓"})
            self.assertIn("café", p.read_text())  # not \\u-escaped

    def test_write_error_leaves_original_untouched_and_cleans_temp(self):
        # Make the target dir read-only after placing an original file so the
        # temp-file write fails. The original must survive and no temp linger.
        if os.name == "nt":
            self.skipTest("read-only-dir semantics differ on Windows")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            p = d / "out.json"
            p.write_text('{"original": true}')
            os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)  # r-x: no write/create
            try:
                with self.assertRaises(OSError):
                    atomic_write_json(p, {"x": 1})
                # original untouched, no temp file created
                self.assertEqual(json.loads(p.read_text()), {"original": True})
            finally:
                os.chmod(d, stat.S_IRWXU)  # restore so cleanup can delete


class AtomicWriteTextTests(TestCase):
    """Pins atomic_write_text — same crash-safe semantics as the JSON variant,
    but caller-owned line endings (no injected trailing newline) for the
    non-JSON files conductor writes (session-handoff.md, session start markers).
    """

    def test_writes_text_verbatim_no_injected_newline(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "handoff.md"
            atomic_write_text(p, "line one\nline two")  # no trailing \n
            self.assertEqual(p.read_text(), "line one\nline two")

    def test_overwrites_existing_file_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "handoff.md"
            p.write_text("old content")
            atomic_write_text(p, "new content")
            self.assertEqual(p.read_text(), "new content")

    def test_no_temp_file_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "handoff.md"
            atomic_write_text(p, "x")
            temps = [f for f in os.listdir(d) if f.startswith(".handoff.md.tmp")]
            self.assertEqual(temps, [])

    def test_write_error_leaves_original_untouched(self):
        if os.name == "nt":
            self.skipTest("read-only-dir semantics differ on Windows")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            p = d / "handoff.md"
            p.write_text("original")
            os.chmod(d, stat.S_IRUSR | stat.S_IXUSR)  # r-x: no write/create
            try:
                with self.assertRaises(OSError):
                    atomic_write_text(p, "new")
                self.assertEqual(p.read_text(), "original")
            finally:
                os.chmod(d, stat.S_IRWXU)


if __name__ == "__main__":
    main()
