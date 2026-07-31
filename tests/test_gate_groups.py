"""Tests for cross-phase gate groups (``<!-- gate_group: <name> -->``).

Gate groups are the cross-phase "red now, fixed in a later phase" declaration
(plan-format-contract.md §"Phase Gate Groups"): a run of phases that gate
TOGETHER at their terminal (last contiguous) member. Non-terminal members defer
their checkpoint (no verifier fan-out, ``[checkpoint: deferred <group>]``
marker); the terminal member gates the group's accumulated diff and on PASS
stamps every member with a real SHA.

These mirror the verify-mode / deps test shapes: the directive is re-parsed
plan.md metadata (NOT persisted), so each test builds a temp plan.md and asserts
on the parse + the checkpoint gate + the stamp lifecycle. They pin the recorded
persistence decision (``to_plan_structure`` drops ``gate_group``) and the four
validation kinds (``single_member`` / ``non_contiguous`` / ``empty`` /
``unparsed``).
"""
import io
import json
import re
import shutil
import sys
from pathlib import Path
from unittest import TestCase, main

# tests/ sits at repo root; scripts/ is the import root for track_state.
_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

from track_state import plan_parse  # noqa: E402
from track_state import helpers  # noqa: E402
from track_state.misc import (  # noqa: E402
    _stamp_checkpoint_in_plan, _stamp_deferred_checkpoint_in_plan)

# Reuse the git-backed track fixtures (real plan.md + state on disk).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_step import _make_state, _git_track_dir  # noqa: E402

from scripts.track_state.dispatch import cmd_phase_checkpoint_review  # noqa: E402


# --- plan.md fixtures --------------------------------------------------------

_PLAN_3PHASE_GROUP = """# Plan

## Phase 1: bump dep <!-- gate_group: spring3 -->
- [ ] [Config] bump spring

## Phase 2: rename <!-- gate_group: spring3 -->
- [ ] [Migrate] javax to jakarta

## Phase 3: wire up <!-- gate_group: spring3 -->
- [ ] [Manual] verify
"""

_PLAN_FLAT = """# Plan

## Phase 1: a
- [ ] [Manual] verify

## Phase 2: b
- [ ] [Manual] verify
"""


def _write_plan(tmp_path, text):
    Path(tmp_path, "plan.md").write_text(text)


def _all_terminal_state(n_phases):
    """A state dict with ``n_phases`` phases, every task ``completed``."""
    phases = []
    for i in range(1, n_phases + 1):
        phases.append({
            "name": f"Phase {i}", "status": "pending",
            "tasks": [{"name": f"t{i}", "status": "completed",
                       "commit_sha": f"{i:07x}"}],
        })
    return _make_state(current_phase_index=n_phases, current_task_index=1,
                       phases=phases)


def _run(fn, *args):
    """Capture a stamp-only command's stdout JSON."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


# --- parse -------------------------------------------------------------------

class GateGroupParseTests(TestCase):
    def test_extracts_group_name(self):
        d = tempfile_path()
        _write_plan(d, _PLAN_3PHASE_GROUP)
        r = plan_parse.parse_plan(Path(d, "plan.md"))
        groups = [ph["gate_group"] for ph in r["phases"]]
        self.assertEqual(groups, ["spring3", "spring3", "spring3"])
        self.assertTrue(all(ph["gate_group_has_comment"] for ph in r["phases"]))
        self.assertTrue(all(not ph["gate_group_failures"]
                            for ph in r["phases"]))

    def test_case_insensitive_unifies_group(self):
        d = tempfile_path()
        _write_plan(d, "# Plan\n\n"
                       "## Phase 1: a <!-- gate_group: Spring3 -->\n"
                       "- [ ] [Manual] verify\n"
                       "## Phase 2: b <!-- gate_group: spring3 -->\n"
                       "- [ ] [Manual] verify\n")
        r = plan_parse.parse_plan(Path(d, "plan.md"))
        # Both lowercased to the same identity → one group, no validation issue.
        self.assertEqual([ph["gate_group"] for ph in r["phases"]],
                         ["spring3", "spring3"])
        self.assertEqual(plan_parse.validate_gate_groups(r), [])

    def test_no_directive_yields_none(self):
        d = tempfile_path()
        _write_plan(d, _PLAN_FLAT)
        r = plan_parse.parse_plan(Path(d, "plan.md"))
        for ph in r["phases"]:
            self.assertIsNone(ph["gate_group"])
            self.assertFalse(ph["gate_group_has_comment"])

    def test_empty_body_surfaces_failure(self):
        d = tempfile_path()
        _write_plan(d, "# Plan\n\n## Phase 1: a <!-- gate_group: -->\n"
                       "- [ ] [Manual] verify\n"
                       "## Phase 2: b <!-- gate_group: -->\n"
                       "- [ ] [Manual] verify\n")
        r = plan_parse.parse_plan(Path(d, "plan.md"))
        for ph in r["phases"]:
            self.assertTrue(ph["gate_group_has_comment"])
            self.assertIsNone(ph["gate_group"])
        # validate_gate_groups flags both as empty.
        kinds = {i["kind"] for i in plan_parse.validate_gate_groups(r)}
        self.assertIn("empty", kinds)


# --- validation --------------------------------------------------------------

class GateGroupValidationTests(TestCase):
    def _plan(self, body):
        d = tempfile_path()
        _write_plan(d, "# Plan\n\n" + body)
        return plan_parse.parse_plan(Path(d, "plan.md"))

    def test_single_member_warns(self):
        r = self._plan("## Phase 1: a <!-- gate_group: lone -->\n"
                       "- [ ] [Manual] verify\n")
        kinds = {i["kind"] for i in plan_parse.validate_gate_groups(r)}
        self.assertIn("single_member", kinds)

    def test_non_contiguous_warns(self):
        r = self._plan("## Phase 1: a <!-- gate_group: g -->\n"
                       "- [ ] [Manual] verify\n"
                       "## Phase 2: b\n- [ ] [Manual] verify\n"
                       "## Phase 3: c <!-- gate_group: g -->\n"
                       "- [ ] [Manual] verify\n")
        kinds = {i["kind"] for i in plan_parse.validate_gate_groups(r)}
        self.assertIn("non_contiguous", kinds)

    def test_valid_contiguous_group_is_clean(self):
        r = self._plan("## Phase 1: a <!-- gate_group: g -->\n"
                       "- [ ] [Manual] verify\n"
                       "## Phase 2: b <!-- gate_group: g -->\n"
                       "- [ ] [Manual] verify\n")
        self.assertEqual(plan_parse.validate_gate_groups(r), [])


# --- persistence decision ----------------------------------------------------

class GateGroupPersistenceTests(TestCase):
    """``gate_group`` is re-derived metadata — ``to_plan_structure`` MUST drop it
    (mirrors ``verify_modes``). Pins the recorded persistence decision."""

    def test_to_plan_structure_drops_gate_group(self):
        d = tempfile_path()
        _write_plan(d, _PLAN_3PHASE_GROUP)
        r = plan_parse.parse_plan(Path(d, "plan.md"))
        for ph in plan_parse.to_plan_structure(r)["phases"]:
            self.assertNotIn("gate_group", ph)
            self.assertEqual(set(ph.keys()), {"name", "tasks"})


# --- checkpoint gate (defer + terminal) --------------------------------------

class PhaseNeedsCheckpointTests(TestCase):
    """``_phase_needs_checkpoint`` defers non-terminal members and gates the
    terminal member — the load-bearing gate logic."""

    def _track(self, plan_text, n_phases=3):
        state = _all_terminal_state(n_phases)
        return _git_track_dir(state, plan_content=plan_text)

    def test_non_terminal_member_defers(self):
        d = self._track(_PLAN_3PHASE_GROUP)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        state = _load(d)
        # Phase 1 & 2 are non-terminal members → defer (None) + stamp deferred.
        self.assertIsNone(helpers._phase_needs_checkpoint(d, state, 1))
        self.assertIsNone(helpers._phase_needs_checkpoint(d, state, 2))
        plan = Path(d, "plan.md").read_text()
        self.assertIn("[checkpoint: deferred spring3]", plan)

    def test_terminal_member_gates(self):
        d = self._track(_PLAN_3PHASE_GROUP)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        state = _load(d)
        # Phase 3 is the terminal member → returns its index (needs checkpoint).
        self.assertEqual(helpers._phase_needs_checkpoint(d, state, 3), 3)

    def test_deferral_is_idempotent(self):
        d = self._track(_PLAN_3PHASE_GROUP)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        state = _load(d)
        helpers._phase_needs_checkpoint(d, state, 1)
        plan_after_first = Path(d, "plan.md").read_text()
        helpers._phase_needs_checkpoint(d, state, 1)
        plan_after_second = Path(d, "plan.md").read_text()
        # No double-stamp / no clobber.
        self.assertEqual(plan_after_first.count("[checkpoint: deferred spring3]"),
                         plan_after_second.count("[checkpoint: deferred spring3]"))

    def test_ungrouped_phase_gates_normally(self):
        d = self._track(_PLAN_FLAT, n_phases=2)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        state = _load(d)
        self.assertEqual(helpers._phase_needs_checkpoint(d, state, 1), 1)


class DeferredStampRecognizedTests(TestCase):
    """A ``[checkpoint: deferred <group>]`` marker reads as "checkpoint present"
    so the deferral is idempotent on re-read."""

    def test_deferred_marker_counts_as_present(self):
        d = tempfile_path()
        _write_plan(d, "# Plan\n\n## Phase 1: a <!-- gate_group: g --> "
                       "[checkpoint: deferred g]\n- [ ] [Manual] verify\n"
                       "## Phase 2: b <!-- gate_group: g -->\n"
                       "- [ ] [Manual] verify\n")
        state = _all_terminal_state(2)
        save(d, state)
        # Phase 1 already carries a deferred marker → reads as present → None.
        self.assertIsNone(helpers._phase_needs_checkpoint(d, state, 1))


# --- terminal-gate multi-stamp ----------------------------------------------

class TerminalPassStampsAllMembersTests(TestCase):
    """``cmd_phase_checkpoint_review`` PASSED on a terminal member stamps EVERY
    member of the group (deferred markers trade for real SHAs)."""

    def test_terminal_pass_stamps_all_members(self):
        state = _all_terminal_state(3)
        d = _git_track_dir(state, plan_content=_PLAN_3PHASE_GROUP)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # Trigger deferral on the non-terminal members first (simulating the
        # spine observing their terminal tasks before the terminal gate fires).
        st = _load(d)
        helpers._phase_needs_checkpoint(d, st, 1)
        helpers._phase_needs_checkpoint(d, st, 2)
        # Terminal PASS — only the terminal member (Phase 3) surfaces as the
        # pending checkpoint (non-terminal members deferred).
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", "abcdef0", "")
        self.assertTrue(o["ok"])
        self.assertTrue(o["stamped"])
        self.assertEqual(o["gate_group_members"], [1, 2, 3])
        plan = Path(d, "plan.md").read_text()
        # Every member carries the real SHA; no deferred marker remains.
        for phase in (1, 2, 3):
            self.assertTrue(
                re.search(rf"^##\s+Phase\s+{phase}\b.*\[checkpoint:\s+abcdef0\]",
                          plan, re.MULTILINE),
                f"Phase {phase} must carry the real checkpoint SHA")
        self.assertNotIn("[checkpoint: deferred", plan)

    def test_non_grouped_pass_does_not_report_members(self):
        # A normal (ungrouped) phase PASS reports no gate_group_members.
        state = _all_terminal_state(1)
        d = _git_track_dir(state, plan_content=_PLAN_FLAT)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", "1234567", "")
        self.assertTrue(o["ok"])
        self.assertIsNone(o.get("gate_group_members"))


# --- stamp helpers (unit) ----------------------------------------------------

class StampHelperTests(TestCase):
    def test_strip_regex_handles_deferred_before_real_stamp(self):
        # A deferred marker must be stripped before stamping a real SHA on top.
        d = tempfile_path()
        _write_plan(d, "## Phase 1: a <!-- gate_group: g --> "
                       "[checkpoint: deferred g]\n")
        r = _stamp_checkpoint_in_plan(d, 1, "aaaaaaa")
        self.assertIn("ok", r)
        plan = Path(d, "plan.md").read_text()
        self.assertIn("[checkpoint: aaaaaaa]", plan)
        self.assertNotIn("deferred", plan)

    def test_deferred_stamp_idempotent(self):
        d = tempfile_path()
        _write_plan(d, "## Phase 1: a <!-- gate_group: g -->\n")
        r1 = _stamp_deferred_checkpoint_in_plan(d, 1, "g")
        self.assertTrue(r1.get("deferred"))
        r2 = _stamp_deferred_checkpoint_in_plan(d, 1, "g")
        self.assertTrue(r2.get("noop"))  # already stamped → noop

    def test_deferred_stamp_never_clobbers_real_sha(self):
        # A phase already carrying a real checkpoint SHA must NOT be clobbered
        # back to deferred (the terminal gate already passed for it).
        d = tempfile_path()
        _write_plan(d, "## Phase 1: a <!-- gate_group: g --> "
                       "[checkpoint: aaaaaaa]\n")
        r = _stamp_deferred_checkpoint_in_plan(d, 1, "g")
        self.assertTrue(r.get("noop"))
        plan = Path(d, "plan.md").read_text()
        self.assertIn("[checkpoint: aaaaaaa]", plan)
        self.assertNotIn("deferred", plan)


# --- resolve helpers ---------------------------------------------------------

class ResolveGateGroupsTests(TestCase):
    def test_resolve_returns_ordered_members(self):
        d = tempfile_path()
        _write_plan(d, _PLAN_3PHASE_GROUP)
        groups = helpers._resolve_gate_groups(Path(d, "plan.md"))
        self.assertEqual(groups, {"spring3": [1, 2, 3]})

    def test_resolve_missing_plan_is_empty(self):
        # Fail-open: no plan.md → no groups → every phase gates itself normally.
        self.assertEqual(helpers._resolve_gate_groups(Path("/nonexistent/plan.md")), {})

    def test_terminal_membership(self):
        d = tempfile_path()
        _write_plan(d, _PLAN_3PHASE_GROUP)
        self.assertEqual(helpers._phase_gate_group_membership(d, 1),
                         ("spring3", False))
        self.assertEqual(helpers._phase_gate_group_membership(d, 3),
                         ("spring3", True))
        self.assertEqual(helpers._terminal_gate_group_members(d, 3), [1, 2, 3])
        self.assertEqual(helpers._terminal_gate_group_members(d, 1), [])  # non-terminal


# --- helpers -----------------------------------------------------------------

def tempfile_path():
    import tempfile
    return tempfile.mkdtemp()


def _load(track_dir):
    from track_state.core import load
    return load(track_dir)


def save(track_dir, state):
    from track_state.core import save
    save(track_dir, state)


if __name__ == "__main__":
    main()
