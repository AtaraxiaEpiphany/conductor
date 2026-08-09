"""Tests for Track C2 — the ``checkpoint_policy`` load-bearing branch.

The governing invariant (the plugin-generality campaign's rule): **every freedom
declares an integrity substitute.** A shape that takes the freedom of skipping
its checkpoint (``checkpoint_policy: skip-if-declared``) MUST declare the
verification it runs INSTEAD (``ac_grounding: review`` → the review
attestation). A skip with no substitute would make the "verified against AC-N"
stamp hollow — so it MUST fail-hard, never silently skip.

Three layers are tested:

1. ``checkpoint_skip_decision`` — the pure verdict (``run`` | ``skip`` |
   ``violation``), the one call dispatch makes.
2. ``validate_merged_shapes`` — the save-time cross-field guard (the PRIMARY
   catch; the strict-write gate refuses a freedom-without-substitute shape).
3. ``_phase_needs_checkpoint`` + the two dispatch emit sites (Rail A
   ``cmd_dispatch_next``, Rail B ``cmd_step``) — the runtime waive
   (skip+substitute → no checkpoint emit) and the runtime fail-hard
   (skip+no-substitute → ``shape_violation`` error, defense-in-depth for a
   hand-edited registry that slipped past the save gate).
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.dispatch import (
    cmd_dispatch_next, cmd_step)
from scripts.track_state.helpers import _phase_needs_checkpoint
from scripts.track_state import workflow_shapes as ws
from scripts.track_state import registry_validate as rv


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


# A registry with three shapes injected via ``_load`` monkeypatch:
#   - default            → checkpoint_policy=run (baseline behavior)
#   - review-skip        → skip-if-declared + ac_grounding=review (legitimate waive)
#   - bad-skip           → skip-if-declared + ac_grounding=test (NO substitute)
_SKIP_SHAPES = {
    "default": {
        "nodes": ["spec-planner", "task-executor", "phase-checker"],
        "verifiers": ["ac-tracer", "test-runner"],
        "gates": ["tdd", "coverage", "checkpoint"],
        "ac_grounding": "test", "verify_policy": "checkpoint",
        "checkpoint_policy": "run", "stop_condition": "all_nodes_done",
    },
    "shapes": {
        "default": {
            "nodes": ["spec-planner", "task-executor", "phase-checker"],
            "verifiers": ["ac-tracer", "test-runner"],
            "gates": ["tdd", "coverage", "checkpoint"],
            "ac_grounding": "test", "verify_policy": "checkpoint",
            "checkpoint_policy": "run", "stop_condition": "all_nodes_done",
        },
        "review-skip": {
            "nodes": ["spec-planner", "task-executor", "phase-checker"],
            "verifiers": ["ac-tracer"], "gates": ["checkpoint"],
            "ac_grounding": "review", "verify_policy": "checkpoint",
            "checkpoint_policy": "skip-if-declared",
            "stop_condition": "all_nodes_done",
        },
        "bad-skip": {
            "nodes": ["spec-planner", "task-executor", "phase-checker"],
            "verifiers": ["ac-tracer", "test-runner"],
            "gates": ["tdd", "coverage", "checkpoint"],
            "ac_grounding": "test", "verify_policy": "checkpoint",
            "checkpoint_policy": "skip-if-declared",
            "stop_condition": "all_nodes_done",
        },
    },
}


def _install_shapes(shapes):
    """Swap ``workflow_shapes._load`` (the single source every accessor reads)
    so a test sees ``shapes``. ``_shape``/accessors call ``_load`` as a module
    global, so the patch propagates to ``checkpoint_skip_decision`` and the
    helpers-imported binding alike."""
    if hasattr(ws._load, "cache_clear"):  # original is lru_cache-wrapped
        ws._load.cache_clear()
    ws._load = lambda: shapes


def _restore_load(orig):
    ws._load = orig
    if hasattr(orig, "cache_clear"):  # evict any stale real-registry result
        orig.cache_clear()


def _capture(fn, *args, **kwargs):
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _make_state(shape="default"):
    """A one-phase track whose single task is COMPLETED with a real commit_sha,
    so the phase needs a checkpoint (all terminal, no stamp) unless waived."""
    return {
        "track_id": "cp", "type": "feature", "status": "in_progress",
        "description": "checkpoint policy test",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": _recent_iso(),
        "workflow_shape": shape,
        "phases": [{"name": "Phase 1", "status": "in_progress", "tasks": [
            {"name": "Task A", "status": "completed", "commit_sha": "abc123"},
        ]}],
    }


def _git_track_dir(state, plan_content=None):
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text(plan_content or _PLAN)
    save(d, state)
    env = {
        **__import__("os").environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "-C", d, "init", "-q"], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "commit", "-q", "-m", "init"],
                   check=True, capture_output=True, env=env)
    return d


_PLAN = "# Plan\n\n## Phase 1: Build\n- [x] Task A\n"


class CheckpointSkipDecisionTests(TestCase):
    """Layer 1: the pure verdict dispatch consults."""

    def setUp(self):
        self._orig = ws._load
        _install_shapes(_SKIP_SHAPES)

    def tearDown(self):
        _restore_load(self._orig)

    def test_run_shape_decides_run(self):
        self.assertEqual(ws.checkpoint_skip_decision("default"), "run")

    def test_skip_with_review_substitute_decides_skip(self):
        self.assertEqual(ws.checkpoint_skip_decision("review-skip"), "skip")

    def test_skip_without_substitute_decides_violation(self):
        # THE critical rule: skip-if-declared with no integrity substitute.
        self.assertEqual(ws.checkpoint_skip_decision("bad-skip"), "violation")

    def test_unknown_shape_fail_opens_to_run(self):
        # A typo/unknown shape resolves to default → run (never accidentally skip).
        self.assertEqual(ws.checkpoint_skip_decision("nonexistent"), "run")


class ValidateMergedGuardTests(TestCase):
    """Layer 2: the save-time cross-field guard (the PRIMARY catch). In-memory
    dicts — no overlay needed. Default-inheritance is honored."""

    def _base(self):
        # default includes build-runner so it is valid under the build-tier
        # cross-field guard (a test-grounded shape must run the compile tier).
        return {
            "default": {"nodes": ["spec-planner"],
                        "verifiers": ["ac-tracer", "build-runner", "test-runner"],
                        "gates": ["checkpoint"], "ac_grounding": "test"},
            "shapes": {"default": {"nodes": ["spec-planner"]}},
        }

    def test_skip_without_substitute_rejected(self):
        doc = self._base()
        doc["shapes"]["risky"] = {"checkpoint_policy": "skip-if-declared"}
        errs = rv.validate_merged_shapes(doc)
        self.assertTrue(any("'risky'" in e and "integrity substitute" in e
                            for e in errs), errs)

    def test_skip_with_explicit_review_substitute_accepted(self):
        doc = self._base()
        doc["shapes"]["ok"] = {"checkpoint_policy": "skip-if-declared",
                               "ac_grounding": "review"}
        self.assertEqual(rv.validate_merged_shapes(doc), [])

    def test_skip_with_inherited_review_substitute_accepted(self):
        # The row sets checkpoint_policy but inherits ac_grounding=review from a
        # custom default — the resolved shape has its substitute.
        doc = self._base()
        doc["default"]["ac_grounding"] = "review"
        doc["shapes"]["inh"] = {"checkpoint_policy": "skip-if-declared"}
        self.assertEqual(rv.validate_merged_shapes(doc), [])

    def test_run_shape_unaffected(self):
        doc = self._base()
        doc["shapes"]["normal"] = {"checkpoint_policy": "run",
                                   "ac_grounding": "test"}
        self.assertEqual(rv.validate_merged_shapes(doc), [])

    def test_shipped_baseline_still_validates_clean(self):
        # No shipped shape is skip-if-declared, so the new guard is a no-op there.
        d = json.loads(Path("templates/workflow/workflow-shapes.json").read_text())
        self.assertEqual(rv.validate_merged_shapes(d), [])


class PhaseNeedsCheckpointWaiveTests(TestCase):
    """Layer 3a: the chokepoint. skip+substitute → None (waived, propagates to
    all 8 consumers); run/violation → phase_index (pending)."""

    def setUp(self):
        self._orig = ws._load
        _install_shapes(_SKIP_SHAPES)
        self.d = _git_track_dir(_make_state())
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def tearDown(self):
        _restore_load(self._orig)

    def _state(self, shape):
        from scripts.track_state.core import load
        s = load(self.d)
        s["workflow_shape"] = shape
        return s

    def test_run_shape_needs_checkpoint(self):
        self.assertEqual(
            _phase_needs_checkpoint(self.d, self._state("default"), 1), 1)

    def test_skip_with_substitute_waived(self):
        # review-skip: the checkpoint is waived → None (NOT 1).
        self.assertIsNone(
            _phase_needs_checkpoint(self.d, self._state("review-skip"), 1))

    def test_skip_without_substitute_still_pending(self):
        # bad-skip does NOT waive here (returns 1) — the emit sites catch the
        # violation. This is the "never silent skip" guard: the pending
        # checkpoint surfaces so dispatch can halt on it.
        self.assertEqual(
            _phase_needs_checkpoint(self.d, self._state("bad-skip"), 1), 1)


class DispatchEmitTests(TestCase):
    """Layer 3b: the two emit rails. skip+substitute → no checkpoint emit
    (proceeds); skip+no-substitute → shape_violation error (fail-hard)."""

    def setUp(self):
        self._orig = ws._load
        _install_shapes(_SKIP_SHAPES)

    def tearDown(self):
        _restore_load(self._orig)

    # --- Rail A: cmd_dispatch_next ---

    def test_rail_a_run_shape_emits_checkpoint(self):
        d = _git_track_dir(_make_state("default"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _capture(cmd_dispatch_next, d)
        self.assertEqual(out["action"], "dispatch_phase_checker")

    def test_rail_a_skip_with_substitute_waives_checkpoint(self):
        # review-skip: the checkpoint is waived → no dispatch_phase_checker;
        # with no further work the spine finalizes (proves it proceeded PAST
        # the checkpoint rather than emitting it).
        d = _git_track_dir(_make_state("review-skip"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _capture(cmd_dispatch_next, d)
        self.assertNotEqual(out["action"], "dispatch_phase_checker")
        self.assertEqual(out["action"], "finalize")

    def test_rail_a_skip_without_substitute_fails_hard(self):
        d = _git_track_dir(_make_state("bad-skip"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _capture(cmd_dispatch_next, d)
        self.assertEqual(out["action"], "shape_violation")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["phase"], 1)
        self.assertEqual(out["workflow_shape"], "bad-skip")
        self.assertIn("integrity substitute", out["error"])

    # --- Rail B: cmd_step ---

    def test_rail_b_run_shape_emits_checkpoint(self):
        d = _git_track_dir(_make_state("default"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _capture(cmd_step, d)
        # Rail B's checkpoint entry is the verifier fan-out (dispatch_batch); the
        # synthesizer (dispatch_phase_checker) follows once verdicts land. Either
        # is a checkpoint action — the point is the gate is ACTIVE (not waived,
        # not a violation).
        self.assertIn(out["action"], ("dispatch_batch", "dispatch_phase_checker"))

    def test_rail_b_skip_with_substitute_waives_checkpoint(self):
        d = _git_track_dir(_make_state("review-skip"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _capture(cmd_step, d)
        # Waived: NEITHER checkpoint step emits.
        self.assertNotIn(out["action"],
                         ("dispatch_batch", "dispatch_phase_checker"))

    def test_rail_b_skip_without_substitute_fails_hard(self):
        d = _git_track_dir(_make_state("bad-skip"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = _capture(cmd_step, d)
        self.assertEqual(out["action"], "shape_violation")
        self.assertEqual(out["status"], "error")
        self.assertIn("integrity substitute", out["error"])


if __name__ == "__main__":
    main()
