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

    # --- Issue 1 regressions: state-lock false positive on sanctioned channels ---

    def test_sanctioned_append_handoff_with_remove_in_findings_not_flagged(self):
        """The explorer's exact channel — a read-only ``append-handoff`` whose
        heredoc JSON mentions remove/move/delete — must NOT trip the gate on an
        in_progress track (Layer A allowlist). This is the reported bug."""
        root = self._project("in_progress", "in_progress")
        command = (
            'track-state append-handoff "conductor/tracks/auth_20260706" '
            'P1 T1 --type explore << \'EOF\'\n'
            '{"findings":["remove the handler","move helper to utils","delete the cache"]}\n'
            'EOF'
        )
        self.assertEqual(self.pcc.find_track_state_violations(root, command), [])

    def test_heredoc_body_delete_ignored_for_nonsanctioned(self):
        """A non-sanctioned ``track-state <typo>`` still gets Layer B protection,
        but the delete verb inside its heredoc body is not scanned (argv-only)."""
        root = self._project("in_progress", "in_progress")
        command = (
            "track-state bogus handoff << 'EOF'\n"
            "delete this line\n"
            "EOF"
        )
        self.assertEqual(self.pcc.find_track_state_violations(root, command), [])

    def test_word_boundary_move_not_matching_remove(self):
        """``\\bmove\\b`` must not match ``remove``/``removed`` — the load-bearing
        false-positive vector (``'move' in 'remove'`` was True). Whole-word
        ``remove`` in a non-sanctioned argv is not a move op."""
        root = self._project("in_progress", "in_progress")
        command = 'track-state zz --reason "remove the stale cache"'
        self.assertEqual(self.pcc.find_track_state_violations(root, command), [])

    def test_non_sanctioned_rm_in_argv_still_flagged(self):
        """Layer B still catches a real rm in a non-sanctioned argv (regression
        guard that the allowlist did not neuter the broad scan entirely)."""
        root = self._project("in_progress", "in_progress")
        command = 'track-state bogus && rm conductor/tracks/auth_20260706/plan.md'
        v = self.pcc.find_track_state_violations(root, command)
        self.assertEqual(len(v), 1)
        self.assertIn("deletion", v[0])


class SetupScaffoldingHookCleanTests(TestCase):
    """setup §2.3/§2.4/§2.5 scaffold templates via ``cp``/``sed`` (not Read+Write)
    to keep template bodies out of the orchestrator context. These commands must
    NEVER trip the PreToolUse gates — neither the state-lock verb scan (even with
    an in_progress task) nor is_direct_track_state_modification. Locks the
    cp/sed-ification so a future hook change can't silently break setup."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "pre_command_check_setup", _scripts / "pre-command-check.py")
        cls.pcc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.pcc)

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        td = root / "conductor" / "tracks" / "x_20260708"
        td.mkdir(parents=True)
        (td / "track-state.json").write_text(json.dumps({
            "track_id": "x", "status": "in_progress",
            "phases": [{"name": "P1", "status": "in_progress",
                        "tasks": [{"name": "T1", "status": "in_progress"}]}]}))
        (root / "conductor" / "tracks.md").write_text(
            "- [~] x (conductor/tracks/x_20260708/)\n")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        self.root = root

    def _clean(self, command):
        return (self.pcc.find_track_state_violations(self.root, command) == []
                and not self.pcc.is_direct_track_state_modification(command))

    def test_cp_styleguides_clean(self):
        self.assertTrue(self._clean(
            'cp "${CLAUDE_PLUGIN_ROOT}/templates/code-styleguides/"{general,python}.md '
            'conductor/workflow/code-styleguides/'))

    def test_cp_devcommands_clean(self):
        self.assertTrue(self._clean(
            'cp "${CLAUDE_PLUGIN_ROOT}/templates/dev-commands/"python.md '
            'conductor/workflow/dev-commands/'))

    def test_sed_test_root_clean(self):
        self.assertTrue(self._clean(
            'sed -i "s/{TEST_ROOT}/tests/g" conductor/workflow/testing/strategy.md'))

    def test_cp_and_sed_timestamp_clean(self):
        cmd = ('cp "${CLAUDE_PLUGIN_ROOT}/templates/wiki-overview.md" conductor/overview.md\n'
               'sed -i "s/{TIMESTAMP}/2026-07-08T00:00:00Z/g" conductor/overview.md')
        self.assertTrue(self._clean(cmd))

    def test_claudemd_toc_append_clean(self):
        self.assertTrue(self._clean(
            'grep -q \'<!-- conductor:toc begin -->\' CLAUDE.md 2>/dev/null '
            '|| cat "${CLAUDE_PLUGIN_ROOT}/templates/claude-md-toc.md" >> CLAUDE.md'))

    def test_printf_tracks_registry_clean(self):
        self.assertTrue(self._clean(
            "[ -f conductor/tracks.md ] || printf '# Tracks Registry\\n' > conductor/tracks.md"))


class SanctionedSubcommandDriftTests(TestCase):
    """Guard against allowlist drift: ``_SANCTIONED_TS_SUBCOMMANDS`` must cover
    every subcommand the CLI actually exposes (``_COMMAND_GROUPS`` + the hidden
    ``setup`` alias + ``help``). The set is hardcoded in the hook (importing the
    full track_state package into every Bash PreToolUse call is too heavy), so
    this test is what catches the day a new subcommand ships without being
    allowlisted."""

    def test_allowlist_covers_every_cli_subcommand(self):
        from scripts.track_state.cli import _COMMAND_GROUPS
        declared = {cmd for _group, cmds in _COMMAND_GROUPS for cmd in cmds}
        declared |= {"setup", "help"}  # hidden alias + help command
        missing = declared - set(self.pcc._SANCTIONED_TS_SUBCOMMANDS)
        self.assertFalse(
            missing,
            f"_SANCTIONED_TS_SUBCOMMANDS is missing CLI subcommands: {sorted(missing)}")

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "pre_command_check_drift", _scripts / "pre-command-check.py")
        cls.pcc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.pcc)


if __name__ == "__main__":
    main()
