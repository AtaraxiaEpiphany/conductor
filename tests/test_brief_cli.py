"""Tests for the brief resume-marker CLI (``scripts/track_state/brief.py``).

The marker ``<track_dir>/.conductor/brief-progress.json`` is the /conductor:brief
counterpart of new-track's resume marker: a transient (gitignored, deleted at §5
hand-off) pre-state record that lets an interrupted brief run be detected. These
pin the code contract: idempotent init, idempotent finalize, finalize's
brief_present check, a tolerant reader (corrupt/missing → no crash), and the
resume directive. Mirrors tests/test_new_track_progress.py.
"""
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import brief as br

ROOT = Path(__file__).resolve().parent.parent


def run(fn, *args, **kwargs):
    """Call a cmd fn, return its parsed stdout JSON."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    return json.loads(buf.getvalue())


class _MarkerTestCase(TestCase):
    """Each test gets a temp track dir under a temp conductor root."""

    def setUp(self):
        self._prev_cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="brief-test-")
        self.td = str(Path(self.tmp) / "conductor" / "tracks" / "foo_20260721")

    def tearDown(self):
        os.chdir(self._prev_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def marker_path(self):
        return Path(self.td) / ".conductor" / br._BRIEF_MARKER

    def read_marker_file(self):
        return json.loads(self.marker_path().read_text())


class BriefInitTests(_MarkerTestCase):
    def test_init_creates_marker_with_correct_shape(self):
        out = run(br.cmd_brief_init, self.td, "foo_20260721")
        self.assertTrue(out["ok"])
        self.assertEqual(out["action"], "created")
        self.assertEqual(out["track_id"], "foo_20260721")
        data = self.read_marker_file()
        self.assertEqual(data["track_id"], "foo_20260721")
        self.assertEqual(data["track_dir"], self.td)
        self.assertFalse(data["committed"])

    def test_init_creates_track_dir_and_conductor_dir(self):
        self.assertFalse(Path(self.td).exists())
        run(br.cmd_brief_init, self.td, "foo_20260721")
        self.assertTrue(self.marker_path().exists())

    def test_init_is_idempotent(self):
        """A resumed run never clobbers its own progress — re-init is a no-op."""
        run(br.cmd_brief_init, self.td, "foo_20260721")
        out = run(br.cmd_brief_init, self.td, "different_id_20260721")
        self.assertTrue(out["ok"])
        self.assertEqual(out["action"], "exists")
        # The original track_id survives the second init.
        self.assertEqual(out["track_id"], "foo_20260721")
        self.assertEqual(self.read_marker_file()["track_id"], "foo_20260721")


class BriefFinalizeTests(_MarkerTestCase):
    def test_finalize_removes_marker(self):
        run(br.cmd_brief_init, self.td, "foo_20260721")
        self.assertTrue(self.marker_path().exists())
        out = run(br.cmd_brief_finalize, self.td)
        self.assertTrue(out["ok"])
        self.assertTrue(out["finalized"])
        self.assertTrue(out["removed"])
        self.assertFalse(self.marker_path().exists())

    def test_finalize_is_idempotent(self):
        """A missing marker is a no-op success (re-finalize after partial run)."""
        out = run(br.cmd_brief_finalize, self.td)
        self.assertTrue(out["ok"])
        self.assertFalse(out["removed"])

    def test_finalize_reports_brief_present_true(self):
        run(br.cmd_brief_init, self.td, "foo_20260721")
        (Path(self.td) / "brief.md").write_text("# brief")
        out = run(br.cmd_brief_finalize, self.td)
        self.assertTrue(out["brief_present"])

    def test_finalize_reports_brief_present_false(self):
        """Finalize is the cleanup step — it reports a missing brief but does
        not hard-fail (so cleanup always succeeds; the skill warns)."""
        run(br.cmd_brief_init, self.td, "foo_20260721")
        out = run(br.cmd_brief_finalize, self.td)
        self.assertTrue(out["ok"])
        self.assertFalse(out["brief_present"])


class BriefReaderTests(_MarkerTestCase):
    def test_corrupt_marker_returns_none(self):
        """A half-written file never crashes callers — the reader is tolerant."""
        cdir = Path(self.td) / ".conductor"
        cdir.mkdir(parents=True, exist_ok=True)
        self.marker_path().write_text("{ not json")
        self.assertIsNone(br._brief_read_marker(self.td))

    def test_missing_marker_returns_none(self):
        self.assertIsNone(br._brief_read_marker(self.td))


class BriefResumeTests(TestCase):
    """Resume needs a registry on disk and resolves it from CWD
    (``_find_registry``), so these chdir into a temp project."""

    def setUp(self):
        self._prev_cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="brief-resume-")
        os.chdir(self.tmp)
        (Path(self.tmp) / "conductor").mkdir()
        (Path(self.tmp) / "conductor" / "tracks.md").write_text("# Tracks\n")

    def tearDown(self):
        os.chdir(self._prev_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resume_none_when_no_marker(self):
        out = run(br.cmd_brief_resume)
        self.assertEqual(out["action"], "none")

    def test_resume_finds_committed_false_marker(self):
        td = Path(self.tmp) / "conductor" / "tracks" / "foo_20260721"
        run(br.cmd_brief_init, str(td), "foo_20260721")
        out = run(br.cmd_brief_resume)
        self.assertEqual(out["action"], "resume")
        self.assertEqual(len(out["candidates"]), 1)
        cand = out["candidates"][0]
        self.assertEqual(cand["track_id"], "foo_20260721")
        self.assertFalse(cand["brief_present"])

    def test_resume_ignores_committed_marker(self):
        """A committed:true marker is stale (finalize should have removed it) —
        never resume from one."""
        td = Path(self.tmp) / "conductor" / "tracks" / "foo_20260721"
        run(br.cmd_brief_init, str(td), "foo_20260721")
        # Simulate a stale committed marker finalize failed to delete.
        mpath = td / ".conductor" / br._BRIEF_MARKER
        data = json.loads(mpath.read_text())
        data["committed"] = True
        mpath.write_text(json.dumps(data))
        out = run(br.cmd_brief_resume)
        self.assertEqual(out["action"], "none")


class BriefCliWiringTests(TestCase):
    """The brief subcommands are registered in the CLI surface (help, group,
    sanctioned-subcommand allowlist). Mirrors test_split_command wiring tests."""

    def test_brief_subcommands_listed_in_help(self):
        from scripts.track_state.cli import COMMAND_HELP
        for sub in ("brief-init", "brief-finalize", "brief-resume"):
            self.assertIn(sub, COMMAND_HELP)

    def test_brief_group_exists(self):
        from scripts.track_state.cli import _COMMAND_GROUPS
        groups = {name: cmds for name, cmds in _COMMAND_GROUPS}
        self.assertIn("Brief", groups)
        for sub in ("brief-init", "brief-finalize", "brief-resume"):
            self.assertIn(sub, groups["Brief"])

    def test_brief_subcommands_sanctioned(self):
        """Guard the repeated gotcha: a new track-state subcommand must be added
        to BOTH _COMMAND_GROUPS AND _SANCTIONED_TS_SUBCOMMANDS (else the
        pre-command hook flags it). The drift test in test_extract_track_dirs
        covers groups→sanctioned; this asserts the brief entries specifically."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pcc_brief", ROOT / "scripts" / "pre-command-check.py")
        pcc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pcc)
        for sub in ("brief-init", "brief-finalize", "brief-resume"):
            self.assertIn(sub, pcc._SANCTIONED_TS_SUBCOMMANDS,
                          f"{sub} missing from _SANCTIONED_TS_SUBCOMMANDS")

    def test_brief_dispatch_branches_exist(self):
        """The cli.main dispatch routes brief-init/finalize/resume to the cmd fns."""
        src = (ROOT / "scripts" / "track_state" / "cli.py").read_text()
        self.assertIn('cmd == "brief-init"', src)
        self.assertIn('cmd == "brief-finalize"', src)
        self.assertIn('cmd == "brief-resume"', src)


if __name__ == "__main__":
    main()
