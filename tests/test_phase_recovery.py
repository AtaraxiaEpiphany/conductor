"""Tests for phase-checkpoint recovery routing (Track 2 — "finally succeeds").

A phase checkpoint that FAILS on an auto-routing track (``recovery_policy=auto``
or continuous) does NOT halt for a human. It routes through the SAME failure-
analyst + verdict router at phase granularity (``_step_route_phase_recovery``),
so a long-running track marches through gated phases and finally succeeds
instead of stalling at the boundary. An ``ask``-surface track still halts
byte-identically (the legacy default).

Lifecycle (auto-routing track):
  cmd_phase_checkpoint_review FAILED → phase-recovery marker (stage=failed)
    → step dispatches failure-analyst (PHASE mode — PHASE_INDEX w/o TASK_INDEX)
    → cmd_phase_failure_analyst_verdict writes stage=analyzed
    → step routes:
        retry_modified → reactivate phase tasks + stage=recovering + redispatch
        replan + AC details → stage amendment + ONE ask
        replan w/o AC details / escalate → halt
  A re-FAILED after a retry cycle FOLDS the recovering marker's twin-backstop
  counters forward (recovery-policy.md: "a FAILED increments the counters and
  re-runs the analyst") so the budget binds the retry loop:
  RECOVERY_DRY_K consecutive dry rounds OR analysis_rounds >
  MAX_PHASE_RECOVERY_ROUNDS → halt (escalate). Without the fold the loop is
  unbounded — the regression case pinned by ``test_refail_folds_*`` and
  ``test_loop_with_novel_causes_halts_on_budget``.

Fixture reuse: ``test_step._phase_complete_track`` (ask-surface baseline),
``test_step._git_track_dir`` / ``_make_state`` / ``_step``.
"""
import importlib.util
import io
import json
import shutil
import sys
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load
from scripts.track_state.dispatch import (
    cmd_phase_verdict, cmd_phase_checkpoint_review,
    cmd_phase_failure_analyst_verdict,
    _phase_recovery_read_marker, _phase_recovery_write_marker,
    _amendment_staged_read_marker,
    _modified_guidance_read,
    _phase_cp_marker_path)
from scripts.track_state.constants import (
    RECOVERY_DRY_K, MAX_PHASE_RECOVERY_ROUNDS)
from scripts.track_state import cli

from tests.test_step import (
    _phase_complete_track, _git_track_dir, _make_state, _step)


def _run(fn, *args):
    """Capture a command's stdout JSON (the one ``out(...)`` it emits)."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _phase_complete_auto_track():
    """A Phase-1-terminal, no-checkpoint, ``recovery_policy=auto`` track — the
    auto-routing state that routes a FAILED checkpoint instead of halting."""
    state = _make_state(
        current_phase_index=0, current_task_index=0, recovery_policy="auto",
        phases=[{"name": "Phase 1", "status": "pending", "tasks": [
            {"name": "Task A", "status": "completed", "commit_sha": "abc1234"}]}])
    plan = "# Plan\n\n## Phase 1: Build\n- [x] Task A [abc1234]\n"
    return _git_track_dir(state, plan_content=plan)


def _pending_task_auto_track():
    """Phase-1 in_progress with Task A pending + ``recovery_policy=auto`` — the
    state a retry arm leaves (tasks reactivated to pending, marker recovering)."""
    state = _make_state(
        current_phase_index=1, current_task_index=1, recovery_policy="auto",
        phases=[{"name": "Phase 1", "status": "in_progress", "tasks": [
            {"name": "Task A", "status": "pending"}]}])
    plan = "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n"
    return _git_track_dir(state, plan_content=plan)


def _phase_recovery_marker(track_dir, **fields):
    """Write a phase-recovery marker (defaults to a valid analyzed P1 retry)."""
    base = {"phase": 1, "stage": "analyzed", "category": "deterministic_bug",
            "recommendation": "retry_modified", "root_cause": "import break",
            "modification": "fix the import", "what_was_done": None,
            "reason": "phase-checker FAILED", "ac_verdict": "passed",
            "build_status": "failed", "l1_status": "passed",
            "analysis_rounds": 1, "seen_root_causes": ["import break"],
            "consecutive_empty_rounds": 0, "ac_superseded": None,
            "ac_prime_text": None, "affected_tasks": []}
    base.update(fields)
    _phase_recovery_write_marker(track_dir, base)


def _complete_phase1_tasks(track_dir):
    """Mark every non-terminal Phase-1 task completed in state — simulate the
    re-run finishing at the task level so the checkpoint can re-fan. State is the
    source of truth (``_phase_needs_checkpoint`` reads it; ``_do_sync_plan``
    writes plan FROM state), so this survives ``ensure_healthy``."""
    state = load(track_dir)
    for t in state["phases"][0]["tasks"]:
        if t.get("status") not in ("completed", "skipped", "failed"):
            t["status"] = "completed"
            t.setdefault("commit_sha", "abc1234")
    from scripts.track_state.core import save
    save(track_dir, state)


class RouteNotHaltTests(TestCase):
    """``cmd_phase_checkpoint_review`` FAILED: auto-route (not halt) vs the
    byte-identical ask-surface halt (the legacy default)."""

    def test_failed_on_auto_track_routes_not_halts(self):
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # Seed the verifier fan-out verdicts a re-fan would carry (build FAILED).
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q",
             "failed", "npx tsc --noEmit")
        o = _run(cmd_phase_checkpoint_review, d, "FAILED", None, "build broke")
        self.assertTrue(o["ok"])
        self.assertTrue(o["routed_recovery"])  # the NEW field vs ask-surface
        self.assertFalse(o["stamped"])
        self.assertEqual(o["reason"], "build broke")
        self.assertNotIn("announce the reason and STOP", o["hint"])  # halt hint gone
        # Phase-recovery marker written at stage=failed carrying the verdicts.
        m = _phase_recovery_read_marker(d)
        self.assertEqual(m["stage"], "failed")
        self.assertEqual(m["phase"], 1)
        self.assertEqual(m["build_status"], "failed")
        self.assertEqual(m["l1_status"], "passed")
        self.assertEqual(m["analysis_rounds"], 0)  # first failure: zero
        self.assertEqual(m["seen_root_causes"], [])
        # phase-cp marker cleared (the next FAILED cycle's re-fan writes fresh).
        self.assertFalse(_phase_cp_marker_path(d).exists())

    def test_failed_on_ask_track_is_byte_identical(self):
        # _phase_complete_track is interactive + no recovery_policy → ask-surface.
        # The legacy halt path must be UNCHANGED: no routed_recovery, the halt
        # hint, and no phase-recovery marker written.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "FAILED", "AC1 ungrounded", None,
             "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "FAILED", None, "AC1 not met")
        self.assertTrue(o["ok"])
        self.assertFalse(o["stamped"])
        self.assertEqual(o["reason"], "AC1 not met")
        self.assertNotIn("routed_recovery", o)
        self.assertIn("announce the reason and STOP", o["hint"])
        self.assertIsNone(_phase_recovery_read_marker(d))
        self.assertFalse(_phase_cp_marker_path(d).exists())


class StepDispatchTests(TestCase):
    """``cmd_step`` reads a stage=failed marker → ``dispatch_phase_failure_analyst``
    with a PHASE-mode prompt (PHASE_INDEX without TASK_INDEX)."""

    def test_failed_marker_dispatches_phase_failure_analyst(self):
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "failed", "pytest -q",
             "failed", "npx tsc --noEmit")
        _run(cmd_phase_checkpoint_review, d, "FAILED", None, "build broke")
        o = _step(d)
        self.assertEqual(o["action"], "dispatch_phase_failure_analyst")
        self.assertEqual(o["agent"], "failure-analyst")
        self.assertEqual(o["phase"], 1)
        # PHASE mode: PHASE_INDEX + PHASE_MODE=true, NO TASK_INDEX line.
        self.assertIn("PHASE_INDEX=1", o["prompt"])
        self.assertIn("PHASE_MODE=true", o["prompt"])
        self.assertNotIn("TASK_INDEX", o["prompt"])
        self.assertIn("FAILURE_REASON=build broke", o["prompt"])
        self.assertIn("BUILD_VERIFY_STATUS=failed", o["prompt"])
        self.assertIn("L1_VERIFY_STATUS=failed", o["prompt"])
        # First dispatch: rounds=0 on the marker (the verdict increments to 1).
        self.assertIn("RECOVERY_ROUNDS=0", o["prompt"])
        self.assertIn(f"MAX_PHASE_RECOVERY_ROUNDS={MAX_PHASE_RECOVERY_ROUNDS}",
                      o["prompt"])


class VerdictRoutingTests(TestCase):
    """``_step_route_phase_recovery`` analyzed stage routes the recommendation."""

    def test_retry_modified_reactivates_phase_and_redispatches(self):
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _phase_recovery_marker(d, stage="analyzed", recommendation="retry_modified",
                               modification="fix the import",
                               root_cause="import break")
        o = _step(d)
        # Reactivated → redispatched (the spine re-runs the phase against the fix).
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["agent"], "task-executor")
        # Modification injected on the primary task (the SubagentStart hook reads it).
        self.assertIsNotNone(_modified_guidance_read(d, 1, 1, None))
        # Task reactivated off completed (→ pending/in_progress), not still completed.
        tgt = load(d)["phases"][0]["tasks"][0]
        self.assertIn(tgt["status"], ("pending", "in_progress"))
        # Marker flipped to transparent `recovering` (cmd_step skips it; the
        # counters persist across the re-run→re-fail cycle for the twin backstop).
        self.assertEqual(_phase_recovery_read_marker(d)["stage"], "recovering")

    def test_replan_with_ac_details_stages_amendment_and_asks(self):
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _phase_recovery_marker(d, stage="analyzed", recommendation="replan",
                               category="spec_plan_defect",
                               root_cause="AC-2 contradicts", modification="narrow AC-2",
                               ac_superseded="AC-2",
                               ac_prime_text="a corrected criterion",
                               affected_tasks=["P1.T1"])
        o = _step(d)
        self.assertEqual(o["action"], "ask")
        self.assertEqual(o["decision"]["header"], "Replan")
        # Amendment staged on disk carrying the AC payload (reuses the task-level
        # machinery; the phase's primary task is the reactivation target).
        staged = _amendment_staged_read_marker(d)
        self.assertEqual(staged["ac_superseded"], "AC-2")
        self.assertEqual(staged["ac_prime_text"], "a corrected criterion")
        self.assertEqual(staged["recommendation"], "replan")
        self.assertEqual(staged["task"], 1)  # primary task of phase 1
        # Phase-recovery marker cleared BEFORE the ask (else next step re-routes).
        self.assertIsNone(_phase_recovery_read_marker(d))

    def test_replan_without_ac_details_halts(self):
        # Legacy degrade: no auto-amendment without the AC specifics (the
        # invariant forbids silently rewriting an AC a gate measured against).
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _phase_recovery_marker(d, stage="analyzed", recommendation="replan",
                               category="spec_plan_defect", root_cause="AC-2 wrong")
        o = _step(d)
        self.assertEqual(o["action"], "halt")
        self.assertEqual(o["reason"], "replan")
        self.assertIsNone(_phase_recovery_read_marker(d))  # halt cleared it

    def test_escalate_halts(self):
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _phase_recovery_marker(d, stage="analyzed", recommendation="escalate",
                               category="stuck", root_cause="no path forward")
        o = _step(d)
        self.assertEqual(o["action"], "halt")
        self.assertEqual(o["reason"], "escalate")
        self.assertIsNone(_phase_recovery_read_marker(d))

    def test_verdict_requires_prior_marker(self):
        # cmd_phase_failure_analyst_verdict needs a phase-recovery marker (an
        # ask-surface track halts on FAILED — there is no verdict to transcribe).
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = _run(cmd_phase_failure_analyst_verdict, d, "deterministic_bug",
                 "retry_modified", "cause", "fix")
        self.assertIn("error", o)
        self.assertIn("no phase-recovery marker", o["error"])


class TwinBackstopTests(TestCase):
    """The retry arm is BOUNDED: the dry arm (no novelty) OR the budget arm
    (total rounds) → ``halt`` (escalate). Both reuse the shared twin backstop on
    the per-phase ceiling (``RECOVERY_DRY_K`` / ``MAX_PHASE_RECOVERY_ROUNDS``)."""

    def test_dry_backstop_halts(self):
        # consecutive_empty_rounds >= RECOVERY_DRY_K → the analyst has nothing
        # novel; halt instead of repeating a known-bad modification.
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _phase_recovery_marker(d, stage="analyzed", recommendation="retry_modified",
                               modification="fix", root_cause="cause",
                               consecutive_empty_rounds=RECOVERY_DRY_K)
        o = _step(d)
        self.assertEqual(o["action"], "halt")
        self.assertEqual(o["reason"], "escalate")

    def test_dry_below_k_still_retries(self):
        # consecutive_empty_rounds < RECOVERY_DRY_K → still room for the analyst.
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _phase_recovery_marker(d, stage="analyzed", recommendation="retry_modified",
                               modification="fix", root_cause="cause",
                               consecutive_empty_rounds=RECOVERY_DRY_K - 1)
        o = _step(d)
        self.assertEqual(o["action"], "dispatch")  # retry, not halt

    def test_budget_backstop_halts(self):
        # analysis_rounds > MAX_PHASE_RECOVERY_ROUNDS → halt regardless of novelty.
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _phase_recovery_marker(d, stage="analyzed", recommendation="retry_modified",
                               modification="fix", root_cause="cause",
                               analysis_rounds=MAX_PHASE_RECOVERY_ROUNDS + 1,
                               consecutive_empty_rounds=0)  # still novel
        o = _step(d)
        self.assertEqual(o["action"], "halt")
        self.assertEqual(o["reason"], "escalate")

    def test_refail_folds_recovering_counters_not_reset(self):
        # THE FOLD (regression for the unbounded-loop defect): a re-FAILED after a
        # retry cycle must CARRY the recovering marker's counters forward, NOT
        # reset them. recovery-policy.md: "a FAILED increments the counters and
        # re-runs the analyst." Resetting wipes rounds/seen across every re-fail,
        # defeating BOTH backstops → unbounded loop (the plan's anti-goal).
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _phase_recovery_marker(d, stage="recovering",
                               analysis_rounds=2,
                               seen_root_causes=["import break"],
                               consecutive_empty_rounds=1)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q",
             "failed", "npx tsc --noEmit")
        o = _run(cmd_phase_checkpoint_review, d, "FAILED", None, "build broke again")
        self.assertTrue(o["routed_recovery"])
        m = _phase_recovery_read_marker(d)
        self.assertEqual(m["stage"], "failed")
        # Counters CARRIED FORWARD (a reset would make these 0 / [] / 0).
        self.assertEqual(m["analysis_rounds"], 2)
        self.assertEqual(m["seen_root_causes"], ["import break"])
        self.assertEqual(m["consecutive_empty_rounds"], 1)

    def test_first_failure_starts_counters_at_zero(self):
        # No prior marker → genuinely first failure → counters start at zero.
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q",
             "failed", "npx tsc --noEmit")
        _run(cmd_phase_checkpoint_review, d, "FAILED", None, "build broke")
        m = _phase_recovery_read_marker(d)
        self.assertEqual(m["analysis_rounds"], 0)
        self.assertEqual(m["seen_root_causes"], [])
        self.assertEqual(m["consecutive_empty_rounds"], 0)


class BoundedLoopTests(TestCase):
    """The analyze→retry→re-fail loop is BOUNDED — it halts within
    MAX_PHASE_RECOVERY_ROUNDS+1 rounds. This is the plan's "finally succeeds OR
    escalates, never loops forever" guarantee; a reset-on-re-fail defect would
    make it loop forever (the loop guard would exit with action != "halt")."""

    def test_loop_with_novel_causes_halts_on_budget(self):
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out, rounds = None, 0
        # Novel root cause each round → the DRY arm never fires; this isolates the
        # BUDGET arm. rounds accrue 0→1→2→3→4 and halt at round 4 (> MAX=3).
        while ((out or {}).get("action") != "halt"
               and rounds <= MAX_PHASE_RECOVERY_ROUNDS + 2):
            rounds += 1
            _complete_phase1_tasks(d)  # the re-run finished at the task level
            _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q",
                 "failed", "npx tsc --noEmit")
            _run(cmd_phase_checkpoint_review, d, "FAILED", None, "build broke")
            _step(d)  # stage=failed → dispatch_phase_failure_analyst
            _run(cmd_phase_failure_analyst_verdict, d, "deterministic_bug",
                 "retry_modified", f"novel cause {rounds}", f"fix {rounds}")
            out = _step(d)  # analyzed route → dispatch (recovering) OR halt
        self.assertEqual(out["action"], "halt")
        self.assertEqual(out["reason"], "escalate")
        # Bounded: budget fires by round MAX+1. A reset-on-re-fail defect never
        # accrues rounds, the loop guard (rounds <= MAX+2) exits with no halt,
        # and the assertion above fails — the regression signal.
        self.assertLessEqual(rounds, MAX_PHASE_RECOVERY_ROUNDS + 1)


class RecoveringStageTests(TestCase):
    """A ``recovering`` marker is TRANSPARENT: ``cmd_step`` skips it (stage check)
    so the spine re-dispatches the reactivated tasks normally — it does NOT
    re-route phase recovery. The marker survives so counters persist."""

    def test_recovering_marker_is_skipped_by_step(self):
        d = _pending_task_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _phase_recovery_marker(d, stage="recovering", analysis_rounds=1,
                               seen_root_causes=["import break"])
        o = _step(d)
        # NOT a phase-recovery route (would be dispatch_phase_failure_analyst or a
        # halt); the spine dispatches the pending task normally.
        self.assertEqual(o["action"], "dispatch")
        self.assertEqual(o["agent"], "task-executor")
        # The recovering marker survives (counters persist across the cycle).
        m = _phase_recovery_read_marker(d)
        self.assertEqual(m["stage"], "recovering")
        self.assertEqual(m["analysis_rounds"], 1)


class ResolutionTests(TestCase):
    """A retry cycle that finally PASSES resolves the phase-recovery marker."""

    def test_passed_clears_phase_recovery_marker(self):
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # A recovering marker a retry cycle would leave; a PASSED clears it.
        _phase_recovery_marker(d, stage="recovering", analysis_rounds=2)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", "abc1234", None)
        self.assertTrue(o["ok"])
        self.assertTrue(o["stamped"])
        # Marker cleared → the next step advances instead of re-routing.
        self.assertIsNone(_phase_recovery_read_marker(d))


class CliWiringTests(TestCase):
    """``phase-failure-analyst-verdict`` resolves through ``cli.main`` with its
    flag parse, is listed in help + a command group, and is in the sanctioned-
    subcommand allowlist (else the orchestrator-read-guard hook denies it)."""

    def _invoke(self, argv):
        old_argv, old_out = sys.argv, sys.stdout
        sys.argv, sys.stdout = ["track-state"] + argv, io.StringIO()
        try:
            cli.main()
            return json.loads(sys.stdout.getvalue())
        finally:
            sys.argv, sys.stdout = old_argv, old_out

    def test_phase_failure_analyst_verdict_resolves_via_cli(self):
        d = _phase_complete_auto_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _phase_recovery_marker(d, stage="failed")  # verdict needs a prior marker
        o = self._invoke(["phase-failure-analyst-verdict", d,
                          "--category", "deterministic_bug",
                          "--recommendation", "retry_modified",
                          "--root-cause", "import break",
                          "--modification", "fix the import"])
        self.assertTrue(o["ok"])
        m = _phase_recovery_read_marker(d)
        self.assertEqual(m["stage"], "analyzed")
        self.assertEqual(m["recommendation"], "retry_modified")

    def test_command_listed_in_help_group_and_sanctioned(self):
        grouped = {c for _name, cmds in cli._COMMAND_GROUPS for c in cmds}
        self.assertIn("phase-failure-analyst-verdict", cli.COMMAND_HELP)
        self.assertIn("phase-failure-analyst-verdict", grouped)
        _scripts = Path(__file__).resolve().parent.parent / "scripts"
        _spec = importlib.util.spec_from_file_location(
            "pre_command_check", _scripts / "pre-command-check.py")
        _pcc = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_pcc)
        self.assertIn("phase-failure-analyst-verdict",
                      _pcc._SANCTIONED_TS_SUBCOMMANDS)


if __name__ == "__main__":
    main()
