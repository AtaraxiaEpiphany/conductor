"""Tests for the misroute recovery verdict (task-type ownership R2).

task-executor self-reports exploration-shaped work with a deterministic
MISROUTE signature; failure-analyst transcribes category
``misrouted_explore`` + recommendation ``reroute_explorer``; the verdict
router's arm amends ``[Explore]`` onto the plan task line, mirrors name +
``task_type`` into state, reactivates preserving the retry budget, commits,
and re-dispatches — routing then derives the explorer through the normal
classification path (no dispatch-time override: ``task_type`` is re-derived
from the name, so an override would be reverted by the next sync).

Fixture reuse: the continuous failed track is ``test_step._failed_exhausted_track``
(retry_count=3, exhausted, plan line `- [!] Task A [abc1234]`).
"""
import json
import shutil
import sys
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load
from scripts.track_state.dispatch import (
    _failure_analysis_write_marker, _failure_analysis_marker_path,
)
from scripts.track_state.misc import _amend_plan_task_tag

from tests.test_step import _failed_exhausted_track, _step

AGENT = Path("agents/task-executor.md")
ANALYST = Path("agents/failure-analyst.md")


def _misroute_marker(track_dir, **fields):
    base = {"phase": 1, "task": 1, "subtask": None, "name": "Task A",
            "stage": "analyzed", "category": "misrouted_explore",
            "recommendation": "reroute_explorer",
            "root_cause": "deliverable is findings, not code",
            "modification": None, "what_was_done": None,
            "analysis_rounds": 1,
            "seen_root_causes": [], "consecutive_empty_rounds": 0}
    base.update(fields)
    _failure_analysis_write_marker(track_dir, base)


class RerouteVerdictTests(TestCase):
    """The router arm: plan amended, state mirrored, explorer re-dispatched."""

    def test_reroute_amends_tag_and_redispatches_explorer(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _misroute_marker(d)
        o = _step(d)
        # Re-dispatch routes through the normal classification path — the
        # amended leading tag now derives explorer, not a route override.
        self.assertEqual(o["agent"], "explorer")
        # Plan amended: the label (the defect) is fixed durably.
        plan = Path(d, "plan.md").read_text(encoding="utf-8")
        self.assertIn("[Explore] Task A", plan)
        # State mirrored: name + task_type, retry budget preserved. Status is
        # in_progress (reactivated to pending, then the re-dispatch locked it).
        state = load(d)
        task = state["phases"][0]["tasks"][0]
        self.assertEqual(task["status"], "in_progress")
        self.assertEqual(task["task_type"], "explore")
        self.assertIn("[Explore]", task["name"])
        self.assertEqual(task.get("retry_count"), 3)
        # Handshake consumed.
        self.assertFalse(_failure_analysis_marker_path(d).exists())

    def test_reroute_idempotent_when_already_tagged(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # Pre-tag the plan line AND the state name — the verdict arm must
        # no-op the amendment (already [Explore]) and still re-dispatch.
        Path(d, "plan.md").write_text(
            "# Plan\n\n## Phase 1: Build\n- [!] [Explore] Task A [abc1234]\n",
            encoding="utf-8")
        state = load(d)
        state["phases"][0]["tasks"][0]["name"] = "[Explore] Task A"
        from scripts.track_state.core import save
        save(d, state)
        _misroute_marker(d)
        o = _step(d)
        self.assertEqual(o["agent"], "explorer")
        plan = Path(d, "plan.md").read_text(encoding="utf-8")
        self.assertEqual(plan.count("[Explore]"), 1,
                         "idempotent amend must not double the tag")

    def test_reroute_amend_failure_halts(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # A marker pointing past the plan's task count → amend errors → the
        # defensive halt (never loop).
        _misroute_marker(d, task=7)
        o = _step(d)
        self.assertEqual(o["action"], "halt")

    def test_verdict_transcriber_accepts_new_enums(self):
        from scripts.track_state.dispatch import cmd_failure_analyst_verdict
        import io
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            cmd_failure_analyst_verdict(
                d, "misrouted_explore", "reroute_explorer",
                "findings deliverable routed to executor", None)
            payload = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["recommendation"], "reroute_explorer")


class AmendPlanTaskTagTests(TestCase):
    """The position-keyed plan amendment helper (misroute write home)."""

    def _track(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def test_prepends_tag_after_status_bracket(self):
        d = self._track()
        r = _amend_plan_task_tag(d, 1, 1, "Explore")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["name"], "[Explore] Task A")  # sha stripped
        line = [l for l in Path(d, "plan.md").read_text().splitlines()
                if l.startswith("- [")][0]
        self.assertEqual(line, "- [!] [Explore] Task A [abc1234]")

    def test_ac_comment_and_subtasks_survive(self):
        d = self._track()
        Path(d, "plan.md").write_text(
            "# Plan\n\n## Phase 1: Build\n"
            "- [!] Task A [abc1234]\n  - [x] Sub one <!-- AC-1 -->\n",
            encoding="utf-8")
        r = _amend_plan_task_tag(d, 1, 1, "Explore")
        self.assertTrue(r["ok"], r)
        text = Path(d, "plan.md").read_text(encoding="utf-8")
        self.assertIn("- [!] [Explore] Task A [abc1234]", text)
        self.assertIn("  - [x] Sub one <!-- AC-1 -->", text)

    def test_missing_task_errors_without_write(self):
        d = self._track()
        before = Path(d, "plan.md").read_text(encoding="utf-8")
        r = _amend_plan_task_tag(d, 1, 4, "Explore")
        self.assertIn("error", r)
        self.assertEqual(Path(d, "plan.md").read_text(encoding="utf-8"),
                         before)


class MisrouteSignatureWiringTests(TestCase):
    """The agent-body pins: the self-report signature, the taxonomy row, and
    the phase-mode restriction."""

    def test_executor_self_report_rule(self):
        body = (Path(__file__).resolve().parent.parent / AGENT).read_text(
            encoding="utf-8")
        self.assertIn(
            "MISROUTE: exploration work dispatched to task-executor", body)

    def test_analyst_taxonomy_row(self):
        body = (Path(__file__).resolve().parent.parent / ANALYST).read_text(
            encoding="utf-8")
        self.assertIn("`misrouted_explore`", body)
        self.assertIn("`reroute_explorer`", body)
        # Phase-mode restriction names the new arm.
        self.assertIn("no phase-level `decompose` or `reroute_explorer`",
                      body)


if __name__ == "__main__":
    main()
