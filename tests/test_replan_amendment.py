"""Tests for the replan-as-amendment arm (Track A3).

A ``replan`` failure-analyst verdict WITH the AC details (``ac_superseded`` +
``ac_prime_text``) stages an **in-place additive amendment** instead of halting.
The replan arm (``_step_route_failure_analysis``) writes
``.conductor/amendment-staged.json`` + emits ONE informed confirm
(``_amendment_decision``); ``Apply`` → ``cmd_amend_apply`` splices
``## Amendment N`` onto spec.md (additive), ``Edit manually`` →
``cmd_amend_clear`` abandons the staged splice, ``Halt`` keeps it on disk.

Governing invariant (load-bearing): the splice is ADDITIVE — the original
``- AC-N:`` line is never touched, so every downstream "verified against AC-N"
stamp stays truthful. ``parse_spec`` is section-scoped (a ``## Amendment N``
heading ends the ``## Acceptance Criteria`` section), so the amendment prose is
NOT parsed as a duplicate AC. A replan WITHOUT the AC details degrades to the
legacy halt (the analyst must give the AC specifics — the invariant forbids
silently rewriting an AC a downstream gate already measured against).

Fixture reuse: ``test_step._failed_exhausted_track`` (continuous, P1.T1 failed,
retry_count=3, git-backed), ``test_step._git_track_dir``/``_make_state``, and
``test_step._step`` (captures ``cmd_step`` stdout).
"""
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load
from scripts.track_state.dispatch import (
    cmd_amend_apply, cmd_amend_clear, cmd_failure_analyst_verdict,
    _amendment_staged_read_marker, _amendment_staged_marker_path,
    _amendment_guidance_read, _amendment_guidance_path,
    _failure_analysis_marker_path, _failure_analysis_write_marker)
from scripts.track_state.spec_amend import (
    next_amendment_number, render_amendment, splice_amendment)
from scripts.track_state.spec_parse import parse_spec
from scripts.track_state import cli

from tests.test_step import _failed_exhausted_track, _git_track_dir, _make_state, _step


def _run(fn, *args):
    """Capture a command's stdout JSON (the one ``out(...)`` it emits)."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _marker(track_dir, **fields):
    """Write a failure-analysis marker (defaults to a valid analyzed P1T1 replan)."""
    base = {"phase": 1, "task": 1, "subtask": None, "name": "Task A",
            "stage": "analyzed", "category": "spec_plan_defect",
            "recommendation": "replan",
            "root_cause": "AC-2 contradicts AC-1", "modification": "drop AC-2",
            "what_was_done": None, "analysis_rounds": 1,
            "seen_root_causes": ["AC-2 contradicts AC-1"], "consecutive_empty_rounds": 0,
            "ac_superseded": None, "ac_prime_text": None, "affected_tasks": []}
    base.update(fields)
    _failure_analysis_write_marker(track_dir, base)


def _write_spec(track_dir):
    """A spec whose AC section is terminated only by the appended amendment.

    No ``## Test Scenarios`` follows ``## Acceptance Criteria`` — so the
    amendment heading is the load-bearing section-ender for ``parse_spec``
    (the belt-and-braces half of the additive invariant)."""
    Path(track_dir, "spec.md").write_text(
        "# Spec\n\n"
        "## Overview\n"
        "A short overview.\n\n"
        "## Acceptance Criteria\n"
        "- AC-1: first criterion\n"
        "- AC-2: second criterion\n")


def _git_log_subjects(track_dir):
    out = subprocess.run(["git", "-C", track_dir, "log", "--format=%s"],
                         capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


class ReplanAmendmentRoutingTests(TestCase):
    """``_step_route_failure_analysis`` replan arm: stage + ask, or halt (legacy)."""

    def test_replan_with_ac_details_stages_and_asks(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _marker(d, ac_superseded="AC-2",
                ac_prime_text="a corrected criterion",
                affected_tasks=["P1.T1"])
        o = _step(d)
        # One informed confirm — the ONLY human touchpoint in the recovery router.
        self.assertEqual(o["action"], "ask")
        self.assertEqual(o["decision"]["header"], "Replan")
        # Amendment staged on disk carrying the AC payload.
        staged = _amendment_staged_read_marker(d)
        self.assertEqual(staged["ac_superseded"], "AC-2")
        self.assertEqual(staged["ac_prime_text"], "a corrected criterion")
        self.assertEqual(staged["affected_tasks"], ["P1.T1"])
        self.assertEqual(staged["recommendation"], "replan")
        # Analysis marker cleared BEFORE the ask (else next step re-routes).
        self.assertFalse(_failure_analysis_marker_path(d).exists())

    def test_replan_without_ac_details_still_halts(self):
        # Legacy degrade: no auto-amendment without the AC specifics.
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _marker(d)  # recommendation=replan, no ac_superseded/ac_prime_text
        o = _step(d)
        self.assertEqual(o["action"], "halt")
        self.assertEqual(o["reason"], "replan")
        # Nothing staged; the human edits spec.md themselves.
        self.assertIsNone(_amendment_staged_read_marker(d))
        self.assertFalse(_amendment_staged_marker_path(d).exists())

    def test_amendment_decision_blob_shape(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _marker(d, ac_superseded="AC-2", ac_prime_text="corrected")
        o = _step(d)
        decision = o["decision"]
        # {question, header, options, commands, next} — mirrors _failed_task_decision.
        self.assertEqual(set(decision), {"question", "header", "options", "commands", "next"})
        labels = [opt["label"] for opt in decision["options"]]
        self.assertEqual(labels, ["Apply amendment", "Edit manually", "Halt"])
        # Apply runs amend-apply verbatim; Edit manually runs amend-clear.
        self.assertTrue(any("amend-apply" in c for c in decision["commands"]["Apply amendment"]))
        self.assertTrue(any("amend-clear" in c for c in decision["commands"]["Edit manually"]))
        # Apply / Edit resume the spine; Halt stops.
        self.assertEqual(decision["next"]["Apply amendment"], "step")
        self.assertEqual(decision["next"]["Edit manually"], "step")
        self.assertEqual(decision["next"]["Halt"], "HALT")
        # The question names the superseded AC (informed, not vague).
        self.assertIn("AC-2", decision["question"])

    def test_verdict_then_step_routes_to_amendment_ask(self):
        # End-to-end: cmd_failure_analyst_verdict (A4 payload) → marker → _step ask.
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_failure_analyst_verdict, d,
                 "spec_plan_defect", "replan", "AC-2 contradicts AC-1", "drop AC-2",
                 None, "AC-2", "a corrected criterion", ["P1.T1"])
        self.assertTrue(o["ok"])
        # The replan AC payload reaches the marker.
        m = json.loads(_failure_analysis_marker_path(d).read_text())
        self.assertEqual(m["ac_superseded"], "AC-2")
        self.assertEqual(m["ac_prime_text"], "a corrected criterion")
        self.assertEqual(m["affected_tasks"], ["P1.T1"])
        # …and the spine routes it to the amendment ask.
        step_o = _step(d)
        self.assertEqual(step_o["action"], "ask")
        self.assertEqual(step_o["decision"]["header"], "Replan")


class SpecAmendHelperTests(TestCase):
    """``spec_amend`` — the additive splice helper (no in-code spec mutation else)."""

    def test_next_amendment_number_starts_at_1(self):
        d = _git_track_dir(_make_state())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_spec(d)
        self.assertEqual(next_amendment_number(Path(d, "spec.md")), 1)

    def test_next_amendment_number_increments(self):
        d = _git_track_dir(_make_state())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "spec.md").write_text(
            "## Acceptance Criteria\n- AC-1: one\n\n"
            "## Amendment 2\n- old\n")
        self.assertEqual(next_amendment_number(Path(d, "spec.md")), 3)

    def test_splice_missing_spec_returns_none(self):
        d = _git_track_dir(_make_state())  # no spec.md
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        old_err = sys.stderr
        sys.stderr = io.StringIO()
        try:
            n = splice_amendment(d, "AC-2", "corrected")
        finally:
            sys.stderr = old_err
        self.assertIsNone(n)

    def test_render_uses_prose_labels_not_ac_bullets(self):
        # Belt-and-braces: the amendment block avoids the ``- AC-N`` bullet shape
        # so it can't be misread as an AC even out of section context.
        block = render_amendment(1, "AC-2", "corrected", root_cause="r",
                                 affected_tasks=["P1.T1"])
        self.assertIn("## Amendment 1", block)
        self.assertIn("**Supersedes:** AC-2", block)
        self.assertIn("**Adds:** AC-2′ — corrected", block)
        self.assertNotIn("\n- AC-", block)

    def test_splice_appends_additively_and_returns_number(self):
        d = _git_track_dir(_make_state())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_spec(d)
        before = Path(d, "spec.md").read_text()
        n = splice_amendment(d, "AC-2", "corrected", root_cause="r",
                             affected_tasks=["P1.T1"])
        self.assertEqual(n, 1)
        after = Path(d, "spec.md").read_text()
        # Additive: the original text is a verbatim prefix.
        self.assertTrue(after.startswith(before.rstrip()))
        self.assertIn("## Amendment 1", after)
        self.assertIn("AC-2′", after)


class AmendApplyTests(TestCase):
    """``cmd_amend_apply`` — the ``Apply amendment`` arm (splice + reactivate + commit)."""

    def _stage(self, d):
        """Route a replan verdict to the staged amendment (writes the marker)."""
        _write_spec(d)
        _marker(d, ac_superseded="AC-2", ac_prime_text="a corrected criterion",
                root_cause="AC-2 contradicts AC-1", affected_tasks=["P1.T1"])
        _step(d)  # stages the amendment + clears the analysis marker
        self.assertIsNotNone(_amendment_staged_read_marker(d))

    def test_apply_splices_amendment_additively(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._stage(d)
        o = _run(cmd_amend_apply, d)
        self.assertTrue(o["ok"])
        self.assertEqual(o["applied"], True)
        self.assertEqual(o["amendment_number"], 1)
        text = Path(d, "spec.md").read_text()
        # The original AC line is preserved verbatim (the governing invariant).
        self.assertIn("- AC-2: second criterion", text)
        # …and the amendment is appended.
        self.assertIn("## Amendment 1", text)
        self.assertIn("AC-2′ — a corrected criterion", text)

    def test_apply_keeps_original_acs_in_parse_spec(self):
        # The deepest invariant: parse_spec does NOT pick up the amendment prose
        # as a duplicate AC. The ``## Amendment 1`` heading ends the AC section.
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._stage(d)
        _run(cmd_amend_apply, d)
        inv = parse_spec(Path(d, "spec.md"))
        self.assertEqual(inv["acs"], ["AC-1", "AC-2"])
        self.assertEqual([a["id"] for a in inv["ac_items"]], ["AC-1", "AC-2"])

    def test_apply_reactivates_failing_task_preserving_retry_history(self):
        # failed → pending (the spine re-dispatches against the amended spec);
        # retry_count is preserved — not a free retry.
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._stage(d)
        _run(cmd_amend_apply, d)
        tgt = load(d)["phases"][0]["tasks"][0]
        self.assertEqual(tgt["status"], "pending")
        self.assertEqual(tgt["retry_count"], 3)

    def test_apply_writes_amendment_guidance_injection(self):
        # The [Conductor Amendment] nudge reaches the re-dispatch (modeled on
        # _modified_guidance_block). Keyed phase-task[-subtask].
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._stage(d)
        _run(cmd_amend_apply, d)
        body = _amendment_guidance_read(d, 1, 1, None)
        self.assertIsNotNone(body)
        self.assertIn("[Conductor Amendment]", body)
        self.assertIn("AC-2′", body)
        self.assertTrue(_amendment_guidance_path(d, 1, 1, None).exists())

    def test_apply_clears_staged_marker(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._stage(d)
        _run(cmd_amend_apply, d)
        self.assertFalse(_amendment_staged_marker_path(d).exists())

    def test_apply_commits(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._stage(d)
        _run(cmd_amend_apply, d)
        subjects = _git_log_subjects(d)
        self.assertTrue(any(s.startswith("chore(conductor): Amend spec.md") for s in subjects),
                        f"expected an Amend commit, got {subjects}")

    def test_apply_without_staged_marker_errors(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_amend_apply, d)
        self.assertFalse(o["ok"])
        self.assertIn("no staged amendment", o["error"])


class AmendClearTests(TestCase):
    """``cmd_amend_clear`` — the ``Edit manually`` arm (abandon the staged splice)."""

    def test_clear_removes_staged_marker(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_spec(d)
        _marker(d, ac_superseded="AC-2", ac_prime_text="corrected")
        _step(d)  # stages
        self.assertTrue(_amendment_staged_marker_path(d).exists())
        o = _run(cmd_amend_clear, d)
        self.assertTrue(o["ok"])
        self.assertTrue(o["cleared"])
        self.assertFalse(_amendment_staged_marker_path(d).exists())

    def test_clear_idempotent_when_absent(self):
        # A re-clear after a partial resume is a no-op success.
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_amend_clear, d)
        self.assertTrue(o["ok"])
        self.assertFalse(o["cleared"])

    def test_clear_does_not_touch_spec(self):
        # The human owns the edit; amend-clear only abandons the staged splice.
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_spec(d)
        before = Path(d, "spec.md").read_text()
        _marker(d, ac_superseded="AC-2", ac_prime_text="corrected")
        _step(d)
        _run(cmd_amend_clear, d)
        self.assertEqual(Path(d, "spec.md").read_text(), before)


class CliWiringTests(TestCase):
    def _invoke(self, argv):
        old_argv, old_out = sys.argv, sys.stdout
        sys.argv, sys.stdout = ["track-state"] + argv, io.StringIO()
        try:
            cli.main()
            return json.loads(sys.stdout.getvalue())
        finally:
            sys.argv, sys.stdout = old_argv, old_out

    def test_amend_apply_resolves_via_cli(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_spec(d)
        _marker(d, ac_superseded="AC-2", ac_prime_text="a corrected criterion")
        _step(d)
        o = self._invoke(["amend-apply", d])
        self.assertTrue(o["ok"])
        self.assertIn("## Amendment 1", Path(d, "spec.md").read_text())

    def test_amend_clear_resolves_via_cli(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_spec(d)
        _marker(d, ac_superseded="AC-2", ac_prime_text="corrected")
        _step(d)
        self._invoke(["amend-clear", d])
        self.assertFalse(_amendment_staged_marker_path(d).exists())

    def test_failure_analyst_verdict_ac_flags_via_cli(self):
        d = _failed_exhausted_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._invoke(["failure-analyst-verdict", d,
                      "--category", "spec_plan_defect",
                      "--recommendation", "replan",
                      "--root-cause", "AC-2 wrong", "--modification", "fix it",
                      "--ac-superseded", "AC-2",
                      "--ac-prime-text", "corrected",
                      "--affected-tasks", "P1.T1,P2.T1"])
        m = json.loads(_failure_analysis_marker_path(d).read_text())
        self.assertEqual(m["ac_superseded"], "AC-2")
        self.assertEqual(m["ac_prime_text"], "corrected")
        self.assertEqual(m["affected_tasks"], ["P1.T1", "P2.T1"])

    def test_commands_listed_in_help_and_group(self):
        grouped = {c for _name, cmds in cli._COMMAND_GROUPS for c in cmds}
        for cmd in ("amend-apply", "amend-clear"):
            self.assertIn(cmd, cli.COMMAND_HELP)
            self.assertIn(cmd, grouped)


if __name__ == "__main__":
    main()
