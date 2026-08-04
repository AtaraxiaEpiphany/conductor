"""Tests for ``lint-grill-contract-drift`` — the drift gate that flags a second,
restated grill-discipline home in prompt prose.

The grill discipline is single-homed in ``runtime/contracts/grill-discipline.md``;
brief, spec-reviewer, and discover adopt it by Reading that contract on demand,
never restating it. This gate flags a prompt file that uses the discipline's
signature mechanics (``four-quadrant`` / ``one question at a time``) without
citing the single home — the Bucket-B drift surface.

Load-bearing invariants under test:

- **Flags the mechanic without a ref** (the drift bug shape).
- **Clean when the contract is referenced** — via the Read-on-demand path OR the
  ``[[...]]`` wikilink form (the ref regex is stem-only).
- **Does NOT flag a bare "grill" mention** — ``new-track`` says "run brief for a
  grilled shared understanding"; it routes to the grill, doesn't perform it, so it
  has nothing to restate and no home to cite. The trigger is the signature
  mechanics, not the bare word.
- **Ignores fenced code blocks** — a mechanic named inside a code example is not a
  restatement.
- **The real tree is clean** — every prompt file that triggers also cites the home.
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
    "lgcd", _scripts / "lint-grill-contract-drift.py")
lgcd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lgcd)


class ScanText(TestCase):
    def test_flags_four_quadrant_without_ref(self):
        text = "Hold the four-quadrant stance for the grill."
        self.assertEqual(list(lgcd.scan_text(text)), ["four-quadrant"])

    def test_flags_four_quadrant_case_insensitive(self):
        # Headings carry the mechanic uppercase (## 2.5 FOUR-QUADRANT STANCE).
        text = "## 2.5 FOUR-QUADRANT STANCE"
        self.assertEqual(list(lgcd.scan_text(text)), ["FOUR-QUADRANT"])

    def test_flags_one_question_at_a_time_without_ref(self):
        text = "MUST — one question at a time, via AskUserQuestion."
        self.assertEqual(list(lgcd.scan_text(text)),
                         ["one question at a time"])

    def test_clean_when_contract_referenced_read_on_demand(self):
        # The live shape: trigger + a Read-on-demand pointer to the home.
        text = ("Hold the four-quadrant stance. Read "
                "${CLAUDE_PLUGIN_ROOT}/runtime/contracts/grill-discipline.md "
                "and follow it.")
        self.assertEqual(list(lgcd.scan_text(text)), [])

    def test_clean_when_contract_referenced_wikilink(self):
        # The ref regex is stem-only, so the [[...]] form also satisfies it.
        text = ("four-quadrant lens — see "
                "[[runtime/contracts/grill-discipline]] §2.")
        self.assertEqual(list(lgcd.scan_text(text)), [])

    def test_does_not_flag_bare_grill_mention(self):
        # THE NARROWING: "grilled" describing brief is not a restated discipline.
        # new-track says exactly this and must NOT be flagged (it routes to brief,
        # doesn't grill). A bare \bgrill\b trigger would false-positive here.
        text = ("No brief found — run /conductor:brief for a grilled shared "
                "understanding (recommended), or proceed with minimal Q&A?")
        self.assertEqual(list(lgcd.scan_text(text)), [])

    def test_ignores_fenced_code_block(self):
        text = textwrap.dedent("""\
            Intro line with no trigger word.

            ```markdown
            ## 2.5 FOUR-QUADRANT STANCE
            ```
            """)
        # The only trigger is inside the fenced block -> stripped -> no finding.
        self.assertEqual(list(lgcd.scan_text(text)), [])


class TreeIsClean(TestCase):
    """Regression guard: every prompt file that triggers also cites the home."""

    def test_no_findings(self):
        root = lgcd.get_plugin_root()
        findings = []
        for path in lgcd.watched_files(root):
            findings.extend(lgcd.scan_text(path.read_text(encoding="utf-8")))
        self.assertEqual(findings, [],
                         f"grill-discipline restated without citing its home: "
                         f"{findings}")


if __name__ == "__main__":
    main()
