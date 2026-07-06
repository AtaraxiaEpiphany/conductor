"""Tests for ``track-state registry-add`` — the canonical registry writer.

It exists to kill the format-drift bug class at the source: ``new-track`` §2.6
historically said only "Append entry to tracks.md" with NO format constraint, so
the model wrote freeform lines (no ``(link)``, plain bullet, bold id) that the
reader silently dropped — breaking auto-select AND explicit ``setup <track>``.
``registry-add`` appends the one canonical line (``- [<marker>] <desc>
(conductor/tracks/<id>/)``) from ``track-state.json``, idempotently, and
``new-track`` §2.6 now calls it instead of hand-writing.
"""
import io
import json
import os
import contextlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import core
from scripts.track_state.misc import cmd_registry_add, _iter_registry_entries

_scripts = Path(__file__).resolve().parent.parent / "scripts"
_CLI = _scripts / "track-state"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class _Project:
    """<root>/conductor/{tracks.md, tracks/<id>/track-state.json}."""

    def __init__(self, root):
        self.root = Path(root)
        self.cond = self.root / "conductor"
        self.tracks_dir = self.cond / "tracks"
        self.tracks_dir.mkdir(parents=True, exist_ok=True)
        self.registry = self.cond / "tracks.md"
        # /conductor:setup creates tracks.md before new-track ever runs, so the
        # file exists (possibly empty) at registry-add time — mirror that here.
        if not self.registry.exists():
            self.registry.write_text("")

    def make_track(self, track_id, status="new", description="A track"):
        td = self.tracks_dir / track_id
        td.mkdir(parents=True, exist_ok=True)
        core.save(str(td), {
            "track_id": track_id, "status": status,
            "description": description, "phases": []})
        return td

    def add(self, track_dir, tracks_md_path=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_registry_add(track_dir, tracks_md_path=tracks_md_path)
        return json.loads(buf.getvalue())


class RegistryAddTests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_appends_canonical_line(self):
        td = self.p.make_track("auth_20260706", "new", "Add SSO login")
        r = self.p.add(str(td), str(self.p.registry))
        self.assertTrue(r["ok"])
        self.assertTrue(r["appended"])
        self.assertEqual(r["marker"], " ")
        body = self.p.registry.read_text()
        self.assertIn("- [ ] Add SSO login (conductor/tracks/auth_20260706/)", body)

    def test_marker_reflects_in_progress(self):
        td = self.p.make_track("auth_20260706", "in_progress", "x")
        r = self.p.add(str(td), str(self.p.registry))
        self.assertEqual(r["marker"], "~")
        self.assertIn("- [~] x (conductor/tracks/auth_20260706/)",
                      self.p.registry.read_text())

    def test_idempotent_when_already_present(self):
        td = self.p.make_track("auth_20260706", "new", "x")
        first = self.p.add(str(td), str(self.p.registry))
        self.assertTrue(first["appended"])
        second = self.p.add(str(td), str(self.p.registry))
        self.assertTrue(second["ok"])
        self.assertTrue(second["already_present"])
        self.assertNotIn("appended", second)
        # registry body unchanged — exactly one line
        self.assertEqual(
            len(self.p.registry.read_text().strip().splitlines()), 1)

    def test_idempotent_even_for_freeform_existing_entry(self):
        # An entry that exists in a NON-canonical (link-less) shape is still
        # detected as present, so registry-add won't duplicate it.
        td = self.p.make_track("auth_20260706", "in_progress", "x")
        self.p.registry.write_text("- [~] auth_20260706\n")  # freeform, no link
        r = self.p.add(str(td), str(self.p.registry))
        self.assertTrue(r["already_present"])
        self.assertEqual(self.p.registry.read_text(), "- [~] auth_20260706\n")

    def test_description_falls_back_to_track_id(self):
        td = self.p.make_track("auth_20260706", "new", description="")
        # wipe description to simulate a state missing it
        st_path = td / "track-state.json"
        st = json.loads(st_path.read_text())
        del st["description"]
        st_path.write_text(json.dumps(st))
        self.p.add(str(td), str(self.p.registry))
        # falls back to track_id as the description text
        self.assertIn("- [ ] auth_20260706 (conductor/tracks/auth_20260706/)",
                      self.p.registry.read_text())

    def test_appended_line_round_trips_through_parser(self):
        td = self.p.make_track("auth_20260706", "in_progress", "Add SSO")
        self.p.add(str(td), str(self.p.registry))
        entries = _iter_registry_entries(
            self.p.registry.read_text(), str(self.p.cond))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["track_id"], "auth_20260706")
        self.assertEqual(entries[0]["marker"], "~")


class RegistryAddLocatorTests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        self.p = _Project(self.d)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_auto_locates_registry_from_cwd(self):
        td = self.p.make_track("auth_20260706", "new", "x")
        os.chdir(self.d)  # _find_registry walks up from CWD
        r = self.p.add(str(td))  # no tracks_md_path
        self.assertTrue(r["ok"])
        self.assertTrue(self.p.registry.exists())
        self.assertIn("auth_20260706", self.p.registry.read_text())

    def test_auto_locates_alongside_track_dir(self):
        # No CWD match, but the registry sits at the track's conductor root.
        td = self.p.make_track("auth_20260706", "new", "x")
        elsewhere = tempfile.mkdtemp()
        try:
            os.chdir(elsewhere)  # _find_registry finds nothing from here
            r = self.p.add(str(td))  # falls back to conductor_dir(td)/tracks.md
            self.assertTrue(r["ok"])
            self.assertIn("auth_20260706", self.p.registry.read_text())
        finally:
            shutil.rmtree(elsewhere, ignore_errors=True)

    def test_no_registry_returns_reason(self):
        td = self.p.make_track("auth_20260706", "new", "x")
        # delete the registry so neither locator finds it
        self.p.registry.unlink()
        bare = tempfile.mkdtemp()
        try:
            os.chdir(bare)
            r = self.p.add(str(td))
            self.assertFalse(r["ok"])
            self.assertEqual(r["reason"], "no_registry")
        finally:
            shutil.rmtree(bare, ignore_errors=True)


class RegistryAddCLITests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)
        self.td = self.p.make_track("auth_20260706", "new", "Add SSO")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_cli_appends_and_exits_zero(self):
        proc = _run([sys.executable, str(_CLI), "registry-add", str(self.td),
                     str(self.p.registry)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = json.loads(proc.stdout)
        self.assertTrue(r["ok"])
        self.assertIn("- [ ] Add SSO (conductor/tracks/auth_20260706/)",
                      self.p.registry.read_text())

    def test_cli_help_lists_registry_add(self):
        proc = _run([sys.executable, str(_CLI), "help", "registry-add"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("registry-add", proc.stdout)


if __name__ == "__main__":
    main()
