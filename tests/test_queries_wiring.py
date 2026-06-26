"""Wiring tests: saved wiki queries feed SDD context-loading.

``conductor/queries/`` holds saved ``/conductor:wiki query`` results
(``type: query`` pages). Historically nothing read them back — they were a
human replay cache, cut out of the compounding loop. These tests pin the fix:
spec-planner and explorer now scan the folder by topic overlap and fold a
matching query's sources into the context they load, while the read-strategy
index still lists the folder so the discovery path stays consistent.

Semantics guarded:
- match by topic overlap with the task (scoped reads, not whole-corpus);
- a query is a routing hint + prior answer, NOT ground truth (verify against
  code, don't inherit blindly);
- skip silently when the folder is empty or nothing overlaps;
- record consulted queries in the explorer handoff provenance (consulted_docs).
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SPEC_PLANNER = (ROOT / "agents" / "spec-planner.md").read_text(encoding="utf-8")
EXPLORER = (ROOT / "agents" / "explorer.md").read_text(encoding="utf-8")
INDEX = (ROOT / "templates" / "project-index.md").read_text(encoding="utf-8")


class SpecPlannerConsumesQueriesTests(TestCase):
    def setUp(self):
        # Scope assertions to the Context Discovery section so a later,
        # unrelated mention can't satisfy the wiring check.
        self.ctx = SPEC_PLANNER.split("### 3.2")[0]

    def test_context_discovery_scans_queries_folder(self):
        self.assertIn("conductor/queries/*.md", self.ctx)

    def test_query_step_lives_under_context_discovery(self):
        self.assertIn("Saved Wiki Queries", self.ctx)

    def test_query_sources_are_routing_hints_not_ground_truth(self):
        lower = self.ctx.lower()
        self.assertIn("## sources", lower)        # fold the query's source list
        self.assertIn("routing hint", lower)       # treated as a hint
        self.assertIn("not ground truth", lower)   # not trusted as fact

    def test_skips_silently_when_empty(self):
        self.assertIn("skip silently", self.ctx.lower())


class ExplorerConsumesQueriesTests(TestCase):
    def setUp(self):
        # The wiring must live in Layer 0 (corpus consult), before exploration.
        self.layer0 = EXPLORER.split("## 4.0 EXPLORATION PROTOCOL")[0]

    def test_layer0_scans_queries_folder(self):
        self.assertIn("conductor/queries/*.md", self.layer0)

    def test_query_step_lives_in_layer0(self):
        self.assertIn("Saved wiki queries", self.layer0)

    def test_records_queries_in_consulted_docs(self):
        # A consulted query must surface in the handoff provenance, not be read
        # and forgotten.
        self.assertIn("every query you open", self.layer0.lower())

    def test_verifies_queries_against_code(self):
        # A saved query is a prior synthesized answer — claims must be verified,
        # not inherited blindly.
        self.assertIn("verify its claims against code", self.layer0.lower())

    def test_skips_silently_when_empty(self):
        self.assertIn("skip silently", self.layer0.lower())


class IndexDiscoveryTests(TestCase):
    def test_read_strategy_map_lists_queries(self):
        # spec-planner reads conductor/index.md (seeded from this template) to
        # discover doc paths — the folder must be listed so the scan target is
        # discoverable, not just writable.
        self.assertIn("conductor/queries/", INDEX)


if __name__ == "__main__":
    main()
