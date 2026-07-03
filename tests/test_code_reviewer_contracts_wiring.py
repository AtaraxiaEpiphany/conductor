"""Structural tests for the code-reviewer contract extraction (Phase 1c).

``agents/code-reviewer.md`` externalized two producer/consumer contracts to
``runtime/contracts/`` so they are single sources of truth the review skill and
post-loop auto-review can also read, instead of inlining them only in the agent:

- ``review-result-schema.md`` — the ``review-result.json`` shape §4.1 writes
  (consumed by the review "Apply Fixes" path + post-loop auto-review).
- ``code-reviewer-lens-matrix.md`` — the lens → {§3.4 items, §3.1 sources} table
  §2.6 routes by (the load-bearing context gate; the lens set is also the
  ``conductor:review`` per-lens fan-out dimension).

The agent keeps the procedural framing + every test-pinned token inline (``§2.6``,
``gates §3.1``, ``"lens"``, the default ``review-result.json`` path, lens names in
the §2.0 param table, mode semantics in §2.5); only the reference data moved.
These assert the pointers + relocated content + consumer provenance so the
extraction can't silently regress (content re-inlined, pointer dropped, or the
agent and the contract drifting apart).
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
CONTRACTS = ROOT / "runtime" / "contracts"

SCHEMA_PATH = CONTRACTS / "review-result-schema.md"
MATRIX_PATH = CONTRACTS / "code-reviewer-lens-matrix.md"


class PointerTests(TestCase):
    def setUp(self):
        self.agent = (AGENTS / "code-reviewer.md").read_text(encoding="utf-8")

    def test_agent_points_at_schema_doc(self):
        self.assertIn(
            "${CLAUDE_PLUGIN_ROOT}/runtime/contracts/review-result-schema.md",
            self.agent)

    def test_agent_points_at_lens_matrix_doc(self):
        self.assertIn(
            "${CLAUDE_PLUGIN_ROOT}/runtime/contracts/code-reviewer-lens-matrix.md",
            self.agent)


class SchemaDocTests(TestCase):
    def setUp(self):
        self.schema = SCHEMA_PATH.read_text(encoding="utf-8")

    def test_doc_exists_and_frontmatter_compliant(self):
        self.assertTrue(self.schema.startswith("---\n"))
        self.assertIn("type: concept", self.schema)
        self.assertIn("last_verified:", self.schema)

    def test_frontmatter_names_consumers(self):
        # Both the writer (agent) and the reader paths (review skill) consume the
        # schema; pin both so the contract doesn't drift from its consumers.
        self.assertIn("agents/code-reviewer", self.schema)
        self.assertIn("skills/review", self.schema)

    def test_json_structure_relocated(self):
        # The full JSON shape (status/checks/findings/state_issues/stats) moved
        # here from the agent's §4.1 heredoc.
        for token in (
            '"status": "SUCCESS"',
            '"mode": "full|refute|critique"',
            '"lens": "bugs|security|spec-compliance|tests|null"',
            "plan_compliance",
            "design_doc_consistency",
            '"severity": "Critical|High|Medium|Low"',
            '"stats": {"critical": 0',
        ):
            self.assertIn(token, self.schema, f"schema doc missing: {token}")

    def test_mode_specific_findings_semantics_relocated(self):
        for token in ("survivors", "refuted", "missed"):
            self.assertIn(token, self.schema)


class LensMatrixDocTests(TestCase):
    def setUp(self):
        self.matrix = MATRIX_PATH.read_text(encoding="utf-8")

    def test_doc_exists_and_frontmatter_compliant(self):
        self.assertTrue(self.matrix.startswith("---\n"))
        self.assertIn("type: concept", self.matrix)
        self.assertIn("last_verified:", self.matrix)

    def test_frontmatter_names_consumers(self):
        self.assertIn("agents/code-reviewer", self.matrix)
        self.assertIn("skills/review", self.matrix)

    def test_lens_table_relocated(self):
        # The 5-row lens → {§3.4 items, §3.1 sources} table moved here from §2.6.
        for lens in ("bugs", "security", "spec-compliance", "tests"):
            self.assertIn(f"`{lens}`", self.matrix)
        self.assertIn("all 7 (§3.4.1–§3.4.7)", self.matrix)
        # A source-loading cell — proves the gate column came with the table.
        self.assertIn("track-state.json", self.matrix)

    def test_scope_limit_documented(self):
        # The "items 2/3/6 map to no lens" scope limit relocated with the table.
        self.assertIn("State Consistency", self.matrix)
        self.assertIn("Style Compliance", self.matrix)
        self.assertIn("Skipped/Blocked", self.matrix)


class CrossReferenceTests(TestCase):
    """The two contract docs link each other so a reader landing on either finds
    the sibling (the lens a pass ran under ↔ the JSON it emitted)."""

    def test_schema_links_to_matrix(self):
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn("[[code-reviewer-lens-matrix]]", schema)

    def test_matrix_links_to_schema(self):
        matrix = MATRIX_PATH.read_text(encoding="utf-8")
        self.assertIn("[[review-result-schema]]", matrix)


class AgentInlineInvariantTests(TestCase):
    """The extraction must NOT strip the procedural framing / test-pinned tokens
    that ``test_review_wiring.py`` relies on. Pin them here too so a future
    over-aggressive trim is caught at this layer, not just the review test."""

    def setUp(self):
        self.agent = (AGENTS / "code-reviewer.md").read_text(encoding="utf-8")

    def test_context_gate_prose_kept_inline(self):
        # The load-bearing gate instruction + its §2.6 anchor stay inline.
        self.assertIn("§2.6", self.agent)
        self.assertIn("skip any §3.1 source not in", self.agent.lower())

    def test_default_result_path_kept_inline(self):
        self.assertIn("{TRACK_DIR}/.conductor/review-result.json", self.agent)

    def test_heredoc_mechanic_kept_inline(self):
        # The agent still knows the write mechanism, not just the schema doc.
        self.assertIn("cat > \"{RESULT_PATH}\" << 'EOF'", self.agent)


if __name__ == "__main__":
    main()
