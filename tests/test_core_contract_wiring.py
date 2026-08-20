"""Wiring tests for ``runtime/core-contract.md``'s Anti-Patterns table and
firewall openings (interaction-layer-import D5.3, writing-for-agents sweep).

Every prohibition is paired with its **positive target** — a "Do instead"
column beside the Violation column — because a model told only what NOT to do
invents its own target, often the wrong one. The pairing discipline:

- The **Violation strings stay byte-identical**.
  ``tests/test_on_subagent_start.py::_FLOOR_FORBIDDEN`` curates the subagent
  floor against V5/V9 phrasing and its comments point at "core-contract.md
  V5/V9"; a silent reword of the column orphans that cross-reference.
- **F2/F3 open with the positive form** (the negation rides along), so the
  two most-violated gates lead with what to do.
- The pinned phrase discipline: each pinned phrase must sit contiguous on ONE
  line (grep-style ``assertIn`` does not span line wraps).
"""
import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "runtime" / "core-contract.md"

# The byte-identical Violation column (the floor curation cross-references
# V5/V9; reword any of these and the lockstep breaks silently).
VIOLATIONS = {
    "V1": "Implementation before failing test",
    "V2": "Non-transient marker without `[sha]`",
    "V3": "Skip coverage verification",
    "V4": "Skip Steps 4-7 (TDD-owing tasks)",
    "V5": "Bundle test + implementation in one commit",
    "V6": "Skip phase checkpoint",
    "V7": "Reconstruct/overwrite EXISTING state from plan.md",
    "V8": "More than ONE parent `[~]` + ONE child `[~]` simultaneously",
    "V9": "Skip git notes",
    "V10": "Non-conventional commit message",
    "V11": "Subagent modifying state",
}

RE_ROW = re.compile(r"^\|\s*(V\d+)\s*\|([^|]+)\|([^|]+)\|([^|]+)\|\s*$", re.M)


def _anti_patterns_text():
    text = CONTRACT.read_text(encoding="utf-8")
    start = text.index("## Anti-Patterns")
    return text[start:text.index("##", start + 1)]


def _rows():
    return {code: (violation.strip(), do.strip(), firewall.strip())
            for code, violation, do, firewall
            in RE_ROW.findall(_anti_patterns_text())}


class PositiveTargetPairingTests(TestCase):
    def setUp(self):
        self.rows = _rows()

    def test_every_row_has_a_do_instead_cell(self):
        header = _anti_patterns_text()
        self.assertIn("| Code | Violation", header)
        self.assertIn("Do instead", header)
        self.assertEqual(len(self.rows), len(VIOLATIONS),
                         "expected 11 V-rows; parser rot or a row edit?")

    def test_violation_column_byte_identical(self):
        for code, violation in VIOLATIONS.items():
            self.assertIn(code, self.rows, f"missing row {code}")
            self.assertEqual(
                self.rows[code][0], violation,
                f"{code} Violation string drifted (the floor curation "
                f"cross-references V5/V9 phrasing)")

    def test_every_do_instead_cell_is_an_actionable_positive(self):
        # A positive target starts with a verb (imperative) — not "don't",
        # not "never", not a restatement of the violation.
        for code, (_, do, _) in sorted(self.rows.items()):
            self.assertTrue(do, f"{code} has an empty Do instead cell")
            first = do.split()[0].lower()
            self.assertNotIn(first, ("don't", "never", "no", "not"),
                             f"{code} Do instead is negative: {do!r}")

    def test_v1_pairing_is_test_first_against_red(self):
        self.assertIn("Write the failing test first", self.rows["V1"][1])
        self.assertIn("against red", self.rows["V1"][1])


class FirewallPositiveOpeningTests(TestCase):
    def setUp(self):
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_f2_opens_with_the_positive_form(self):
        f2 = self.text.split("### F2 — TDD Gate", 1)[1].split("###", 1)[0]
        self.assertTrue(
            f2.lstrip().startswith("Write the failing test first"),
            "F2 must open with the positive target, not the prohibition")
        self.assertIn("implement only against red", f2)

    def test_f3_opens_with_the_positive_form(self):
        f3 = self.text.split("### F3 — Coverage Gate", 1)[1].split("###", 1)[0]
        self.assertTrue(
            f3.lstrip().startswith("Run the coverage tool"),
            "F3 must open with the positive target, not the prohibition")
        self.assertIn("never assume", f3)


if __name__ == "__main__":
    main()
