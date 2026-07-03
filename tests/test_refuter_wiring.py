"""Wiring tests for agents/refuter.md.

Locks down the contract every refute-shaped caller (new-track plan refute,
implement skip refute, parallel seam refute) depends on, so the historical
doc-linter drift (a result field advertised with no check behind it, a check
count that rotted) cannot recur here:

- the §3 DOMAIN playbook set is the single source of truth for which domains
  the agent serves, and every domain maps to a result field set;
- the load-bearing SUSTAINED-when-uncertain asymmetry is stated in the body
  (a silent flip to "default REFUTED" would invert every caller's semantics);
- the agent stays read-only at the tool level (no Edit/Write/Bash), so the
  firewall is enforced structurally, not just in prose.

Mirrors the idiom of test_doc_linter_wiring.py: plain Path.read_text() +
structural asserts, no fixtures.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFUTER = (ROOT / "agents" / "refuter.md").read_text(encoding="utf-8")

# Single source of truth: the DOMAIN values the agent knows how to refute, each
# mapped to its §3 playbook number. Adding a DOMAIN without a playbook (or a
# playbook with no domain entry) fails the suite.
DOMAIN_TO_PLAYBOOK = {
    "plan": "3.1",
    "skip": "3.2",
    "seam": "3.3",
}

# Fields a completion result block must carry (STATUS is bookkeeping — the
# machine-readable outcome — so excluded, like doc-linter excludes STATUS/SUMMARY).
REQUIRED_RESULT_FIELDS = {"DOMAIN", "CHALLENGED_CLAIM", "EVIDENCE", "REASONING"}


def _domain_playbooks(text):
    """Map of DOMAIN value -> §3.x number actually defined in the body.

    Parses headings like ``### 3.1 `plan` — ...``.
    """
    out = {}
    for m in re.finditer(r"^### (3\.\d+)\s+`([a-z]+)`", text, re.MULTILINE):
        out[m.group(2)] = m.group(1)
    return out


def _result_blocks(text):
    """All ---REFUTATION RESULT--- blocks (completion block first)."""
    return re.findall(
        r"---REFUTATION RESULT---\n(.*?)\n---END RESULT---", text, re.DOTALL
    )


def _result_block_fields(block):
    """Uppercase data fields in one result block (excludes STATUS bookkeeping)."""
    fields = set(re.findall(r"^([A-Z_]+):", block, re.MULTILINE))
    return fields - {"STATUS"}


class RefuterWiring(unittest.TestCase):
    def setUp(self):
        self.blocks = _result_blocks(REFUTER)
        self.assertGreaterEqual(
            len(self.blocks), 2,
            "refuter.md must define both a completion and a failure result block",
        )

    def test_section_three_playbooks_match_canonical_map(self):
        """Every §3 playbook is accounted for (no surprise domain, no missing one)."""
        self.assertEqual(_domain_playbooks(REFUTER), DOMAIN_TO_PLAYBOOK)

    def test_completion_block_has_required_fields(self):
        """The completion (first) block carries the contract fields every caller parses."""
        fields = _result_block_fields(self.blocks[0])
        missing = REQUIRED_RESULT_FIELDS - fields
        self.assertFalse(missing, f"completion block missing fields: {missing}")

    def test_no_vapor_result_field(self):
        """Every completion-block field is in the canonical set (no orphan field)."""
        fields = _result_block_fields(self.blocks[0])
        vapor = fields - REQUIRED_RESULT_FIELDS
        self.assertFalse(vapor, f"result fields with no contract behind them: {vapor}")

    def test_status_field_present_in_completion_block(self):
        self.assertRegex(self.blocks[0], r"^STATUS:\s", re.MULTILINE)

    def test_failure_block_carries_reason(self):
        """The failure block carries STATUS: FAILURE + a REASON line."""
        failure = self.blocks[1]
        self.assertRegex(failure, r"^STATUS:\s*FAILURE", re.MULTILINE)
        self.assertIn("REASON:", failure)

    def test_result_delimiters_present(self):
        self.assertIn("---REFUTATION RESULT---", REFUTER)
        self.assertIn("---END RESULT---", REFUTER)

    def test_safety_floor_paragraph_present(self):
        """The verbatim 'Core safety floor' anchor must reach the agent body (it is
        also injected at runtime by on-subagent-start.py, but the body paragraph is
        the curatorial belt-and-suspenders every sibling agent carries)."""
        self.assertIn("**Core safety floor:**", REFUTER)
        self.assertIn("SubagentStart hook", REFUTER)

    def test_default_sustained_when_uncertain_is_stated(self):
        """Load-bearing asymmetry: the agent must default to SUSTAINED when uncertain.
        A silent flip to 'default REFUTED' would invert every caller's semantics
        (a refuted claim is dropped; a sustained one is kept)."""
        self.assertIn("Default to SUSTAINED when uncertain", REFUTER)
        # And the direction is stated positively, not inverted.
        self.assertIn("SUSTAINED, not REFUTED", REFUTER)

    def test_tools_are_read_only(self):
        """The firewall is enforced at the tool level: no Edit/Write/Bash. Read-only
        tools mean no firewall-scoping prose is needed and the agent cannot mutate
        state even if it tried."""
        m = re.search(r"^tools:\s*(.+)$", REFUTER, re.MULTILINE)
        self.assertIsNotNone(m, "no tools: frontmatter found")
        tools = {t.strip() for t in m.group(1).split(",")}
        self.assertEqual(tools, {"Read", "Grep", "Glob"}, f"unexpected tools: {tools}")

    def test_assignment_table_carries_dispatch_shape(self):
        """The §2.0 ASSIGNMENT table must name DOMAIN / CLAIM / CONTEXT_PATHS — the
        dispatch envelope every caller fills in."""
        for param in ("DOMAIN", "CLAIM", "CONTEXT_PATHS"):
            self.assertIn(f"`{param}`", REFUTER, f"ASSIGNMENT table missing {param}")


if __name__ == "__main__":
    unittest.main()
