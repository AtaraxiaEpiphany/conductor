"""Tests for the phase-checkpoint handshake commands (WM2 verdict-on-disk, step 2).

``cmd_phase_verdict`` + ``cmd_phase_checkpoint_review`` are stamp-only transcribe
commands mirroring ``cmd_post_loop_review`` (WM2-1): the teleoperator transcribes
a read-only agent's fixed-format RESULT line to one of these, then re-calls
``step``; the spine re-derives the next leaf from the marker. The agent firewall
stays intact (no read-only agent writes state); the §3.2 parse→assemble and §3.7
stamp/halt judgments the prose hand-off asked the model to make now live in code.

Fixture reuse: the git-backed "phase 1 terminal, no checkpoint" track is exactly
``test_step._phase_complete_track`` — imported rather than duplicated.
"""
import io
import json
import shutil
import sys
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.dispatch import (
    cmd_phase_verdict, cmd_phase_checkpoint_review,
    _phase_cp_read_marker, _phase_cp_marker_path)
from scripts.track_state import cli

# Reuse the git-backed "phase 1 complete, no checkpoint" track fixture + builder.
from tests.test_step import (
    _phase_complete_track, _git_track_dir, _make_state, _head_short)


def _run(fn, *args):
    """Capture a stamp-only command's stdout JSON."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _has_checkpoint(plan_text, phase):
    import re
    return bool(re.search(rf"^##\s+Phase\s+{phase}\b.*\[checkpoint:\s*[0-9a-f]+\]",
                          plan_text, re.MULTILINE))


class PhaseVerdictTests(TestCase):
    def test_writes_synth_pending_marker(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        self.assertTrue(o["ok"])
        self.assertEqual(o["phase"], 1)
        self.assertEqual(o["stage"], "synth_pending")
        m = _phase_cp_read_marker(d)
        self.assertEqual(m["stage"], "synth_pending")
        self.assertEqual(m["ac_verdict"], "passed")
        self.assertEqual(m["l1_status"], "passed")
        self.assertEqual(m["l1_command"], "pytest -q")
        self.assertIsNone(m["ac_gate"])

    def test_build_verdict_threads_into_phase_checker_assignment(self):
        # _build_phase_checker emits the BUILD_VERIFY_STATUS/COMMAND +
        # L1_VERIFY_STATUS/COMMAND lines the phase-checker consumes from the
        # marker. Every CODE phase fans out the ac-tracer + build-runner +
        # test-runner triple, so the assignment carries BOTH the build verdict
        # and the L1 verdict (the build tier is the cheapest-first floor).
        from scripts.track_state.dispatch import _build_phase_checker
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        state = {"track_id": "t", "execution_mode": "interactive"}
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q",
             "passed", "npx tsc --noEmit")
        m = _phase_cp_read_marker(d)
        body = _build_phase_checker(d, state, 1, m)
        self.assertIn("BUILD_VERIFY_STATUS=passed", body)
        self.assertIn("BUILD_VERIFY_COMMAND=npx tsc --noEmit", body)
        self.assertIn("L1_VERIFY_STATUS=passed", body)
        self.assertIn("L1_VERIFY_COMMAND=pytest -q", body)

    def test_build_failed_threads_to_phase_checker_assignment(self):
        # THE hard-gate rule (Track 1): a build-runner STATUS: failed verdict
        # threads through to the phase-checker as BUILD_VERIFY_STATUS=failed, the
        # signal phase-checker.md §2.5 turns into STATUS: FAILED (a compile break
        # is a phase failure, never an advance-on-broken-code). The build tier is
        # the cheapest-first floor — it fails before the suite is even read.
        from scripts.track_state.dispatch import _build_phase_checker
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        state = {"track_id": "t", "execution_mode": "interactive"}
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q",
             "failed", "npx tsc --noEmit")
        m = _phase_cp_read_marker(d)
        self.assertEqual(m["build_status"], "failed")
        body = _build_phase_checker(d, state, 1, m)
        self.assertIn("BUILD_VERIFY_STATUS=failed", body)
        self.assertIn("BUILD_VERIFY_COMMAND=npx tsc --noEmit", body)

    def test_build_status_error_is_non_blocking_and_persisted(self):
        # build-runner STATUS: error means "no build command resolvable" (e.g. a
        # pure-Python repo with no compile step) — NON-BLOCKING (phase-checker.md
        # §2.5 treats it as a no-op, not a failure). It still threads through so
        # the checker records it rather than reading an empty verdict as a defect.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q",
                 "error", None)
        self.assertTrue(o["ok"])
        m = _phase_cp_read_marker(d)
        self.assertEqual(m["build_status"], "error")
        self.assertIsNone(m["build_command"])

    def test_unknown_build_status_errors(self):
        # A transcription typo HALTs with a clear error (code guard — never hand
        # the synthesizer a garbage build verdict). Mirrors the l1/ac enum guards.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q",
                 "borked", None)
        self.assertIn("error", o)
        self.assertFalse(_phase_cp_marker_path(d).exists(),
                         "a rejected transcription must not write a marker")

    def test_unknown_ac_verdict_errors(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "bogus", None, None, "passed", "pytest -q")
        self.assertIn("error", o)
        self.assertFalse(_phase_cp_marker_path(d).exists(),
                         "a rejected transcription must not write a marker")

    def test_unknown_l1_status_errors(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "nope", "pytest -q")
        self.assertIn("error", o)

    def test_errors_when_no_pending_checkpoint(self):
        # Phase 1 already has a checkpoint → nothing to synthesize.
        state = _make_state(
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed", "commit_sha": "abc1234"}]}])
        plan = "# Plan\n\n## Phase 1: Build [checkpoint: abc1234]\n- [x] Task A\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        self.assertIn("error", o)
        self.assertFalse(_phase_cp_marker_path(d).exists())

    def test_idempotent_overwrite(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        # A re-fan with fresh verdicts overwrites cleanly (no append/dup).
        _run(cmd_phase_verdict, d, "FAILED", "AC2 bad", None, "failed", "make test")
        m = _phase_cp_read_marker(d)
        self.assertEqual(m["ac_verdict"], "FAILED")
        self.assertEqual(m["ac_gate"], "AC2 bad")
        self.assertEqual(m["l1_status"], "failed")


class PhaseCheckpointReviewTests(TestCase):
    def test_passed_stamps_checkpoint_and_clears_marker(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        sha = _head_short(d)  # real commit — the stamp home verifies it resolves
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", sha, None)
        self.assertTrue(o["ok"])
        self.assertTrue(o["stamped"])
        self.assertEqual(o["sha"], sha)
        plan = (Path(d) / "plan.md").read_text()
        self.assertTrue(_has_checkpoint(plan, 1),
                        "PASSED must stamp [checkpoint: <sha>] on the phase heading")
        self.assertFalse(_phase_cp_marker_path(d).exists(),
                         "marker must be cleared so the next step advances")

    def test_passed_missing_sha_errors(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", None, None)
        self.assertIn("error", o)
        self.assertTrue(_phase_cp_marker_path(d).exists(),
                        "a rejected stamp must leave the marker (re-review, not re-fan)")
        plan = (Path(d) / "plan.md").read_text()
        self.assertFalse(_has_checkpoint(plan, 1))

    def test_passed_bad_sha_errors(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", "notahex", None)
        self.assertIn("error", o)

    def test_failed_clears_marker_with_reason(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "FAILED", "AC1 ungrounded", None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "FAILED", None, "AC1 not met")
        self.assertTrue(o["ok"])
        self.assertFalse(o["stamped"])
        self.assertEqual(o["reason"], "AC1 not met")
        plan = (Path(d) / "plan.md").read_text()
        self.assertFalse(_has_checkpoint(plan, 1), "FAILED must not stamp")
        self.assertFalse(_phase_cp_marker_path(d).exists(),
                         "marker cleared so re-invoke re-fans fresh (§3.7 re-run)")

    def test_failed_without_reason_uses_default(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "FAILED", None, None)
        self.assertEqual(o["reason"], "phase-checker FAILED")

    def test_unknown_status_errors(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "MAYBE", None, None)
        self.assertIn("error", o)
        self.assertTrue(_phase_cp_marker_path(d).exists(),
                        "unknown status leaves the marker intact")

    def test_review_when_already_stamped_is_clean(self):
        # A duplicate review call after the checkpoint is already stamped must
        # not crash (no pending checkpoint) and must clear any stray marker.
        state = _make_state(
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed", "commit_sha": "abc1234"}]}])
        plan = "# Plan\n\n## Phase 1: Build [checkpoint: abc1234]\n- [x] Task A\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", "abc1234", None)
        self.assertTrue(o["ok"])
        self.assertFalse(o["stamped"])


class StampHomeBoundaryEffectsTests(TestCase):
    """A1: the stamp home owns ALL PASSED boundary effects — the phase-cp +
    phase-recovery marker clears and the ``passed`` gate-outcome telemetry live
    in ``_stamp_checkpoint_in_plan``, so the agent's binding Step-8 self-stamp
    (Rail A ``add-checkpoint``) and the review command's PASSED arm converge on
    identical effects. Pre-fix, only review performed them — but review
    early-returns once the agent has stamped, so telemetry feed 3 recorded only
    FAILED rows and a stale recovery marker survived a recovery-then-pass."""

    def _gate_rows(self, d):
        return json.loads(
            (Path(d) / ".conductor" / "gate-outcomes.json").read_text())["rows"]

    def test_agent_self_stamp_clears_markers_and_records_telemetry(self):
        from scripts.track_state.dispatch import (
            _phase_recovery_write_marker, _phase_recovery_read_marker)
        from scripts.track_state.misc import cmd_add_checkpoint
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        # A recovering marker a retry cycle would leave; a PASSED resolves it.
        _phase_recovery_write_marker(
            d, {"phase": 1, "stage": "recovering", "analysis_rounds": 2})
        o = _run(cmd_add_checkpoint, d, 1, _head_short(d))
        self.assertTrue(o["ok"])
        self.assertFalse(_phase_cp_marker_path(d).exists(),
                         "the self-stamp must clear the synth-pending marker")
        self.assertIsNone(_phase_recovery_read_marker(d),
                          "the self-stamp must resolve a recovery cycle")
        rows = self._gate_rows(d)
        self.assertTrue(rows, "the self-stamp must record passed gate rows")
        self.assertTrue(all(r["verdict"] == "passed" for r in rows))

    def test_review_after_self_stamp_converges_without_duplicate_rows(self):
        # The live-path sequence: agent self-stamps (Rail A), then the
        # teleoperator transcribes the result to review PASSED. Review becomes
        # an idempotent router — it must NOT double-append telemetry.
        from scripts.track_state.misc import cmd_add_checkpoint
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        sha = _head_short(d)
        _run(cmd_add_checkpoint, d, 1, sha)
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", sha, None)
        self.assertTrue(o["ok"])
        self.assertFalse(o["stamped"], "already stamped — idempotent router")
        self.assertEqual(len(self._gate_rows(d)), 3,
                         "one settlement, one set of rows")

    def test_eight_char_sha_accepted(self):
        # A3: git %h auto-extends past 7 on large repos — the write gates
        # accept git's own output instead of spuriously FAILEDing the
        # checkpoint at Step 8.
        from scripts.track_state.misc import cmd_add_checkpoint
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        sha = _head_short(d, width=8)
        self.assertEqual(len(sha), 8)
        o = _run(cmd_add_checkpoint, d, 1, sha)
        self.assertTrue(o["ok"])
        self.assertIn(f"[checkpoint: {sha}]",
                      (Path(d) / "plan.md").read_text())

    def test_non_integer_phase_errors_cleanly(self):
        # A4: cli passes the positional raw — a non-integer phase must error
        # as JSON, not traceback past the agent's `ok: true` check.
        from scripts.track_state.misc import cmd_add_checkpoint
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_add_checkpoint, d, "one", "abc1234")
        self.assertIn("error", o)
        self.assertIn("integer", o["error"])
        self.assertFalse(_has_checkpoint((Path(d) / "plan.md").read_text(), 1))

    def test_hallucinated_sha_rejected_in_repo(self):
        # A5: a well-formed fabricated sha would stamp a lying artifact that
        # breaks the NEXT phase's `git diff <checkpoint> HEAD` — fail closed
        # wherever a repo is discoverable.
        from scripts.track_state.misc import cmd_add_checkpoint
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_add_checkpoint, d, 1, "1234567")
        self.assertIn("error", o)
        self.assertIn("does not resolve", o["error"])
        self.assertFalse(_has_checkpoint((Path(d) / "plan.md").read_text(), 1))

    def test_repo_less_dir_stamps_fail_open(self):
        # A5's fail-open side: a plain tmpdir (no git) cannot verify — the
        # format gate alone decides. (The detector-consistency tests below
        # exercise the same path incidentally; this pins the contract.)
        import tempfile
        from scripts.track_state.misc import _stamp_checkpoint_in_plan
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [x] A\n")
        r = _stamp_checkpoint_in_plan(d, 1, "abc1234")
        self.assertTrue(r.get("ok"))


class VerdictEnvelopeTests(TestCase):
    """A2/A7: the verdict-handshake envelopes surface ``ensure_healthy``
    auto-fixes (+ the relayed bookkeeping commit) and a verdict-completeness
    advisory, instead of dropping both on the floor."""

    def test_fixes_surfaced_with_bookkeeping_line(self):
        # The live incident: phase-boundary auto-fixes leave track-state.json
        # dirty with no relayed commit — surface them so the teleoperator
        # stages the file instead of reverting a modification it did not make.
        import scripts.track_state.dispatch as dispatch_mod
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        state, _fixes, _verrors = dispatch_mod.ensure_healthy(d)
        orig = dispatch_mod.ensure_healthy

        def fake_healthy(_td):
            return state, ["phase-status propagation"], []

        dispatch_mod.ensure_healthy = fake_healthy
        self.addCleanup(setattr, dispatch_mod, "ensure_healthy", orig)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        self.assertEqual(o["fixes_applied"], ["phase-status propagation"])
        self.assertIn("git add -A", o["bookkeeping"])
        self.assertIn("auto-fix", o["bookkeeping"])

    def test_no_fix_keys_when_clean(self):
        # Post-fix shape: phase status already propagated + all verdicts
        # transcribed → no fixes, no advisory, no bookkeeping line.
        state = _make_state(
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "completed", "tasks": [
                {"name": "Task A", "status": "completed",
                 "commit_sha": "abc1234"}]}])
        plan = "# Plan\n\n## Phase 1: Build\n- [x] Task A\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q",
                  "passed", "npx tsc --noEmit")
        self.assertNotIn("fixes_applied", o)
        self.assertNotIn("bookkeeping", o)
        self.assertNotIn("missing_verdicts", o)

    def test_missing_verdicts_advisory(self):
        # A7: a fanned verifier with no transcribed status flags at the CLI —
        # cheaper than the agent's dispatch-defect FAILURE backstop.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, None, None)
        self.assertTrue(o["ok"])
        self.assertEqual(o["missing_verdicts"], ["build-runner", "test-runner"])
        self.assertIn("phase-verdict", o["hint"])

    def test_complete_verdicts_carry_no_advisory(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q",
                  "passed", "npx tsc --noEmit")
        self.assertTrue(o["ok"])
        self.assertNotIn("missing_verdicts", o)

    def test_code_free_phase_absent_tiers_are_not_missing(self):
        # A code-free phase narrows out the CODE tiers — their absence is
        # legitimate fan-out narrowing, not a transcription miss.
        state = _make_state(
            current_phase_index=0, current_task_index=0,
            phases=[{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "[Config] Task A", "status": "completed",
                 "commit_sha": "abc1234"}]}])
        plan = "# Plan\n\n## Phase 1: Build\n- [x] [Config] Task A\n"
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_verdict, d, "passed", None, None, None, None)
        self.assertTrue(o["ok"])
        self.assertNotIn("missing_verdicts", o)


class CheckpointStampDetectorConsistencyTests(TestCase):
    """The four checkpoint-stamp detectors (``plan_parse._CHECKPOINT``,
    ``validate``, ``helpers._phase_needs_checkpoint``,
    ``misc._stamp_checkpoint_in_plan``) must agree on what counts as a stamp —
    including a hand-authored/legacy no-space stamp (``[checkpoint:abcdef1]``).
    An earlier ``\\s+``/``\\s*`` split let the parser strip a no-space stamp the
    gate/removal regexes then missed → a re-run gate + a duplicate stamp."""

    def _track_with_stamp(self, stamp):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "plan.md").write_text(
            f"# Plan\n\n## Phase 1: Build {stamp}\n- [x] Task A\n")
        # Phase fully terminal so _phase_needs_checkpoint reaches the stamp check.
        from scripts.track_state.core import save
        save(d, {
            "track_id": "cp", "type": "feature", "status": "in_progress",
            "current_phase_index": 0, "current_task_index": 0,
            "phases": [{"name": "Phase 1", "status": "pending", "tasks": [
                {"name": "Task A", "status": "completed", "commit_sha": "abc1234"}]}],
        })
        return d

    def test_no_space_stamp_recognized_as_checkpointed(self):
        # helpers._phase_needs_checkpoint (was \s+) must recognize a no-space
        # stamp the way plan_parse/validate do, so the gate does not re-run for
        # an already-checkpointed phase.
        from scripts.track_state.helpers import _phase_needs_checkpoint
        from scripts.track_state.core import load
        d = self._track_with_stamp("[checkpoint:abcdef1]")
        self.assertIsNone(_phase_needs_checkpoint(d, load(d), 1))

    def test_no_space_stamp_stripped_on_restamp_no_duplicate(self):
        # misc._stamp_checkpoint_in_plan (was \s+) must strip a no-space stamp
        # before re-stamping, so the heading does not carry two stamps.
        from scripts.track_state.misc import _stamp_checkpoint_in_plan
        d = self._track_with_stamp("[checkpoint:abcdef1]")
        r = _stamp_checkpoint_in_plan(d, 1, "fedcba9")
        self.assertTrue(r.get("ok"))
        text = Path(d, "plan.md").read_text()
        self.assertEqual(text.count("[checkpoint:"), 1)
        self.assertIn("[checkpoint: fedcba9]", text)


class CliWiringTests(TestCase):
    """The two commands resolve through cli.main (which reads sys.argv) with
    their flag parse, and are listed in help + a command group. The sanctioned
    -subcommand drift test asserts the allowlist covers them."""

    def _invoke(self, argv):
        old_argv, old_out = sys.argv, sys.stdout
        sys.argv, sys.stdout = ["track-state"] + argv, io.StringIO()
        try:
            cli.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_out

    def test_phase_verdict_resolves_via_cli(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._invoke(["phase-verdict", d, "--ac-verdict", "passed",
                      "--l1-status", "passed", "--l1-command", "pytest -q"])
        self.assertTrue(_phase_cp_marker_path(d).exists())

    def test_phase_checkpoint_review_resolves_via_cli(self):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        self._invoke(["phase-checkpoint-review", d, "--status", "PASSED",
                      "--sha", _head_short(d)])
        plan = (Path(d) / "plan.md").read_text()
        self.assertTrue(_has_checkpoint(plan, 1))

    def test_commands_listed_in_help_and_group(self):
        grouped = {c for _name, cmds in cli._COMMAND_GROUPS for c in cmds}
        for sub in ("phase-verdict", "phase-checkpoint-review"):
            self.assertIn(sub, cli.COMMAND_HELP)
            self.assertIn(sub, grouped)


if __name__ == "__main__":
    main()
