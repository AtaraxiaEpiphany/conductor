"""Tests for ``lint-prose-impl-leak`` — the drift gate that flags rotting
``file.ext:NN`` line-number citations in markdown prose.

Load-bearing invariants under test:

- **Detects the citation** in plain prose AND in an inline code span (where the
  live drift bugs hid — `` `sync.py:42-67` ``).
- **Does NOT fire inside fenced code blocks** — a ``.py:NN`` in a real command
  or URL is not a prose citation.
- **Does NOT fire on non-code extensions** — ``README.md:12``, a ``host:443``
  port, an ``::`` pytest node id are all exempt.
- **The real tree is clean** — the regression guard: after the fixes, scanning
  every watched markdown file yields zero findings.
"""
import importlib.util
import sys
import textwrap
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))
sys.path.insert(0, str(_scripts / "lib"))

_spec = importlib.util.spec_from_file_location(
    "lpil", _scripts / "lint-prose-impl-leak.py")
lpil = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lpil)


class ScanText(TestCase):
    def test_flags_plain_prose_citation(self):
        text = "The staging set lives in git_ops.py:183."
        self.assertEqual(list(lpil.scan_text(text)), ["git_ops.py:183"])

    def test_flags_inline_code_span(self):
        # The live drift-bug shape: `sync.py:42-67` inside backticks in prose.
        text = "positional matching (`sync.py:42-67`) is the trap."
        self.assertEqual(list(lpil.scan_text(text)), ["sync.py:42-67"])

    def test_ignores_fenced_code_block(self):
        text = textwrap.dedent("""\
            Intro line.

            ```bash
            # a real command, not a prose citation
            python3 scripts/foo.py 2>&1 | grep stuff.py:99
            ```

            Outro line.
            """)
        self.assertEqual(list(lpil.scan_text(text)), [])

    def test_ignores_non_code_extension(self):
        text = "see README.md:12 and https://host.example:443/path"
        self.assertEqual(list(lpil.scan_text(text)), [])

    def test_ignores_pytest_double_colon_nodeid(self):
        # `tests/test_x.py::test_a` is `::` not `:digit` — must not match.
        text = "run `pytest tests/test_x.py::test_a`"
        self.assertEqual(list(lpil.scan_text(text)), [])


class TreeIsClean(TestCase):
    """Regression guard: the watched markdown tree has zero rotting citations."""

    def test_no_findings(self):
        root = lpil.get_plugin_root()
        findings = []
        for path in lpil.watched_files(root):
            findings.extend(lpil.scan_text(path.read_text(encoding="utf-8")))
        self.assertEqual(findings, [], f"rotting line-number citations: {findings}")


if __name__ == "__main__":
    main()
