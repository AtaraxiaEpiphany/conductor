r"""Gap #7 — F2/F3 advisory gates now run on the dispatch-finalize hot path
(WARN-only), not just the orphaned process-result path. Both paths call the
shared ``_evaluate_gates`` helper, so the two cannot drift; these tests lock
that the finalize envelope surfaces ``coverage_gate``/``tdd_gate``/``coverage_pct``
and matches the helper's own verdict.

dispatch-finalize performs real git commits, so each test builds a git-backed
track dir (the same fixture pattern as ``test_compact_output``).
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save
from scripts.track_state.dispatch import cmd_dispatch_finalize
from scripts.track_state.helpers import _extract_tags_for_task
from scripts.track_state.result import _evaluate_gates


def _out_captured(fn, *args, **kwargs):
    """Capture stdout (must be a single JSON object). Returns parsed dict."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_git_track_dir(task_name="Task A"):
    """git repo + track-state.json (Task in_progress) + plan.md."""
    d = tempfile.mkdtemp()
    for args in (["git", "init", d],
                 ["git", "-C", d, "config", "user.email", "t@t.com"],
                 ["git", "-C", d, "config", "user.name", "T"]):
        subprocess.run(args, capture_output=True, check=True)
    Path(d, "README.md").write_text("# t")
    subprocess.run(["git", "-C", d, "add", "README.md"], capture_output=True, check=True)
    subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, check=True)
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    state = {
        "track_id": "test",
        "type": "feature",
        "status": "in_progress",
        "description": "test track",
        "current_phase_index": 1,
        "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [{
            "name": "Phase 1",
            "status": "pending",
            "tasks": [{"name": task_name, "status": "in_progress"}],
        }],
    }
    save(d, state)
    return d


def _write_success_result(d, *, coverage_pct=None, commit_sha="abc1234",
                          files_changed="src/foo.py tests/test_foo.py",
                          tc_coverage=None):
    cond = Path(d, ".conductor")
    cond.mkdir(exist_ok=True)
    payload = {
        "status": "SUCCESS",
        "commit_sha": commit_sha,
        "summary": "Done",
        "phase": 1,
        "task": 1,
        "subtask": None,
        "task_name": "Task A",
        "files_changed": files_changed,
    }
    if coverage_pct is not None:
        payload["coverage_pct"] = coverage_pct
    if tc_coverage is not None:
        payload["tc_coverage"] = tc_coverage
    (cond / "result.json").write_text(json.dumps(payload))


class FinalizeCoverageGateTests(TestCase):
    """Sub-80% coverage must surface as a FAILED gate; >=80% as PASS."""

    def test_emits_coverage_gate_fail_below_threshold(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=50)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["status"], "success")
        # Verdict prefix is stable; remediation clause is appended so the
        # agent can self-correct (start with the fix, not just the symptom).
        self.assertTrue(result["coverage_gate"].startswith("FAILED (50% < 80%)"))
        self.assertIn("≥80%", result["coverage_gate"])
        self.assertEqual(result["coverage_pct"], 50)

    def test_emits_coverage_gate_pass_at_threshold(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=90)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["coverage_gate"], "PASS")
        self.assertEqual(result["coverage_pct"], 90)

    def test_no_coverage_pct_keeps_pass_omits_pct(self):
        """A result without coverage_pct can't fail the gate and omits the field
        (mirrors process-result, which only emits coverage_pct when present)."""
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=None)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["coverage_gate"], "PASS")
        self.assertNotIn("coverage_pct", result)


class FinalizeTagExemptionTests(TestCase):
    """[Docs]/[Config]/[Chore]/[Manual] skip the coverage gate even under 80%."""

    def test_docs_tag_exempt_from_coverage_gate(self):
        d = _make_git_track_dir(task_name="[Docs] Update README")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=50)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["coverage_gate"], "PASS")

    def test_config_tag_exempt_from_coverage_gate(self):
        d = _make_git_track_dir(task_name="[Config] Tune knobs")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=40)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["coverage_gate"], "PASS")


class FinalizeSharedHelperParityTests(TestCase):
    """The finalize envelope carries ``_evaluate_gates``' verdict verbatim —
    the contract that keeps the hot path and process-result from drifting."""

    def test_envelope_matches_helper_verdict(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=50)
        # Re-read the exact result the finalize path will consume.
        r = json.loads((Path(d) / ".conductor" / "result.json").read_text())
        state = load(d)
        tags = _extract_tags_for_task(state, "1", "1")
        # Mirror dispatch-finalize's normalization of the commit SHA.
        from scripts.track_state.helpers import _normalize_sha
        code_sha = _normalize_sha(r.get("commit_sha", ""))
        exp_cov, exp_tdd, exp_pct = _evaluate_gates(tags, r, code_sha, d)

        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["coverage_gate"], exp_cov)
        self.assertEqual(result["tdd_gate"], exp_tdd)
        self.assertEqual(result["coverage_pct"], exp_pct)

    def test_helper_parity_with_process_result_inputs(self):
        """Sanity: the same ``_evaluate_gates`` inputs yield identical tuples
        regardless of which code path assembled them (the drift guard)."""
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        r = {"coverage_pct": 50, "files_changed": "tests/test_x.py"}
        # process-result pulls tags from state; dispatch-finalize does the same.
        tags_pr = _extract_tags_for_task(load(d), "1", "1")
        cov_pr, tdd_pr, pct_pr = _evaluate_gates(tags_pr, r, "abc1234", d)
        # A second call with identical args is deterministic and equal.
        cov2, tdd2, pct2 = _evaluate_gates(tags_pr, r, "abc1234", d)
        self.assertEqual((cov_pr, tdd_pr, pct_pr), (cov2, tdd2, pct2))
        self.assertTrue(cov_pr.startswith("FAILED (50% < 80%)"))
        self.assertIn("≥80%", cov_pr)  # remediation appended


class FinalizeACIntegrityGateTests(TestCase):
    """ac_integrity_gate (track-level, WARN-only) surfaces in the dispatch-finalize
    envelope and survives --compact — proving it's in COMPACT_FIELDS. Computed
    after completion, never blocks (mirrors coverage_gate/tdd_gate)."""

    def test_na_gate_when_no_spec(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _write_success_result(d, coverage_pct=90)
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["ac_integrity_gate"], "N/A")

    def test_failed_gate_with_bad_spec_survives_compact(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # AC-1 has no TC and no plan trace → gate FAILED.
        Path(d, "spec.md").write_text("# S\n## Acceptance Criteria\n- AC-1: x\n")
        _write_success_result(d, coverage_pct=90)
        result = _out_captured(cmd_dispatch_finalize, d)
        # Field present under default --compact ⇒ it's in the COMPACT_FIELDS
        # allowlist (else emit() would strip it).
        self.assertIn("ac_integrity_gate", result)
        self.assertTrue(result["ac_integrity_gate"].startswith("FAILED"))


class TCConsistencyGateUnitTests(TestCase):
    """``_tc_consistency_gate`` verdict logic — declared (plan comment) vs
    claimed (``tc_coverage``). No git/state needed: the gate reads plan.md by
    index and the TC IDs out of the result dict."""

    _PLAN = ("# Plan\n\n## Phase 1: Build\n"
             "- [ ] Task A <!-- AC-2, TC-2.1, TC-2.2 -->\n")

    def _gate(self, plan_text, result, tags=None, test_files=None):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        if plan_text is not None:
            Path(d, "plan.md").write_text(plan_text)
        for relpath, content in (test_files or []):
            p = Path(d, relpath)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        from scripts.track_state.result import _tc_consistency_gate
        return _tc_consistency_gate(d, result, tags)

    def test_pass_when_claimed_superset_of_declared(self):
        r = {"phase": 1, "task": 1, "tc_coverage": "TC-2.1 TC-2.2"}
        self.assertEqual(self._gate(self._PLAN, r), "PASS")

    def test_wrong_ac_when_no_overlap(self):
        r = {"phase": 1, "task": 1, "tc_coverage": "TC-1.1 TC-1.3"}
        g = self._gate(self._PLAN, r)
        self.assertTrue(g.startswith("WRONG_AC"))
        self.assertIn("TC-1.1", g)
        self.assertIn("none of declared", g)

    def test_partial_names_the_missing_declared_tc(self):
        r = {"phase": 1, "task": 1, "tc_coverage": "TC-2.1"}
        g = self._gate(self._PLAN, r)
        self.assertTrue(g.startswith("PARTIAL"))
        self.assertIn("TC-2.2", g)  # the missing declared TC is named in the fix

    def test_unknown_when_no_tc_coverage(self):
        g = self._gate(self._PLAN, {"phase": 1, "task": 1})
        self.assertTrue(g.startswith("UNKNOWN"))

    def test_na_when_task_declares_no_refs(self):
        plan = "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n"
        r = {"phase": 1, "task": 1, "tc_coverage": "TC-9.9"}
        self.assertEqual(self._gate(plan, r), "N/A")

    def test_na_when_no_plan(self):
        r = {"phase": 1, "task": 1, "tc_coverage": "TC-2.1"}
        self.assertEqual(self._gate(None, r), "N/A")

    # --- Third link of the chain: claimed (tc_coverage) ↔ grounded (real tests).
    # Grounding is a refinement of PASS only, so every case below claims a
    # superset of the declared TCs (consistency = PASS) and varies the measured
    # test set. Pass tags=[] to enable grounding (tags=None skips it, back-compat).

    def test_pass_grounded_when_claimed_has_real_tests(self):
        r = {"phase": 1, "task": 1, "tc_coverage": "TC-2.1 TC-2.2"}
        tests = [("tests/test_a.py", "def test_TC_2_1_a():\n    pass\n"
                  "def test_TC_2_2_a():\n    pass\n")]
        self.assertEqual(self._gate(self._PLAN, r, tags=[], test_files=tests),
                         "PASS (grounded)")

    def test_pass_partial_grounding_names_ungrounded_tc(self):
        r = {"phase": 1, "task": 1, "tc_coverage": "TC-2.1 TC-2.2"}
        tests = [("tests/test_a.py", "def test_TC_2_1_a():\n    pass\n")]
        g = self._gate(self._PLAN, r, tags=[], test_files=tests)
        self.assertTrue(g.startswith("PASS (PARTIAL grounding"))
        self.assertIn("TC-2.2", g)

    def test_pass_unground_when_claimed_disjoint_from_real_tests(self):
        # measured non-empty (a stray TC-9.9) but disjoint from claimed → UNGROUND.
        r = {"phase": 1, "task": 1, "tc_coverage": "TC-2.1 TC-2.2"}
        tests = [("tests/test_other.py", "def test_TC_9_9_other():\n    pass\n")]
        g = self._gate(self._PLAN, r, tags=[], test_files=tests)
        self.assertTrue(g.startswith("PASS (UNGROUND"))
        self.assertIn("TC-2.1", g)

    def test_silent_pass_when_convention_not_adopted(self):
        # No test_TC_* functions → measured empty → plain PASS (rate carries it).
        r = {"phase": 1, "task": 1, "tc_coverage": "TC-2.1 TC-2.2"}
        self.assertEqual(self._gate(self._PLAN, r, tags=[]), "PASS")

    def test_tag_exempt_skips_grounding(self):
        # [Config] is test-exempt → grounding skipped even with disjoint tests.
        r = {"phase": 1, "task": 1, "tc_coverage": "TC-2.1 TC-2.2"}
        tests = [("tests/test_other.py", "def test_TC_9_9_other():\n    pass\n")]
        self.assertEqual(self._gate(self._PLAN, r, tags=["Config"],
                                    test_files=tests), "PASS")


class FinalizeTCConsistencyGateTests(TestCase):
    """tc_consistency_gate surfaces in the dispatch-finalize envelope and
    survives ``--compact`` (so it's in the COMPACT_FIELDS allowlist), computed
    after completion — never blocks (mirrors coverage_gate/tdd_gate)."""

    def test_wrong_ac_surfaces_and_survives_compact(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # Task declares TC-2.1/TC-2.2; the agent claims the wrong AC's TCs.
        Path(d, "plan.md").write_text(
            "# Plan\n\n## Phase 1: Build\n- [ ] Task A <!-- AC-2, TC-2.1, TC-2.2 -->\n")
        _write_success_result(d, coverage_pct=90, tc_coverage="TC-1.1 TC-1.3")
        result = _out_captured(cmd_dispatch_finalize, d)
        # Field present under default --compact ⇒ it's in COMPACT_FIELDS
        # (else emit() would strip it).
        self.assertIn("tc_consistency_gate", result)
        self.assertTrue(result["tc_consistency_gate"].startswith("WRONG_AC"))

    def test_pass_when_claimed_matches_declared(self):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "plan.md").write_text(
            "# Plan\n\n## Phase 1: Build\n- [ ] Task A <!-- AC-2, TC-2.1, TC-2.2 -->\n")
        _write_success_result(d, coverage_pct=90, tc_coverage="TC-2.1 TC-2.2")
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(result["tc_consistency_gate"], "PASS")

    def test_grounded_annotation_survives_compact(self):
        # Grounding rides the existing tc_consistency_gate field (folding adds no
        # new COMPACT_FIELDS entry). When real tests ground the claimed TCs the
        # verdict refines to "PASS (grounded)" and must survive --compact.
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "plan.md").write_text(
            "# Plan\n\n## Phase 1: Build\n- [ ] Task A <!-- AC-2, TC-2.1, TC-2.2 -->\n")
        test_dir = Path(d, "tests")
        test_dir.mkdir()
        (test_dir / "test_a.py").write_text(
            "def test_TC_2_1_a():\n    pass\n"
            "def test_TC_2_2_a():\n    pass\n")
        _write_success_result(d, coverage_pct=90, tc_coverage="TC-2.1 TC-2.2")
        result = _out_captured(cmd_dispatch_finalize, d)
        self.assertIn("tc_consistency_gate", result)  # survived --compact
        self.assertEqual(result["tc_consistency_gate"], "PASS (grounded)")


class ShapeGateCompositionTests(TestCase):
    """Stage 2b: a gate fires iff ``(gate in gates_for(shape)) and (not task_exempt)``.
    The composition lives at the advisory single-source (:func:`_evaluate_gates`),
    so this is the chokepoint both finalize paths flow through. A migration-shape
    track drops tdd/coverage at the track level → a <80% non-exempt task shows
    PASS/PASS; a default-shape track fires coverage exactly as today."""

    def setUp(self):
        from scripts.track_state import workflow_shapes as ws
        self._ws = ws
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")
        self._cwd = os.getcwd()

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        os.chdir(self._cwd)
        self._ws._load.cache_clear()

    def _mk_track(self, shape):
        d = _make_git_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        if shape:  # set the track's workflow_shape (absent => default)
            state = load(d)
            state["workflow_shape"] = shape
            save(d, state)
        # Project overlay registering a migration shape that drops tdd/coverage.
        proj = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, proj, ignore_errors=True)
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        Path(proj, "conductor", "workflow", "workflow-shapes.json").write_text(
            json.dumps({"shapes": {"migration": {
                "nodes": ["spec-planner", "task-executor", "phase-checker"],
                "verifiers": ["ac-tracer", "test-runner"],
                "gates": ["checkpoint"],
                "verify_policy": "checkpoint",
                "stop_condition": "all_nodes_done"}}}), encoding="utf-8")
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._ws._load.cache_clear()
        return d

    def test_default_shape_fires_coverage_gate(self):
        # No workflow_shape => default => coverage gate ON (default-identical):
        # a <80% non-exempt task is FAILED.
        d = self._mk_track(None)
        r = {"coverage_pct": 40, "files_changed": "src/foo.py"}
        cov, _tdd, _pct = _evaluate_gates([], r, "abc1234", d)
        self.assertTrue(cov.startswith("FAILED (40% < 80%)"))

    def test_migration_shape_drops_coverage_and_tdd(self):
        # migration shape => gates=[checkpoint] => coverage+tdd OFF at the track
        # level: the SAME <80% non-exempt task is PASS/PASS.
        d = self._mk_track("migration")
        r = {"coverage_pct": 40, "files_changed": "src/foo.py"}
        cov, tdd, _pct = _evaluate_gates([], r, "abc1234", d)
        self.assertEqual(cov, "PASS")
        self.assertEqual(tdd, "PASS")


if __name__ == "__main__":
    main()
