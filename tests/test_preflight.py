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


if __name__ == "__main__":
    main()
