"""Tests for task-type ownership — planner-authored labels + advisory lint.

The pure engine (``task_profiles.rank_tags`` / ``derive_task_tag`` /
``strip_dispatch_tags``) is now the LINT oracle: labels are planner-authored
content, and ``init-from-plan``'s declared-vs-signals advisory compares the
declared leading tag against what the conservative matcher would have
suggested (decision: conductor/design/task-type-ownership.md). The
``propose-tags`` subcommand is deleted — the wiring pins assert the dead
route is gone end-to-end.

Pinned semantics:

- **the lint compares declared vs suggested** — agreement is silent,
  disagreement prints an advisory naming both, never blocks;
- **strip before matching** — the declared tag's own keywords must not match
  themselves;
- **strict plurality + over-tag guard** — the oracle stays conservative
  (``derive_task_tag`` of feature-marker text returns None);
- **the planner authors** — spec-planner pins the judgment framing and the
  deleted command round-trip; TAG_CONFIRM and the relay stay wired.
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.track_state import task_profiles as tp  # noqa: E402

CLI = ROOT / "scripts" / "track-state"
SKILL = ROOT / "skills" / "new-track" / "SKILL.md"
PLANNER = ROOT / "agents" / "spec-planner.md"


def _out_captured(fn, *args, **kwargs):
    """Capture stdout/stderr from a command fn. Returns (parsed_json, stderr)."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue()), sys.stderr.getvalue()
    finally:
        sys.stdout, sys.stderr = old_out, old_err


class _ShippedRegistry(TestCase):
    """Rank against the SHIPPED registry: no project overlay, fresh cache."""

    def setUp(self):
        self._prior = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        tp._load.cache_clear()

    def tearDown(self):
        if self._prior is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior
        tp._load.cache_clear()


class RankTagsTests(_ShippedRegistry):
    """``rank_tags`` — the pure lint-engine core (no plurality/guard/fail-open)."""

    def test_empty_or_blank_text_ranks_nothing(self):
        self.assertEqual(tp.rank_tags(""), [])
        self.assertEqual(tp.rank_tags("   \n  "), [])

    def test_hits_are_distinct_per_signal(self):
        # One signal repeated in the text counts ONCE (distinct-hit scoring —
        # "docs ... docs ... docs" is 1 hit, not 3).
        ranked = tp.rank_tags("update the docs; the docs index; docs cleanup")
        top = ranked[0]
        self.assertEqual(top["tag"], "Docs")
        self.assertEqual(top["hits"], ["docs"])

    def test_ranked_desc_and_registry_order_stable(self):
        # A genuine 1-1 tie: the LIST is ordered (score desc, registry order
        # within a tie) and deterministic.
        ranked = tp.rank_tags("survey the readme")
        self.assertEqual([(c["tag"], c["score"]) for c in ranked],
                         [("Explore", 1), ("Docs", 1)])

    def test_deterministic_repeat(self):
        text = "explore and map the unfamiliar ingestion pipeline"
        self.assertEqual(tp.rank_tags(text), tp.rank_tags(text))

    def test_candidates_capped_at_three(self):
        # The JSON surface stays small even with a text hitting every tag.
        text = ("explore and map the readme config documentation, bump the "
                "dependency, verify by hand, walkthrough the staging deploy")
        self.assertGreater(len([t for t in tp.TAG_VOCAB()
                                if tp.auto_propose_for(t)]), 3)
        self.assertLessEqual(len(tp.rank_tags(text)), 3)

    def test_word_boundary_matching(self):
        # The shared word-boundary matcher: "ci" glued inside "discipline"
        # must not hit Chore's `ci` signal — one level up from the same pin
        # in test_derive_task_tag.
        ranked = tp.rank_tags("add discipline and specificity to input parsing")
        self.assertNotIn("Chore", [c["tag"] for c in ranked])

    def test_opt_in_tags_never_surface(self):
        # auto_propose: false rows ([Refactor], [Migrate]) are authored, never
        # ranked — even when their when_to_use tokens saturate the text.
        ranked = tp.rank_tags(
            "refactor the user service for readability; upgrade and rename")
        tags = [c["tag"] for c in ranked]
        self.assertNotIn("Refactor", tags)
        self.assertNotIn("Migrate", tags)


class StripDispatchTagsTests(_ShippedRegistry):
    """``strip_dispatch_tags`` — the lint's inverse of the declared tag."""

    def test_strips_declared_tag_leaving_clean_description(self):
        self.assertEqual(tp.strip_dispatch_tags("[Explore] map the pipeline"),
                         "map the pipeline")
        self.assertEqual(tp.strip_dispatch_tags("Task A"), "Task A")

    def test_whitespace_collapses(self):
        self.assertEqual(tp.strip_dispatch_tags("[Docs]  update  readme "),
                         "update readme")

    def test_non_vocab_brackets_survive(self):
        # Commit-SHA suffix brackets ([abc1234] on failed task lines) are not
        # registry tags — the vocab-built extractor leaves them alone.
        self.assertEqual(tp.strip_dispatch_tags("[Explore] Task A [abc1234]"),
                         "Task A [abc1234]")


class GoldenCorpusTests(_ShippedRegistry):
    """The golden lint-oracle corpus — realistic task descriptions pinned to
    ``derive_task_tag``'s conservative suggestion against the SHIPPED
    registry. A signal edit that shifts any row is an intentional change:
    update the row in the SAME commit as the registry edit (the same contract
    the propose-shape corpus holds)."""

    # (description, derive_task_tag suggestion)
    GOLDEN = (
        ("Add a payment retry queue with idempotency keys", None),
        ("Update the README installation section", "Docs"),
        ("Explore and map the unfamiliar ingestion pipeline", "Explore"),
        ("Bump the dependency versions and update the renovate pin", "Chore"),
        # The 1-1 tie: ambiguity, not a suggestion (strict plurality).
        ("survey the readme", None),
        # Feature work that touches config: the over-tag guard suppresses.
        ("add user login feature that reads db config", None),
        ("Fix the login redirect loop", None),
    )

    def test_corpus_pins(self):
        for desc, suggested in self.GOLDEN:
            with self.subTest(desc=desc):
                self.assertEqual(tp.derive_task_tag(desc), suggested)


class TagAdvisoryTests(_ShippedRegistry):
    """``_tag_signal_advisories`` — the R1 lint (agreement silent, disagreement
    printed, never blocking)."""

    @staticmethod
    def _structure(*task_names):
        return {"phases": [{"name": "Phase 1", "tasks": [
            {"name": n} for n in task_names]}]}

    def _advisories(self, *task_names):
        from scripts.track_state.quality import _tag_signal_advisories
        return _tag_signal_advisories(self._structure(*task_names))

    def test_agreement_is_silent(self):
        # Plain implementation task: declared default, signals None → silent.
        self.assertEqual(self._advisories("Add a payment retry queue"), [])

    def test_declared_explore_signals_none_advises(self):
        # THE planner-judgment case: an exploration task whose prose misses
        # every signal keyword. Declared [Explore] is CORRECT; the advisory
        # prompts a double-check, never an override.
        adv = self._advisories("[Explore] find out how auth really behaves")
        self.assertEqual(len(adv), 1)
        self.assertIn("P1.T1", adv[0])
        self.assertIn("[Explore]", adv[0])
        self.assertIn("untagged", adv[0])

    def test_untagged_signals_docs_advises(self):
        adv = self._advisories("Update the README installation section")
        self.assertEqual(len(adv), 1)
        self.assertIn("declared untagged", adv[0])
        self.assertIn("[Docs]", adv[0])

    def test_positions_and_multiple_tasks(self):
        adv = self._advisories("Add a retry queue",
                               "[Explore] survey the ingestion pipeline")
        self.assertEqual(adv, [])  # default+None silent; Explore declared+hit

    def test_lint_survives_matcher_error(self):
        # Fail-open: a registry blow-up inside the oracle yields no advisory
        # (init must never block on the lint).
        import scripts.track_state.quality as quality
        orig = quality.derive_task_tag
        quality.derive_task_tag = None  # calling None raises TypeError
        try:
            self.assertEqual(quality._tag_signal_advisories(
                self._structure("Update the README")), [])
        finally:
            quality.derive_task_tag = orig


class InitFromPlanLintTests(_ShippedRegistry):
    """``cmd_init_from_plan --check`` carries the advisories (telemetry)."""

    def test_check_output_carries_tag_advisories(self):
        from scripts.track_state.quality import cmd_init_from_plan
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "plan.md").write_text(
                "# Plan\n\n## Phase 1: Build\n"
                "- [ ] Update the README installation section <!-- AC-1 -->\n",
                encoding="utf-8")
            r, _err = _out_captured(cmd_init_from_plan, tmp, "lint_20260831",
                                    "feature", "d", check=True)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r.get("tag_advisories", [])), 1)
        self.assertIn("[Docs]", r["tag_advisories"][0])

    def test_check_silent_when_agreeing(self):
        from scripts.track_state.quality import cmd_init_from_plan
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "plan.md").write_text(
                "# Plan\n\n## Phase 1: Build\n"
                "- [ ] Add a payment retry queue <!-- AC-1 -->\n",
                encoding="utf-8")
            r, _err = _out_captured(cmd_init_from_plan, tmp, "lint_20260831",
                                    "feature", "d", check=True)
        self.assertTrue(r["ok"])
        self.assertNotIn("tag_advisories", r)


class ProposeTagsDeletionTests(TestCase):
    """The dead route is gone end-to-end: no subcommand, no CLI arm, no
    sanctioned-set entry, no help entry (single-source-authority precedent —
    a matcher-owned label implies it still owns labels)."""

    def test_misc_command_deleted(self):
        from scripts.track_state import misc
        self.assertFalse(hasattr(misc, "cmd_propose_tags"))

    def test_cli_dispatch_branch_gone(self):
        src = (ROOT / "scripts" / "track_state" / "cli.py").read_text(
            encoding="utf-8")
        self.assertNotIn('cmd == "propose-tags"', src)

    def test_no_track_dir_resolution_entry_gone(self):
        from scripts.track_state import cli
        self.assertNotIn("propose-tags", cli._TD_NO_RESOLUTION_COMMANDS)

    def test_help_entry_gone(self):
        from scripts.track_state.cli import COMMAND_HELP
        self.assertNotIn("propose-tags", COMMAND_HELP)

    def test_naming_group_member_gone(self):
        from scripts.track_state.cli import _COMMAND_GROUPS
        groups = {name: cmds for name, cmds in _COMMAND_GROUPS}
        self.assertNotIn("propose-tags", groups["Naming"])

    def test_sanctioned_set_gone(self):
        # pre-command-check file-loads commands.py and derives the sanctioned
        # set — the derivation itself is the guarantee under test.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pcc_tto", ROOT / "scripts" / "pre-command-check.py")
        pcc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pcc)
        self.assertNotIn("propose-tags", pcc._SANCTIONED_TS_SUBCOMMANDS)


class PlannerAuthorshipTests(TestCase):
    """The R1 pins: spec-planner authors the label from the registry vocab
    (judgment, no command round-trip), the advisory is named, TAG_CONFIRM
    stays wired, and the Bash tool the §3.1 fetch needs is allowed."""

    def setUp(self):
        self.planner = PLANNER.read_text(encoding="utf-8")

    def test_planner_authors_not_matches(self):
        self.assertIn("YOU author the label", self.planner)
        self.assertIn("guidance for your judgment", self.planner)

    def test_command_roundtrip_deleted(self):
        self.assertNotIn("propose-tags", self.planner)
        self.assertNotIn("adopt `proposed`", self.planner)

    def test_deliverable_classification_rule_present(self):
        self.assertIn("classify by the task's deliverable", self.planner)

    def test_advisory_pointer_present(self):
        self.assertIn("init-from-plan --check", self.planner)
        self.assertIn("advisory", self.planner)

    def test_tools_line_has_bash(self):
        # The §3.1 registry-doc fetch is a Bash command — the tools line must
        # allow it.
        self.assertIn("tools: Read, Write, Grep, Glob, Edit, Bash",
                      self.planner)

    def test_result_block_carries_tag_confirm(self):
        self.assertIn("TAG_CONFIRM:", self.planner)


class ConfirmRelayTests(TestCase):
    """new-track §2.3 relay pins — the orchestrator owns the human review loop
    (the spec-reviewer precedent), one AskUserQuestion round, drops applied
    inline."""

    def setUp(self):
        self.skill = SKILL.read_text(encoding="utf-8")

    def test_relay_present_after_parse(self):
        self.assertIn("tag-confirm relay", self.skill)
        self.assertIn("TAG_CONFIRM:", self.skill)

    def test_one_askuserquestion_round(self):
        self.assertIn("ONE `AskUserQuestion` round", self.skill)

    def test_drop_option_is_default_tdd(self):
        self.assertIn("Drop to default TDD", self.skill)

    def test_drop_applied_inline_via_edit(self):
        self.assertIn("inline via Edit", self.skill)

    def test_regen_reparse_reruns_relay(self):
        # The regen loop's re-parse routes through the relay too — a re-dispatch
        # that re-tags a declined task must not silently land.
        self.assertIn("run the **tag-confirm relay** above", self.skill)
        self.assertIn("never re-ask", self.skill)


if __name__ == "__main__":
    main()
