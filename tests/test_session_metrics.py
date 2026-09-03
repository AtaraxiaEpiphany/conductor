r"""Gap #10 — revive session-metrics.

``session-start`` now stamps ``int(time.time())`` to
``.data/logs/.session-{id}.start``; ``session-end::log_session_duration``
already read + unlinked that file, so this closes the metrics loop that was
dead (session-end read a file nothing wrote). The stamp is skipped on
``compact`` — compaction is a mid-session event and would reset the timer.
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, _scripts / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ss = _load("session_start_g10", "session-start.py")
_se = _load("session_end_g10", "session-end.py")


class WriteSessionStartTests(TestCase):
    """_write_session_start stamps an int timestamp atomically."""

    def test_writes_int_timestamp_file(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            before = int(time.time())
            _ss._write_session_start(d, "sess-123")
            start_file = d / "logs" / ".session-sess-123.start"
            self.assertTrue(start_file.exists())
            ts = int(start_file.read_text().strip())
            self.assertGreaterEqual(ts, before)
            self.assertLessEqual(ts, int(time.time()))

    def test_empty_session_id_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            _ss._write_session_start(Path(td), "")
            logs = Path(td) / "logs"
            self.assertFalse(logs.exists() or any(logs.glob("*")))

    def test_creates_logs_dir_if_missing(self):
        with tempfile.TemporaryDirectory() as td:
            _ss._write_session_start(Path(td), "s1")
            self.assertTrue((Path(td) / "logs").is_dir())


class SessionMetricsRoundTripTests(TestCase):
    """session-end::log_session_duration reads the stamp, logs duration, unlinks."""

    def test_end_logs_duration_and_unlinks_start_file(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            start_file = d / "logs" / ".session-abc.start"
            start_file.parent.mkdir(parents=True)
            start_file.write_text(str(int(time.time()) - 100))  # backdate ~100s
            metrics_log = d / "logs" / "session-metrics.log"

            _se.log_session_duration("abc", start_file, metrics_log)

            self.assertTrue(metrics_log.exists())
            line = metrics_log.read_text()
            self.assertIn("session=abc", line)
            self.assertIn("duration_seconds=", line)
            self.assertFalse(start_file.exists())  # unlinked after reading

    def test_end_noop_when_start_file_missing(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            metrics_log = d / "logs" / "session-metrics.log"
            _se.log_session_duration("abc", d / "logs" / ".session-abc.start",
                                     metrics_log)
            self.assertFalse(metrics_log.exists())  # nothing to log


class MainStampBehaviorTests(TestCase):
    """main() wiring: the stamp and advisory scans run on startup/resume but are
    skipped on compact (compaction is mid-session; keep that context minimal)."""

    def _run_main(self, source, session_id, digest_marker=""):
        td = tempfile.mkdtemp()
        # Restore the prior value (the conftest suite pin), never bare-pop:
        # stripping it mid-suite sends every later env-inheriting writer to
        # the tier-4 <plugin>/.data fallback.
        prior = os.environ.get("CLAUDE_PLUGIN_DATA")

        def _restore():
            if prior is None:
                os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            else:
                os.environ["CLAUDE_PLUGIN_DATA"] = prior
        self.addCleanup(_restore)
        os.environ["CLAUDE_PLUGIN_DATA"] = td
        # read_hook_input caches stdin in a module-global; reset it so each call
        # re-reads our fresh stdin instead of returning a prior test's payload.
        _ss.read_hook_input.__globals__["_cached_hook_input"] = None
        # Stub the heavy content builders so the test is fast and isolated.
        # digest_marker lets a gate test assert get_loop_digest's output is
        # appended on non-compact sources and absent on compact.
        orig = (_ss.get_conductor_content, _ss.get_session_handoff,
                _ss.get_wiki_drift_warnings, _ss.get_loop_digest)
        _ss.get_conductor_content = lambda *a, **k: ""
        _ss.get_session_handoff = lambda *a, **k: ""
        _ss.get_wiki_drift_warnings = lambda *a, **k: ""
        _ss.get_loop_digest = lambda *a, **k: digest_marker
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(json.dumps({
            "session_id": session_id, "source": source, "cwd": td}))
        buf = io.StringIO()
        sys.stdout = buf
        try:
            _ss.main()
        except SystemExit:
            pass  # write_simple_output exits 0 after emitting
        finally:
            sys.stdin, sys.stdout = old_in, old_out
            (_ss.get_conductor_content, _ss.get_session_handoff,
             _ss.get_wiki_drift_warnings, _ss.get_loop_digest) = orig
        return Path(td), buf.getvalue()

    def test_main_writes_start_file_on_startup(self):
        td, _ = self._run_main("startup", "st1")
        self.assertTrue((td / "logs" / ".session-st1.start").exists())

    def test_main_skips_start_file_on_compact(self):
        td, _ = self._run_main("compact", "cmp")
        self.assertFalse((td / "logs" / ".session-cmp.start").exists())

    def test_loop_digest_present_on_startup(self):
        _td, out = self._run_main("startup", "s1", digest_marker="DIGEST-MARKER")
        self.assertIn("DIGEST-MARKER", out)

    def test_loop_digest_present_on_resume(self):
        _td, out = self._run_main("resume", "s2", digest_marker="DIGEST-MARKER")
        self.assertIn("DIGEST-MARKER", out)

    def test_loop_digest_absent_on_compact(self):
        _td, out = self._run_main("compact", "s3", digest_marker="DIGEST-MARKER")
        self.assertNotIn("DIGEST-MARKER", out)


if __name__ == "__main__":
    main()
