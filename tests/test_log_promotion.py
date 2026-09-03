"""Regression: session-start telemetry lands in the PROJECT's .conductor/logs.

The log-position tier ladder (``lib.env.resolve_data_dir``) resolves
``$CLAUDE_PROJECT_DIR/.conductor`` once the hook payload's ``cwd`` has been
promoted (``infer_project_dir_from_payload`` at the ``read_hook_input``
chokepoint). This pins the ordering for session-start specifically — every
data-dir/log-dir resolution in its ``main`` must happen AFTER the payload
read, else the session's telemetry silently falls through to the shared
``<plugin>/.data`` (the wrong-position symptom reported from a live install).
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_TESTS = Path(__file__).resolve().parent
_SCRIPTS = _TESTS.parent / "scripts"


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ss = _load("session_start_promo", "session-start.py")


class SessionStartLogPlacementTests(TestCase):
    def setUp(self):
        # Neither override may pin the resolver: the test exercises the
        # payload-cwd promotion tier, so both tier-1 and pre-set tier-2 envs
        # are cleared and restored.
        self._saved = {k: os.environ.get(k)
                       for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR",
                                 "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # The promotion sets CLAUDE_PROJECT_DIR globally; _saved restore above
        # already handles it (None → popped).

    def _run_main(self, session_id, payload_cwd):
        # read_hook_input caches stdin in a module-global; reset so this test's
        # payload is the one promoted.
        _ss.read_hook_input.__globals__["_cached_hook_input"] = None
        orig = (_ss.get_conductor_content, _ss.get_session_handoff,
                _ss.get_wiki_drift_warnings, _ss.get_loop_digest)
        _ss.get_conductor_content = lambda *a, **k: ""
        _ss.get_session_handoff = lambda *a, **k: ""
        _ss.get_wiki_drift_warnings = lambda *a, **k: ""
        _ss.get_loop_digest = lambda *a, **k: ""
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(json.dumps({
            "session_id": session_id, "source": "startup",
            "cwd": str(payload_cwd)}))
        buf = io.StringIO()
        sys.stdout = buf
        try:
            _ss.main()
        except SystemExit:
            pass
        finally:
            sys.stdin, sys.stdout = old_in, old_out
            (_ss.get_conductor_content, _ss.get_session_handoff,
             _ss.get_wiki_drift_warnings, _ss.get_loop_digest) = orig

    def test_startup_stamp_and_warning_land_in_project_logs(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root,
                                                            ignore_errors=True))
        # The conductor/tracks/ dir is what makes the payload cwd's ancestor a
        # "project" for the promotion walk.
        (root / "proj" / "conductor" / "tracks" / "alpha").mkdir(parents=True)

        self._run_main("promo1", root / "proj")

        logs = root / "proj" / ".conductor" / "logs"
        # The duration-metrics stamp (source=startup, non-compact).
        self.assertTrue((logs / ".session-promo1.start").exists(),
                        "session start stamp must land in <project>/.conductor/logs")
        # The backgrounding warning (env unset → warning path) resolves the
        # same data dir — both writers must sit on the promoted side.
        self.assertTrue((logs / "session-start.log").exists(),
                        "session-start.log must land in <project>/.conductor/logs")
        # And the promotion itself fired (tier-2 env set to the project root).
        self.assertEqual(os.environ.get("CLAUDE_PROJECT_DIR"),
                         str(root / "proj"))


if __name__ == "__main__":
    main()
