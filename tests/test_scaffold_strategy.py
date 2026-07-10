"""Tests for ``scaffold-strategy`` — promotes setup §2.4 step-3 (testing/strategy.md
``{TEST_ROOT}`` substitution) into a self-verifying script.

Covers root resolution (analysis.json / ``--test-root`` override / greenfield
default), byte-exactness modulo the token, and the clean HALT-with-remediation
failure paths. Invokes the script end-to-end via subprocess (faithful L1).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "scaffold-strategy.py"
_TEMPLATE = _REPO / "templates" / "testing" / "strategy.md"

_FAKE_TPL = "root: `{TEST_ROOT}`\nsee {TEST_ROOT}/x\n"  # 2 tokens


def _run(cwd, *args):
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_ROOT", None)  # hermetic: force __file__-based fallback
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(cwd), env=env)


class ScaffoldStrategyTests(TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.out = self.root / "conductor" / "workflow" / "testing" / "strategy.md"
        self.tpl = self.root / "_tpl.md"
        self.tpl.write_text(_FAKE_TPL)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _analysis(self, obj):
        a = self.root / "conductor" / ".conductor" / "analysis.json"
        a.parent.mkdir(parents=True, exist_ok=True)
        a.write_text(json.dumps(obj))
        return a

    def test_brownfield_substitutes_test_dirs_zero(self):
        self._analysis({"structure": {"test_dirs": ["app/tests/"]}})
        r = _run(self.root, "--template", str(self.tpl), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.out.read_text()
        self.assertNotIn("{TEST_ROOT}", body)
        self.assertIn("app/tests", body)
        self.assertIn("2 tokens", r.stdout)

    def test_greenfield_defaults_to_tests(self):
        # no analysis.json present
        r = _run(self.root, "--template", str(self.tpl), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("root=tests", r.stdout)
        self.assertNotIn("{TEST_ROOT}", self.out.read_text())

    def test_override_flag_beats_analysis(self):
        self._analysis({"structure": {"test_dirs": ["app/tests/"]}})
        r = _run(self.root, "--template", str(self.tpl), "--out", str(self.out),
                 "--test-root", "__tests__")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.out.read_text()
        self.assertIn("__tests__", body)
        self.assertNotIn("app/tests", body)

    def test_byte_exact_modulo_token(self):
        self._analysis({"structure": {"test_dirs": ["tests"]}})
        _run(self.root, "--template", str(self.tpl), "--out", str(self.out))
        self.assertEqual(self.out.read_text(), _FAKE_TPL.replace("{TEST_ROOT}", "tests"))

    def test_corrupt_analysis_halts_with_remediation(self):
        a = self.root / "conductor" / ".conductor" / "analysis.json"
        a.parent.mkdir(parents=True, exist_ok=True)
        a.write_text("{bad json")
        r = _run(self.root, "--template", str(self.tpl), "--out", str(self.out))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)
        self.assertIn("--test-root", r.stderr)  # remediation hint closes the loop

    def test_missing_template_halts(self):
        r = _run(self.root, "--template", str(self.root / "nope.md"), "--out", str(self.out))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)

    def test_real_template_has_test_root_token(self):
        # guards template regression: if the token disappears the script becomes a silent no-op
        self.assertGreater(_TEMPLATE.read_text().count("{TEST_ROOT}"), 0)

    def test_real_template_substitutes_byte_exact(self):
        r = _run(self.root, "--template", str(_TEMPLATE), "--out", str(self.out),
                 "--test-root", "tests")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.out.read_text(), _TEMPLATE.read_text().replace("{TEST_ROOT}", "tests"))


if __name__ == "__main__":
    main()
