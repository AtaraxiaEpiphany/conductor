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
    _append_execution_record, _extract_candidates, _extract_subtask_section,
)
from tests.test_step import _head_short  # real commit shas (stamp home verifies)


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
        o = _stamp_checkpoint_in_plan(d, 1, _head_short(d))
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
        o = self._capture(cmd_add_checkpoint, d, 1, _head_short(d))
        self.assertTrue(o["ok"])
        doc_path = Path(d) / ".conductor" / "track-findings.md"
        self.assertTrue(doc_path.exists(),
                        "add-checkpoint must compile track-findings.md")
        self.assertIn("rail-a durable finding", doc_path.read_text())

    def test_passed_checkpoint_triggers_compile(self):
        from scripts.track_state.dispatch import cmd_phase_checkpoint_review
        d = self._track()
        self._seed_finding(d, "phase-1 durable finding")
        o = self._capture(cmd_phase_checkpoint_review, d, "PASSED", _head_short(d), None)
        self.assertTrue(o["ok"])
        self.assertTrue(o["stamped"])
        doc_path = Path(d) / ".conductor" / "track-findings.md"
        self.assertTrue(doc_path.exists(), "PASSED must compile track-findings.md")
        self.assertIn("phase-1 durable finding", doc_path.read_text())

    def test_failed_checkpoint_compiles_without_stamp(self):
        # Findings/artifact edge: the FAILED arm compiles too (fail-open) — a
        # failed phase is often where the learning is, and the recovery
        # analyst/retry cycle both read a fresh track-findings.md. No stamp:
        # compiling must never read as an advance.
        from scripts.track_state.dispatch import cmd_phase_checkpoint_review
        d = self._track()
        self._seed_finding(d, "failed-phase durable finding")
        o = self._capture(cmd_phase_checkpoint_review, d, "FAILED", None, "AC1 not met")
        self.assertTrue(o["ok"])
        self.assertFalse(o["stamped"])
        doc_path = Path(d) / ".conductor" / "track-findings.md"
        self.assertTrue(doc_path.exists(),
                        "FAILED must compile track-findings (no stamp)")
        self.assertIn("failed-phase durable finding", doc_path.read_text())

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
            o = _stamp_checkpoint_in_plan(d, 1, _head_short(d))
            # Stamp still succeeds — the advisory compile error was swallowed.
            self.assertTrue(o["ok"], "checkpoint must stamp even if compile raises")
        finally:
            misc_mod.compile_track_findings = original


def _strip_compiled_at(text):
    """Drop the `_Last compiled:` line so idempotency compares stable content."""
    return "\n".join(l for l in text.splitlines() if not l.startswith("_Last compiled:"))


class ArtifactsRollTests(TestCase):
    """The task-artifact ledger roll (findings/artifact edge): a SUCCESS
    finalize writes a ``## Task Artifacts`` block as its own ``##``-level
    handoff section — the durable home, since result.json is reaped at
    finalize. FAILURE never rolls (no artifact fact from an incomplete task);
    the block must not corrupt the subtask slice above it."""

    def setUp(self):
        self.d = _make_track()
        self.addCleanup(__import__("shutil").rmtree, self.d, ignore_errors=True)

    def _handoff(self, p=1, t=1):
        return (Path(self.d) / ".conductor" / "handoff" / f"P{p}T{t}.md"
                ).read_text()

    def _success_result(self):
        return {"status": "SUCCESS", "task_name": "Task A", "attempt": 1,
                "phase": 1, "task": 1,
                "commit_sha": "abc1234", "summary": "wrote baseline",
                "artifacts": [{"path": "reports/baseline.md",
                               "role": "pre-migration metrics"}],
                "artifacts_used": ["docs/inventory.md"]}

    def test_success_rolls_artifacts_block(self):
        _append_execution_record(self.d, 1, 1, None, self._success_result())
        h = self._handoff()
        self.assertIn("## Task Artifacts", h)
        self.assertIn("### Produced", h)
        self.assertIn("- reports/baseline.md — pre-migration metrics", h)
        self.assertIn("### Used", h)
        self.assertIn("- docs/inventory.md", h)

    def test_success_without_artifacts_writes_no_block(self):
        r = self._success_result()
        del r["artifacts"], r["artifacts_used"]
        _append_execution_record(self.d, 1, 1, None, r)
        self.assertNotIn("## Task Artifacts", self._handoff())

    def test_failure_never_rolls(self):
        r = self._success_result()
        r["status"] = "FAILURE"
        r["failure_detail"] = {"what_was_done": "x", "failure_reason": "y",
                               "suggested_next_step": "z"}
        _append_execution_record(self.d, 1, 1, None, r)
        self.assertNotIn("## Task Artifacts", self._handoff())

    def test_legacy_process_result_success_rolls(self):
        # The legacy raw-JSON finalize path shares _append_execution_record —
        # one roll hook covers serial + wave + legacy.
        from scripts.track_state.result import cmd_process_result
        cond = Path(self.d) / ".conductor"
        cond.mkdir(parents=True, exist_ok=True)
        (cond / "result.json").write_text(
            json.dumps(self._success_result()))
        with io.StringIO() as buf:
            old, sys.stdout = sys.stdout, buf
            try:
                cmd_process_result(self.d)
            finally:
                sys.stdout = old
        self.assertIn("## Task Artifacts", self._handoff())

    def test_subtask_slice_survives_artifacts_block(self):
        # The roll writes AFTER the subtask section as a sibling ## heading.
        # _extract_subtask_section terminates at the next ## header — the
        # slice must still contain the final attempt and exclude the block.
        _append_execution_record(
            self.d, 1, 1, 2,
            dict(self._success_result(), task_name="sub two"))
        content = self._handoff()
        slc = _extract_subtask_section(content, 2)
        self.assertIn("## Subtask 2: sub two", slc)
        self.assertIn("### Attempt 1", slc)          # final attempt intact
        self.assertNotIn("## Task Artifacts", slc)   # block is not subtask's


class ArtifactsHarvestRenderTests(TestCase):
    """Harvest + catalog render: the roll's ## Task Artifacts blocks are
    collected (deduped, capped, role-split) into artifacts_produced/used
    buckets and rendered as a ## Task Artifacts catalog section — an
    artifacts-only track is a real doc, never the empty stub."""

    def setUp(self):
        self.d = _make_track()
        self.addCleanup(__import__("shutil").rmtree, self.d, ignore_errors=True)

    def _handoff_dir(self):
        h = Path(self.d) / ".conductor" / "handoff"
        h.mkdir(parents=True, exist_ok=True)
        return h

    def _seed(self, stem, produced=(), used=()):
        bullets = ""
        if produced:
            bullets += "### Produced\n" + "".join(
                f"- {p}\n" for p in produced)
        if used:
            bullets += "### Used\n" + "".join(f"- {u}\n" for u in used)
        (self._handoff_dir() / f"{stem}.md").write_text(
            f"# {stem}\n\n## Task Artifacts | 2026-09-03T00:00:00+00:00\n\n"
            f"{bullets}")

    def test_roll_then_compile_renders_catalog(self):
        _append_execution_record(
            self.d, 1, 1, None,
            {"status": "SUCCESS", "task_name": "Task A", "attempt": 1,
             "summary": "s", "artifacts": [{"path": "reports/baseline.md",
                                            "role": "pre-migration metrics"}],
             "artifacts_used": []})
        r = compile_track_findings(self.d)
        self.assertEqual(r["artifacts_produced_count"], 1)
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertIn("## Task Artifacts", doc)
        self.assertIn(
            "- reports/baseline.md — pre-migration metrics _— source P1T1 (Phase 1)_",
            doc)

    def test_artifacts_only_track_is_not_stub(self):
        self._seed("P1T1", produced=["reports/baseline.md — metrics"])
        r = compile_track_findings(self.d)
        self.assertTrue(r["compiled"])
        self.assertEqual(r["artifacts_produced_count"], 1)
        doc = (Path(self.d) / ".conductor" / "track-findings.md").read_text()
        self.assertIn("reports/baseline.md", doc)
        self.assertNotIn("No durable findings recorded yet", doc)

    def test_role_split_and_no_role(self):
        self._seed("P1T1", produced=["a.md — the role", "b.md"])
        h = _extract_candidates(Path(self.d) / ".conductor" / "handoff")
        self.assertEqual(h["artifacts_produced"], [
            {"path": "a.md", "role": "the role", "source": "P1T1"},
            {"path": "b.md", "role": "", "source": "P1T1"},
        ])

    def test_used_bucket_collected(self):
        self._seed("P2T1", used=["a.md", "b.md"])
        h = _extract_candidates(Path(self.d) / ".conductor" / "handoff")
        self.assertEqual(h["artifacts_used"], [
            {"path": "a.md", "source": "P2T1"},
            {"path": "b.md", "source": "P2T1"},
        ])

    def test_dedup_across_handoffs(self):
        self._seed("P1T1", produced=["shared.md — role"])
        self._seed("P2T1", produced=["shared.md — role", "only-later.md"])
        h = _extract_candidates(Path(self.d) / ".conductor" / "handoff")
        self.assertEqual([a["path"] for a in h["artifacts_produced"]],
                         ["shared.md", "only-later.md"])

    def test_capped_per_kind_per_file(self):
        self._seed("P1T1",
                   produced=[f"p{i}.md" for i in range(10)],
                   used=[f"u{i}.md" for i in range(10)])
        h = _extract_candidates(Path(self.d) / ".conductor" / "handoff")
        self.assertEqual(len(h["artifacts_produced"]), 8)
        self.assertEqual(len(h["artifacts_used"]), 8)
        self.assertIn("p7.md", [a["path"] for a in h["artifacts_produced"]])
        self.assertNotIn("p8.md", [a["path"] for a in h["artifacts_produced"]])

    def test_walk_stops_at_next_h2_block(self):
        # A ## Task Artifacts block ends at the next ## heading — bullets in
        # a later section must not leak into the ledger buckets.
        (self._handoff_dir() / "P1T1.md").write_text(
            "# P1T1\n\n## Task Artifacts | ts\n\n### Produced\n- a.md\n\n"
            "## Execution Record\n\n- not-an-artifact.md\n")
        h = _extract_candidates(Path(self.d) / ".conductor" / "handoff")
        self.assertEqual([a["path"] for a in h["artifacts_produced"]],
                         ["a.md"])


if __name__ == "__main__":
    main()
