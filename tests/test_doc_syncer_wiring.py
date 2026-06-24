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


if __name__ == "__main__":
    main()
