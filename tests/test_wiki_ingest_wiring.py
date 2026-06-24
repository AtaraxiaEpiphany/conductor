"""Structural tests for the wiki ingest sub-command + doc-syncer ad-hoc mode (Q1).

wiki ingest uncouples the wiki from the track lifecycle: an arbitrary source
routes through the SAME canonical writer (doc-syncer) that post-track ingest
uses, so merge-not-append / idempotency / the drift gate are preserved. These
assert the wiring on both ends.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


class WikiSkillIngestTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "wiki" / "SKILL.md").read_text(encoding="utf-8")

    def test_ingest_routed_and_tooled(self):
        self.assertIn("`ingest`", self.skill)
        self.assertIn("**Section 6.0**", self.skill)
        # URL ingest needs WebFetch.
        self.assertIn("WebFetch", self.skill)

    def test_ingest_section_dispatches_doc_syncer_ad_hoc(self):
        self.assertIn("## 6.0 INGEST", self.skill)
        self.assertIn("SOURCE_TYPE=ad-hoc", self.skill)
        self.assertIn("conductor:doc-syncer", self.skill)
        # Raw source is working memory, never a tracked corpus file (3-channel model).
        self.assertIn("mktemp", self.skill)

    def test_ingest_cleans_up_transient_source(self):
        self.assertIn('rm -f "$SRC"', self.skill)


class DocSyncerAdHocModeTests(TestCase):
    def setUp(self):
        self.agent = (ROOT / "agents" / "doc-syncer.md").read_text(encoding="utf-8")

    def test_assignment_has_mode_params(self):
        for token in ("SOURCE_TYPE", "SOURCE_PATH", "SOURCE_NAME", "ad-hoc"):
            self.assertIn(token, self.agent)

    def test_source_path_is_the_spec_in_ad_hoc(self):
        self.assertIn("SOURCE_PATH", self.agent)
        # §3.1 routes ad-hoc SOURCE_PATH through the same pipeline as spec.md.
        self.assertIn("is** the spec", self.agent)

    def test_ad_hoc_skips_harvest(self):
        self.assertIn("Skip this step entirely", self.agent)

    def test_wiki_ingest_commit_tags(self):
        self.assertIn("[wiki-ingest]", self.agent)

    def test_ingest_log_operation(self):
        self.assertIn("INGEST", self.agent)
        self.assertIn("Ad-hoc ingest", self.agent)

    def test_ad_hoc_never_touches_track_state(self):
        # The contract: ad-hoc ingest never mutates track-state.json (V11 respected).
        self.assertIn("Never touch `track-state.json`", self.agent)


if __name__ == "__main__":
    main()
