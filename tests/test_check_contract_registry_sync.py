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
import tempfile
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

    def test_single_grammar_example_not_flagged(self):
        # A task-line template carries exactly one tag literal — never a closed set.
        line = "- [ ] [Migrate] bump the spring-boot parent <!-- AC-1 -->"
        literals = ccrs._vocab_literals()
        bracketed, bare = ccrs._max_per_run(line, literals)
        self.assertLess(bracketed, 3)
        self.assertLess(bare, 3)


def _scan_text(text):
    """Scan a synthetic doc fragment end-to-end → findings list.

    Mirrors what ``_scan_doc`` does over a real watched file, so a detector can
    be proven on a drift-shaped line in isolation (table + prose detectors).
    """
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
        path = Path(f.name)
    try:
        return list(ccrs._scan_doc(path, ccrs._vocab_literals()))
    finally:
        path.unlink()


class BehavioralLadderDetector(TestCase):
    """≥2 'On `<mode>` …' rungs restating per-mode behavior = drift.

    Each mode's behavior lives in its registry ``protocol``; a prose ladder
    restating it is the first thing to drift. This is the phase-checker §3 /
    contract "Resolution" shape the flag migration collapsed.
    """

    def test_two_rungs_detected(self):
        line = "On `compile` run BUILD; On `anchor` run the freeze check."
        rungs = ccrs._behavioral_ladder_modes(line, ccrs._vocab_literals())
        self.assertGreaterEqual(len(rungs), 2)
        self.assertIn("compile", rungs)
        self.assertIn("anchor", rungs)

    def test_single_rung_not_a_ladder(self):
        line = "On `compile` the build runs."
        rungs = ccrs._behavioral_ladder_modes(line, ccrs._vocab_literals())
        self.assertLess(len(rungs), 2)

    def test_scan_flags_a_ladder(self):
        findings = _scan_text(
            "Run each: On `compile` do X; On `test` do Y; On `none` do Z.\n")
        self.assertTrue(any("behavioral ladder" in m for _, m in findings),
                        f"expected the ladder flagged; got {findings}")


class TightModeEnumDetector(TestCase):
    """≥3 modes joined tightly by ,// + a debt/gate marker = a restated mode set.

    The registry flags (``closes_debt`` / ``carries_debt`` / ``build_gated``)
    replace these restated sets, so naming the concept alongside a tight 3+ mode
    run is the same drift shape as a closed-set marker.
    """

    def test_explicit_gates_compile_test_anchor_flagged(self):
        # The pre-migration spec-reviewer shape — "explicit gates
        # (compile/test/anchor)" restates the closes_debt set. Tight enum +
        # "explicit gate" marker trips. (Supersedes the old assertion that this
        # subset line was benign — the migration's whole point is that it isn't.)
        findings = _scan_text(
            "a none phase sitting among phases with explicit gates (compile/test/anchor)\n")
        self.assertTrue(findings,
                        f"expected the restated closes_debt set flagged; got {findings}")

    def test_debt_closure_compile_test_start_flagged(self):
        # "no later compile/test/start phase to close the debt" — tight enum +
        # "close the debt" marker. The other pre-migration spec-reviewer shape.
        findings = _scan_text(
            "a none phase with no later compile/test/start phase to close the debt\n")
        self.assertTrue(findings,
                        f"expected the debt-closure set flagged; got {findings}")

    def test_directive_examples_without_marker_not_flagged(self):
        # Legitimate directive examples — modes in separate quoted clauses, no
        # completeness/debt marker — must NOT trip.
        findings = _scan_text(
            "emit `verify: compile` on phase 1 and `verify: test,start` on phase 2\n")
        self.assertFalse(findings,
                         f"scattered directive examples must not trip: {findings}")


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


class WatchedSetTests(TestCase):
    """The watched set must cover every doc/agent whose prose references the
    registry vocab — a migrated agent left un-watched would drift silently."""

    def test_migrated_review_agents_are_watched(self):
        # refuter + phase-checker prose was migrated to flag references; both
        # must be policed for restated sets.
        self.assertIn("agents/refuter.md", ccrs.WATCHED)
        self.assertIn("agents/phase-checker.md", ccrs.WATCHED)
        self.assertIn("agents/spec-reviewer.md", ccrs.WATCHED)


class TreeIntegrationTests(TestCase):
    """The full lint over the real tree is green — the load-bearing guard that
    the migrated prose (flag references, not literal sets) and the wiring
    (deferring agents injected; named flags surfaced) hold together."""

    def _tree_findings(self):
        from env import get_plugin_root
        root = get_plugin_root()
        literals = ccrs._vocab_literals()
        findings = []
        for rel in ccrs.WATCHED:
            path = root / rel
            if not path.exists():
                continue
            for lineno, msg in ccrs._scan_doc(path, literals):
                findings.append(f"{rel}:{lineno}: {msg}")
        findings.extend(ccrs._scan_code_literals(root))
        hook = ccrs._load_hook()
        findings.extend(ccrs._check_defer_implies_injected(root, hook))
        findings.extend(ccrs._check_flag_coverage(root, hook))
        return findings

    def test_full_lint_is_clean_on_tree(self):
        self.assertFalse(self._tree_findings(),
                         "registry-vocab drift on the real tree")


class DeferImpliesInjectedTests(TestCase):
    """A watched agent that defers to the [Conductor Registry] block must be in
    ``_REGISTRY_AGENTS`` — the assertion that would have caught the original
    half-wired migration (reviewer prose pointed at the block before the
    reviewers were injected)."""

    def test_clean_on_tree(self):
        from env import get_plugin_root
        hook = ccrs._load_hook()
        self.assertFalse(ccrs._check_defer_implies_injected(get_plugin_root(), hook),
                         "a watched agent defers to the block but isn't injected")

    def test_fires_when_deferring_agent_absent_from_allowlist(self):
        # Simulate the original bug: a watched agent doc references the block,
        # but its name is absent from _REGISTRY_AGENTS (block never arrives).
        hook = ccrs._load_hook()
        saved_agents = set(hook._REGISTRY_AGENTS)
        saved_watched = list(ccrs.WATCHED)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "agents").mkdir()
            (root / "agents" / "spec-reviewer.md").write_text(
                "Audit tag membership via your [Conductor Registry] block "
                "(TAG_VOCAB / MODE_VOCAB).")
            ccrs.WATCHED = ["agents/spec-reviewer.md"]
            hook._REGISTRY_AGENTS = set()  # reviewer NOT injected → the bug
            try:
                findings = ccrs._check_defer_implies_injected(root, hook)
            finally:
                ccrs.WATCHED = saved_watched
                hook._REGISTRY_AGENTS = saved_agents
        self.assertTrue(findings, "a deferring agent absent from the allowlist must be flagged")
        self.assertIn("spec-reviewer", "\n".join(findings))

    def test_non_deferring_agent_not_flagged(self):
        # An agent doc that never references the block is not flagged even if
        # absent from the allowlist (the assertion keys on the defer reference).
        hook = ccrs._load_hook()
        saved_agents = set(hook._REGISTRY_AGENTS)
        saved_watched = list(ccrs.WATCHED)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "agents").mkdir()
            (root / "agents" / "spec-reviewer.md").write_text(
                "Audit the spec. No registry reference here.")
            ccrs.WATCHED = ["agents/spec-reviewer.md"]
            hook._REGISTRY_AGENTS = set()
            try:
                findings = ccrs._check_defer_implies_injected(root, hook)
            finally:
                ccrs.WATCHED = saved_watched
                hook._REGISTRY_AGENTS = saved_agents
        self.assertFalse(findings,
                         f"non-deferring agent must not be flagged: {findings}")


class FlagCoverageTests(TestCase):
    """Every registry flag a watched agent names must be surfaced by the block
    (prose → flag → block-data), AND every flag the declaration maps must
    actually be emitted (the declaration can't drift from the render)."""

    def test_clean_on_tree(self):
        from env import get_plugin_root
        hook = ccrs._load_hook()
        self.assertFalse(ccrs._check_flag_coverage(get_plugin_root(), hook),
                         "a watched agent names a flag the block doesn't surface")

    def test_honesty_every_declared_token_is_emitted(self):
        # The reverse direction: each {flag: token} the declaration claims must
        # appear in the rendered reviewer block. Catches a renderer that stops
        # emitting a token while reviewer_block_flags still claims it.
        hook = ccrs._load_hook()
        block = hook._registry_for_reviewer()
        missing = {name: tok for name, tok in hook.reviewer_block_flags().items()
                   if tok not in block}
        self.assertFalse(missing,
                         f"declared flags not emitted by the block: {missing}")

    def test_fires_when_renderer_drops_a_token_prose_names(self):
        # Simulate: the renderer stops emitting `over-tag` while spec-reviewer
        # prose still names `over_tag_risk`. The explicit map routes the name to
        # the `over-tag` token (NOT name.replace('_','-') = 'over-tag-risk').
        from env import get_plugin_root
        hook = ccrs._load_hook()
        root = get_plugin_root()
        real = hook._registry_for_reviewer
        hook._registry_for_reviewer = lambda: real().replace("over-tag", "")
        try:
            findings = ccrs._check_flag_coverage(root, hook)
        finally:
            hook._registry_for_reviewer = real
        self.assertTrue(findings,
                        "a dropped token the prose names must be flagged")
        self.assertIn("over_tag_risk", "\n".join(findings))
        self.assertIn("`over-tag`", "\n".join(findings))


if __name__ == "__main__":
    main()