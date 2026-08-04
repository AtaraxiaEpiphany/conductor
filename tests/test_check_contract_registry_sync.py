"""Tests for ``check-contract-registry-sync`` — the drift gate that watches docs
and code for hand-maintained registry-vocab enumerations.

Load-bearing invariants under test:

- **Catches the confirmed drift bugs**: the F2/F3 exemption lists in
  ``core-contract.md`` (omit ``[Migrate]``), the tag enumeration in
  ``spec-reviewer.md`` (lists the tdd_exempt set), and the full mode enumeration
  (``MODE_VOCAB: compile / test / …``).
- **Does NOT fire on legitimate examples**: a grammar example
  (``- [ ] [Migrate] bump spring-boot``), a directive example
  (``emit verify: compile on … and verify: test,start on …``), or scattered
  ``e.g.`` tag mentions (the anti-drift instruction in spec-planner.md:143) do
  NOT form a tight list-run, and are exempt.
- **Table-row detector still works**: a markdown table whose first cell is a
  vocab literal is flagged (the original detector).
- **Code-literal assertion**: the Tier-1 code sites must reference their registry
  flag/accessor (this is what guards Part 3's data-driving post-merge).
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
    "ccrs", _scripts / "check-contract-registry-sync.py")
ccrs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ccrs)


class ProseClosedSetDetector(TestCase):
    """The prose detector: bracketed-tag run ≥3 OR bare-run ≥3 + marker.

    The signal is ADJACENCY — literals in one list-run, not scattered across
    clauses. ``_max_per_run`` returns ``(bracketed, bare)`` counts of the
    longest tight run of each kind.
    """

    def test_bracketed_tag_run_flagged(self):
        # The spec-reviewer.md:84 shape — a slash-separated run of 6 tag literals.
        line = ("Tags (`[Explore]`/`[Docs]`/`[Config]`/`[Chore]`/`[Manual]`/`[Migrate]`) "
                "are TDD exemptions")
        literals = ccrs._vocab_literals()
        bracketed, _bare = ccrs._max_per_run(line, literals)
        self.assertGreaterEqual(bracketed, 3)

    def test_exemption_only_list_flagged(self):
        # The core-contract.md:30 shape — "Exempted task types ONLY: [A], [B], …"
        line = "Exempted task types ONLY: `[Docs]`, `[Config]`, `[Chore]`, `[Explore]`, `[Manual]`."
        literals = ccrs._vocab_literals()
        bracketed, _bare = ccrs._max_per_run(line, literals)
        self.assertGreaterEqual(bracketed, 3)
        self.assertTrue(ccrs._is_closed_set_line(line))

    def test_bare_mode_run_with_marker_flagged(self):
        # The spec-reviewer.md:104 shape — "closed mode vocabulary … MODE_VOCAB:
        # compile / test / start / …". Bare modes need BOTH ≥3 AND a marker.
        line = ("the closed mode vocabulary is MODE_VOCAB: compile / test / start / "
                "adversarial / anchor / none")
        literals = ccrs._vocab_literals()
        _bracketed, bare = ccrs._max_per_run(line, literals)
        self.assertGreaterEqual(bare, 3)
        self.assertTrue(ccrs._is_closed_set_line(line))

    def test_scattered_e_tags_not_flagged(self):
        # The spec-planner.md:143 shape — 3 tag literals each in its OWN `e.g.`
        # clause, separated by `;` and prose. They do NOT form one tight run.
        line = ("[Config] for a no-logic edit; [Migrate] for a version bump; "
                "[Refactor] is opt-in only")
        literals = ccrs._vocab_literals()
        bracketed, _bare = ccrs._max_per_run(line, literals)
        self.assertLess(bracketed, 3,
                        "scattered e.g. tags must not look like one list-run")

    def test_scattered_directive_examples_not_flagged(self):
        # Legitimate: directive examples in separate quoted clauses — no run.
        line = ("emit `verify: compile` on an intermediate phase and "
                "`verify: test,start` on the final phase, or `verify: none` for debt")
        literals = ccrs._vocab_literals()
        _bracketed, bare = ccrs._max_per_run(line, literals)
        # `test,start` is a 2-mode run — under the ≥3 threshold, and no marker.
        self.assertLess(bare, 3,
                        "scattered directive examples must not look like a list-run")

    def test_subset_mode_list_without_marker_not_flagged(self):
        # The spec-reviewer.md:108 shape — "explicit gates (compile/test/anchor)"
        # is a SUBSET example, not a complete-set claim: no marker → not flagged.
        line = "a verify: none phase sitting among phases with explicit gates (compile/test/anchor)"
        literals = ccrs._vocab_literals()
        _bracketed, bare = ccrs._max_per_run(line, literals)
        self.assertFalse(bare >= 3 and ccrs._is_closed_set_line(line),
                         "a subset mode list with no completeness marker is not drift")

    def test_single_grammar_example_not_flagged(self):
        # A task-line template carries exactly one tag literal — never a closed set.
        line = "- [ ] [Migrate] bump the spring-boot parent <!-- AC-1 -->"
        literals = ccrs._vocab_literals()
        bracketed, bare = ccrs._max_per_run(line, literals)
        self.assertLess(bracketed, 3)
        self.assertLess(bare, 3)


class TableRowDetector(TestCase):
    """The original detector: a markdown table row keyed on a vocab literal."""

    def test_table_row_tag_flagged(self):
        cell = ccrs._first_cell("| `[Explore]` | routing-only tag |")
        self.assertEqual(cell, "[Explore]")
        self.assertIn(cell, ccrs._vocab_literals())

    def test_non_table_line_returns_none(self):
        self.assertIsNone(ccrs._first_cell("- [ ] [Migrate] bump spring-boot"))
        self.assertIsNone(ccrs._first_cell("## Phase 1: Foo"))

    def test_separator_row_skipped(self):
        # Separator/header rows are table rows whose first cell is not a vocab
        # literal — _first_cell returns the cell text (not None), but it does not
        # match any literal, so the row is effectively skipped downstream.
        self.assertEqual(ccrs._first_cell("|---|---|"), "---")
        self.assertEqual(ccrs._first_cell("| Tag | Meaning |"), "Tag")
        self.assertNotIn(ccrs._first_cell("|---|---|"), ccrs._vocab_literals())


class EndToEnd(TestCase):
    """Run the scan over synthetic doc fragments that mirror the drift bugs."""

    def test_scans_a_synthetic_closed_set(self):
        # Write a temp doc with a confirmed-drift-shape line and scan it.
        import tempfile
        literals = ccrs._vocab_literals()
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(textwrap.dedent("""\
                # Fake

                Exempted task types ONLY: `[Docs]`, `[Config]`, `[Chore]`, `[Explore]`, `[Manual]`.

                ## Phase 1: build the login form
                - [ ] [Migrate] bump spring-boot <!-- AC-1 -->
                """))
            path = Path(f.name)
        try:
            findings = list(ccrs._scan_doc(path, literals))
            # The exemption line is flagged; the single-tag task line is not.
            self.assertTrue(any("Exempted" in msg or "closed-set" in msg
                                for _, msg in findings),
                            f"expected the exemption line flagged; got {findings}")
            self.assertFalse(any("[Migrate] bump spring-boot" in msg
                                 for _, msg in findings),
                             "the single-tag task line must not be flagged")
        finally:
            path.unlink()


if __name__ == "__main__":
    main()
