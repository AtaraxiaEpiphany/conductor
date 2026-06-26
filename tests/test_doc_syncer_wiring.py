"""Structural tests for doc-syncer two-step CoT (O6) + queries/ indexing (O7)."""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


class TwoStepCoTTests(TestCase):
    def setUp(self):
        self.agent = (ROOT / "agents" / "doc-syncer.md").read_text(encoding="utf-8")

    def test_two_step_framing_present(self):
        # The ingest is split analysis -> generation, not a fused read+write.
        self.assertIn("two-step", self.agent)
        self.assertIn("STEP 1", self.agent)
        self.assertIn("Holistic Analysis", self.agent)

    def test_step1_produces_analysis_artifacts(self):
        for token in ("New entities", "Contradictions", "Targeted docs", "Cross-reference candidates"):
            self.assertIn(token, self.agent)

    def test_step2_generation_labeled(self):
        self.assertIn("STEP 2", self.agent)
        self.assertIn("generation", self.agent)

    def test_idempotent_noop_path(self):
        # An ingest that adds nothing must report SKIPPED, not invent changes.
        self.assertIn("SKIPPED", self.agent)
        self.assertIn("idempotent ingest", self.agent)


class QueriesIndexTests(TestCase):
    def test_project_index_lists_queries(self):
        text = (ROOT / "templates" / "project-index.md").read_text(encoding="utf-8")
        self.assertIn("conductor/queries/", text)
        self.assertIn("Wiki Queries", text)


class DocSyncProcedureExtractionTests(TestCase):
    """The per-document analysis table, proposal template, and Phase 2 synthesis
    specs were relocated from agents/doc-syncer.md into conductor/design/
    doc-sync-procedure.md (loaded on demand). These guard the wiring so the
    pointer and the relocated content can't silently drift apart."""

    def setUp(self):
        self.agent = (ROOT / "agents" / "doc-syncer.md").read_text(encoding="utf-8")
        self.proc_path = ROOT / "conductor" / "design" / "doc-sync-procedure.md"
        self.proc = self.proc_path.read_text(encoding="utf-8")

    def test_agent_points_at_procedure_doc(self):
        # The §4/§5/§7.1/§7.1b bodies collapsed to pointers; the agent must name
        # the doc it now reads, else it silently loses the procedure at runtime.
        self.assertIn("conductor/design/doc-sync-procedure.md", self.agent)

    def test_procedure_doc_exists_and_frontmatter_compliant(self):
        # Scoped design doc must carry provenance frontmatter (doc-conventions).
        self.assertTrue(self.proc.startswith("---\n"))
        self.assertIn("type: concept", self.proc)
        self.assertIn("last_verified:", self.proc)
        self.assertIn("agents/doc-syncer", self.proc)  # consumer = source

    def test_procedure_doc_carries_relocated_content(self):
        # The proposal template + variants + all 8 per-document rows moved here.
        for token in (
            "Proposal template",
            "caution",       # Product Guidelines variant
            "terms",         # Glossary variant
            "Product Definition",
            "Tech Stack",
            "Product Guidelines",
            "System Architecture",
            "Database Schema",
            "API Specifications",
            "UX/UI Design Spec",
            "Glossary",
        ):
            self.assertIn(token, self.proc, f"procedure doc missing relocated token: {token}")

    def test_synthesis_specs_relocated(self):
        # §B overview + §C purpose specs moved here (§7.1/§7.1b bodies are pointers).
        for token in ("Overview Regeneration Spec", "Purpose Update Spec"):
            self.assertIn(token, self.proc)

    def test_agent_body_dropped_inline_proposal_template(self):
        # The literal proposal prompt now lives in the procedure doc, not the
        # agent body (the whole point of the extraction). Belt-and-suspenders:
        # if someone re-inlines it, this flags the regression.
        self.assertNotIn("Proposed changes:\\n\\n{specific additions", self.agent)
        self.assertIn("Proposed changes:\\n\\n{specific additions", self.proc)


if __name__ == "__main__":
    main()
