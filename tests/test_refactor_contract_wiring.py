r"""Wiring tests for the Step 5 (Refactor) progressive-disclosure split.

The refactor *procedure* lives in ``runtime/contracts/refactor.md`` (loaded
on-demand only when Step 5 runs); the task-executor §4.0 binding is a terse
pointer that names the boundary invariants + the contract path. This keeps the
executor's always-loaded prompt small — it is a small-window model and context
budget is the scarce resource — while retaining the full procedure one Read away.

Guards:
- the contract exists, carries concept frontmatter, and states the procedure
  (two tiers, green-confirm via Step 6, revert-on-red, the public-API → Step 7
  deviation boundary, the ~6-round cap, the no-fence-widening note);
- the agent binding points at it via the ``${CLAUDE_PLUGIN_ROOT}`` prefix (so it
  resolves in a foreign project — the test_no_dangling_runtime_contracts guard);
- the four boundary invariants stay inline in the binding, so the agent knows the
  boundary without loading the doc (defense in depth).
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "runtime" / "contracts" / "refactor.md"
AGENT = ROOT / "agents" / "task-executor.md"


class RefactorContractTests(TestCase):
    def setUp(self):
        self.doc = CONTRACT.read_text(encoding="utf-8")

    def test_contract_exists_with_frontmatter(self):
        self.assertTrue(CONTRACT.is_file(), "runtime/contracts/refactor.md must exist")
        self.assertIn("type: concept", self.doc)
        self.assertIn("agents/task-executor", self.doc)  # sources:

    def test_contract_states_the_four_invariants(self):
        # The procedure doc is the authoritative home for the boundary + the how.
        for needle in ("behavior-preserving", "diff-scoped", "git revert",
                       "~6 rounds"):
            self.assertIn(needle, self.doc)

    def test_contract_states_procedure_and_deviation_boundary(self):
        # Two tiers + the public-API → Step 7 routing + the green-confirm source.
        self.assertIn("Mechanical", self.doc)
        self.assertIn("Tactical", self.doc)
        self.assertIn("SPEC_DEVIATION", self.doc)
        self.assertIn("PURPOSE=coverage", self.doc)  # the green-confirm dispatch

    def test_contract_documented_no_fence_widening(self):
        # Step 5 adds no Agent-tool dispatch kind — the §5.0 fence is unchanged.
        self.assertIn("no `Agent`-tool dispatch kind", self.doc)


class RefactorBindingPointerTests(TestCase):
    def setUp(self):
        self.agent = AGENT.read_text(encoding="utf-8")

    def test_binding_points_at_contract_with_plugin_root(self):
        # Must be ${CLAUDE_PLUGIN_ROOT}-prefixed or it dangles in a foreign
        # project (test_no_dangling_runtime_contracts enforces this dir-wide).
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/runtime/contracts/refactor.md",
                      self.agent)

    def test_binding_is_a_pointer_with_inline_boundary(self):
        # The binding is a pointer, but the boundary stays inline (not a bare
        # one-line link) so the invariants are visible before the doc is loaded.
        self.assertIn("Step 5 (Refactor)", self.agent)
        self.assertIn("Load `${CLAUDE_PLUGIN_ROOT}/runtime/contracts/refactor.md`",
                      self.agent)
        self.assertIn("behavior-preserving", self.agent)


if __name__ == "__main__":
    main()
