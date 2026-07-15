"""Tests for ``verify-strategy`` — the deterministic invariant checker for a
subagent-generated ``testing/strategy.md``.

The strategy-writer agent writes the doc freely from its live inspection of the
project; this checker is the deterministic backstop asserting the load-bearing
contract clauses downstream agents depend on. Covers the compliant doc (exit 0) and
each missing-clause rejection (exit 1 + HALT). Subprocess end-to-end, mirroring
``test_scaffold_strategy.py``'s faithful-L1 style.
"""
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path
from unittest import TestCase, main

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "verify-strategy.py"

# A fully-compliant doc: every invariant present. Edit copies of this to drop one
# clause at a time for the rejection cases.
_COMPLIANT = """\
# Testing Strategy

## Test Directory Structure

### test_root: `tests`

All test files MUST be created under `tests`. NEVER co-locate test files with source.

## File Placement Policy

### Mirror Rule

Source-to-test path mapping:

    src/{pkg}/{file}.py  ->  tests/{pkg}/test_{file}.py

### Existing Convention Rule

Before creating any test file, scan `tests/` for existing tests and follow the
established naming and placement convention.

## Coverage

- Threshold: >80% for all new code.
- Enforcement: Coverage gate (Firewall F3). No commit if below threshold.
"""


def _run(cwd, *args):
    env = dict(os.environ)
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(cwd), env=env)


class VerifyStrategyTests(TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, name, text):
        p = self.root / name
        p.write_text(text)
        return p

    def test_compliant_doc_passes(self):
        p = self._write("strat.md", _COMPLIANT)
        r = _run(self.root, "--out", str(p))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)

    def test_compliant_with_higher_coverage_passes(self):
        # >80% floor is a minimum; a higher stated threshold is fine.
        doc = _COMPLIANT.replace(">80%", ">=90%")
        p = self._write("strat.md", doc)
        r = _run(self.root, "--out", str(p))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_file_halts(self):
        r = _run(self.root, "--out", str(self.root / "nope.md"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)
        self.assertIn("missing", r.stderr)

    def test_empty_file_halts(self):
        p = self._write("strat.md", "")
        r = _run(self.root, "--out", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)
        self.assertIn("empty", r.stderr)

    def test_missing_test_root_rule_halts(self):
        doc = _COMPLIANT.replace("All test files MUST be created under `tests`.", "Tests go somewhere.")
        doc = doc.replace("### test_root: `tests`", "### layout")
        self.assertNotIn("must be created under", doc.lower())
        self.assertNotIn("test_root", doc.lower())
        self.assertNotIn("test root", doc.lower())
        p = self._write("strat.md", doc)
        r = _run(self.root, "--out", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)
        self.assertIn("test-root", r.stderr)

    def test_missing_mirror_rule_halts(self):
        doc = _COMPLIANT.replace(
            "Source-to-test path mapping:\n\n    src/{pkg}/{file}.py  ->  tests/{pkg}/test_{file}.py",
            "Follow standard placement.",
        )
        doc = doc.replace("### Mirror Rule", "### Placement")
        self.assertNotIn("mirror", doc.lower())
        self.assertNotIn("→", doc)
        self.assertNotIn("->", doc)
        self.assertNotIn("maps to", doc.lower())
        p = self._write("strat.md", doc)
        r = _run(self.root, "--out", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)
        self.assertIn("mirror", r.stderr.lower())

    def test_missing_coverage_gate_halts(self):
        doc = _COMPLIANT.replace("- Threshold: >80% for all new code.",
                                 "- Coverage is encouraged.")
        self.assertNotIn("%", doc)
        p = self._write("strat.md", doc)
        r = _run(self.root, "--out", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)
        self.assertIn("coverage gate", r.stderr.lower())

    def test_low_coverage_threshold_halts(self):
        # The generator may raise the threshold but never below the 80% floor.
        doc = _COMPLIANT.replace(">80%", ">50%")
        p = self._write("strat.md", doc)
        r = _run(self.root, "--out", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)
        self.assertIn("50%", r.stderr)
        self.assertIn("floor", r.stderr.lower())

    def test_missing_existing_convention_halts(self):
        doc = _COMPLIANT.replace(
            "Before creating any test file, scan `tests/` for existing tests and follow the\n"
            "established naming and placement convention.",
            "Pick a pattern and use it.",
        )
        doc = doc.replace("### Existing Convention Rule", "### Pattern")
        self.assertNotIn("convention", doc.lower())
        p = self._write("strat.md", doc)
        r = _run(self.root, "--out", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)
        self.assertIn("existing convention", r.stderr.lower())

    def test_bare_doc_reports_all_missing(self):
        # A doc missing every clause should list them all (not fail on the first).
        p = self._write("strat.md", "# Testing Strategy\njust some notes\n")
        r = _run(self.root, "--out", str(p))
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)
        self.assertIn("test-root", r.stderr)
        self.assertIn("mirror", r.stderr.lower())
        self.assertIn("coverage gate", r.stderr.lower())
        self.assertIn("existing convention", r.stderr.lower())


class VerifyStrategyDeterministicTemplateTests(TestCase):
    """The deterministic scaffold-strategy.py output must pass the checker — the two
    paths share the same contract invariants, so the generic doc is a known-good
    fixture."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_scaffold_output_passes_verify(self):
        template = _REPO / "templates" / "testing" / "strategy.md"
        out = self.root / "strategy.md"
        scaffold = _REPO / "scripts" / "scaffold-strategy.py"
        env = dict(os.environ)
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        # Produce the default (no-detection = full doc, all langs) rendered strategy.
        r = subprocess.run([sys.executable, str(scaffold), "--template", str(template),
                            "--out", str(out), "--test-root", "tests"],
                           capture_output=True, text=True, cwd=str(self.root), env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        # That rendered doc must satisfy every invariant the generator is held to.
        v = _run(self.root, "--out", str(out))
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("OK", v.stdout)


if __name__ == "__main__":
    main()
