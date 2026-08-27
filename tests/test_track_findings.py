"""Tests for the cross-phase track-findings compiler (#track-findings).

``compile_track_findings`` compiles durable findings (explorer
graduation_candidates + Key Findings + Gotchas & Constraints + decision
entries — the harvest ``_extract_candidates`` produces) into
``{TRACK_DIR}/.conductor/track-findings.md``: the cross-phase bridge a later
phase's explorer/task-executor reads before re-exploring.

Covers: compile + dedup, the findings/gotchas widen (sentinel + cap),
idempotent recompilation, empty-harvest stub, the stamp-path trigger
(``_stamp_checkpoint_in_plan`` — reached via both ``add-checkpoint`` (Rail A)
and ``phase_checkpoint_review`` PASSED (Rail B); FAILED never stamps → never
compiles), and the fail-open invariant (a compile error never blocks the
stamp).
"""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.handoff import (
    cmd_append_handoff, compile_track_findings, cmd_compile_track_findings,
)


def _out_captured(fn, *args, **kwargs):
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _make_track(description="Add token refresh", track_id="auth"):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    save(d, {
        "track_id": track_id, "type": "feature", "status": "in_progress",
        "description": description, "current_phase_index": 1,
        "current_task_index": 1, "updated_at": "2026-07-20T00:00:00+00:00",
        "phases": [{"name": "Phase 1", "status": "pending",
                    "tasks": [{"name": "Task A", "status": "pending"},
                              {"name": "Task B", "status": "pending"}]}],
    })
    return d


def _explore_payload(graduation=None, **extra):
    # Payload meets the explore completeness gate (summary >= 20 chars, >= 1
    # finding, >= 1 files_inventory entry).
    payload = {"summary": "Substantive exploration summary for the test track.",
               "findings": ["f1"], "architecture": "A", "gotchas": [],
               "files_inventory": [{"path": "src/a.ts", "purpose": "P"}],
               "recommended": "", "out_of_scope": []}
    payload["graduation_candidates"] = graduation or []
    payload.update(extra)
    return json.dumps(payload)


class CompileTrackFindingsTests(TestCase):
    def setUp(self):
        self.d = _make_track()
        self.addCleanup(__import__("shutil").rmtree, self.d, ignore_errors=True)

    def test_compiles_graduation_and_decisions_with_sources(self):
        cmd_append_handoff(self.d, 1, 1, "explore",
                           _explore_payload(["auth uses JWT with 15m expiry"]))
        cmd_append_handoff(self.d, 1, 1, "decision", json.dumps({
            "title": "Use JWT", "chosen": "JWT", "reasoning": "stateless",
            "options": "A/B", "tradeoffs": "revocation",
        }))
        r = compile_track_findings(self.d)
        self.assertTrue(r["compiled"])
        self.assertEqual(r["graduation_count"], 1)
        self.assertEqual(r["decisions_count"], 1)
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertIn("Add token refresh", doc)  # description in title
        self.assertIn("- auth uses JWT with 15m expiry _— source P1T1 (Phase 1)_", doc)
        self.assertIn("### Use JWT _— source P1T1 (Phase 1)_", doc)
        self.assertIn("**Chosen**: JWT", doc)
        self.assertIn("**Reasoning**: stateless", doc)

    def test_dedup_across_handoffs(self):
        # Same finding text in P1T1 and P1T2 → compiled once.
        cmd_append_handoff(self.d, 1, 1, "explore",
                           _explore_payload(["shared finding", "only in T1"]))
        cmd_append_handoff(self.d, 1, 2, "explore",
                           _explore_payload(["shared finding"]))
        r = compile_track_findings(self.d)
        self.assertEqual(r["graduation_count"], 2)  # shared + only-in-T1
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertEqual(doc.count("- shared finding _— source"), 1)
        self.assertIn("only in T1", doc)

    def test_idempotent_recompilation(self):
        cmd_append_handoff(self.d, 1, 1, "explore",
                           _explore_payload(["stable finding"]))
        compile_track_findings(self.d)
        first = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        compile_track_findings(self.d)  # second compile
        second = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        # Pure function of handoffs: the finding block is identical (the only
        # varying line is the _Last compiled_ timestamp).
        self.assertEqual(_strip_compiled_at(first), _strip_compiled_at(second))

    def test_staleness_label_old_finding(self):
        # A Phase-1 finding compiled at the Phase-5 checkpoint shows its age —
        # the cue for a reader to verify harder before relying on it.
        cmd_append_handoff(self.d, 1, 1, "explore",
                           _explore_payload(["auth uses JWT with 15m expiry"]))
        compile_track_findings(self.d, current_phase=5)
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertIn("Phase 1, 4 phases ago", doc)
        # The header explains the convention so the age label is not mysterious.
        self.assertIn("Staleness", doc)

    def test_staleness_label_fresh_finding(self):
        # Same-phase finding (recorded this checkpoint) shows no age — fresh.
        cmd_append_handoff(self.d, 1, 1, "explore",
                           _explore_payload(["just recorded"]))
        compile_track_findings(self.d, current_phase=1)
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertIn("(Phase 1)", doc)
        self.assertNotIn("ago", doc)

    def test_staleness_label_one_phase_singular(self):
        # 1-phase-old uses the singular "phase", not "phases".
        cmd_append_handoff(self.d, 1, 1, "explore", _explore_payload(["x"]))
        compile_track_findings(self.d, current_phase=2)
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertIn("Phase 1, 1 phase ago", doc)

    def test_staleness_label_no_current_phase(self):
        # Manual CLI invoke (no pending checkpoint) → source-phase-only label.
        # The label must never lie about age it can't compute.
        cmd_append_handoff(self.d, 1, 1, "explore", _explore_payload(["x"]))
        compile_track_findings(self.d)  # current_phase defaults to None
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertIn("(Phase 1)", doc)
        self.assertNotIn("ago", doc)

    def test_empty_harvest_writes_stub(self):
        # No explorer ran → the doc is still written as a minimal stub so the
        # read side sees a consistent "nothing yet" rather than a missing file.
        r = compile_track_findings(self.d)
        self.assertTrue(r["compiled"])
        self.assertEqual(r["graduation_count"], 0)
        self.assertEqual(r["decisions_count"], 0)
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertIn("No durable findings recorded yet", doc)

    def test_findings_and_gotchas_are_carried(self):
        # The widen: explore Key Findings + Gotchas & Constraints bullets ride
        # the compile alongside graduation candidates, each with its source.
        cmd_append_handoff(self.d, 1, 1, "explore", _explore_payload(
            [], findings=["auth lives in lib/auth.py", "tokens rotate on refresh"],
            gotchas=["clock skew breaks expiry checks"]))
        r = compile_track_findings(self.d)
        self.assertEqual(r["findings_count"], 2)
        self.assertEqual(r["gotchas_count"], 1)
        self.assertEqual(r["graduation_count"], 0)
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertIn("## Key Findings", doc)
        self.assertIn("- auth lives in lib/auth.py _— source P1T1 (Phase 1)_", doc)
        self.assertIn("## Gotchas & Constraints", doc)
        self.assertIn("- clock skew breaks expiry checks", doc)
        # Findings alone (no graduation/decisions) still compile a real doc,
        # not the empty stub.
        self.assertNotIn("No durable findings recorded yet", doc)

    def test_none_sentinel_bullets_not_collected(self):
        # An explorer that recorded no findings renders `- None` bullets — the
        # sentinel must not ride the harvest as a finding/gotcha. (Written
        # directly: cmd_append_handoff's explore gate requires >=1 finding.)
        handoff_dir = Path(self.d) / ".conductor" / "handoff"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        (handoff_dir / "P1T1.md").write_text(
            "# P1T1\n\n## Exploration Notes\n\n"
            "### Key Findings\n- None\n\n### Gotchas & Constraints\n- None\n")
        r = compile_track_findings(self.d)
        self.assertEqual(r["findings_count"], 0)
        self.assertEqual(r["gotchas_count"], 0)
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertNotIn("- None", doc)

    def test_findings_capped_per_handoff(self):
        # A rambling explorer cannot flood the compiled doc: at most
        # _FINDINGS_CAP_PER_TASK bullets per kind per handoff file.
        handoff_dir = Path(self.d) / ".conductor" / "handoff"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        (handoff_dir / "P1T1.md").write_text(
            "# P1T1\n\n## Exploration Notes\n\n### Key Findings\n"
            + "".join(f"- finding {i}\n" for i in range(10)))
        r = compile_track_findings(self.d)
        self.assertEqual(r["findings_count"], 8)
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertIn("- finding 7", doc)
        self.assertNotIn("- finding 8", doc)
        self.assertNotIn("- finding 9", doc)

    def test_dedup_findings_across_handoffs(self):
        cmd_append_handoff(self.d, 1, 1, "explore",
                           _explore_payload([], findings=["shared insight"]))
        cmd_append_handoff(self.d, 1, 2, "explore",
                           _explore_payload([], findings=["shared insight"]))
        r = compile_track_findings(self.d)
        self.assertEqual(r["findings_count"], 1)

    def test_none_graduation_is_empty(self):
        # Empty graduation_candidates renders `_None_` in the handoff → harvest
        # yields zero → doc is the empty stub.
        cmd_append_handoff(self.d, 1, 1, "explore", _explore_payload([]))
        r = compile_track_findings(self.d)
        self.assertEqual(r["graduation_count"], 0)
        self.assertTrue(r["compiled"])

    def test_cli_wrapper_emits_ok(self):
        cmd_append_handoff(self.d, 1, 1, "explore",
                           _explore_payload(["a finding"]))
        r = _out_captured(cmd_compile_track_findings, self.d)
        self.assertTrue(r["ok"])
        self.assertEqual(r["graduation_count"], 1)
        self.assertTrue(r["compiled"])
        self.assertTrue(r["path"].endswith("track-findings.md"))


class StampPathTests(TestCase):
    """The compile trigger is single-homed in ``_stamp_checkpoint_in_plan``:
    any successful stamp compiles (both Rail A ``add-checkpoint`` and Rail B
    ``phase_checkpoint_review`` PASSED funnel through it), FAILED never stamps
    → never compiles, and a compile error is fail-open (never blocks the
    stamp)."""

    def setUp(self):
        # _phase_complete_track() already returns a git-backed track dir (it
        # calls _git_track_dir internally); don't double-wrap.
        from tests.test_step import _phase_complete_track
        self._phase_complete_track = _phase_complete_track
        self._dirs = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _track(self):
        d = self._phase_complete_track()
        self._dirs.append(d)
        return d

    def _capture(self, fn, *args):
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        try:
            fn(*args)
            return json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout, sys.stderr = old_out, old_err

    def _seed_finding(self, d, text, source="P1T1"):
        """Write a handoff file directly with a Graduation Candidates section,
        bypassing cmd_append_handoff's state lookup (the fixture's state shape
        isn't always append-friendly; we only need the harvest parser to see
        the finding)."""
        handoff_dir = Path(d) / ".conductor" / "handoff"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        (handoff_dir / f"{source}.md").write_text(
            f"# {source}\n\n## Exploration Notes\n\n"
            f"### Graduation Candidates (durable → corpus; for corpus-writer harvest)\n"
            f"- {text}\n")

    def test_stamp_helper_triggers_compile(self):
        # The single home: stamping directly (the shared helper) compiles.
        from scripts.track_state.misc import _stamp_checkpoint_in_plan
        d = self._track()
        self._seed_finding(d, "stamp-path durable finding")
        o = _stamp_checkpoint_in_plan(d, 1, "abc1234")
        self.assertTrue(o["ok"])
        doc_path = Path(d) / ".conductor" / "track-findings.md"
        self.assertTrue(doc_path.exists(), "a successful stamp must compile")
        self.assertIn("stamp-path durable finding", doc_path.read_text())

    def test_add_checkpoint_triggers_compile(self):
        # Rail A: the phase-checker agent stamps via add-checkpoint — that
        # path compiled nothing pre-fix (the compile lived only in the Rail B
        # review command), so track-findings.md never materialized.
        from scripts.track_state.misc import cmd_add_checkpoint
        d = self._track()
        self._seed_finding(d, "rail-a durable finding")
        o = self._capture(cmd_add_checkpoint, d, 1, "abc1234")
        self.assertTrue(o["ok"])
        doc_path = Path(d) / ".conductor" / "track-findings.md"
        self.assertTrue(doc_path.exists(),
                        "add-checkpoint must compile track-findings.md")
        self.assertIn("rail-a durable finding", doc_path.read_text())

    def test_passed_checkpoint_triggers_compile(self):
        from scripts.track_state.dispatch import cmd_phase_checkpoint_review
        d = self._track()
        self._seed_finding(d, "phase-1 durable finding")
        o = self._capture(cmd_phase_checkpoint_review, d, "PASSED", "abc1234", None)
        self.assertTrue(o["ok"])
        self.assertTrue(o["stamped"])
        doc_path = Path(d) / ".conductor" / "track-findings.md"
        self.assertTrue(doc_path.exists(), "PASSED must compile track-findings.md")
        self.assertIn("phase-1 durable finding", doc_path.read_text())

    def test_failed_checkpoint_does_not_compile(self):
        from scripts.track_state.dispatch import cmd_phase_checkpoint_review
        d = self._track()
        self._seed_finding(d, "should not graduate mid-track")
        o = self._capture(cmd_phase_checkpoint_review, d, "FAILED", None, "AC1 not met")
        self.assertTrue(o["ok"])
        self.assertFalse(o["stamped"])
        doc_path = Path(d) / ".conductor" / "track-findings.md"
        self.assertFalse(doc_path.exists(),
                         "FAILED must NOT compile track-findings (no advance)")

    def test_compile_failure_is_fail_open(self):
        # A broken compile must not block the checkpoint stamp. Monkeypatch
        # compile_track_findings in the misc module (where the single-homed
        # trigger now lives) to raise.
        import scripts.track_state.misc as misc_mod
        original = misc_mod.compile_track_findings
        misc_mod.compile_track_findings = lambda td, current_phase=None: (
            _ for _ in ()).throw(RuntimeError("boom"))
        try:
            from scripts.track_state.misc import _stamp_checkpoint_in_plan
            d = self._track()
            o = _stamp_checkpoint_in_plan(d, 1, "abc1234")
            # Stamp still succeeds — the advisory compile error was swallowed.
            self.assertTrue(o["ok"], "checkpoint must stamp even if compile raises")
        finally:
            misc_mod.compile_track_findings = original


def _strip_compiled_at(text):
    """Drop the `_Last compiled:` line so idempotency compares stable content."""
    return "\n".join(l for l in text.splitlines() if not l.startswith("_Last compiled:"))


if __name__ == "__main__":
    main()
