"""Tests for ``track-state propose-shape`` — planning-as-data Phase B (D2/D3).

The pure selection engine behind new-track §2.1's shape step:
``workflow_shapes.rank_shapes`` signal-matches a track description against each
candidate shape's authored ``signals`` (the planning-layer mirror of
``derive_task_tag``), and ``misc.cmd_propose_shape`` composes the proposal JSON
the skill consumes (proposed / confirm_required / chosen entry — the skill never
re-derives gates, grounding, or the planning-docfile path).

Pinned semantics (the D3 confirm contract):

- **default is silent** — ``confirm_required=false``, no prompt;
- **a single-hit candidate still surfaces** (the USER confirm is the bar — the
  inverse of ``derive_task_tag``'s >=2, whose bar exists because a tag silently
  exempts gates);
- **strict plurality** — a top score TIED with the runner-up is ambiguity, not
  a proposal: default wins silently (``set-workflow-shape`` stays the override);
- **--brief is fail-open** — an absent brief ranks on the description alone;
- the wiring pins keep the §2.1 keyword block deleted and the RESEARCH_NOTES
  envelope flowing (the research-first Prelude's hand-off).
"""
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.track_state import workflow_shapes as ws  # noqa: E402
from scripts.track_state.misc import cmd_propose_shape  # noqa: E402

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
        ws._load.cache_clear()

    def tearDown(self):
        if self._prior is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior
        ws._load.cache_clear()


class RankCoreTests(_ShippedRegistry):
    """``signals_for`` + ``rank_shapes`` — the pure core."""

    def test_default_shape_declares_no_signals(self):
        # default is the fail-open fallback, NEVER a competitor (an opt-in
        # mirror: shapes compete only when their signals are authored).
        self.assertEqual(ws.signals_for("default"), ())
        for shape in ("migration", "deliverable", "research-first"):
            self.assertTrue(ws.signals_for(shape),
                            f"{shape} must author signals to be a candidate")

    def test_signals_lowercased(self):
        # The matcher lowercases the text; signals must be lowercase already.
        for shape in ws.SHAPES_VOCAB():
            for sig in ws.signals_for(shape):
                self.assertEqual(sig, sig.lower())

    def test_empty_or_blank_text_ranks_nothing(self):
        self.assertEqual(ws.rank_shapes(""), [])
        self.assertEqual(ws.rank_shapes("   \n  "), [])

    def test_hits_are_distinct_per_signal(self):
        # One signal repeated in the text counts ONCE (distinct-hit scoring —
        # "migrate ... migrate ... migrate" is 1 hit, not 3).
        ranked = ws.rank_shapes("migrate the auth; migrate the billing; migrate")
        top = ranked[0]
        self.assertEqual(top["shape"], "migration")
        self.assertEqual(top["hits"], ["migrate"])

    def test_ranked_desc_and_registry_order_stable(self):
        ranked = ws.rank_shapes("investigate the report")
        # A genuine 1-1 tie: BOTH candidates score 1 — the LIST is ordered
        # (score desc, registry order within a tie) and deterministic.
        self.assertEqual([(c["shape"], c["score"]) for c in ranked],
                         [("research-first", 1), ("deliverable", 1)])

    def test_deterministic_repeat(self):
        text = "a behavior-preserving migration with a backfill"
        self.assertEqual(ws.rank_shapes(text), ws.rank_shapes(text))

    def test_candidates_capped_at_three(self):
        # The JSON surface stays small even with a text hitting every shape.
        text = ("migrate upgrade port rename document docs runbook report "
                "research guide onboarding investigate understand unfamiliar "
                "explore survey spike")
        self.assertLessEqual(len(ws.rank_shapes(text)), 3)

    def test_word_boundary_matching(self):
        # The shared word-boundary matcher (task_profiles._signal_in): a
        # signal glued to letters on an edge does not match — "docs" must not
        # hit "docker" (right edge glued), so a docker chore is not a
        # deliverable.
        ranked = ws.rank_shapes("rebuild the docker compose stack")
        self.assertNotIn("deliverable",
                         [c["shape"] for c in ranked])


class ProposeShapeTests(_ShippedRegistry):
    """``cmd_propose_shape`` — the D2/D3 proposal contract."""

    def _propose(self, description, brief_path=None):
        result, _err = _out_captured(cmd_propose_shape, description, brief_path)
        return result

    def test_migration_description_proposes_migration(self):
        r = self._propose(
            "Migrate the auth stack from AngularJS to React, porting every "
            "component behavior-preserving")
        self.assertTrue(r["ok"])
        self.assertEqual(r["proposed"], "migration")
        self.assertTrue(r["confirm_required"])
        # The chosen entry carries everything the skill records — it never
        # re-derives gates/grounding/path from the registry.
        chosen = r["chosen"]
        self.assertEqual(chosen["gates"], ["checkpoint"])
        self.assertEqual(chosen["ac_grounding"], "test")
        self.assertEqual(chosen["planning_doc"], "migration.md")
        self.assertTrue(Path(chosen["planning_doc_path"]).is_file())
        # The rationale names the consequence deterministically.
        self.assertIn("drops the tdd/coverage gate", chosen["rationale"])

    def test_plain_feature_defaults_silently(self):
        r = self._propose("Add a payment retry queue with idempotency keys")
        self.assertTrue(r["ok"])
        self.assertEqual(r["proposed"], "default")
        self.assertFalse(r["confirm_required"])
        self.assertEqual(r["candidates"], [])
        self.assertEqual(r["chosen"]["ac_grounding"], "test")
        self.assertTrue(r["chosen"]["planning_doc_path"].endswith("default.md"))

    def test_tie_falls_back_to_default_silently(self):
        # "investigate the report" hits research-first AND deliverable 1-1 —
        # a tie is ambiguity, not a proposal (strict plurality).
        r = self._propose("investigate the report")
        self.assertEqual(r["proposed"], "default")
        self.assertFalse(r["confirm_required"])
        self.assertEqual([c["score"] for c in r["candidates"]], [1, 1])

    def test_single_hit_still_surfaces(self):
        # The inverse of derive_task_tag's >=2 bar: ONE distinct hit proposes
        # (the user confirm is the guard — a shape never lands unconfirmed).
        r = self._propose("Onboarding material for the payments team")
        self.assertEqual(r["proposed"], "deliverable")
        self.assertTrue(r["confirm_required"])
        self.assertEqual(r["candidates"][0]["score"], 1)
        self.assertEqual(r["candidates"][0]["hits"], ["onboarding"])

    def test_brief_joins_the_ranking_text(self):
        # Description alone ranks nothing; the brief's exploration language
        # flips the proposal — the (description ⊕ brief) join is real.
        r0 = self._propose("rework the payment module")
        self.assertEqual(r0["proposed"], "default")
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("The module is unfamiliar; we must investigate it and "
                    "survey its structure before planning any change.\n")
            brief = f.name
        self.addCleanup(os.unlink, brief)
        r1 = self._propose("rework the payment module", brief)
        self.assertTrue(r1["brief_used"])
        self.assertEqual(r1["proposed"], "research-first")

    def test_missing_brief_fails_open(self):
        r = self._propose("migrate the auth stack",
                          "/nonexistent/brief.md")
        self.assertTrue(r["ok"])
        self.assertFalse(r["brief_used"])
        # The description's own hit still ranked — nothing was blocked.
        self.assertEqual(r["proposed"], "migration")

    def test_empty_description_errors(self):
        r = self._propose("   ")
        self.assertFalse(r["ok"])
        self.assertIn("missing description", r["error"])


class ProposeShapeCLITests(_ShippedRegistry):
    """The subprocess surface — the cli.py elif arm + the non-resolution of the
    description positional (a free-text description is never a track-dir
    lookup; a bare `no_match` resolution exit would break the command)."""

    def _run(self, *args):
        env = {**os.environ}
        env.pop("CLAUDE_PROJECT_DIR", None)
        proc = subprocess.run(
            [sys.executable, str(CLI), "propose-shape", *args],
            capture_output=True, text=True, env=env, cwd=str(ROOT),
        )
        return proc

    def test_cli_happy_path(self):
        proc = self._run("Upgrade the framework, a behavior-preserving port")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["proposed"], "migration")

    def test_cli_brief_flag(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write("investigate and understand the flow first\n")
            brief = f.name
        self.addCleanup(os.unlink, brief)
        proc = self._run("rework it", "--brief", brief)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["brief_used"])
        self.assertEqual(payload["proposed"], "research-first")

    def test_cli_help_lists_command(self):
        proc = subprocess.run(
            [sys.executable, str(CLI), "help", "propose-shape"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("propose-shape", proc.stdout)


class NewTrackSwapWiringTests(TestCase):
    """The §2.1 swap + §2.3 wiring pins: selection is data-driven (the keyword
    block stays deleted), the confirm is the ONE generic D3 question, and the
    Prelude's envelope field flows into both spec-planner envelopes."""

    def setUp(self):
        self.skill = SKILL.read_text(encoding="utf-8")
        self.planner = PLANNER.read_text(encoding="utf-8")

    def test_step3_calls_the_matcher(self):
        self.assertIn("track-state propose-shape", self.skill)

    def test_keyword_block_stays_deleted(self):
        # The hand-maintained keyword lists were the drift liability Phase B
        # deletes — a restored "(keywords:" fragment means the second home is
        # back.
        self.assertNotIn("(keywords:", self.skill)

    def test_confirm_switches_on_confirm_required(self):
        self.assertIn("Switch on `confirm_required`", self.skill)
        # The ONE generic confirm — recommended = the proposal, alternative =
        # default. No per-shape prose in the skill (it lives in the JSON).
        self.assertIn("(Recommended)", self.skill)

    def test_records_play_path_from_json(self):
        # $PLAY_PATH (the chosen shape's planning docfile path) comes from the
        # JSON entry — the skill never resolves the docfile itself.
        self.assertIn("$PLAY_PATH", self.skill)
        self.assertIn("`planning_doc_path`", self.skill)

    def test_prelude_rule_before_dispatch(self):
        self.assertIn("**Shape Prelude (pre-planning steps).**", self.skill)
        self.assertIn("## Prelude (orchestrator)", self.skill)
        # A failed Prelude never blocks planning.
        self.assertIn("never blocks planning", self.skill)

    def test_research_notes_in_both_envelopes(self):
        # First dispatch AND the regen retry envelope both carry the field —
        # a retry must not silently lose the exploration notes.
        self.assertEqual(self.skill.count("RESEARCH_NOTES="), 2)

    def test_planner_input_row_exists(self):
        self.assertIn("`RESEARCH_NOTES`", self.planner)
        self.assertIn("read it FIRST", self.planner)


class SpecPlannerDocfileCollapseTests(TestCase):
    """Phase C pins — the PLAY_FILE envelope flows into BOTH spec-planner
    dispatches, and the planner now READS its per-shape doctrine from the
    docfile instead of re-encoding it (the shape enumeration and the
    grounding doctrine were the drift liabilities this collapse deletes)."""

    def setUp(self):
        self.skill = SKILL.read_text(encoding="utf-8")
        self.planner = PLANNER.read_text(encoding="utf-8")

    def test_play_file_in_both_envelopes(self):
        # First dispatch AND the regen retry both carry the docfile path — a
        # retry must not silently lose the planning procedure (the same pin
        # RESEARCH_NOTES holds).
        self.assertEqual(self.skill.count("PLAY_FILE="), 2)

    def test_play_file_row_names_the_fallback_chain(self):
        # The envelope row: PLAY_FILE → registry-doc --shape → default
        # tested-code procedure (fail-open every step).
        self.assertIn("`PLAY_FILE`", self.planner)
        self.assertIn("planning docfile", self.planner)
        self.assertIn("registry-doc --shape", self.planner)

    def test_planner_reads_docfile_first(self):
        self.assertIn("Read your planning docfile FIRST", self.planner)
        # The conflict rule: format contract owns machine anchors, docfile
        # owns procedure.
        self.assertIn(
            "format contract wins on machine anchors and the docfile wins "
            "on procedure", self.planner)

    def test_shape_row_stays_de_enumerated(self):
        # The closed shape set lives in the registry — an enumerated ladder in
        # the agent body is the second home this collapse deletes.
        self.assertIn("never enumerated here", self.planner)
        self.assertNotIn("`default` / `migration`", self.planner)

    def test_grounding_doctrine_delegated_to_docfile(self):
        # The substrate contract (which anchors to emit) stays in §4.1; HOW to
        # ground well is single-homed in the docfiles.
        self.assertIn("How to ground WELL is the planning docfile's job",
                      self.planner)
        self.assertIn("AC grounding keys off `AC_GROUNDING`", self.planner)


if __name__ == "__main__":
    main()
