"""Wiring test for the three Workflow Studio subcommands.

A new ``track-state`` subcommand must be registered in FOUR places or the
machinery breaks:

1. ``cli.COMMAND_HELP`` — or ``help`` mis-reports it.
2. ``cli._COMMAND_GROUPS`` — or it is absent from grouped help (and from the
   global sanctioned-coverage gate in ``test_extract_track_dirs``).
3. ``cli._NO_TRACK_DIR_COMMANDS`` — these take no track-dir (they scan
   ``sys.argv[2:]`` for flags, like ``registry-doc``); without this entry the
   arity guard in ``main()`` rejects them with a phantom "missing <track-dir>".
4. ``pre-command-check._SANCTIONED_TS_SUBCOMMANDS`` — or the broad rm/mv/delete
   verb scan false-positives on the subcommand word.

This is exactly the drift the existing wiring tests (``test_registry_doc``,
``test_brief_cli``) pin for their commands; this one pins it for the studio.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))
_CLI = _scripts / "track-state"

from track_state import cli  # noqa: E402

# pre-command-check.py is a standalone script (not under the track_state
# package) — import it as a source file, mirroring test_registry_doc.
_spec = importlib.util.spec_from_file_location(
    "pre_command_check", _scripts / "pre-command-check.py")
_pcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pcc)

_STUDIO_CMDS = ("shape-studio", "registry-json", "registry-save")


class StudioWiring(TestCase):
    def test_all_four_registrations_present(self):
        grouped = {c for _name, cmds in cli._COMMAND_GROUPS for c in cmds}
        for sub in _STUDIO_CMDS:
            self.assertIn(sub, cli.COMMAND_HELP, f"{sub} missing from COMMAND_HELP")
            self.assertIn(sub, grouped, f"{sub} missing from _COMMAND_GROUPS")
            self.assertIn(sub, cli._NO_TRACK_DIR_COMMANDS,
                          f"{sub} missing from _NO_TRACK_DIR_COMMANDS")
            self.assertIn(sub, _pcc._SANCTIONED_TS_SUBCOMMANDS,
                          f"{sub} missing from _SANCTIONED_TS_SUBCOMMANDS")

    def test_grouped_under_workflow_studio(self):
        group = dict(cli._COMMAND_GROUPS)
        self.assertEqual(group["Workflow Studio"], list(_STUDIO_CMDS))


class RegistryJsonCliSmoke(TestCase):
    """``registry-json`` is a thin CLI wrapper over the data layer — pin it."""

    def test_emits_snapshot_json(self):
        proc = subprocess.run(
            [sys.executable, str(_CLI), "registry-json", "--which", "shapes"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        snap = json.loads(proc.stdout)
        self.assertEqual(snap["which"], "shapes")
        self.assertIn("merged", snap)
        self.assertIn("origins", snap)

    def test_bad_which_emits_error_envelope(self):
        proc = subprocess.run(
            [sys.executable, str(_CLI), "registry-json", "--which", "bogus"],
            capture_output=True, text=True,
        )
        snap = json.loads(proc.stdout)
        self.assertFalse(snap["ok"])


if __name__ == "__main__":
    main()
