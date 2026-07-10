"""Structural tests for the wiki ingest sub-command + the doc-sync split (Q1).

wiki ingest uncouples the wiki from the track lifecycle: an arbitrary source
routes through the SAME canonical doc-sync pipeline (corpus-writer Phase 1 +
wiki-synthesizer Phase 2 + an advisory wiki-differ) that post-track ingest uses,
so merge-not-append / idempotency / the drift gate are preserved. doc-syncer was
split at the §6.0-commit / §7.0-regen boundary; these assert the wiring on both
ends of ingest and that the split agents carry the ad-hoc-mode contract between
them.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
_WIKI = ROOT / "skills" / "wiki"


def _skill_surface() -> str:
    """Router (SKILL.md) + reference bodies — after the references/ split a
    sub-command's wiring may live in either file."""
    parts = [(_WIKI / "SKILL.md").read_text(encoding="utf-8")]
    for ref in ("query", "ingest", "build"):
        p = _WIKI / "references" / f"{ref}.md"
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


class WikiSkillIngestTests(TestCase):
    def setUp(self):
        self.skill = _skill_surface()

    def test_ingest_routed_and_tooled(self):
        self.assertIn("`ingest`", self.skill)
        self.assertIn("**Section 6.0**", self.skill)
        # URL ingest needs WebFetch.
        self.assertIn("WebFetch", self.skill)

    def test_ingest_section_dispatches_pipeline(self):
        self.assertIn("## 6.0 INGEST", self.skill)
        self.assertIn("SOURCE_TYPE=ad-hoc", self.skill)
        # Phase 1 of the split pipeline.
        self.assertIn("conductor:corpus-writer", self.skill)
        # Phase 2 of the split pipeline.
        self.assertIn("conductor:wiki-synthesizer", self.skill)
        # Advisory drift verify of the regen.
        self.assertIn("conductor:wiki-differ", self.skill)
        # The old monolithic agent is gone from the dispatch.
        self.assertNotIn("conductor:doc-syncer", self.skill)
        # Raw source is working memory, never a tracked corpus file (3-channel model).
        self.assertIn("mktemp", self.skill)

    def test_ingest_cleans_up_transient_source(self):
        self.assertIn('rm -f "$SRC"', self.skill)


class CorpusWriterAdHocModeTests(TestCase):
    """Phase 1 of the split carries the ad-hoc ingest contract: the source IS the
    spec, the harvest is skipped, and commits are tagged [wiki-ingest]. The ad-hoc
    mode params (SOURCE_TYPE / SOURCE_PATH / SOURCE_NAME) live in corpus-writer."""

    def setUp(self):
        self.agent = (ROOT / "agents" / "corpus-writer.md").read_text(encoding="utf-8")

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

    def test_ad_hoc_never_touches_track_state(self):
        # The contract: ad-hoc ingest never mutates track-state.json (V11 respected).
        self.assertIn("Never touch `track-state.json`", self.agent)

    def test_does_not_regenerate_overview(self):
        # The split's seam: corpus-writer (Phase 1) must NOT touch overview/purpose/
        # log — those are wiki-synthesizer's Phase 2. Guards against both phases
        # silently claiming the wiki-synthesis responsibility.
        self.assertIn("wiki-synthesizer", self.agent)
        self.assertIn("do NOT touch `overview.md`", self.agent)


def _frontmatter_tools(agent_text: str) -> str:
    """Return the raw `tools:` value line from an agent's YAML frontmatter."""
    in_fm = False
    for line in agent_text.splitlines():
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm and line.startswith("tools:"):
            return line.split("tools:", 1)[1].strip()
    return ""


class WikiSynthesizerAdHocLogTests(TestCase):
    """Phase 2 of the split owns the log. The INGEST log operation (ad-hoc mode)
    lives in wiki-synthesizer — corpus-writer does not log."""

    def setUp(self):
        self.agent = (ROOT / "agents" / "wiki-synthesizer.md").read_text(encoding="utf-8")

    def test_ingest_log_operation(self):
        self.assertIn("INGEST", self.agent)
        self.assertIn("Ad-hoc ingest", self.agent)

    def test_regenerates_overview_and_purpose(self):
        # Phase 2's job: overview regen + purpose + log.
        self.assertIn("overview.md", self.agent)
        self.assertIn("purpose.md", self.agent)
        self.assertIn("log.md", self.agent)

    def test_runs_automatic_no_askuserquestion(self):
        # Phase 2 is automatic — no AskUserQuestion in its tools (Phase 1 already
        # confirmed corpus edits). Check the frontmatter tools, not the body —
        # the body legitimately *says* "no AskUserQuestion". Pinned so a future
        # edit doesn't re-introduce an interactive gate into the auto-owned
        # synthesis phase.
        self.assertNotIn("AskUserQuestion", _frontmatter_tools(self.agent))


if __name__ == "__main__":
    main()
