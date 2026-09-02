"""Tests for the grounding fan-out's dispatch half — new-track §2.2.5.

Pins the seam end to end (design: conductor/design/grounding-fanout
§Mechanism 2–4):

- ``misc``/``workflow_shapes`` fog gate wiring is covered by
  ``test_propose_grounding``; here the SKILL step + the code-assembled
  prompt are the surface under test;
- ``dispatch.cmd_grounding_prompt`` — the plan-refute-prompt pattern applied
  to the fan-out: slice charters ride the prompt, the description comes from
  the new-track resume marker, three DISTINCT task slots (the append-handoff
  read-modify-write race is killed structurally, not by locking);
- the skill consumes the gate's JSON contract (ask only on foggy, skip when
  research-first, survivors-only RESEARCH_NOTES, never blocks planning);
- spec-planner reads EACH semicolon-joined path.
"""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.track_state import dispatch  # noqa: E402
from scripts.track_state.dispatch import (  # noqa: E402
    cmd_grounding_prompt, _GROUNDING_SLICES,
)
from scripts.track_state.new_track import (  # noqa: E402
    cmd_new_track_init, cmd_new_track_finalize,
)
from scripts.track_state.handoff import (  # noqa: E402
    cmd_append_handoff, _write_task_handoff,
)

SKILL = ROOT / "skills" / "new-track" / "SKILL.md"
PLANNER = ROOT / "agents" / "spec-planner.md"


def _out_captured(fn, *args, **kwargs):
    old_out = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old_out


class SliceTableTests(TestCase):
    """The slice table — three concern-disjoint charters, three distinct slots."""

    def test_exactly_three_slices(self):
        self.assertEqual(sorted(_GROUNDING_SLICES), [1, 2, 3])

    def test_charters_name_concrete_deliverables(self):
        # Each charter must demand CONCRETE names (the grounding contract —
        # "name the modules/interfaces/test tiers", never "understand").
        for n, (name, charter) in _GROUNDING_SLICES.items():
            self.assertIn("Name", charter, f"slice {n} charter must enumerate")
            self.assertTrue(charter.endswith("."), f"slice {n} charter punctuated")


class GroundingPromptTests(TestCase):
    """``cmd_grounding_prompt`` — prompt assembly from the resume marker."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = Path(self._tmp.name) / "track"
        _out_captured(cmd_new_track_init, str(self.td), "gf-test",
                      "integrate the billing API", "feature")

    def tearDown(self):
        _out_captured(cmd_new_track_finalize, str(self.td))
        self._tmp.cleanup()

    def test_slice_validated(self):
        for bad in (0, 4, None, "two"):
            res = _out_captured(cmd_grounding_prompt, str(self.td), bad)
            self.assertFalse(res["ok"], f"slice {bad!r} must be rejected")
            self.assertIn("slice", res["error"])

    def test_missing_marker_is_an_error(self):
        res = _out_captured(cmd_grounding_prompt, str(self.td / "nope"), 1)
        self.assertFalse(res["ok"])
        self.assertIn("new-track-progress", res["error"])

    def test_prompt_carries_pre_plan_convention_and_slot(self):
        res = _out_captured(cmd_grounding_prompt, str(self.td), 1)
        self.assertTrue(res["ok"])
        self.assertEqual(res["agent"], "explorer")
        self.assertEqual(res["slot"], "P0T1")
        self.assertEqual(res["notes_path"],
                         f"{self.td}/.conductor/handoff/P0T1.md")
        for token in (f"TRACK_DIR={self.td}", "PHASE=0", "TASK=1",
                      "PRE_PLAN=1", "SLICE=architecture / data-flow",
                      "CHARTER:", "append-handoff"):
            self.assertIn(token, res["prompt"], f"prompt must carry {token!r}")

    def test_each_slice_gets_distinct_slot_and_charter(self):
        # The structural race fix: three writers must never share a slot.
        slots = set()
        for n in (1, 2, 3):
            res = _out_captured(cmd_grounding_prompt, str(self.td), n)
            self.assertTrue(res["ok"])
            slots.add(res["slot"])
            self.assertIn(f"TASK={n}", res["prompt"])
            self.assertIn(_GROUNDING_SLICES[n][0], res["prompt"])
            self.assertIn(_GROUNDING_SLICES[n][1], res["prompt"])
        self.assertEqual(slots, {"P0T1", "P0T2", "P0T3"})

    def test_description_comes_from_marker(self):
        res = _out_captured(cmd_grounding_prompt, str(self.td), 2)
        self.assertIn("integrate the billing API", res["prompt"])

    def test_no_writes(self):
        # Read-only: nothing beyond the setUp marker materializes from prompt
        # assembly (no plan/spec/state, no handoff slots).
        _out_captured(cmd_grounding_prompt, str(self.td), 3)
        leftovers = [str(p.relative_to(self.td))
                     for p in self.td.rglob("*")
                     if p.is_file() and p.name != "new-track-progress.json"]
        self.assertEqual(leftovers, [],
                         f"grounding-prompt must not write: {leftovers}")


class SkillWiringTests(TestCase):
    """§2.2.5 in the skill body — the gate's consumption contract."""

    def setUp(self):
        self.skill = SKILL.read_text(encoding="utf-8")

    def test_section_present_after_brief_detection(self):
        self.assertIn("### 2.2.5 Grounding Fan-out Gate", self.skill)
        self.assertLess(self.skill.index("### 2.2b Brief Detection"),
                        self.skill.index("### 2.2.5 Grounding Fan-out Gate"))
        self.assertLess(self.skill.index("### 2.2.5 Grounding Fan-out Gate"),
                        self.skill.index("### 2.3 Dispatch Spec-Planner"))

    def test_runs_the_pure_gate(self):
        self.assertIn("propose-grounding", self.skill)

    def test_asks_only_on_foggy(self):
        self.assertIn("foggy: false", self.skill)
        self.assertIn("foggy: true", self.skill)

    def test_skips_when_research_first(self):
        # The double-exploration guard: research-first's Prelude explores.
        sec = self.skill[self.skill.index("### 2.2.5"):
                         self.skill.index("### 2.3 Dispatch")]
        self.assertIn("research-first", sec)

    def test_prompts_assembled_in_code(self):
        self.assertIn("grounding-prompt", self.skill)
        self.assertIn("verbatim", self.skill)

    def test_failure_never_blocks_planning(self):
        sec = self.skill[self.skill.index("### 2.2.5"):
                         self.skill.index("### 2.3 Dispatch")]
        self.assertIn("survivors", sec)
        self.assertIn("never blocks planning", sec)

    def test_absent_block_drops_slice(self):
        # The mask case: the stop-guard/filter probe result.json as a boolean
        # on the SHARED file, so a sibling's fresh write masks one explorer's
        # missing result — the per-agent verdict is recovered here, at the
        # stdout-block layer (the only layer with per-agent identity).
        sec = self.skill[self.skill.index("### 2.2.5"):
                         self.skill.index("### 2.3 Dispatch")]
        self.assertIn("Absent block", sec)
        self.assertIn("treat as FAILURE", sec)

    def test_multi_path_research_notes_in_envelope(self):
        self.assertIn("semicolon-joined", self.skill)
        self.assertIn("RESEARCH_NOTES={exploration-notes path, "
                      "semicolon-joined paths (§2.2.5 fan-out), or N/A}",
                      self.skill)

    def test_no_new_resume_key(self):
        # Resume-marker keys are code-guarded (new_track._STEP_ORDER); the
        # fan-out deliberately stamps nothing — pin the closure.
        from scripts.track_state.new_track import _STEP_ORDER
        self.assertNotIn("grounding", _STEP_ORDER)
        self.assertNotIn("fan_out", _STEP_ORDER)


class PlannerWiringTests(TestCase):
    """spec-planner consumes multi-path RESEARCH_NOTES."""

    def setUp(self):
        self.planner = PLANNER.read_text(encoding="utf-8")

    def test_input_table_documents_joined_paths(self):
        self.assertIn("semicolon-joined", self.planner)

    def test_reads_each_before_scan(self):
        self.assertIn("read EACH", self.planner)


EXPLORER = ROOT / "agents" / "explorer.md"

_FULL_EXPLORE_JSON = json.dumps({
    "summary": "Auth flows through middleware/session boundary.",
    "findings": ["session middleware owns the boundary"],
    "architecture": "request → middleware → session store",
    "gotchas": ["session store is lazy"],
    "files_inventory": [{"path": "src/auth.py", "purpose": "auth boundary",
                         "key_exports": "", "related_docs": ""}],
    "consulted_docs": [],
    "recommended": "extend the middleware",
    "out_of_scope": [],
    "graduation_candidates": [],
})


class PrePlanRecordTests(TestCase):
    """The fan-out's record channel is pre-state tolerant (fail-open).

    §2.2.5 runs BEFORE init-from-plan creates track-state.json — the stateless
    window is load-bearing (parallel dispatch safety). append-handoff must
    record the P0T slices without state, never mint state, and keep the
    completeness gate armed.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.td = Path(self._tmp.name) / "track"
        (self.td / ".conductor").mkdir(parents=True)
        # Deliberately NO track-state.json / plan.md / spec.md.

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_handoff_explore_pre_state(self):
        res = _out_captured(cmd_append_handoff, str(self.td), 0, 1,
                            "explore", _FULL_EXPLORE_JSON)
        self.assertTrue(res.get("ok"), res)
        notes = self.td / ".conductor" / "handoff" / "P0T1.md"
        self.assertTrue(notes.is_file())
        body = notes.read_text(encoding="utf-8")
        self.assertIn("Exploration Notes", body)
        self.assertIn("Task 1", body)  # stateless name fallback in the header

    def test_sparse_gate_fires_pre_state(self):
        with self.assertRaises(SystemExit):
            cmd_append_handoff(str(self.td), 0, 2, "explore",
                               json.dumps({"summary": "x"}))

    def test_pre_state_append_mints_no_state(self):
        _out_captured(cmd_append_handoff, str(self.td), 0, 3,
                      "explore", _FULL_EXPLORE_JSON)
        self.assertFalse((self.td / "track-state.json").exists())

    def test_write_task_handoff_none_state_missing_file(self):
        path = _write_task_handoff(str(self.td), 0, 1, "## section", None)
        self.assertTrue(Path(path).is_file())

    def test_pre_state_index_is_initializing_form(self):
        _out_captured(cmd_append_handoff, str(self.td), 0, 1,
                      "explore", _FULL_EXPLORE_JSON)
        index = (self.td / "handoff.md").read_text(encoding="utf-8")
        self.assertIn("Initializing", index)  # _ensure_handoff_index form
        self.assertNotIn("*No tasks started yet.*", index)  # not the {} sync


class ExplorerBodyTests(TestCase):
    """The explorer body owns the PRE_PLAN mode branch (agent contract, not
    just the assembled prompt line)."""

    def setUp(self):
        self.body = EXPLORER.read_text(encoding="utf-8")

    def test_mode_branch_documented(self):
        self.assertIn("Mode select — check your input first", self.body)
        self.assertIn("PRE_PLAN=1", self.body)

    def test_mode_branch_skips_plan_read(self):
        sec = self.body[self.body.index("## 3.0 SELF-LOAD CONTEXT"):
                        self.body.index("### 3.1")]
        self.assertIn("Never Read `plan.md`/`spec.md`", sec)

    def test_corpus_consult_survives_pre_plan(self):
        sec = self.body[self.body.index("## 3.0 SELF-LOAD CONTEXT"):
                        self.body.index("### 3.1")]
        self.assertIn("§3.1 (corpus consult) still applies in full", sec)


if __name__ == "__main__":
    main()
