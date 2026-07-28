"""Structural tests for the project-analyzer result trim + analysis.json (#14).

`project-analyzer` stays READ-ONLY (no Write tool) — its stdout block is the only
channel, so a deep lean-block+pointer trim is incompatible with keeping the agent
read-only while still persisting the full tree. The realization: (a) a SHALLOW
trim drops the genuinely-dead fields the agent used to emit (`maturity` — always
`brownfield` since this agent runs only on brownfield projects; `suggested_styleguides`
— `setup` derives styleguides from `languages` via its own table; `suggested_workflow`
— `setup` uses a fixed workflow); (b) `setup` §2.0 persists the full detection tree
the agent returns to `conductor/.conductor/analysis.json` for later consumers
(doc-syncer seeding, future wiki queries). These assert the wiring so the trim and
the persistence can't be silently reverted, and so the agent cannot quietly gain a
Write capability.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


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


class ProjectAnalyzerContractTests(TestCase):
    def setUp(self):
        self.agent = (ROOT / "agents" / "project-analyzer.md").read_text(encoding="utf-8")

    def test_agent_stays_read_only(self):
        # The plan's explicit constraint: keep this agent read-only. A brownfield
        # analyzer reads someone's existing codebase — it must NOT gain a Write
        # capability (unlike wiki-differ/code-reviewer, whose scoped writes are to
        # conductor-owned artifacts). Pinned so the G3 trim can't be "solved" by
        # silently adding Write here.
        tools = _frontmatter_tools(self.agent)
        self.assertNotIn("Write", tools)
        self.assertNotIn("Edit", tools)

    def test_dead_fields_trimmed_from_block(self):
        # `maturity` is constant (always brownfield), `suggested_styleguides` and
        # `suggested_workflow` are unused (setup derives both itself). Emitting them
        # is dead weight that misleads — they're trimmed from the result block.
        # Assert each is absent from the ---ANALYSIS RESULT--- block specifically.
        block = self.agent.split("---ANALYSIS RESULT---", 1)[1]
        block = block.split("---END ANALYSIS RESULT---", 1)[0]
        self.assertNotIn('"maturity"', block)
        self.assertNotIn('"suggested_styleguides"', block)
        self.assertNotIn('"suggested_workflow"', block)

    def test_live_fields_retained(self):
        # The fields setup actually consumes inline are retained.
        block = self.agent.split("---ANALYSIS RESULT---", 1)[1]
        block = block.split("---END ANALYSIS RESULT---", 1)[0]
        self.assertIn('"project_type"', block)
        self.assertIn('"languages"', block)
        self.assertIn('"frameworks"', block)
        self.assertIn('"architecture"', block)
        self.assertIn('"build_tools"', block)
        self.assertIn('"test_frameworks"', block)


class SetupPersistenceWiringTests(TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")

    def test_setup_persists_full_tree_to_analysis_json(self):
        # The high-value half of #14: the analyzer's one-pass detection is
        # persisted to conductor/.conductor/analysis.json so it is not lost —
        # later consumers (doc-syncer seeding, future wiki queries) read it back.
        # The persist is code-owned via persist-analysis.py (tolerant parse) so a
        # weak model's near-miss JSON doesn't crash setup or persist garbage.
        self.assertIn(".conductor/analysis.json", self.skill)
        self.assertIn("persist-analysis.py", self.skill)
        self.assertIn("persist the full detection tree", self.skill)

    def test_setup_tolerant_parse_and_bounded_redispatch(self):
        # #7: the parse is tolerant (repairs fences/trailing-commas/smart-quotes)
        # and a totally-unparseable block triggers ONE bounded re-dispatch rather
        # than persisting garbage. Pinned so the deterministic enforcement can't
        # be reverted to a fragile hand-rolled ``json.loads``.
        self.assertIn("tolerant", self.skill)
        self.assertIn("re-dispatch `conductor:project-analyzer` ONCE", self.skill)

    def test_setup_operates_on_live_fields_after_persist(self):
        # After persisting, setup's subsequent steps use the live fields
        # (languages, frameworks) — confirming the persistence is non-destructive
        # (the block is still parsed for the Tech Stack pre-fill).
        self.assertIn("`languages`", self.skill)
        self.assertIn("`frameworks`", self.skill)


if __name__ == "__main__":
    main()
