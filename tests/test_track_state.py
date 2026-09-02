"""Tests for track_state: recover/resume, auto-fix, backup, init validation."""
import json
import shutil
import tempfile
import io
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

from scripts.track_state.core import load, save
from scripts.track_state.validate import (
    _fix_stale_in_progress, _fix_terminal_current_indices,
    _auto_fix, cmd_validate, ensure_healthy,
)
from scripts.track_state.dispatch import cmd_recover, cmd_dispatch_next
from scripts.track_state.quality import cmd_init_from_plan, cmd_set_mode, _validate_plan_structure, _init_core
from scripts.track_state.plan_parse import parse_plan, to_plan_structure, collect_ac_refs
from scripts.track_state.misc import cmd_shas, cmd_derive_name


def _out_captured(fn, *args, **kwargs):
    """Capture stdout from a function call. Returns (result_json, stderr_text)."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue()), sys.stderr.getvalue()
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _recent_iso():
    """ISO timestamp 1 hour ago — a 'recent' updated_at that won't trigger stale-fix."""
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_state(**overrides):
    """Build a minimal valid state dict. Default updated_at is 1 hour ago (recent)."""
    state = {
        "track_id": "test",
        "type": "feature",
        "status": "in_progress",
        "description": "test track",
        "current_phase_index": 1,
        "current_task_index": 1,
        "updated_at": _recent_iso(),
        "phases": [
            {
                "name": "Phase 1",
                "status": "pending",
                "tasks": [
                    {"name": "Task A", "status": "pending"},
                    {"name": "Task B", "status": "pending"},
                ],
            }
        ],
    }
    state.update(overrides)
    return state


def _make_track_dir(state=None, plan_content=None):
    """Create a temp track dir with optional state and plan.md."""
    d = tempfile.mkdtemp()
    if plan_content:
        Path(d, "plan.md").write_text(plan_content)
    else:
        Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n- [ ] Task B\n")
    if state:
        save(d, state)
    return d


class TestBackupFallback(TestCase):
    """core.py: backup-on-save and fallback-load."""

    def test_backup_created_on_first_save(self):
        d = tempfile.mkdtemp()
        save(d, _make_state())
        self.assertTrue((Path(d) / "track-state.json.bak").exists())
        shutil.rmtree(d)

    def test_fallback_on_corruption(self):
        d = _make_track_dir(_make_state())
        state_file = Path(d) / "track-state.json"
        # Corrupt main file
        state_file.write_text("{corrupted!!!")
        # Should fall back to backup
        recovered = load(d)
        self.assertEqual(recovered["track_id"], "test")
        # Main file should be restored
        with open(state_file) as f:
            self.assertEqual(json.load(f)["track_id"], "test")
        shutil.rmtree(d)

    def test_double_corruption_raises(self):
        d = _make_track_dir(_make_state())
        state_file = Path(d) / "track-state.json"
        bak_file = Path(d) / "track-state.json.bak"
        state_file.write_text("{bad")
        bak_file.write_text("{also bad")
        with self.assertRaises(json.JSONDecodeError):
            load(d)
        shutil.rmtree(d)


class TestFixStaleInProgress(TestCase):
    """validate.py: _fix_stale_in_progress."""

    def test_recent_state_no_fix(self):
        state = _make_state()
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        fixes = _fix_stale_in_progress(state, threshold_hours=24)
        self.assertEqual(fixes, [])

    def test_stale_task_reset_to_pending(self):
        state = _make_state(updated_at="2020-01-01T00:00:00+00:00")
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        fixes = _fix_stale_in_progress(state, threshold_hours=24)
        self.assertEqual(len(fixes), 1)
        self.assertEqual(state["phases"][0]["tasks"][0]["status"], "pending")

    def test_stale_subtask_reset(self):
        state = _make_state(updated_at="2020-01-01T00:00:00+00:00")
        state["phases"][0]["tasks"][0]["subtasks"] = [
            {"name": "Sub A", "status": "in_progress"}
        ]
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        fixes = _fix_stale_in_progress(state, threshold_hours=24)
        self.assertEqual(len(fixes), 2)
        self.assertEqual(state["phases"][0]["tasks"][0]["subtasks"][0]["status"], "pending")


class TestFixTerminalCurrentIndices(TestCase):
    """validate.py: _fix_terminal_current_indices."""

    def test_active_task_no_fix(self):
        state = _make_state(current_phase_index=1, current_task_index=1)
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        fixes = _fix_terminal_current_indices(state)
        self.assertEqual(fixes, [])

    def test_stale_subtask_under_active_parent_advances(self):
        """in_progress parent + current_subtask_index at a COMPLETED subtask
        must advance to the next pending subtask. Previously the active-parent
        early-return skipped the subtask staleness check, so cmd_recover was
        fed a completed subtask target after a mid-parent crash."""
        state = _make_state(
            current_phase_index=1, current_task_index=1, current_subtask_index=1,
        )
        parent = state["phases"][0]["tasks"][0]
        parent["status"] = "in_progress"
        parent["subtasks"] = [
            {"name": "S1", "status": "completed", "commit_sha": "abc1234"},
            {"name": "S2", "status": "pending"},
            {"name": "S3", "status": "pending"},
        ]
        fixes = _fix_terminal_current_indices(state)
        self.assertEqual(len(fixes), 1)
        self.assertIn("P1.T1.S2", fixes[0])
        self.assertIn("advanced to active subtask", fixes[0])
        # Parent stays in_progress; only the subtask index advances.
        self.assertEqual(state["current_phase_index"], 1)
        self.assertEqual(state["current_task_index"], 1)
        self.assertEqual(state["current_subtask_index"], 2)
        self.assertEqual(parent["status"], "in_progress")

    def test_active_subtask_under_active_parent_no_fix(self):
        """An active subtask under an active parent is a valid target — no fix."""
        state = _make_state(
            current_phase_index=1, current_task_index=1, current_subtask_index=2,
        )
        parent = state["phases"][0]["tasks"][0]
        parent["status"] = "in_progress"
        parent["subtasks"] = [
            {"name": "S1", "status": "completed", "commit_sha": "abc1234"},
            {"name": "S2", "status": "in_progress"},
        ]
        fixes = _fix_terminal_current_indices(state)
        self.assertEqual(fixes, [])
        self.assertEqual(state["current_subtask_index"], 2)

    def test_terminal_advances_to_next_pending(self):
        state = _make_state(current_phase_index=1, current_task_index=1)
        state["phases"][0]["tasks"][0]["status"] = "completed"
        state["phases"][0]["tasks"][0]["commit_sha"] = "abc1234"
        fixes = _fix_terminal_current_indices(state)
        self.assertEqual(len(fixes), 1)
        self.assertEqual(state["current_phase_index"], 1)
        self.assertEqual(state["current_task_index"], 2)

    def test_all_terminal_clears_indices(self):
        state = _make_state(current_phase_index=1, current_task_index=1)
        for t in state["phases"][0]["tasks"]:
            t["status"] = "completed"
            t["commit_sha"] = "abc1234"
        fixes = _fix_terminal_current_indices(state)
        self.assertTrue(len(fixes) > 0)
        self.assertEqual(state["current_phase_index"], 0)
        self.assertEqual(state["current_task_index"], 0)

    def test_zero_indices_migrates_to_first_pending(self):
        """Legacy 0-based cpi=0/cti=0 with pending tasks → scan forward to P1.T1."""
        state = _make_state(current_phase_index=0, current_task_index=0)
        fixes = _fix_terminal_current_indices(state)
        self.assertEqual(len(fixes), 1)
        self.assertIn("migrated to 1-based", fixes[0])
        self.assertEqual(state["current_phase_index"], 1)
        self.assertEqual(state["current_task_index"], 1)

    def test_zero_indices_no_pending_stays_zero(self):
        """cpi=0/cti=0 with all-terminal tasks → no migration (true sentinel)."""
        state = _make_state(current_phase_index=0, current_task_index=0)
        for t in state["phases"][0]["tasks"]:
            t["status"] = "completed"
            t["commit_sha"] = "abc1234"
        fixes = _fix_terminal_current_indices(state)
        self.assertEqual(fixes, [])
        self.assertEqual(state["current_phase_index"], 0)
        self.assertEqual(state["current_task_index"], 0)


class TestAutoFix(TestCase):
    """validate.py: _auto_fix comprehensive."""

    def test_clamps_out_of_range_indices(self):
        state = _make_state(current_phase_index=99, current_task_index=99)
        fixes = _auto_fix(state)
        self.assertTrue(any("clamped" in f for f in fixes))
        self.assertEqual(state["current_phase_index"], 1)
        self.assertEqual(state["current_task_index"], 2)

    def test_stale_and_terminal_combined(self):
        state = _make_state(
            updated_at="2020-01-01T00:00:00+00:00",
            current_phase_index=0, current_task_index=0,
        )
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        fixes = _auto_fix(state)
        # Should reset stale + potentially adjust indices
        self.assertTrue(len(fixes) >= 1)
        self.assertEqual(state["phases"][0]["tasks"][0]["status"], "pending")


class TestCmdValidate(TestCase):
    """validate.py: cmd_validate always-run auto-fix."""

    def test_dry_run_reports_fixable(self):
        d = _make_track_dir(_make_state(
            updated_at="2020-01-01T00:00:00+00:00",
            current_phase_index=1, current_task_index=1,
        ))
        state = load(d)
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        save(d, state)

        result, _ = _out_captured(cmd_validate, d)
        # Dry-run: reports fixes but does NOT persist
        self.assertTrue(result.get("fixable"))
        self.assertTrue(len(result.get("fixes", [])) > 0)
        self.assertNotIn("fixed", result)  # no "fixed" key in dry-run
        # State should NOT be changed (dry-run)
        unchanged = load(d)
        self.assertEqual(unchanged["phases"][0]["tasks"][0]["status"], "in_progress")
        shutil.rmtree(d)

    def test_fix_flag_persists(self):
        d = _make_track_dir(_make_state(
            updated_at="2020-01-01T00:00:00+00:00",
            current_phase_index=1, current_task_index=1,
        ))
        state = load(d)
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        save(d, state)

        result, _ = _out_captured(cmd_validate, d, fix=True)
        self.assertTrue(result.get("fixed"))
        fixed = load(d)
        self.assertEqual(fixed["phases"][0]["tasks"][0]["status"], "pending")
        shutil.rmtree(d)


class TestEnsureHealthy(TestCase):
    """validate.py: ensure_healthy."""

    def test_returns_state_and_fixes(self):
        d = _make_track_dir(_make_state())
        state, fixes, errors = ensure_healthy(d)
        self.assertIsNotNone(state)
        self.assertEqual(errors, [])
        shutil.rmtree(d)

    def test_fixes_stale_and_saves(self):
        d = _make_track_dir(_make_state(
            updated_at="2020-01-01T00:00:00+00:00",
            current_phase_index=1, current_task_index=1,
        ))
        state = load(d)
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        save(d, state)

        state, fixes, errors = ensure_healthy(d)
        self.assertTrue(len(fixes) > 0)
        reloaded = load(d)
        self.assertEqual(reloaded["phases"][0]["tasks"][0]["status"], "pending")
        shutil.rmtree(d)

    def test_returns_none_on_missing_file(self):
        state, fixes, errors = ensure_healthy("/nonexistent/path")
        self.assertIsNone(state)
        self.assertTrue(len(errors) > 0)


class TestCmdRecover(TestCase):
    """dispatch.py: cmd_recover with auto-fix and terminal advancement."""

    def test_healthy_state_returns_current(self):
        d = _make_track_dir(_make_state(
            current_phase_index=1, current_task_index=1,
        ))
        state = load(d)
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        save(d, state)

        result, _ = _out_captured(cmd_recover, d)
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["name"], "Task A")
        shutil.rmtree(d)

    def test_terminal_indices_auto_advance(self):
        d = _make_track_dir(_make_state(
            current_phase_index=1, current_task_index=1,
        ))
        state = load(d)
        state["phases"][0]["tasks"][0]["status"] = "completed"
        state["phases"][0]["tasks"][0]["commit_sha"] = "abc1234"
        save(d, state)

        result, _ = _out_captured(cmd_recover, d)
        self.assertEqual(result["name"], "Task B")
        self.assertEqual(result["phase"], 1)
        self.assertEqual(result["task"], 2)
        self.assertTrue(len(result.get("fixes_applied", [])) > 0)
        shutil.rmtree(d)

    def test_no_active_task_when_all_done(self):
        d = _make_track_dir(_make_state(
            current_phase_index=1, current_task_index=1,
        ))
        state = load(d)
        for t in state["phases"][0]["tasks"]:
            t["status"] = "completed"
            t["commit_sha"] = "abc1234"
        save(d, state)

        result, _ = _out_captured(cmd_recover, d)
        self.assertEqual(result["status"], "no_active_task")
        shutil.rmtree(d)

    def test_fixes_applied_in_output(self):
        d = _make_track_dir(_make_state(
            updated_at="2020-01-01T00:00:00+00:00",
            current_phase_index=1, current_task_index=1,
        ))
        state = load(d)
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        save(d, state)

        result, stderr = _out_captured(cmd_recover, d)
        self.assertIn("fixes_applied", result)
        self.assertTrue(len(result["fixes_applied"]) > 0)
        self.assertIn("auto-fixed", stderr)
        shutil.rmtree(d)


class TestDispatchNextAutoFix(TestCase):
    """dispatch.py: cmd_dispatch_next auto-fixes before dispatch."""

    def test_auto_fixes_before_dispatch(self):
        d = _make_track_dir(_make_state(
            updated_at="2020-01-01T00:00:00+00:00",
            current_phase_index=1, current_task_index=1,
        ))
        state = load(d)
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        save(d, state)

        result, stderr = _out_captured(cmd_dispatch_next, d)
        self.assertIn("auto-fixed", stderr)
        self.assertEqual(result.get("action"), "dispatch_executor")
        shutil.rmtree(d)


class TestInitValidation(TestCase):
    """quality.py: _validate_plan_structure and plan.md cross-check."""

    def test_valid_structure(self):
        plan = {"phases": [{"name": "P1", "tasks": [{"name": "T1"}]}]}
        errors = _validate_plan_structure(plan)
        self.assertEqual(errors, [])

    def test_empty_phases(self):
        errors = _validate_plan_structure({"phases": []})
        self.assertEqual(len(errors), 1)
        self.assertIn("at least 1 phase", errors[0])

    def test_missing_phase_name(self):
        errors = _validate_plan_structure({"phases": [{"tasks": [{"name": "T1"}]}]})
        self.assertTrue(any("missing name" in e for e in errors))

    def test_empty_tasks(self):
        errors = _validate_plan_structure({"phases": [{"name": "P1", "tasks": []}]})
        self.assertTrue(any("at least 1 task" in e for e in errors))

    def test_missing_task_name(self):
        errors = _validate_plan_structure({"phases": [{"name": "P1", "tasks": [{}]}]})
        self.assertTrue(any("missing name" in e for e in errors))

    def test_missing_subtask_name(self):
        errors = _validate_plan_structure(
            {"phases": [{"name": "P1", "tasks": [{"name": "T1", "subtasks": [{"name": ""}]}]}]})
        self.assertTrue(any("Subtask" in e for e in errors))

    def test_init_rejects_bad_structure(self):
        d = tempfile.mkdtemp()
        result = _init_core(d, {"phases": []}, 't1_20260626', 'feature', 'desc')
        self.assertFalse(result["ok"])
        self.assertTrue(len(result["errors"]) > 0)
        shutil.rmtree(d)

    def test_init_warns_on_plan_mismatch(self):
        d = tempfile.mkdtemp()
        Path(d, "plan.md").write_text(
            "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n- [ ] Task B\n- [ ] Task C\n")
        structure = {"phases": [
            {"name": "Build", "tasks": [{"name": "Task A"}, {"name": "Task B"}]},
        ]}
        result = _init_core(d, structure, 't1_20260626', 'feature', 'desc')
        self.assertTrue(result["ok"])
        self.assertIn("warnings", result)
        self.assertTrue(any("3 tasks" in w for w in result["warnings"]))
        shutil.rmtree(d)

    def test_init_no_warnings_when_matching(self):
        d = tempfile.mkdtemp()
        Path(d, "plan.md").write_text(
            "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n- [ ] Task B\n")
        structure = {"phases": [
            {"name": "Build", "tasks": [{"name": "Task A"}, {"name": "Task B"}]},
        ]}
        result = _init_core(d, structure, 't1_20260626', 'feature', 'desc')
        self.assertTrue(result["ok"])
        self.assertNotIn("warnings", result)
        shutil.rmtree(d)

    def test_init_rejects_undated_id(self):
        # shortname_YYYYMMDD guard: a bare id fails BEFORE mkdir (track names
        # are unrecoverable post-commit) with a derive-name hint.
        d = tempfile.mkdtemp()
        sub = str(Path(d, "auth_gateway"))
        good = {"phases": [{"name": "P1", "tasks": [{"name": "T1"}]}]}
        result = _init_core(sub, good, "auth_gateway", "feature", "desc")
        self.assertFalse(result["ok"])
        self.assertTrue(any("shortname_YYYYMMDD" in e for e in result["errors"]))
        self.assertFalse(Path(sub).exists(), "no directory should be created on a bad id")
        shutil.rmtree(d)

    def test_init_accepts_dated_id(self):
        d = tempfile.mkdtemp()
        good = {"phases": [{"name": "P1", "tasks": [{"name": "T1"}]}]}
        result = _init_core(d, good, "auth_gateway_20260626", "feature", "desc")
        self.assertTrue(result["ok"])
        shutil.rmtree(d)


class TestDeriveName(TestCase):
    """cmd_derive_name: deterministic shortname_YYYYMMDD resolution (stateless)."""

    _fixed_now = datetime(2026, 6, 26, 12, 0, 0)

    @patch("scripts.track_state.misc.datetime")
    def _derive(self, raw, mock_dt):
        mock_dt.now.return_value = self._fixed_now
        return _out_captured(cmd_derive_name, raw)[0]

    def test_lowercases_and_underscores(self):
        r = self._derive("Auth-Gateway")
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_id"], "auth_gateway_20260626")
        self.assertEqual(r["track_dir"], "conductor/tracks/auth_gateway_20260626")
        self.assertEqual(r["shortname"], "auth_gateway")
        self.assertEqual(r["date"], "20260626")

    def test_collapses_repeats_and_trims(self):
        r = self._derive("  Auth__Gateway 2!! ")
        self.assertEqual(r["track_id"], "auth_gateway_2_20260626")

    def test_strips_existing_date(self):
        # A pre-existing date is re-stamped to today, never doubled.
        r = self._derive("auth_gateway_20250101")
        self.assertEqual(r["track_id"], "auth_gateway_20260626")

    def test_same_day_idempotent(self):
        self.assertEqual(self._derive("auth-gateway")["track_id"],
                         self._derive("auth_gateway")["track_id"])

    def test_empty_falls_back_to_track(self):
        r = self._derive("---")
        self.assertEqual(r["track_id"], "track_20260626")
        self.assertEqual(r["shortname"], "track")


class TestLegacy0BasedMigration(TestCase):
    """validate.py: migration of old 0-based stored indices to 1-based."""

    def _make_old_state(self, **overrides):
        """Build a state dict mimicking old 0-based storage."""
        state = {
            "track_id": "legacy",
            "type": "feature",
            "status": "in_progress",
            "description": "old 0-based track",
            "current_phase_index": 0,
            "current_task_index": 0,
            "updated_at": _recent_iso(),
            "phases": [
                {
                    "name": "Phase 1",
                    "status": "pending",
                    "tasks": [
                        {"name": "Task A", "status": "pending"},
                        {"name": "Task B", "status": "pending"},
                    ],
                },
                {
                    "name": "Phase 2",
                    "status": "pending",
                    "tasks": [
                        {"name": "Task C", "status": "pending"},
                    ],
                },
            ],
        }
        state.update(overrides)
        return state

    def test_validate_warns_on_legacy_zero_indices(self):
        """Dry-run validate warns about legacy 0-based data with pending tasks."""
        d = _make_track_dir(self._make_old_state())
        result, _ = _out_captured(cmd_validate, d)
        self.assertTrue(result["valid"])
        self.assertTrue(any("legacy 0-based" in w for w in result["warnings"]))
        self.assertTrue(result["fixable"])
        self.assertTrue(any("migrated to 1-based" in f for f in result["fixes"]))
        shutil.rmtree(d)

    def test_validate_fix_migrates_zero_to_one(self):
        """validate --fix migrates cpi=0/cti=0 to first pending task."""
        d = _make_track_dir(self._make_old_state())
        result, _ = _out_captured(cmd_validate, d, fix=True)
        self.assertTrue(result["fixed"])
        fixed = load(d)
        self.assertEqual(fixed["current_phase_index"], 1)
        self.assertEqual(fixed["current_task_index"], 1)
        shutil.rmtree(d)

    def test_validate_no_false_positive_on_sentinel(self):
        """cpi=0/cti=0 with all tasks terminal does NOT trigger legacy warning."""
        state = self._make_old_state()
        for phase in state["phases"]:
            phase["status"] = "completed"
            for t in phase["tasks"]:
                t["status"] = "completed"
                t["commit_sha"] = "abc1234"
        d = _make_track_dir(state)
        result, _ = _out_captured(cmd_validate, d)
        self.assertFalse(any("legacy 0-based" in w for w in result["warnings"]))
        shutil.rmtree(d)

    def test_off_by_one_auto_corrects_via_terminal_scan(self):
        """Old cpi=1 pointing to completed phase gets advanced to next pending."""
        state = self._make_old_state(current_phase_index=1, current_task_index=1)
        state["phases"][0]["tasks"][0]["status"] = "completed"
        state["phases"][0]["tasks"][0]["commit_sha"] = "abc1234"
        state["phases"][0]["tasks"][1]["status"] = "completed"
        state["phases"][0]["tasks"][1]["commit_sha"] = "def5678"
        d = _make_track_dir(state)
        result, _ = _out_captured(cmd_validate, d, fix=True)
        fixed = load(d)
        # Should advance past the all-terminal phase to Phase 2, Task C
        self.assertEqual(fixed["current_phase_index"], 2)
        self.assertEqual(fixed["current_task_index"], 1)
        shutil.rmtree(d)

    def test_ensure_healthy_migrates_legacy(self):
        """ensure_healthy auto-migrates legacy 0-based indices."""
        d = _make_track_dir(self._make_old_state())
        state, fixes, errors = ensure_healthy(d)
        self.assertTrue(any("migrated to 1-based" in f for f in fixes))
        self.assertEqual(errors, [])
        self.assertEqual(state["current_phase_index"], 1)
        self.assertEqual(state["current_task_index"], 1)
        shutil.rmtree(d)

    def test_recover_migrates_legacy_and_dispatches(self):
        """cmd_recover auto-fixes legacy 0-based data and returns the first task."""
        d = _make_track_dir(self._make_old_state())
        result, stderr = _out_captured(cmd_recover, d)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["name"], "Task A")
        self.assertEqual(result["phase"], 1)
        self.assertEqual(result["task"], 1)
        self.assertTrue(any("migrated to 1-based" in f
                           for f in result.get("fixes_applied", [])))
        shutil.rmtree(d)


class TestDispatchFinalizeShaWriteback(TestCase):
    """dispatch.py: cmd_dispatch_finalize writes conductor SHA when code_sha is empty."""

    def _make_git_track_dir(self):
        """Create a temp dir with git repo, track-state.json, and plan.md."""
        import subprocess
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        subprocess.run(["git", "init", d], capture_output=True, check=True)
        subprocess.run(["git", "-C", d, "config", "user.email", "test@test.com"],
                        capture_output=True, check=True)
        subprocess.run(["git", "-C", d, "config", "user.name", "Test"],
                        capture_output=True, check=True)
        # Initial commit so HEAD exists
        Path(d, "README.md").write_text("# test")
        subprocess.run(["git", "-C", d, "add", "README.md"], capture_output=True, check=True)
        subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, check=True)

        plan_text = "# Plan\n\n## Phase 1: Build\n- [ ] Task A\n- [ ] Task B\n"
        Path(d, "plan.md").write_text(plan_text)

        state = _make_state()
        state["phases"][0]["tasks"][0]["status"] = "in_progress"
        save(d, state)
        return d

    def test_sha_writeback_when_code_sha_empty(self):
        """When subagent provides no commit_sha, conductor commit SHA is stored."""
        from scripts.track_state.dispatch import cmd_dispatch_finalize

        d = self._make_git_track_dir()
        # Create result.json with SUCCESS but empty commit_sha
        cond_dir = Path(d, ".conductor")
        cond_dir.mkdir(exist_ok=True)
        result = {
            "status": "SUCCESS",
            "commit_sha": "",
            "summary": "Done",
            "phase": 1,
            "task": 1,
            "subtask": None,
            "task_name": "Task A",
        }
        (cond_dir / "result.json").write_text(json.dumps(result))

        out, _ = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(out.get("status"), "success")
        self.assertTrue(out.get("committed"))

        # Verify track-state.json has a non-empty commit_sha
        state = load(d)
        sha = state["phases"][0]["tasks"][0].get("commit_sha", "")
        self.assertTrue(len(sha) == 7, f"Expected 7-char SHA, got: '{sha}'")

        # Verify plan.md has the SHA annotation
        plan = Path(d, "plan.md").read_text()
        self.assertIn(f"[{sha}]", plan)

    def test_code_sha_preserved_when_provided(self):
        """When subagent provides a commit_sha, it is preserved in track-state.json."""
        from scripts.track_state.dispatch import cmd_dispatch_finalize

        d = self._make_git_track_dir()
        # Create result.json with a code commit_sha
        cond_dir = Path(d, ".conductor")
        cond_dir.mkdir(exist_ok=True)
        result = {
            "status": "SUCCESS",
            "commit_sha": "abc1234",
            "summary": "Done",
            "phase": 1,
            "task": 1,
            "subtask": None,
            "task_name": "Task A",
        }
        (cond_dir / "result.json").write_text(json.dumps(result))

        out, _ = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(out.get("status"), "success")

        # code_sha should be preserved (not replaced by conductor commit SHA)
        state = load(d)
        sha = state["phases"][0]["tasks"][0].get("commit_sha", "")
        self.assertEqual(sha, "abc1234")

    def test_synthesized_result_captures_head_sha(self):
        """When result.json is missing, synthesized result captures HEAD SHA."""
        from scripts.track_state.dispatch import cmd_dispatch_finalize

        d = self._make_git_track_dir()
        # Simulate a code commit by the subagent
        Path(d, "code.ts").write_text("// impl")
        import subprocess
        subprocess.run(["git", "-C", d, "add", "code.ts"], capture_output=True, check=True)
        subprocess.run(["git", "-C", d, "commit", "-m", "feat: implement task"],
                        capture_output=True, check=True)
        head_sha = subprocess.run(
            ["git", "-C", d, "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, check=True
        ).stdout.strip()

        # NO result.json — dispatch-finalize must synthesize from state
        out, _ = _out_captured(cmd_dispatch_finalize, d)
        self.assertEqual(out.get("status"), "success")

        # The synthesized result should have captured the HEAD SHA
        state = load(d)
        sha = state["phases"][0]["tasks"][0].get("commit_sha", "")
        self.assertEqual(sha, head_sha, f"Expected HEAD SHA {head_sha}, got '{sha}'")

        # Verify plan.md has the SHA annotation
        plan = Path(d, "plan.md").read_text()
        self.assertIn(f"[{sha}]", plan)


class TestCmdStartIdempotent(TestCase):
    """quality.cmd_start owns its commit and is idempotent end-to-end.

    Regression: the start commit used to be the model's prose ``git commit``
    after ``track-state start``, unguarded → a re-invocation of the step skill
    produced a *second* "start" commit even though ``cmd_start`` itself no-op'd.
    The commit now lives inside the ``status == "new"`` branch, so re-running
    ``start`` is a true no-op.
    """

    def _make_git_track_dir(self):
        import subprocess
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        subprocess.run(["git", "init", d], capture_output=True, check=True)
        subprocess.run(["git", "-C", d, "config", "user.email", "test@test.com"],
                        capture_output=True, check=True)
        subprocess.run(["git", "-C", d, "config", "user.name", "Test"],
                        capture_output=True, check=True)
        Path(d, "README.md").write_text("# test")
        subprocess.run(["git", "-C", d, "add", "README.md"], capture_output=True, check=True)
        subprocess.run(["git", "-C", d, "commit", "-m", "init"], capture_output=True, check=True)
        # status:"new" is what cmd_start transitions away from.
        save(d, _make_state(status="new", track_id="dblstart"))
        return d

    def _head_subject(self, d):
        import subprocess
        return subprocess.run(["git", "-C", d, "log", "-1", "--format=%s"],
                              capture_output=True, text=True).stdout.strip()

    def test_first_start_commits_second_is_noop(self):
        from scripts.track_state.quality import cmd_start
        d = self._make_git_track_dir()

        r1 = _out_captured(cmd_start, d)[0]
        self.assertTrue(r1["ok"])
        self.assertEqual(r1["status"], "in_progress")
        self.assertTrue(r1.get("committed"))
        after_first = self._head_subject(d)
        self.assertIn("Start track", after_first)

        r2 = _out_captured(cmd_start, d)[0]
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["status"], "in_progress")
        self.assertEqual(r2.get("message"), "already started")
        self.assertNotIn("committed", r2)  # no commit claim on the no-op path

        # The HEAD subject is unchanged — no second "start" commit.
        self.assertEqual(self._head_subject(d), after_first)

    def test_start_does_not_emit_prose_commit_on_already_started(self):
        # Guards the exact user-reported scenario: re-running the step skill on a
        # track that's already in_progress must add zero commits.
        import subprocess
        from scripts.track_state.quality import cmd_start
        d = self._make_git_track_dir()

        _out_captured(cmd_start, d)  # transition once
        between = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
        _out_captured(cmd_start, d)  # re-run (simulates skill re-invocation)
        after = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
        self.assertEqual(between, after)


class TestInitFromPlan(TestCase):
    """plan_parse + cmd_init_from_plan: mechanical extraction + plan.md syntax checks."""

    def _plan(self, body):
        d = tempfile.mkdtemp()
        Path(d, "plan.md").write_text(body)
        return d

    GOOD = (
        "# Implementation Plan: Demo\n\n"
        "## Phase 1: Foundation\n"
        "- [ ] Task: Set up schemas <!-- AC-1, TC-1.1 -->\n"
        "- [ ] Task: Build endpoints <!-- AC-2 -->\n"
        "  - [ ] Subtask: GET endpoint\n"
        "  - [ ] Subtask: POST endpoint\n"
        "- [ ] [Manual] Task: Conductor - User Manual Verification 'Phase 1'\n\n"
        "## Phase 2: Polish\n"
        "- [ ] Task: Add tests <!-- AC-3 -->\n"
        "- [ ] [Manual] Task: Conductor - User Manual Verification 'Phase 2'\n"
    )

    def test_parse_extracts_structure(self):
        d = self._plan(self.GOOD)
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertEqual(parsed["errors"], [])
        self.assertEqual([p["name"] for p in parsed["phases"]], ["Foundation", "Polish"])
        self.assertEqual(parsed["phases"][0]["tasks"][1]["subtasks"],
                         ["Subtask: GET endpoint", "Subtask: POST endpoint"])

    def test_parse_keywordless_task_lines(self):
        """The ``Task:``/``Subtask:`` prefix is optional convention — a plan
        written without it parses cleanly and stores the bare description as the
        name, so no 'Task:'/'Subtask:' noise pollutes track-state.json. Contrast
        test_parse_extracts_structure, where the legacy keyword leaked into the
        stored subtask name. This is the new spec-planner default (§4.2)."""
        body = (
            "# Implementation Plan: Demo\n\n"
            "## Phase 1: Foundation\n"
            "- [ ] build the API <!-- AC-1, TC-1.1 -->\n"
            "  - [ ] create the data model\n"
            "  - [ ] create the route handlers\n"
            "- [ ] [Manual] Conductor - User Manual Verification 'Phase 1'\n"
        )
        parsed = parse_plan(Path(self._plan(body), "plan.md"))
        self.assertEqual(parsed["errors"], [])
        task = parsed["phases"][0]["tasks"][0]
        self.assertEqual(task["name"], "build the API")
        self.assertEqual(task["ac_refs"], ["AC-1"])
        self.assertEqual(task["tc_refs"], ["TC-1.1"])
        self.assertEqual(task["subtasks"],
                         ["create the data model", "create the route handlers"])
        # The [Manual] verification line still parses as a task without the keyword.
        self.assertIn("[Manual]", parsed["phases"][0]["tasks"][1]["name"])

    def test_parse_strips_html_comments_keeps_tags(self):
        d = self._plan("## Phase 1: P\n- [ ] [Config] Task: thing <!-- AC-1 -->\n"
                       "- [ ] [Manual] Task: verify\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertEqual(parsed["errors"], [])
        name = parsed["phases"][0]["tasks"][0]["name"]
        self.assertIn("[Config]", name)
        self.assertNotIn("<!--", name)
        self.assertNotIn("AC-1", name)

    def test_parse_captures_ac_refs_before_strip(self):
        # The <!-- AC-n, TC-n.n --> annotation is captured into ac_refs/tc_refs
        # on the parent task dict BEFORE _clean_name strips it from the name —
        # so ac_integrity can trace ACs to tasks without changing stored names.
        d = self._plan(self.GOOD)
        parsed = parse_plan(Path(d, "plan.md"))
        p1 = parsed["phases"][0]
        self.assertEqual(p1["tasks"][0]["ac_refs"], ["AC-1"])
        self.assertEqual(p1["tasks"][0]["tc_refs"], ["TC-1.1"])
        self.assertEqual(p1["tasks"][1]["ac_refs"], ["AC-2"])
        self.assertEqual(p1["tasks"][1]["tc_refs"], [])
        # Subtasks stay plain strings (inherit AC context; shape unchanged).
        self.assertIsInstance(p1["tasks"][1]["subtasks"][0], str)
        # Phase 2 task carries AC-3.
        self.assertEqual(parsed["phases"][1]["tasks"][0]["ac_refs"], ["AC-3"])
        # Aggregator de-dupes across the whole plan, first-seen order.
        self.assertEqual(collect_ac_refs(parsed), ["AC-1", "AC-2", "AC-3"])

    def test_error_bad_marker(self):
        d = self._plan("## Phase 1: P\n- [X] Task: bad\n- [ ] [Manual] Task: v\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("invalid" in e and "[X]" in e for e in parsed["errors"]))

    def test_error_task_before_phase(self):
        d = self._plan("- [ ] Task: orphan\n\n## Phase 1: P\n- [ ] [Manual] Task: v\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("before any Phase" in e for e in parsed["errors"]))

    def test_error_subtask_without_parent(self):
        d = self._plan("## Phase 1: P\n  - [ ] Subtask: no parent\n- [ ] [Manual] Task: v\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("no parent task" in e for e in parsed["errors"]))

    def test_error_non_contiguous_phases(self):
        d = self._plan("## Phase 1: P\n- [ ] [Manual] Task: v\n\n"
                       "## Phase 3: Q\n- [ ] [Manual] Task: v2\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("out of order" in e for e in parsed["errors"]))

    def test_error_duplicate_phase(self):
        d = self._plan("## Phase 1: P\n- [ ] [Manual] Task: v\n\n"
                       "## Phase 1: Q\n- [ ] [Manual] Task: v2\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("duplicate phase" in e for e in parsed["errors"]))

    def test_error_empty_phase(self):
        d = self._plan("## Phase 1: P\n\n## Phase 2: Q\n- [ ] [Manual] Task: v\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("no tasks" in e for e in parsed["errors"]))

    def test_error_empty_bracket_task(self):
        # "- [] Task" (empty brackets — the modal LLM typo for "- [ ]") sits in
        # the gap between _TASK_LINE (one valid char) and _BAD_MARKER_LINE (one
        # char): zero chars match neither, so without the malformed guard the
        # line is silently dropped from track-state.json.
        d = self._plan("## Phase 1: P\n- [] Task: foo\n- [ ] [Manual] Task: v\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("malformed" in e and "[]" in e for e in parsed["errors"]))

    def test_error_empty_bracket_subtask(self):
        # Indented empty bracket — the subtask variant from the user's report
        # (a tab indent is also \s, matching _BRACKET_TOKEN's leading (\s*)).
        d = self._plan("## Phase 1: P\n- [ ] Task: parent\n"
                       "\t- [] subtask missing\n- [ ] [Manual] Task: v\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("malformed" in e and "subtask" in e and "[]" in e
                            for e in parsed["errors"]))

    def test_error_wrong_width_bracket(self):
        # Two-space and two-char brackets also fall in the gap (width != 1).
        for bad in ("- [  ] Task: a", "- [xy] Task: b"):
            with self.subTest(bad=bad):
                d = self._plan(f"## Phase 1: P\n{bad}\n- [ ] [Manual] Task: v\n")
                parsed = parse_plan(Path(d, "plan.md"))
                self.assertTrue(any("malformed" in e for e in parsed["errors"]))

    def test_known_tag_routes_to_missing_checkbox(self):
        # A dispatch tag as the first token is NOT a malformed bracket — it is a
        # tag-without-checkbox and must keep the more accurate "missing checkbox"
        # message (not the malformed one). Guards the _KNOWN_BRACKET_TOKEN allow-list.
        d = self._plan("## Phase 1: P\n- [Manual] Task: foo\n- [ ] [Manual] Task: v\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("missing" in e and "[ ]" in e for e in parsed["errors"]))
        self.assertFalse(any("malformed" in e for e in parsed["errors"]))

    def test_error_plain_bullet_in_phase(self):
        # A plain dash-bullet inside a Phase with no checkbox, no Task:/Subtask:
        # keyword, and no <!-- annotation ("- do the thing") is silently dropped
        # today. The [ ] checkbox is the sole mandatory element (rule 1), so the
        # phase-guarded plain catch-all flags it.
        d = self._plan("## Phase 1: P\n- do the thing\n- [ ] [Manual] Task: v\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("missing its '[ ]' checkbox" in e and "line 2" in e
                            for e in parsed["errors"]), parsed["errors"])

    def test_error_tagged_bullet_without_checkbox_or_keyword(self):
        # "- [Explore] map the module" — tag present, but no checkbox AND no
        # keyword: also silently dropped today. The plain catch-all closes it.
        d = self._plan("## Phase 1: P\n- [Explore] map the module\n"
                       "- [ ] [Manual] Task: v\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertTrue(any("missing its '[ ]' checkbox" in e and "line 2" in e
                            for e in parsed["errors"]), parsed["errors"])

    def test_plain_bullet_before_phase_not_flagged(self):
        # A plain bullet BEFORE the first ## Phase is legitimate intro prose, not
        # a malformed task — the plain detector is phase-guarded.
        d = self._plan("Some intro prose.\n\n- a plain bullet\n\n"
                       "## Phase 1: P\n- [ ] [Manual] Task: v\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertFalse(any("missing its '[ ]' checkbox" in e
                             for e in parsed["errors"]), parsed["errors"])

    def test_init_check_rejects_plain_bullet(self):
        # The viewer surface: init-from-plan --check reports ok:false for a plain
        # unchecked bullet so the defect is visible before any state is written.
        d = self._plan("## Phase 1: P\n- do the thing\n- [ ] [Manual] Task: v\n")
        result, _ = _out_captured(
            cmd_init_from_plan, d, "demo_20260702", "feature", "desc",
            check=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("missing its '[ ]' checkbox" in e
                            for e in result.get("errors", [])))

    def test_valid_plan_not_flagged_by_malformed_guard(self):
        # Regression: the malformed guard must not false-positive on real
        # checkboxes, multi-char dispatch tags on valid lines, or [N/A] prose.
        d = self._plan(self.GOOD)
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertEqual(parsed["errors"], [])
        self.assertFalse(any("malformed" in e for e in parsed["warnings"]))

    def test_init_check_rejects_empty_bracket(self):
        # The viewer surface: init-from-plan --check must report ok:false (not a
        # lying ok:true) when a bracket is malformed, so the defect is visible
        # before any state is written.
        d = self._plan("## Phase 1: P\n- [] Task: foo\n- [ ] [Manual] Task: v\n")
        result, _ = _out_captured(
            cmd_init_from_plan, d, "demo_20260702", "feature", "desc",
            check=True)
        self.assertFalse(result["ok"])
        self.assertTrue(any("malformed" in e and "[]" in e
                            for e in result.get("errors", [])))

    def test_warn_missing_manual(self):
        d = self._plan("## Phase 1: P\n- [ ] Task: real work\n")
        parsed = parse_plan(Path(d, "plan.md"))
        self.assertEqual(parsed["errors"], [])
        # The validator keys off the manual ROUTE (route_for == "manual"), not a
        # "[Manual]" substring — so the warning names the route, not the tag, and
        # a project overlay that renames/adds a manual-route tag still trips it.
        self.assertTrue(any("manual-route" in w for w in parsed["warnings"]))

    def test_to_plan_structure_shape(self):
        d = self._plan(self.GOOD)
        structure = to_plan_structure(parse_plan(Path(d, "plan.md")))
        # Task WITH subtasks carries the key; task WITHOUT omits it.
        p1 = structure["phases"][0]
        self.assertNotIn("subtasks", p1["tasks"][0])
        self.assertIn("subtasks", p1["tasks"][1])
        self.assertEqual(len(p1["tasks"][1]["subtasks"]), 2)

    def test_check_dry_run_writes_nothing(self):
        d = self._plan(self.GOOD)
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101",
                                  "feature", "demo track", "interactive", check=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["check"])
        self.assertEqual(result["phases"], 2)
        self.assertFalse((Path(d, "track-state.json")).exists())

    def test_init_from_plan_creates_state(self):
        d = self._plan(self.GOOD)
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101",
                                  "feature", "demo track", "interactive")
        self.assertTrue(result["ok"])
        self.assertEqual(result["phases"], 2)
        state = load(d)
        self.assertEqual(state["track_id"], "demo_20260101")
        self.assertEqual(state["execution_mode"], "interactive")
        ph1 = state["phases"][0]
        self.assertEqual(ph1["name"], "Foundation")
        self.assertEqual(len(ph1["tasks"]), 3)  # 2 impl + 1 manual
        self.assertEqual([t["name"] for t in ph1["tasks"][1]["subtasks"]],
                         ["Subtask: GET endpoint", "Subtask: POST endpoint"])
        self.assertTrue(all(t["status"] == "pending" for t in ph1["tasks"]))

    def test_init_clears_orphan_result_json(self):
        # Pre-plan → post-plan boundary reap: the §2.2.5 grounding fan-out's
        # parallel explorers share the single-slot result.json mailbox
        # (last-write-wins, content unconsumed pre-plan). State creation must
        # leave the slot clean — an orphan must NOT survive into the
        # post-plan window where dispatch-finalize reads on existence.
        d = self._plan(self.GOOD)
        cond = Path(d, ".conductor")
        cond.mkdir()
        (cond / "result.json").write_text('{"status": "SUCCESS"}')
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101",
                                  "feature", "demo track")
        self.assertTrue(result["ok"])
        self.assertFalse((cond / "result.json").exists())
        self.assertTrue(Path(d, "track-state.json").exists())

    def test_init_check_does_not_clear_result_json(self):
        # --check is read-only: validating a plan must not reap the mailbox.
        d = self._plan(self.GOOD)
        cond = Path(d, ".conductor")
        cond.mkdir()
        (cond / "result.json").write_text('{"status": "SUCCESS"}')
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101",
                                  "feature", "demo track", check=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["check"])
        self.assertTrue((cond / "result.json").exists())

    def test_init_from_plan_rejects_malformed(self):
        d = self._plan("## Phase 1: P\n- [X] bad\n")
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101",
                                  "feature", "demo track")
        self.assertFalse(result["ok"])
        self.assertFalse((Path(d, "track-state.json")).exists())

    def test_init_from_plan_missing_file(self):
        d = tempfile.mkdtemp()
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101",
                                  "feature", "demo track")
        self.assertFalse(result["ok"])
        self.assertTrue(any("not found" in e for e in result["errors"]))

    def test_init_from_plan_no_mismatch_warning(self):
        # Structure derived from plan.md → count cross-check is clean by construction.
        d = self._plan(self.GOOD)
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101",
                                  "feature", "demo track")
        self.assertNotIn("warnings", result)

    def test_init_from_plan_refuses_to_overwrite_existing_state(self):
        # V7: re-running init on a live track must not wipe existing progress.
        d = self._plan(self.GOOD)
        _out_captured(cmd_init_from_plan, d, "demo_20260101", "feature", "demo track")
        live = load(d)
        live["status"] = "in_progress"
        live["phases"][0]["tasks"][0]["status"] = "completed"
        live["phases"][0]["tasks"][0]["commit_sha"] = "abc1234"
        save(d, live)

        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101",
                                  "feature", "demo track")
        self.assertFalse(result["ok"])
        self.assertTrue(any("already exists" in e for e in result["errors"]))
        # Existing progress is preserved untouched.
        after = load(d)
        self.assertEqual(after["status"], "in_progress")
        self.assertEqual(after["phases"][0]["tasks"][0]["status"], "completed")
        self.assertEqual(after["phases"][0]["tasks"][0]["commit_sha"], "abc1234")

    def test_init_from_plan_force_rebootstraps(self):
        d = self._plan(self.GOOD)
        _out_captured(cmd_init_from_plan, d, "demo_20260101", "feature", "demo track")
        live = load(d)
        live["phases"][0]["tasks"][0]["status"] = "completed"
        save(d, live)

        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101",
                                  "feature", "demo track", force=True)
        self.assertTrue(result["ok"])
        after = load(d)
        # --force resets the whole track back to its bootstrap state.
        self.assertEqual(after["status"], "new")
        self.assertTrue(all(t["status"] == "pending"
                            for t in after["phases"][0]["tasks"]))


class TestExecutionMode(TestCase):
    """execution_mode enum validation at init + set-mode on an existing track."""

    def _plan_dir(self, plan="# Plan\n\n## Phase 1: Build\n- [ ] Task A\n- [ ] Task B\n"):
        d = tempfile.mkdtemp()
        Path(d, "plan.md").write_text(plan)
        return d

    def test_init_accepts_continuous(self):
        d = self._plan_dir()
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101", "feature", "d", "continuous")
        self.assertTrue(result["ok"])
        self.assertEqual(load(d)["execution_mode"], "continuous")

    def test_init_accepts_interactive(self):
        d = self._plan_dir()
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101", "feature", "d", "interactive")
        self.assertTrue(result["ok"])
        self.assertEqual(load(d)["execution_mode"], "interactive")

    def test_init_rejects_autonomous(self):
        # 'autonomous' was advertised by stale `init` help but is not a valid enum.
        d = self._plan_dir()
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101", "feature", "d", "autonomous")
        self.assertFalse(result["ok"])
        self.assertFalse((Path(d, "track-state.json")).exists())
        self.assertTrue(any("execution_mode" in e for e in result["errors"]))

    def test_init_rejects_typo(self):
        d = self._plan_dir()
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101", "feature", "d", "continuouos")
        self.assertFalse(result["ok"])

    def test_init_none_leaves_unset(self):
        # None means "leave execution_mode unset" (back-compat) — not an error.
        d = self._plan_dir()
        result, _ = _out_captured(cmd_init_from_plan, d, "demo_20260101", "feature", "d", None)
        self.assertTrue(result["ok"])

    def test_set_mode_flips_interactive_to_continuous(self):
        d = self._plan_dir()
        _out_captured(cmd_init_from_plan, d, "demo_20260101", "feature", "d", "interactive")
        result, _ = _out_captured(cmd_set_mode, d, "continuous")
        self.assertTrue(result["ok"])
        self.assertEqual(result["previous"], "interactive")
        self.assertEqual(result["execution_mode"], "continuous")
        self.assertEqual(load(d)["execution_mode"], "continuous")

    def test_set_mode_rejects_missing(self):
        d = self._plan_dir()
        _out_captured(cmd_init_from_plan, d, "demo_20260101", "feature", "d", "interactive")
        result, _ = _out_captured(cmd_set_mode, d, None)
        self.assertFalse(result["ok"])
        # Unchanged.
        self.assertEqual(load(d)["execution_mode"], "interactive")

    def test_set_mode_rejects_invalid(self):
        d = self._plan_dir()
        _out_captured(cmd_init_from_plan, d, "demo_20260101", "feature", "d", "interactive")
        result, _ = _out_captured(cmd_set_mode, d, "autonomous")
        self.assertFalse(result["ok"])
        # Unchanged.
        self.assertEqual(load(d)["execution_mode"], "interactive")


class TestManualTaskRouting(TestCase):
    """dispatch.py: [Manual] routing is gated on execution_mode.

    continuous auto-defers (no human in the loop); interactive surfaces the
    task via a `manual_task` action so the orchestrator can ask the user.
    """

    def _track(self, mode):
        state = _make_state(
            execution_mode=mode,
            current_phase_index=1,
            current_task_index=1,
            phases=[{
                "name": "Phase 1",
                "status": "pending",
                "tasks": [{"name": "[Manual] verify UI", "status": "pending"}],
            }],
        )
        plan = "# Plan\n\n## Phase 1: Build\n- [ ] [Manual] verify UI\n"
        return _make_track_dir(state, plan_content=plan)

    def test_interactive_surfaces_manual_task(self):
        d = self._track("interactive")
        try:
            result, _ = _out_captured(cmd_dispatch_next, d)
            self.assertEqual(result.get("action"), "manual_task")
            self.assertEqual(result.get("execution_mode"), "interactive")
        finally:
            shutil.rmtree(d)

    def test_continuous_auto_defers_manual_task(self):
        d = self._track("continuous")
        try:
            result, _ = _out_captured(cmd_dispatch_next, d)
            self.assertEqual(result.get("action"), "defer_manual")
            self.assertEqual(result.get("execution_mode"), "continuous")
        finally:
            shutil.rmtree(d)


class TestShasRange(TestCase):
    """misc.py: cmd_shas emits a review `range` that includes the first commit."""

    @staticmethod
    def _state_with_shas(shas):
        tasks = [
            {"name": f"Task {i}", "status": "completed", "commit_sha": sha}
            for i, sha in enumerate(shas)
        ]
        return _make_state(phases=[{"name": "Phase 1", "status": "completed", "tasks": tasks}])

    def test_range_includes_first_commit_via_parent(self):
        # range must be first~1..last so git diff covers the first task's own changes;
        # first..last alone compares endpoint trees and masks the first commit.
        d = _make_track_dir(self._state_with_shas(["d1a9574", "aaaaaaa", "1b3f259"]))
        try:
            result, _ = _out_captured(cmd_shas, d)
            self.assertEqual(result["count"], 3)
            self.assertEqual(result["first"], "d1a9574")
            self.assertEqual(result["last"], "1b3f259")
            self.assertEqual(result["range"], "d1a9574~1..1b3f259")
        finally:
            shutil.rmtree(d)

    def test_single_sha_range_uses_parent(self):
        d = _make_track_dir(self._state_with_shas(["abcdef0"]))
        try:
            result, _ = _out_captured(cmd_shas, d)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["range"], "abcdef0~1..abcdef0")
        finally:
            shutil.rmtree(d)

    def test_empty_range_is_none(self):
        d = _make_track_dir(_make_state())  # no completed tasks with SHAs
        try:
            result, _ = _out_captured(cmd_shas, d)
            self.assertEqual(result["count"], 0)
            self.assertIsNone(result["first"])
            self.assertIsNone(result["last"])
            self.assertIsNone(result["range"])
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    main()
