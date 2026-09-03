"""Tests for scripts/lib/env.py resolution tiers.

Covers the cwd-fallback tier added so logs/failures land in the *project*
``.conductor/`` dir even when ``CLAUDE_PROJECT_DIR`` is not injected (observed
empty in live session shells), instead of silently dropping into ``<plugin>/.data``.
"""
import os
import shutil
import tempfile
import unittest
from importlib import reload
from pathlib import Path


class GetDataDirResolutionTests(unittest.TestCase):
    def setUp(self):
        # Snapshot env vars we mutate so every test restores them.
        self._prior = {
            k: os.environ.get(k)
            for k in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PROJECT_DIR")
        }
        self._cwd = os.getcwd()

    def tearDown(self):
        for k, v in self._prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.chdir(self._cwd)

    def _import_env(self):
        # Re-import so module-level state (none today, but defensively) is fresh.
        import lib.env as env
        reload(env)
        return env

    def test_plugin_data_used_when_no_project_context(self):
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.environ["CLAUDE_PLUGIN_DATA"] = tmp
        env = self._import_env()
        self.assertEqual(env.get_data_dir(), Path(tmp))

    def test_project_dir_beats_plugin_data(self):
        """The live-session regression: Claude Code injects CLAUDE_PLUGIN_DATA
        into every plugin hook (the plugin's own state dir). Project context
        must outrank it — under the old order (plugin-data first) every live
        session's telemetry landed in ~/.claude/plugins/data/<plugin>/logs
        regardless of the project being worked on ("logs in the wrong place").
        """
        tmp_plugin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_plugin, ignore_errors=True)
        tmp_project = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_project, ignore_errors=True)
        os.environ["CLAUDE_PLUGIN_DATA"] = tmp_plugin
        os.environ["CLAUDE_PROJECT_DIR"] = tmp_project
        env = self._import_env()
        self.assertEqual(env.get_data_dir(), Path(tmp_project) / ".conductor")

    def test_cwd_tracks_beat_plugin_data(self):
        """Same regression via the cwd tier: a conductor/tracks/ cwd resolves
        the project even when the injected CLAUDE_PLUGIN_DATA is present."""
        tmp_plugin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_plugin, ignore_errors=True)
        os.environ["CLAUDE_PLUGIN_DATA"] = tmp_plugin
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (Path(tmp) / "conductor" / "tracks").mkdir(parents=True)
        os.chdir(tmp)
        env = self._import_env()
        self.assertEqual(env.get_data_dir(), Path(tmp) / ".conductor")

    def test_project_dir_env_used(self):
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        env = self._import_env()
        self.assertEqual(env.get_data_dir(), Path(tmp) / ".conductor")

    def test_cwd_fallback_when_tracks_present(self):
        """No env vars set + conductor/tracks/ in cwd → cwd/.conductor.

        This is the regression guard: without the cwd tier the resolver falls
        straight through to <plugin>/.data, landing logs in the plugin tree.
        """
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # Simulate a real project: a conductor/ tree with a tracks subdir.
        (Path(tmp) / "conductor" / "tracks").mkdir(parents=True)
        os.chdir(tmp)
        env = self._import_env()
        self.assertEqual(env.get_data_dir(), Path(tmp) / ".conductor")

    def test_no_cwd_fallback_without_tracks(self):
        """No env vars + no conductor/tracks/ in cwd → plugin/.data fail-safe.

        The fallback is the LAST resort — it collides across concurrent
        projects, so it must be LOUD (a stderr warning) rather than silent.
        Assert the fallback is used AND the warning fired.
        """
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # Bare dir, no conductor/tracks — must NOT match the cwd heuristic.
        os.chdir(tmp)
        env = self._import_env()
        # Reset the one-shot warning guard so this test observes the fire.
        env._PLUGIN_FALLBACK_WARNED = False
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            data_dir = env.get_data_dir()
        # Should be the plugin fail-safe, NOT tmp/.conductor.
        self.assertNotEqual(data_dir, Path(tmp) / ".conductor")
        self.assertTrue(str(data_dir).endswith(os.path.join(".claude", "conductor-plugin", ".data"))
                        or data_dir.name == ".data")
        # The trap must be visible, not silent.
        self.assertIn("SHARED plugin dir", buf.getvalue())

    def test_infer_project_dir_from_payload_cwd(self):
        """Payload cwd with conductor/tracks/ promotes to CLAUDE_PROJECT_DIR.

        This is the core concurrency fix: a hook whose PROCESS cwd is the
        plugin dir (common) still resolves the PROJECT from the payload, so
        logs land project-scoped instead of in the shared plugin dir. Without
        it, two concurrent projects collide into one unreadable log.
        """
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        env = self._import_env()
        project = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        # Real project shape: conductor/tracks present.
        (Path(project) / "conductor" / "tracks").mkdir(parents=True)
        # Process cwd is ELSEWHERE (simulating a hook firing from the plugin).
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        os.chdir(elsewhere)
        # Payload carries the project cwd.
        resolved = env.infer_project_dir_from_payload({"cwd": project})
        self.assertEqual(resolved, project)
        self.assertEqual(os.environ["CLAUDE_PROJECT_DIR"], project)
        # And get_data_dir now resolves the project, NOT the plugin fallback.
        self.assertEqual(env.get_data_dir(), Path(project) / ".conductor")

    def test_project_dir_env_beats_cwd_heuristic(self):
        """Explicit CLAUDE_PROJECT_DIR takes precedence over the cwd heuristic."""
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        # cwd has tracks, but explicit env points elsewhere — env wins.
        (Path(other) / "conductor" / "tracks").mkdir(parents=True)
        os.chdir(other)
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        env = self._import_env()
        self.assertEqual(env.get_data_dir(), Path(tmp) / ".conductor")


if __name__ == "__main__":
    unittest.main()
