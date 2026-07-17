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

    def test_explicit_plugin_data_wins(self):
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.environ["CLAUDE_PLUGIN_DATA"] = tmp
        env = self._import_env()
        self.assertEqual(env.get_data_dir(), Path(tmp))

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
        """No env vars + no conductor/tracks/ in cwd → plugin/.data fail-safe."""
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # Bare dir, no conductor/tracks — must NOT match the cwd heuristic.
        os.chdir(tmp)
        env = self._import_env()
        # Should be the plugin fail-safe, NOT tmp/.conductor.
        self.assertNotEqual(env.get_data_dir(), Path(tmp) / ".conductor")
        self.assertTrue(str(env.get_data_dir()).endswith(os.path.join(".claude", "conductor-plugin", ".data"))
                        or env.get_data_dir().name == ".data")

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
