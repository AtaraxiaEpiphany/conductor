"""Tests for ``lib.path_utils.extract_track_dirs`` — the shared registry->track-dir
enumerator used by 6 scripts (pre-command-check, on-stop-conductor,
state-consistency-check, session-end, on-batch-complete, lint-track-state).

Regression (BUG 3): the old extractor used markdown-link regex
``\\[.*?\\]\\(([^)]+))\\)``, which matched ONLY the section format's
``[text](link)``. It returned ``[]`` for the **checkbox** format (the default
``new-track`` output) and the **table** format — so the in-progress
``rm``/``mv``/``delete`` guard and the consistency/lint checks silently no-op'd
on the most common registry shape. It also returned section links as
``tracks/<id>`` (conductor-root-relative), which joined to the wrong
``<cwd>/tracks/<id>`` — so even section format was effectively broken.

The contract every caller relies on: paths are **project-root-relative**
(``cwd / d / "track-state.json"`` resolves), because ``find_tracks_registry``
only matches ``<cwd>/conductor/tracks.md`` (cwd IS the project root).
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts / "lib") not in sys.path:
    sys.path.insert(0, str(_scripts / "lib"))
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from lib.path_utils import extract_track_dirs, find_tracks_registry  # noqa: E402


def _project_with(registry_body, track_ids):
    """Build <root>/conductor/{tracks.md, tracks/<id>/track-state.json}."""
    root = Path(tempfile.mkdtemp())
    cond = root / "conductor"
    (cond / "tracks").mkdir(parents=True)
    for tid in track_ids:
        td = cond / "tracks" / tid
        td.mkdir(parents=True)
        (td / "track-state.json").write_text(
            json.dumps({"track_id": tid, "status": "in_progress", "phases": []}))
    (cond / "tracks.md").write_text(registry_body)
    return root


class ExtractTrackDirsFormatTests(TestCase):
    def test_checkbox_canonical_with_parens_in_description(self):
        # The default new-track format, with a parenthetical in the description
        # that used to be captured AS the link.
        root = _project_with(
            "- [~] Add SSO (OAuth2) login (conductor/tracks/sso-oauth2_20260706/)\n",
            ["sso-oauth2_20260706"])
        dirs = extract_track_dirs(find_tracks_registry(root))
        self.assertEqual(dirs, ["conductor/tracks/sso-oauth2_20260706"])
        # cwd / d must reach a real dir
        self.assertTrue((root / dirs[0]).is_dir())

    def test_all_three_formats_resolve(self):
        root = _project_with(
            "- [~] CB (conductor/tracks/cb_20260706/)\n"
            "### se_20260706\n\n- **Status:** in_progress\n"
            "- **Path:** [link](tracks/se_20260706/)\n"
            "| ta_20260706 | feature | in_progress | desc |\n",
            ["cb_20260706", "se_20260706", "ta_20260706"])
        dirs = extract_track_dirs(find_tracks_registry(root))
        self.assertEqual(dirs, [
            "conductor/tracks/cb_20260706",
            "conductor/tracks/se_20260706",
            "conductor/tracks/ta_20260706",
        ])
        for d in dirs:
            self.assertTrue((root / d / "track-state.json").exists(),
                            f"{d} did not resolve to a real track-state.json")

    def test_conductor_relative_checkbox_link_normalized(self):
        # Legacy "tracks/<id>/" (conductor-root-relative) -> conductor/tracks/<id>.
        root = _project_with(
            "- [~] leg (tracks/leg_20260706/)\n", ["leg_20260706"])
        self.assertEqual(extract_track_dirs(find_tracks_registry(root)),
                         ["conductor/tracks/leg_20260706"])


class ExtractTrackDirsEdgeTests(TestCase):
    def test_empty_link_ghost_skipped(self):
        root = _project_with(
            "- [~] ghost ()\n"
            "- [~] real (conductor/tracks/real_20260706/)\n",
            ["real_20260706"])
        self.assertEqual(extract_track_dirs(find_tracks_registry(root)),
                         ["conductor/tracks/real_20260706"])

    def test_non_track_markdown_table_ignored(self):
        # A generic table WITHOUT a status word is not a track registry table.
        root = _project_with(
            "| column | other |\n| --- | --- |\n| a | b |\n", [])
        self.assertEqual(extract_track_dirs(find_tracks_registry(root)), [])

    def test_absolute_and_url_links_dropped(self):
        root = _project_with(
            "- [~] ext (https://example.com/x/)\n"
            "- [~] abs (/absolute/dir/)\n"
            "- [~] real (conductor/tracks/real_20260706/)\n",
            ["real_20260706"])
        self.assertEqual(extract_track_dirs(find_tracks_registry(root)),
                         ["conductor/tracks/real_20260706"])

    def test_dedup_preserves_order(self):
        # Same track listed twice -> one entry.
        root = _project_with(
            "- [~] a (conductor/tracks/a_20260706/)\n"
            "- [~] a again (conductor/tracks/a_20260706/)\n",
            ["a_20260706"])
        self.assertEqual(extract_track_dirs(find_tracks_registry(root)),
                         ["conductor/tracks/a_20260706"])

    def test_missing_file_returns_empty(self):
        self.assertEqual(extract_track_dirs(Path("/no/such/tracks.md")), [])

    def test_checkbox_without_link_recovered(self):
        # new-track §2.6 wrote freeform entries with no (link); the old extractor
        # dropped them. The _\d{8} token fallback recovers.
        root = _project_with("- [~] auth_20260706\n", ["auth_20260706"])
        self.assertEqual(extract_track_dirs(find_tracks_registry(root)),
                         ["conductor/tracks/auth_20260706"])

    def test_plain_bullet_recovered(self):
        root = _project_with("- auth_20260706 — Add SSO login\n", ["auth_20260706"])
        self.assertEqual(extract_track_dirs(find_tracks_registry(root)),
                         ["conductor/tracks/auth_20260706"])

    def test_bold_id_recovered(self):
        root = _project_with("- [~] **auth_20260706**: login\n", ["auth_20260706"])
        self.assertEqual(extract_track_dirs(find_tracks_registry(root)),
                         ["conductor/tracks/auth_20260706"])


class StateLockIntegrationTests(TestCase):
    """End-to-end: the pre-command hook's in-progress rm/mv guard now fires for
    checkbox-format registries (the realistic default). Before the fix,
    ``find_track_state_violations`` returned ``[]`` for every checkbox registry.
    """

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "pre_command_check", _scripts / "pre-command-check.py")
        cls.pcc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.pcc)

    def _project(self, track_status, task_status):
        root = Path(tempfile.mkdtemp())
        td = root / "conductor" / "tracks" / "auth_20260706"
        td.mkdir(parents=True)
        (td / "track-state.json").write_text(json.dumps({
            "track_id": "auth_20260706", "status": track_status,
            "phases": [{"name": "P1", "status": "in_progress",
                        "tasks": [{"name": "T1", "status": task_status}]}]}))
        (root / "conductor" / "tracks.md").write_text(
            "- [~] Auth (OAuth2) login (conductor/tracks/auth_20260706/)\n")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        return root

    def test_checkbox_in_progress_rm_is_flagged(self):
        root = self._project("in_progress", "in_progress")
        v = self.pcc.find_track_state_violations(
            root, "rm -f conductor/tracks/auth_20260706/track-state.json")
        self.assertEqual(len(v), 1)
        self.assertIn("auth_20260706", v[0])
        self.assertIn("deletion", v[0])

    def test_checkbox_completed_rm_not_flagged(self):
        root = self._project("completed", "completed")
        v = self.pcc.find_track_state_violations(
            root, "rm -f conductor/tracks/auth_20260706/track-state.json")
        self.assertEqual(v, [])


if __name__ == "__main__":
    main()
