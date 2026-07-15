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

# Fake template exercising the language marker: a Go-only row, a shared JS/TS row,
# and an untagged (language-agnostic) line.
_FAKE_TPL_LANG = (
    "test_root: `{TEST_ROOT}`\n"                                   # untagged, always kept
    "| Go | `{name}_test.go` | <!-- lang:go -->\n"                 # go only
    "| JS/TS | `{module}.test.ts` | <!-- lang:javascript typescript -->\n"
    "core rule always present\n"                                   # untagged, always kept
)


def _strip_lang_markers(text):
    """Mirror the script's marker stripping for byte-exact expectations.

    Matches ``filter_by_language``: remove a trailing ``<!-- lang:... -->`` marker
    and any spaces/tabs preceding it on the same line, preserving newlines and
    blank separator lines (NOT the greedy ``\\s*$`` which would eat blank lines).
    """
    import re
    return re.sub(r"[ \t]*<!--\s*lang:[a-z+#0-9 ]*?\s*-->[ \t]*$", "", text, flags=re.MULTILINE)


def _run(cwd, *args):
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_ROOT", None)  # hermetic: force __file__-based fallback
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          capture_output=True, text=True, cwd=str(cwd), env=env)


def _run_with_env(cwd, env_overrides, *args):
    """Like ``_run`` but lets a test inject a specific ``CLAUDE_PLUGIN_ROOT``.

    Used to pin the priority-inversion fix: a stale/wrong env var must NOT
    override the always-correct ``__file__``-based root (env.py falls back and
    warns rather than HALTing on a template path it computed wrong).
    """
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.update(env_overrides)
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
        self.assertIn("root=app/tests", r.stdout)

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
        # No language detection → full doc, markers stripped, token resolved.
        # Byte-exact against the template with {TEST_ROOT}→tests and every
        # `<!-- lang:... -->` marker removed.
        r = _run(self.root, "--template", str(_TEMPLATE), "--out", str(self.out),
                 "--test-root", "tests")
        self.assertEqual(r.returncode, 0, r.stderr)
        expected = _strip_lang_markers(_TEMPLATE.read_text()).replace("{TEST_ROOT}", "tests")
        self.assertEqual(self.out.read_text(), expected)

    def test_real_template_filters_to_python(self):
        # With --languages python the emitted doc carries the Python placement
        # row and excludes the other 7 languages' rows, with no markup surviving.
        r = _run(self.root, "--template", str(_TEMPLATE), "--out", str(self.out),
                 "--test-root", "tests", "--languages", "python")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.out.read_text()
        self.assertNotIn("{TEST_ROOT}", body)
        self.assertNotIn("<!-- lang:", body)
        # Python row present; representative rows from other langs dropped.
        self.assertIn("test_{module}.py", body)
        self.assertNotIn("`{name}_test.go`", body)          # Go
        self.assertNotIn("`{name}_test.dart`", body)        # Dart
        self.assertNotIn("`{Class}Tests.cs`", body)         # C#
        # Python mirror example present; non-Python examples dropped.
        self.assertIn("test_user.py", body)
        self.assertNotIn("Button.test.tsx", body)
        # Python cache bullet present; non-Python cache bullets dropped.
        self.assertIn("PYTHONPYCACHEPREFIX", body)
        self.assertNotIn("GOCACHE", body)
        self.assertIn("langs=python", r.stdout)

    def test_wrong_plugin_root_env_falls_back_and_warns(self):
        # Priority-inversion regression: a stale/wrong CLAUDE_PLUGIN_ROOT must
        # NOT make the script HALT on a missing template. get_plugin_root() now
        # treats the env var as a hint validated against the __file__-derived
        # ground truth, falling back (with a stderr warning) when they disagree.
        r = _run_with_env(self.root, {"CLAUDE_PLUGIN_ROOT": "/tmp/nonexistent-plugin"},
                          "--template", str(self.tpl), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("{TEST_ROOT}", self.out.read_text())  # substitution still ran
        self.assertIn("does not match", r.stderr)              # discrepancy surfaced

    def test_matching_plugin_root_env_honored_silently(self):
        # When CLAUDE_PLUGIN_ROOT points at the real plugin root, it is honored
        # and produces no warning (the common production path).
        r = _run_with_env(self.root, {"CLAUDE_PLUGIN_ROOT": str(_REPO)},
                          "--template", str(self.tpl), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("does not match", r.stderr)


class ScaffoldLanguageFilterTests(TestCase):
    """Language-aware filtering: marker drop/keep, detection, fallback, aliases."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.out = self.root / "strategy.md"
        self.tpl = self.root / "_tpl.md"
        self.tpl.write_text(_FAKE_TPL_LANG)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _analysis(self, obj):
        a = self.root / "conductor" / ".conductor" / "analysis.json"
        a.parent.mkdir(parents=True, exist_ok=True)
        a.write_text(json.dumps(obj))
        return a

    def _run(self, *args):
        env = dict(os.environ)
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        return subprocess.run([sys.executable, str(_SCRIPT), *args],
                              capture_output=True, text=True, cwd=str(self.root), env=env)

    def test_filter_python_drops_go_and_jsts(self):
        r = self._run("--template", str(self.tpl), "--out", str(self.out), "--languages", "python")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.out.read_text()
        self.assertNotIn("`{name}_test.go`", body)   # go row dropped
        self.assertNotIn("`{module}.test.ts`", body)  # js/ts row dropped
        self.assertIn("core rule always present", body)  # untagged kept
        self.assertIn("test_root: `tests`", body)     # token resolved
        self.assertNotIn("<!-- lang:", body)          # no markup survives

    def test_filter_go_keeps_go_row_stripped(self):
        r = self._run("--template", str(self.tpl), "--out", str(self.out), "--languages", "go")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.out.read_text()
        self.assertIn("`{name}_test.go`", body)       # go row kept
        self.assertNotIn("`{module}.test.ts`", body)  # js/ts row dropped
        self.assertNotIn("<!-- lang:go -->", body)    # marker stripped

    def test_filter_typescript_keeps_shared_jsts_row(self):
        # A row tagged with both javascript+typescript survives on a partial match.
        r = self._run("--template", str(self.tpl), "--out", str(self.out), "--languages", "typescript")
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.out.read_text()
        self.assertIn("`{module}.test.ts`", body)
        self.assertNotIn("`{name}_test.go`", body)

    def test_no_languages_no_analysis_keeps_all(self):
        # Greenfield: no analysis.json, no --languages → full doc (markers stripped).
        r = self._run("--template", str(self.tpl), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.out.read_text()
        self.assertIn("`{name}_test.go`", body)       # kept
        self.assertIn("`{module}.test.ts`", body)     # kept
        self.assertIn("langs=all (no detection)", r.stdout)
        self.assertNotIn("<!-- lang:", body)

    def test_analysis_drives_detection_brownfield(self):
        # analysis.json languages[] filters exactly as --languages would.
        self._analysis({"languages": [{"name": "Python"}, {"name": "Go"}],
                        "structure": {"test_dirs": ["tests"]}})
        r = self._run("--template", str(self.tpl), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.out.read_text()
        self.assertIn("`{name}_test.go`", body)       # go kept
        self.assertNotIn("`{module}.test.ts`", body)  # js/ts dropped (not detected)
        self.assertIn("langs=go,python", r.stdout)

    def test_empty_languages_list_keeps_all(self):
        self._analysis({"languages": [], "structure": {"test_dirs": ["tests"]}})
        r = self._run("--template", str(self.tpl), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.out.read_text()
        self.assertIn("`{name}_test.go`", body)       # no detection → keep all
        self.assertIn("langs=all (no detection)", r.stdout)

    def test_unknown_language_name_keeps_all(self):
        # An unrecognized stack (Rust) yields no known keys → keep-all fallback.
        self._analysis({"languages": [{"name": "Rust"}], "structure": {"test_dirs": ["tests"]}})
        r = self._run("--template", str(self.tpl), "--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        body = self.out.read_text()
        self.assertIn("`{name}_test.go`", body)
        self.assertIn("langs=all (no detection)", r.stdout)

    def test_alias_normalization_cpp_csharp(self):
        # `c++`/`c#` on the CLI normalize to the cpp/csharp marker keys.
        cpp_tpl = self.root / "_cpp.md"
        cpp_tpl.write_text("core\n| C++ | `_test.cc` | <!-- lang:cpp -->\n| C# | `Tests.cs` | <!-- lang:csharp -->\n")
        out2 = self.root / "strategy2.md"
        env = dict(os.environ)
        env.pop("CLAUDE_PLUGIN_ROOT", None)
        r = subprocess.run([sys.executable, str(_SCRIPT), "--template", str(cpp_tpl),
                            "--out", str(out2), "--languages", "c++"],
                           capture_output=True, text=True, cwd=str(self.root), env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        body = out2.read_text()
        self.assertIn("`_test.cc`", body)             # cpp row kept
        self.assertNotIn("`Tests.cs`", body)          # csharp row dropped

    def test_unknown_cli_language_halts(self):
        r = self._run("--template", str(self.tpl), "--out", str(self.out), "--languages", "klingon")
        self.assertEqual(r.returncode, 1)
        self.assertIn("HALT", r.stderr)
        self.assertIn("--languages", r.stderr.lower())
