"""Structural tests for the wiki deep-research fan-out (E).

The ``query`` path is reshaped from a single ``wiki-researcher`` dispatch into a
**fan-out-and-synthesize** (Rail A prose — no Workflow JS):

- ``§4.2.1 Route & Decompose`` — the skill reads overview + index, routes the
  topic, and decomposes it into 1..N scoped sub-queries (classify-and-act:
  single-corner collapses to one dispatch; multi-corner fans out).
- ``§4.2.2 Fan Out`` — N>=2 researchers dispatched in ONE message (parallel).
- ``§4.2.3 Synthesize`` — merge answers, dedupe sources, note contradictions.
- ``§4.2.4 Citation Verify`` — every ``[[wikilink]]`` must resolve (Glob); dead
  citations are dropped and announced (generate-and-filter / no-silent-caps).

The fan-out reuses ``conductor:wiki-researcher`` **unchanged** — the scoped
``TOPIC`` constrains each branch to its corner, so the split *avoids* maxTurns
pressure rather than needing a bump or a new agent. These tests pin that
invariant so a future edit doesn't quietly grow the agent.

E also chains a **post-ingest doc-linter advisory** after §6.2 (ad-hoc ingest of
an arbitrary source is when lint violations land) — which revises the old §5.0
claim that "the wiki skill never dispatches doc-linter".
"""
import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
_WIKI = ROOT / "skills" / "wiki"


def _read(*parts: str) -> str:
    return _WIKI.joinpath(*parts).read_text(encoding="utf-8")


# The wiki skill is a thin router (SKILL.md) whose heavy sub-command bodies
# (query/ingest/build) live in references/*.md (progressive disclosure). Wiring
# may sit in either file after the split; assert against the UNION so a pinned
# invariant is not silently moved off-page.
SKILL = "\n\n".join([_read("SKILL.md"),
                     _read("references", "query.md"),
                     _read("references", "ingest.md"),
                     _read("references", "build.md")])
RESEARCHER = (ROOT / "agents" / "wiki-researcher.md").read_text(encoding="utf-8")
_QUERY_REF = _read("references", "query.md")
_INGEST_REF = _read("references", "ingest.md")


def _frontmatter_value(agent_text: str, key: str) -> str:
    """Return the frontmatter value for ``key`` (e.g. maxTurns, tools)."""
    in_fm = False
    for line in agent_text.splitlines():
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm and line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return ""


# §4.2 lives between §4.1 and §4.3 — isolate it so assertions don't accidentally
# match text in the ingest section (which also dispatches agents in sequence).
def _section(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i + len(start))
    return text[i : j if j >= 0 else len(text)]


QUERY_SECTION = _section(_QUERY_REF, "### 4.2 Research", "### 4.3 Present Answer")
INGEST_SECTION = _section(_INGEST_REF, "## 6.0 INGEST", "### 6.3")


class QueryFanoutTopologyTests(TestCase):
    """§4.2 is a fan-out-and-synthesize, not a single dispatch."""

    def test_section_was_reshaped(self):
        # The old "Dispatch Wiki Researcher" single-dispatch heading is gone,
        # replaced by the fan-out-and-synthesize framing.
        self.assertIn("### 4.2 Research", SKILL)
        self.assertIn("fan-out-and-synthesize", QUERY_SECTION)

    def test_four_stage_pipeline(self):
        # Route -> Fan Out -> Synthesize -> Citation Verify.
        self.assertIn("4.2.1 Route", SKILL)
        self.assertIn("4.2.2 Fan Out", SKILL)
        self.assertIn("4.2.3 Synthesize", SKILL)
        self.assertIn("4.2.4 Citation Verify", SKILL)

    def test_multi_corner_fans_out_in_one_message(self):
        # N>=2 researchers go in ONE message (parallel) — the prose fan-out.
        self.assertIn("ONE message", QUERY_SECTION)
        self.assertIn("parallel fan-out", QUERY_SECTION.lower())

    def test_single_corner_collapses_to_one_dispatch(self):
        # classify-and-act: a narrow topic must NOT pay the fan-out overhead.
        lower = QUERY_SECTION.lower()
        self.assertIn("single-corner", lower)
        self.assertIn("single dispatch", lower)


class NoSilentCapsTests(TestCase):
    """The fan-out breadth is bounded AND the bound is surfaced, not silent."""

    def test_cap_at_four(self):
        # The fan-out is capped (bound on breadth / cost).
        self.assertIn("Cap at 4", QUERY_SECTION)

    def test_truncation_is_announced(self):
        # When the cap bites, the dropped corners are announced — no silent cap.
        lower = QUERY_SECTION.lower()
        self.assertIn("more than 4", lower)
        self.assertIn("truncation is surfaced, not silent", lower)


class CitationVerifyTests(TestCase):
    """§4.2.4 verifies every citation resolves before the answer is presented."""

    def test_verify_step_present(self):
        self.assertIn("Citation Verify", SKILL)

    def test_drops_unresolvable_citations(self):
        # Dead [[wikilinks]] are dropped (not silently presented unverified).
        lower = QUERY_SECTION.lower()
        self.assertIn("unresolvable", lower)
        self.assertIn("dropped", lower)


class SynthesizeContractTests(TestCase):
    def test_merges_and_dedupes_sources(self):
        lower = QUERY_SECTION.lower()
        self.assertIn("union", lower)
        self.assertIn("deduped", lower)

    def test_notes_contradictions_does_not_pick_a_side(self):
        # A fan-out can return conflicting answers; the merge must surface that,
        # not silently choose one branch's view.
        lower = QUERY_SECTION.lower()
        self.assertIn("contradiction", lower)


class WikiResearcherUnchangedTests(TestCase):
    """The fan-out reuses wiki-researcher unchanged — no bump, no new agent.

    Each branch is a NARROWER topic than the original broad query, so the
    existing maxTurns/tools are sufficient. Pinned so a future edit doesn't
    quietly grow the agent when the prose fan-out already does the work.
    """

    def test_no_maxturns_bump(self):
        self.assertEqual(_frontmatter_value(RESEARCHER, "maxTurns"), "25")

    def test_tools_unchanged_read_only(self):
        tools = _frontmatter_value(RESEARCHER, "tools")
        self.assertIn("Read", tools)
        self.assertIn("Grep", tools)
        self.assertIn("Glob", tools)
        # Still read-only — no Edit/Write/Agent added to "enable" deep research.
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Write", tools)
        self.assertNotIn("Agent", tools)

    def test_skill_states_no_agent_change(self):
        # §4.2 must call out that the fan-out needs no bump / new agent, so the
        # reasoning survives next to the design.
        lower = QUERY_SECTION.lower()
        self.assertIn("unchanged", lower)
        self.assertIn("maxturns", lower)
        self.assertIn("deep-research agent", lower)


class PostIngestDocLinterAdvisoryTests(TestCase):
    """§6.2 chains a one-shot doc-linter advisory after the doc-sync pipeline."""

    def test_doc_linter_dispatch_in_ingest(self):
        self.assertIn("conductor:doc-linter", INGEST_SECTION)

    def test_it_is_advisory_not_the_repair_loop(self):
        # The advisory is one-shot; the loop-until-dry + refute repair loop
        # stays owned by /conductor:wiki-doctor lint.
        lower = INGEST_SECTION.lower()
        self.assertIn("advisory", lower)
        self.assertIn("not", lower)
        self.assertIn("wiki-doctor lint", lower)

    def test_lint_result_block_referenced(self):
        self.assertIn("---DOC LINT RESULT---", INGEST_SECTION)


class ErrorHandlingRevisionTests(TestCase):
    """§5.0's old 'wiki skill never dispatches doc-linter' claim is revised."""

    def test_old_never_claim_is_gone(self):
        self.assertNotIn("never dispatches doc-linter", SKILL)

    def test_revised_claim_acknowledges_the_advisory(self):
        # The revision scopes the dispatch to the §6.2 post-ingest advisory and
        # keeps wiki-doctor as the repair-loop owner.
        self.assertIn("dispatches `doc-linter` only as the §6.2", SKILL)
        self.assertIn("wiki-doctor lint", SKILL)


if __name__ == "__main__":
    main()
