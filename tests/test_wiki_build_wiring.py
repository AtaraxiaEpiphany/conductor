"""Structural tests for the `wiki build` sub-command (bulk organize-and-file).

`wiki build <dir|file|url|block>` is the missing *batch* wiki-construction
operation — the counterpart to `wiki ingest` (single source). It reuses the SAME
canonical doc-sync pipeline (corpus-writer Phase 1 + wiki-synthesizer Phase 2 +
advisory wiki-differ/doc-linter) as ingest and post-track sync, so there is still
exactly one ingestion engine. These assert the wiring: build is routed, it reuses
the split agents unchanged (no new agent), it is plan-then-execute, it files
external references via the EXISTING conductor/resource/ convention (not a new
top-level layer), and it surfaces its caps. Also pins the §6.0 disambiguation
(ingest = single source) and the setup cold-start offer.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
_WIKI = ROOT / "skills" / "wiki"


def _skill_surface() -> str:
    """Router (SKILL.md) + reference bodies — after the references/ split a
    sub-command's wiring may live in either file."""
    parts = [(_WIKI / "SKILL.md").read_text(encoding="utf-8")]
    for ref in ("query", "ingest", "build", "doc-sync-pipeline"):
        p = _WIKI / "references" / f"{ref}.md"
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


class WikiSkillBuildRoutingTests(TestCase):
    def setUp(self):
        self.skill = _skill_surface()

    def test_build_subcommand_listed(self):
        self.assertIn("`build <source>`", self.skill)

    def test_build_routed_to_section_7(self):
        self.assertIn("| `build` |", self.skill)
        self.assertIn("**Section 7.0**", self.skill)

    def test_build_in_usage_help(self):
        self.assertIn("build <source>", self.skill)

    def test_build_section_exists(self):
        self.assertIn("## 7.0 BUILD", self.skill)

    def test_build_needs_webfetch_for_url(self):
        # URL build needs WebFetch (same as ingest).
        self.assertIn("WebFetch", self.skill)


class WikiSkillBuildPipelineTests(TestCase):
    """build reuses the split doc-sync pipeline unchanged — no new agent, no
    parallel ingestion path. corpus-writer (Phase 1) per chunk, then
    wiki-synthesizer (Phase 2) once, then the advisory wiki-differ + doc-linter."""

    def setUp(self):
        self.skill = _skill_surface()

    def test_reuses_corpus_writer_and_synthesizer(self):
        self.assertIn("conductor:corpus-writer", self.skill)
        self.assertIn("conductor:wiki-synthesizer", self.skill)

    def test_advisory_differ_and_linter_tail(self):
        self.assertIn("conductor:wiki-differ", self.skill)
        self.assertIn("conductor:doc-linter", self.skill)

    def test_ad_hoc_mode(self):
        # build dispatches the agents in their existing ad-hoc mode (no new mode).
        self.assertIn("SOURCE_TYPE=ad-hoc", self.skill)

    def test_no_new_agent_invented(self):
        # The old monolithic agent must stay gone; build must not invent a peer.
        self.assertNotIn("conductor:doc-syncer", self.skill)

    def test_wiki_ingest_commit_tags(self):
        # build files under the same [wiki-ingest] provenance as ad-hoc ingest.
        self.assertIn("[wiki-ingest]", self.skill)


class WikiSkillBuildPlanThenExecuteTests(TestCase):
    """build is plan-then-execute: an advisory plan the human confirms once,
    then batched execution (chunks of <= 8 so corpus-writer confirms per chunk,
    not per source)."""

    def setUp(self):
        self.skill = _skill_surface()

    def test_has_plan_and_execute_phases(self):
        self.assertIn("Phase A", self.skill)
        self.assertIn("Phase B", self.skill)

    def test_plan_is_advisory(self):
        self.assertIn("advisory", self.skill)

    def test_confirms_once_via_askuserquestion(self):
        self.assertIn("AskUserQuestion", self.skill)

    def test_batches_into_chunks(self):
        # Per-source dispatch would be N confirmation rounds; build chunks instead.
        self.assertIn("chunk", self.skill.lower())
        self.assertIn("≤ 8", self.skill)

    def test_plan_written_to_transient_tmp(self):
        # Raw sources + plan are working memory under /tmp, never tracked corpus
        # files; not .conductor/ (not reliably gitignored here).
        self.assertIn("mktemp", self.skill)
        self.assertIn("/tmp", self.skill)

    def test_cleans_up_transient_files(self):
        self.assertIn("rm -f /tmp/wiki-build-*", self.skill)


class WikiSkillBuildReferencesLayerTests(TestCase):
    """External reference docs (no scoped-doc signal) file into the EXISTING
    conductor/resource/ home (type: resource), which is queryable but deliberately
    NOT routed into task-executor context. build must NOT introduce a new
    top-level conductor/references/ layer — that would re-create the two-files-
    must-agree drift (D5) the design avoids."""

    def setUp(self):
        self.skill = _skill_surface()

    def test_references_file_into_existing_resource_home(self):
        self.assertIn("conductor/resource/", self.skill)
        self.assertIn("type: resource", self.skill)

    def test_no_new_top_level_references_layer(self):
        self.assertNotIn("conductor/references/", self.skill)


class WikiSkillBuildNoSilentCapsTests(TestCase):
    def setUp(self):
        self.skill = _skill_surface()

    def test_source_cap_is_surfaced(self):
        # A cap on sources must be announced, not silent.
        self.assertIn("no silent caps", self.skill.lower())
        self.assertIn("surfaced", self.skill.lower())


class WikiSkillIngestDisambiguationTests(TestCase):
    """Now that `build` is the bulk verb, §6.0 ingest is reworded to 'single
    source' so the two are unambiguous."""

    def setUp(self):
        self.skill = _skill_surface()

    def test_ingest_reworded_single_source(self):
        self.assertIn("Ingest a single source", self.skill)


class SetupColdStartTests(TestCase):
    """setup offers to populate the wiki from existing brownfield docs on day one
    via /wiki build — the fix for the empty-wiki cold-start that made the wiki
    feel useless. Optional; never blocks setup."""

    def setUp(self):
        self.setup = (ROOT / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")

    def test_setup_offers_wiki_build(self):
        self.assertIn("/conductor:wiki build", self.setup)

    def test_setup_detects_existing_docs(self):
        # Detection targets the docs a brownfield project actually has.
        self.assertIn("README", self.setup)


class DesignNoteTests(TestCase):
    """The build design is locked in a design note under conductor/design/ so the
    decisions (reuse-not-rebuild, references via existing convention, plan-then-
    execute) survive outside the skill prose."""

    def test_design_note_exists(self):
        self.assertTrue(
            (ROOT / "conductor" / "design" / "wiki-build-skill.md").exists(),
            "wiki-build design note missing under conductor/design/",
        )


if __name__ == "__main__":
    main()
