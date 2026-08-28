"""Tests for ``track-state propose-tags`` — the tag axis of planning-as-data.

The pure selection engine behind spec-planner §4.2's tag step:
``task_profiles.rank_tags`` signal-matches a task description against each
auto-proposable tag's ``signals`` (the task-layer mirror of
``workflow_shapes.rank_shapes``), and ``misc.cmd_propose_tags`` composes the
proposal JSON the planner consumes (proposed / confirm_required / candidates —
the planner never re-matches the signal tables by hand).

Pinned semantics (the gate-confirm asymmetry):

- **gate-neutral is silent** — ``confirm_required=false``, no relay;
- **gate-dropping asks** — a proposed tag that exempts TDD and/or coverage
  sets ``confirm_required=true``; the planner lists the task under
  ``TAG_CONFIRM:`` and new-track §2.3 relays ONE keep-vs-drop question;
- **the over-tag guard does not suppress here** — a weak-hit exempt candidate
  on feature-marker text is SURFACED with a confirm (derive_task_tag of the
  same text returns None — the asymmetry pin);
- **strict plurality** — a tied top score is ambiguity, not a proposal:
  ``proposed=null``, leave the task untagged;
- the swap pins keep the planner's hand-mirroring deleted (mechanical matcher
  only) and the relay wired on both sides.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.track_state import task_profiles as tp  # noqa: E402
from scripts.track_state.misc import cmd_propose_tags  # noqa: E402

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
    """``rank_tags`` — the pure core (no plurality/guard/fail-open)."""

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


class ProposeTagsTests(_ShippedRegistry):
    """``cmd_propose_tags`` — the proposal contract."""

    def _propose(self, description):
        result, _err = _out_captured(cmd_propose_tags, description)
        return result

    def test_docs_description_proposes_and_confirms(self):
        r = self._propose("Update the README installation section")
        self.assertTrue(r["ok"])
        self.assertEqual(r["proposed"], "Docs")
        self.assertTrue(r["confirm_required"])  # Docs drops both gates
        # The chosen entry carries the resolved row — the planner never
        # re-derives route/exemptions from the registry.
        chosen = r["chosen"]
        self.assertEqual(chosen["tag"], "Docs")
        self.assertTrue(chosen["tdd_exempt"])
        self.assertTrue(chosen["coverage_exempt"])
        self.assertTrue(chosen["over_tag_risk"])
        self.assertIn("Markdown/docs ONLY", chosen["when_to_use"])
        # The default entry is the untagged alternative for the relay's
        # drop option.
        self.assertIsNone(r["default"]["tag"])
        self.assertFalse(r["default"]["tdd_exempt"])

    def test_plain_feature_proposes_nothing(self):
        r = self._propose("Add a payment retry queue with idempotency keys")
        self.assertTrue(r["ok"])
        self.assertIsNone(r["proposed"])
        self.assertFalse(r["confirm_required"])
        self.assertEqual(r["candidates"], [])
        self.assertIsNone(r["chosen"])

    def test_tie_falls_back_to_untagged_silently(self):
        # "survey the readme" hits Explore AND Docs 1-1 — a tie is ambiguity,
        # not a proposal (strict plurality); both stay visible.
        r = self._propose("survey the readme")
        self.assertIsNone(r["proposed"])
        self.assertFalse(r["confirm_required"])
        self.assertEqual([c["score"] for c in r["candidates"]], [1, 1])

    def test_over_tag_asymmetry_pin(self):
        # THE pin: feature-marker text with a weak exempt hit — derive_task_tag
        # suppresses to None (the over-tag guard), propose-tags SURFACES the
        # candidate with confirm_required=true (surface + confirm is the bar).
        desc = "add user login feature that reads db config"
        self.assertIsNone(tp.derive_task_tag(desc))
        r = self._propose(desc)
        self.assertTrue(r["ok"])
        self.assertEqual(r["proposed"], "Config")
        self.assertTrue(r["confirm_required"])
        self.assertEqual(r["candidates"][0]["hits"], ["config"])

    def test_gate_neutral_overlay_tag_confirms_false(self):
        # A project tag with signals but NO exemptions proposes SILENTLY —
        # the asymmetry is the gate drop, not the proposal itself.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "conductor" / "workflow"
            d.mkdir(parents=True)
            (d / "task-type-profiles.json").write_text(json.dumps(
                {"tags": {"Deploy": {
                    "route": "executor",
                    "when_to_use": "Deploy the staging stack",
                    "signals": ["deployit", "stage deploy"]}}}),
                encoding="utf-8")
            os.environ["CLAUDE_PROJECT_DIR"] = tmp
            tp._load.cache_clear()
            try:
                r, _ = _out_captured(cmd_propose_tags,
                                     "deployit the api service")
            finally:
                # The tmpdir dies with the context manager — never let the
                # poisoned CLAUDE_PROJECT_DIR leak into later tests.
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
                tp._load.cache_clear()
            self.assertTrue(r["ok"])
            self.assertEqual(r["proposed"], "Deploy")
            self.assertFalse(r["confirm_required"])
            self.assertFalse(r["chosen"]["tdd_exempt"])
            self.assertFalse(r["chosen"]["coverage_exempt"])

    def test_empty_description_errors(self):
        r = self._propose("   ")
        self.assertFalse(r["ok"])
        self.assertIn("missing description", r["error"])


class GoldenCorpusTests(_ShippedRegistry):
    """The golden propose-tags corpus — realistic task descriptions pinned to
    their proposals against the SHIPPED registry. A signal edit that shifts any
    row is an intentional change: update the row in the SAME commit as the
    registry edit (the same contract the propose-shape corpus holds)."""

    # (description, proposed, confirm_required, candidates[(tag, score)])
    GOLDEN = (
        ("Add a payment retry queue with idempotency keys", None, False, []),
        ("Update the README installation section",
         "Docs", True, [("Docs", 1)]),
        ("Explore and map the unfamiliar ingestion pipeline",
         "Explore", True, [("Explore", 2)]),
        ("Bump the dependency versions and update the renovate pin",
         "Chore", True, [("Chore", 3)]),
        # The 1-1 tie: ambiguity, not a proposal (strict plurality → untagged).
        ("survey the readme", None, False,
         [("Explore", 1), ("Docs", 1)]),
        # Feature work that touches config: surfaced + confirmed (the guard's
        # silent suppression stays in derive_task_tag — this is the asymmetry).
        ("add user login feature that reads db config",
         "Config", True, [("Config", 1)]),
        ("Fix the login redirect loop", None, False, []),
    )

    def test_corpus_pins(self):
        for desc, proposed, confirm, cands in self.GOLDEN:
            with self.subTest(desc=desc):
                r, _ = _out_captured(cmd_propose_tags, desc)
                self.assertTrue(r["ok"])
                self.assertEqual(r["proposed"], proposed)
                self.assertEqual(r["confirm_required"], confirm)
                self.assertEqual(
                    [(c["tag"], c["score"]) for c in r["candidates"]],
                    cands)


class ProposeTagsCLITests(_ShippedRegistry):
    """The subprocess surface — the cli.py elif arm + the non-resolution of the
    description positional (a free-text description is never a track-dir
    lookup; a bare `no_match` resolution exit would break the command)."""

    def _run(self, *args):
        env = {**os.environ}
        env.pop("CLAUDE_PROJECT_DIR", None)
        return subprocess.run(
            [sys.executable, str(CLI), "propose-tags", *args],
            capture_output=True, text=True, env=env, cwd=str(ROOT))

    def test_cli_happy_path(self):
        proc = self._run("Update the README installation section")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["proposed"], "Docs")
        self.assertTrue(payload["confirm_required"])

    def test_cli_help_lists_command(self):
        proc = subprocess.run(
            [sys.executable, str(CLI), "help", "propose-tags"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("propose-tags", proc.stdout)

    def test_takes_no_track_dir_resolution(self):
        from scripts.track_state import cli
        self.assertIn("propose-tags", cli._TD_NO_RESOLUTION_COMMANDS)


class WiringTests(TestCase):
    """cli/commands wiring (test_command_surface auto-enforces the rest)."""

    def test_help_entry_exists(self):
        from scripts.track_state.cli import COMMAND_HELP
        self.assertIn("propose-tags", COMMAND_HELP)

    def test_naming_group_member(self):
        from scripts.track_state.cli import _COMMAND_GROUPS
        groups = {name: cmds for name, cmds in _COMMAND_GROUPS}
        self.assertIn("propose-tags", groups["Naming"])

    def test_sanctioned_via_precommand_guard(self):
        # pre-command-check file-loads commands.py and derives the sanctioned
        # set — the derivation itself is the guarantee under test.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pcc_propose_tags", ROOT / "scripts" / "pre-command-check.py")
        pcc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pcc)
        self.assertIn("propose-tags", pcc._SANCTIONED_TS_SUBCOMMANDS)

    def test_dispatch_branch_exists(self):
        src = (ROOT / "scripts" / "track_state" / "cli.py").read_text(
            encoding="utf-8")
        self.assertIn('cmd == "propose-tags"', src)


class SpecPlannerSwapTests(TestCase):
    """The §3.1/§4.2 swap pins: the planner matches MECHANICALLY (the
    hand-mirroring of the registry-doc signal tables is deleted) and can
    actually run the command (Bash in its tools line)."""

    def setUp(self):
        self.planner = PLANNER.read_text(encoding="utf-8")

    def test_planner_calls_the_matcher(self):
        self.assertIn("track-state propose-tags", self.planner)

    def test_hand_mirror_sentence_gone(self):
        # The pre-swap clause re-implemented the matcher in prose — the exact
        # drift liability this swap deletes.
        self.assertNotIn("same way `derive_task_tag` does", self.planner)
        self.assertNotIn("`derive_task_tag`", self.planner)

    def test_tools_line_has_bash(self):
        # The §3.1 fetch and §4.2 matcher are Bash commands — the tools line
        # must actually allow them (it didn't before this campaign: the
        # registry-doc fetch was already unrunnable).
        self.assertIn("tools: Read, Write, Grep, Glob, Edit, Bash",
                      self.planner)

    def test_result_block_carries_tag_confirm(self):
        self.assertIn("TAG_CONFIRM:", self.planner)
        self.assertIn("confirm_required", self.planner)


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
