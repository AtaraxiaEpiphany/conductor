"""Structural tests for the doc-syncer Phase1/Phase2 split (G1).

``agents/doc-syncer.md`` was split at the §6.0-commit / §7.0-regen boundary into
two narrower agents, dispatched in sequence by the orchestrator:

- ``agents/corpus-writer.md`` (Phase 1): two-step analysis → AskUserQuestion
  proposals → user-confirmed corpus edits + graduation → the
  ``docs(conductor): Synchronize docs …[{TRACK_ID}]`` commit.
- ``agents/wiki-synthesizer.md`` (Phase 2): overview regen + purpose + log +
  inline drift gate → the ``docs(conductor): Wiki sync …[{TRACK_ID}]`` commit.
  Runs automatically (no AskUserQuestion).

Plus a skill-level ``conductor:wiki-differ`` advisory verify of the regenerated
overview (post-commit, non-blocking — consistent with §7.3's "verification never
blocks the commit"). Both agents stay Agent-free (no nest-dispatch); the verify
is sequenced by the skill, honoring the conductor model.

These assert the wiring so the split (and the firewall seam between the two
phases) can't be silently reverted, and so the two-phase dispatch sequence in
both callers (post-loop track mode + wiki ingest ad-hoc mode) is pinned.
"""
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"


def _frontmatter_tools(agent_text: str) -> str:
    in_fm = False
    for line in agent_text.splitlines():
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm and line.startswith("tools:"):
            return line.split("tools:", 1)[1].strip()
    return ""


class SplitTopologyTests(TestCase):
    """The split is structural: doc-syncer is gone, two narrower agents exist."""

    def test_doc_syncer_agent_deleted(self):
        # A leftover doc-syncer.md would be confusing cruft now that the pipeline
        # is split. Pinned so a half-revert (re-adding the monolith) is caught.
        self.assertFalse((AGENTS / "doc-syncer.md").exists())

    def test_corpus_writer_agent_exists(self):
        self.assertTrue((AGENTS / "corpus-writer.md").exists())

    def test_wiki_synthesizer_agent_exists(self):
        self.assertTrue((AGENTS / "wiki-synthesizer.md").exists())

    def test_corpus_writer_has_askuserquestion_wiki_synthesizer_does_not(self):
        # The split's defining seam: Phase 1 is interactive (confirms corpus
        # edits), Phase 2 is automatic (auto-owned synthesis). Pinned so the
        # interactivity boundary can't drift back into Phase 2.
        cw = (AGENTS / "corpus-writer.md").read_text(encoding="utf-8")
        ws = (AGENTS / "wiki-synthesizer.md").read_text(encoding="utf-8")
        self.assertIn("AskUserQuestion", _frontmatter_tools(cw))
        self.assertNotIn("AskUserQuestion", _frontmatter_tools(ws))

    def test_neither_phase_nest_dispatches(self):
        # Conductor invariant: subagents don't have the Agent tool (no
        # nest-dispatch). The wiki-differ verify is sequenced by the SKILL, not
        # nested inside a phase. Pinned so a future edit doesn't give a phase the
        # Agent tool to "simplify" the verify.
        for name in ("corpus-writer.md", "wiki-synthesizer.md"):
            tools = _frontmatter_tools((AGENTS / name).read_text(encoding="utf-8"))
            self.assertNotIn("Agent", tools, f"{name} must not have the Agent tool")


class FirewallSeamTests(TestCase):
    """The two phases must not claim each other's write surface."""

    def setUp(self):
        self.cw = (AGENTS / "corpus-writer.md").read_text(encoding="utf-8")
        self.ws = (AGENTS / "wiki-synthesizer.md").read_text(encoding="utf-8")

    def test_corpus_writer_forbids_overview_purpose_log(self):
        # overview/purpose/log are wiki-synthesizer's Phase 2. corpus-writer must
        # name this prohibition so it never races Phase 2 on the wiki files.
        for token in ("overview.md", "purpose.md", "log.md"):
            self.assertIn(token, self.cw)

    def test_wiki_synthesizer_forbids_scoped_doc_content_edits(self):
        # Scoped corpus doc CONTENT is corpus-writer's Phase 1. wiki-synthesizer
        # only adds ## See Also cross-refs (if Phase 1 confirmed them) and
        # synthesizes — it must not edit scoped-doc bodies.
        self.assertIn("scoped corpus doc", self.ws)

    def test_each_phase_names_the_other(self):
        # Each phase must name its counterpart so the pipeline ordering is
        # self-documenting (Phase 1 → Phase 2) inside the agents themselves.
        self.assertIn("wiki-synthesizer", self.cw)
        self.assertIn("corpus-writer", self.ws)


class CorpusWriterTwoStepCoTTests(TestCase):
    """The two-step CoT (O6) + idempotent no-op live in corpus-writer (Phase 1)."""

    def setUp(self):
        self.agent = (AGENTS / "corpus-writer.md").read_text(encoding="utf-8")

    def test_two_step_framing_present(self):
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
        self.assertIn("SKIPPED", self.agent)
        self.assertIn("idempotent ingest", self.agent)

    def test_points_at_procedure_doc(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/runtime/contracts/doc-sync-procedure.md", self.agent)


class WikiSynthesizerPhaseTests(TestCase):
    """Phase 2 synthesis specs (overview §B + purpose §C) live in wiki-synthesizer."""

    def setUp(self):
        self.agent = (AGENTS / "wiki-synthesizer.md").read_text(encoding="utf-8")

    def test_points_at_procedure_doc(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/runtime/contracts/doc-sync-procedure.md", self.agent)

    def test_drift_gate_present(self):
        # The inline §4.4 drift gate (broken wikilinks + moved + coverage) with
        # auto-owned repair, and the load-bearing "never blocks the commit" rule.
        self.assertIn("Drift Gate", self.agent)
        self.assertIn("never blocks the commit", self.agent)

    def test_coverage_check_present(self):
        # Phase 2 owns the orphan/coverage check (docs with zero inbound
        # wikilinks from overview) — the one drift dimension the Phase 1 inline
        # analysis doesn't cover.
        self.assertIn("coverage check", self.agent.lower())

    def test_commit_is_load_bearing(self):
        self.assertIn("[{TRACK_ID}]", self.agent)
        self.assertIn("archive", self.agent)


class DocSyncProcedureExtractionTests(TestCase):
    """The per-document table + proposal template + synthesis specs were relocated
    to ${CLAUDE_PLUGIN_ROOT}/runtime/contracts/doc-sync-procedure.md. Both phases of the split now consume
    it; guard the pointer + the consumer provenance so they can't drift apart."""

    def setUp(self):
        self.cw = (AGENTS / "corpus-writer.md").read_text(encoding="utf-8")
        self.ws = (AGENTS / "wiki-synthesizer.md").read_text(encoding="utf-8")
        self.proc_path = ROOT / "runtime" / "contracts" / "doc-sync-procedure.md"
        self.proc = self.proc_path.read_text(encoding="utf-8")

    def test_both_phases_point_at_procedure_doc(self):
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/runtime/contracts/doc-sync-procedure.md", self.cw)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/runtime/contracts/doc-sync-procedure.md", self.ws)

    def test_procedure_doc_frontmatter_names_both_consumers(self):
        # The old frontmatter named only `agents/doc-syncer`; after the split it
        # must name both new consumers as sources.
        self.assertIn("agents/corpus-writer", self.proc)
        self.assertIn("agents/wiki-synthesizer", self.proc)
        self.assertNotIn("agents/doc-syncer", self.proc)

    def test_procedure_doc_exists_and_frontmatter_compliant(self):
        self.assertTrue(self.proc.startswith("---\n"))
        self.assertIn("type: concept", self.proc)
        self.assertIn("last_verified:", self.proc)

    def test_procedure_doc_carries_relocated_content(self):
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
        for token in ("Overview Regeneration Spec", "Purpose Update Spec"):
            self.assertIn(token, self.proc)


class DocSyncTwoLayerCrossReferenceTests(TestCase):
    """The two-phase engine is described in two places: this procedure contract
    (the *content/reference* layer — what to analyze + synthesize) and the wiki
    skill's `references/doc-sync-pipeline.md` (the *orchestration* layer — how to
    dispatch, sequence, and parse in ad-hoc mode). They are complementary, not
    duplicative; bidirectional cross-refs keep a future edit from silently forking
    the "what Phase 1/2 does" wording (the D5 two-files-must-agree hazard)."""

    def setUp(self):
        self.proc = (ROOT / "runtime" / "contracts" / "doc-sync-procedure.md").read_text(encoding="utf-8")
        self.pipe = (ROOT / "skills" / "wiki" / "references" / "doc-sync-pipeline.md").read_text(encoding="utf-8")

    def test_procedure_points_at_pipeline(self):
        self.assertIn("doc-sync-pipeline", self.proc)

    def test_pipeline_points_at_procedure(self):
        self.assertIn("doc-sync-procedure", self.pipe)


class HookAndDispatcherWiringTests(TestCase):
    """The split's mechanical wiring: hook matchers, recovery/reminder registries,
    and the two caller dispatch sequences (post-loop + wiki ingest)."""

    def setUp(self):
        self.hooks = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        self.post_loop = (ROOT / "templates" / "post-loop.md").read_text(encoding="utf-8")
        self.wiki = (ROOT / "skills" / "wiki" / "SKILL.md").read_text(encoding="utf-8")

    def test_hooks_matchers_include_both_phases(self):
        # Both subagent matchers are matcherless (the roster gates), so the two
        # new agents reach the hooks with the built-ins; the dead merged name
        # must not linger anywhere in hooks.json.
        import json
        for event in ("SubagentStart", "SubagentStop"):
            for entry in json.loads(self.hooks)["hooks"][event]:
                self.assertNotIn("matcher", entry)
        self.assertNotIn("doc-syncer", self.hooks)

    def test_stop_stdout_block_registry_has_both_phases(self):
        # Both phases carry recovery: "stdout-block" rows (close-tag recovery
        # contracts) in the agent-roster registry; the dead merged name is gone.
        sys.path.insert(0, str(ROOT / "scripts"))
        from track_state import agent_roster as ar
        self.assertEqual(ar.recovery_kind_for("corpus-writer"), "stdout-block")
        self.assertEqual(ar.recovery_kind_for("wiki-synthesizer"), "stdout-block")
        self.assertNotIn("doc-syncer", ar.merged_agent_names())

    def test_start_reminder_registry_has_both_phases(self):
        # Both phases carry roster fence rows (SubagentStart reminders).
        sys.path.insert(0, str(ROOT / "scripts"))
        from track_state import agent_roster as ar
        self.assertIn("---DOC SYNC RESULT---", ar.reminder_for("corpus-writer"))
        self.assertIn("---DOC SYNC RESULT---", ar.reminder_for("wiki-synthesizer"))

    def test_post_loop_dispatches_three_step_pipeline(self):
        # post-loop §6.0 sequences corpus-writer → wiki-synthesizer → wiki-differ.
        self.assertIn("conductor:corpus-writer", self.post_loop)
        self.assertIn("conductor:wiki-synthesizer", self.post_loop)
        self.assertIn("conductor:wiki-differ", self.post_loop)
        self.assertNotIn("conductor:doc-syncer", self.post_loop)

    def test_post_loop_two_tier_resumability_gate(self):
        # The split adds a clean interruption point between Phase 1 and Phase 2,
        # so the gate must distinguish "Phase 1 done" (Synchronize docs commit)
        # from "all done" (Wiki sync commit). Pin the two-tier check.
        self.assertIn("two-tier", self.post_loop)
        self.assertIn("Wiki sync for track", self.post_loop)
        self.assertIn("resuming doc-sync at Phase 2", self.post_loop)

    def test_post_loop_advisory_verify_is_non_blocking(self):
        # The wiki-differ verify is advisory (post-commit), consistent with the
        # plugin's non-blocking verification philosophy.
        self.assertIn("Advisory verify", self.post_loop)
        self.assertIn("non-blocking", self.post_loop)

    def test_wiki_ingest_dispatches_three_step_pipeline(self):
        self.assertIn("conductor:corpus-writer", self.wiki)
        self.assertIn("conductor:wiki-synthesizer", self.wiki)
        self.assertIn("conductor:wiki-differ", self.wiki)


class ArchiveGateIntactTests(TestCase):
    """The track-state archive gate greps git log for a `[{TRACK_ID}]` commit
    (docs_synced_for_track) — it is agnostic to which agent made the commit. Both
    corpus-writer (Phase 1) and wiki-synthesizer (Phase 2) make `[{TRACK_ID}]`
    commits, so the gate holds. Pin the commit-tag presence in both agents."""

    def setUp(self):
        self.cw = (AGENTS / "corpus-writer.md").read_text(encoding="utf-8")
        self.ws = (AGENTS / "wiki-synthesizer.md").read_text(encoding="utf-8")

    def test_phase1_commit_satisfies_gate_alone(self):
        # corpus-writer's Phase 1 commit carries [{TRACK_ID}] and the agent notes
        # it satisfies the archive gate on its own (before Phase 2).
        self.assertIn("Synchronize docs", self.cw)
        self.assertIn("satisfies the archive gate on its own", self.cw)

    def test_phase2_commit_also_carries_track_id(self):
        self.assertIn("Wiki sync", self.ws)
        self.assertIn("[{TRACK_ID}]", self.ws)


if __name__ == "__main__":
    main()
