"""Tests for track-state preflight — the centralized track-core setup check."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
_CLI = _scripts / "track-state"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class PreflightTests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _files(self, *names):
        for n in names:
            p = Path(self.d, n)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")

    def _state(self, body):
        Path(self.d, "track-state.json").write_text(body)

    def _preflight(self):
        proc = _run([sys.executable, str(_CLI), "preflight", self.d])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_all_present_ok(self):
        self._files("spec.md", "plan.md")
        self._state(json.dumps({"phases": []}))
        r = self._preflight()
        self.assertTrue(r["ok"])
        self.assertEqual(r["missing"], [])
        self.assertFalse(r["invalid_state"])

    def test_missing_files_reported(self):
        self._files("spec.md")  # plan.md + track-state.json missing
        r = self._preflight()
        self.assertFalse(r["ok"])
        self.assertIn("plan.md", r["missing"])
        self.assertIn("track-state.json", r["missing"])

    def test_nothing_present(self):
        r = self._preflight()
        self.assertFalse(r["ok"])
        self.assertEqual(set(r["missing"]), {"spec.md", "plan.md", "track-state.json"})

    def test_invalid_state_flagged(self):
        self._files("spec.md", "plan.md")
        self._state("{not valid json")
        r = self._preflight()
        self.assertFalse(r["ok"])
        self.assertTrue(r["invalid_state"])
        self.assertEqual(r["missing"], [])  # files exist; state is the problem

    def test_exits_zero_even_on_failure(self):
        # Like validate, preflight reports via `ok` and never non-zero exits.
        self._files("spec.md")
        proc = _run([sys.executable, str(_CLI), "preflight", self.d])
        self.assertEqual(proc.returncode, 0)

    def test_help_lists_preflight(self):
        proc = _run([sys.executable, str(_CLI), "help", "preflight"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("preflight", proc.stdout)

    def test_no_hint_for_normal_track(self):
        self._files("spec.md", "plan.md")
        self._state(json.dumps({"phases": []}))
        r = self._preflight()
        self.assertIsNone(r.get("hint"))

    def test_hint_when_track_dir_is_the_registry_file(self):
        # The original bug: model passes conductor/tracks.md (the registry file)
        # as the track_dir. preflight must surface a targeted hint.
        self._files("tracks.md")  # a file named tracks.md, no core files
        proc = _run([sys.executable, str(_CLI), "preflight", str(Path(self.d, "tracks.md"))])
        self.assertEqual(proc.returncode, 0)
        r = json.loads(proc.stdout)
        self.assertFalse(r["ok"])
        self.assertIsNotNone(r.get("hint"))
        self.assertIn("registry", r["hint"].lower())

    def test_hint_when_track_dir_is_conductor_root(self):
        # A dir that holds tracks.md but no spec/plan/state — the conductor root,
        # not a track dir.
        (Path(self.d) / "tracks.md").write_text("x")
        proc = _run([sys.executable, str(_CLI), "preflight", self.d])
        self.assertEqual(proc.returncode, 0)
        r = json.loads(proc.stdout)
        self.assertFalse(r["ok"])
        self.assertIsNotNone(r.get("hint"))
        self.assertIn("conductor root", r["hint"])


class PreflightWorkflowFilesTests(TestCase):
    """Project-level workflow files (conductor/workflow/) are now gated by
    preflight, fail-open when no conductor root is locatable.

    These live at the conductor ROOT, not in the track dir, so the test builds a
    real project layout (conductor/tracks.md + conductor/tracks/foo/…) instead
    of the bare-temp-dir layout the core-file tests above use.
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _project(self, *, workflow=True):
        """One-track project layout under self.d; returns the track-dir path."""
        root = Path(self.d)
        track = root / "conductor" / "tracks" / "foo"
        track.mkdir(parents=True, exist_ok=True)
        (root / "conductor" / "tracks.md").write_text("- [foo](tracks/foo)\n")
        for f in ("spec.md", "plan.md"):
            (track / f).write_text("x")
        (track / "track-state.json").write_text(json.dumps({"phases": []}))
        wf = root / "conductor" / "workflow"
        if workflow:
            wf.mkdir(parents=True, exist_ok=True)
            (wf / "index.md").write_text("x")
        return str(track)

    def _preflight(self, track):
        proc = _run([sys.executable, str(_CLI), "preflight", track])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_workflow_present_ok(self):
        r = self._preflight(self._project(workflow=True))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["missing_workflow"], [])
        self.assertEqual(r["missing"], [])

    def test_missing_workflow_files_reported(self):
        r = self._preflight(self._project(workflow=False))
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["missing_workflow"], ["workflow/index.md"])
        # track-core files are fine — only the project-level index is missing
        self.assertEqual(r["missing"], [])

    def test_fail_open_without_conductor_root(self):
        # Bare track dir with NO tracks.md ancestor → workflow check is skipped
        # (fail-open). A non-standard layout can never HALT setup via this gate.
        d = tempfile.mkdtemp()
        try:
            for f in ("spec.md", "plan.md"):
                (Path(d) / f).write_text("x")
            (Path(d) / "track-state.json").write_text(json.dumps({"phases": []}))
            proc = _run([sys.executable, str(_CLI), "preflight", d])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            r = json.loads(proc.stdout)
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["missing_workflow"], [])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
