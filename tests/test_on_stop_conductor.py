r"""Tests for on-stop-conductor.py — the deterministic per-skill Stop hook that
replaced the inline ``type: prompt`` Stop blocks in the implement/parallel skills.

Pure helpers are exercised directly via importlib; ``main()`` is driven via
subprocess (stdin JSON -> stdout JSON) for the block / allow / stop_hook_active
contract. Mirrors the test_state_consistency_check.py harness.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "on_stop_conductor", _scripts / "on-stop-conductor.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
stale_serial_locks = _mod.stale_serial_locks
plan_drift_errors = _mod.plan_drift_errors
conductor_state_changes = _mod.conductor_state_changes
audit_track = _mod.audit_track

_HOOK = _scripts / "on-stop-conductor.py"


def _state(*tasks, status="in_progress", track_id="t1"):
    return {
        "track_id": track_id,
        "status": status,
        "current_phase_index": 1,
        "current_task_index": 1,
        "phases": [{"name": "Phase 1", "status": "pending", "tasks": list(tasks)}],
    }


def _write_track(root: Path, state: dict, plan_body: str = None):
    tdir = root / "conductor" / "tracks" / state["track_id"]
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "track-state.json").write_text(json.dumps(state))
    if plan_body is not None:
        (tdir / "plan.md").write_text(plan_body)
    return tdir


def _registry(root: Path, track_id: str):
    (root / "conductor").mkdir(parents=True, exist_ok=True)
    (root / "conductor" / "tracks.md").write_text(
        f"- [{track_id}](conductor/tracks/{track_id})\n"
    )


def _init_git(root: Path):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def _run_hook(root: Path, payload: dict) -> dict:
    """Run the hook with the given stdin JSON; return parsed stdout JSON."""
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=root,
    )
    # decision:block exits 2 — that's expected, not a test failure.
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


# --------------------------------------------------------------------------- #
# stale_serial_locks
# --------------------------------------------------------------------------- #
class StaleSerialLocksTests(TestCase):
    def test_flags_non_wave_in_progress(self):
        state = _state({"name": "A", "status": "in_progress"})
        self.assertEqual(stale_serial_locks(state, set()), ["P1.T1"])

    def test_wave_loc_exempt(self):
        state = _state({"name": "A", "status": "in_progress"})
        self.assertEqual(stale_serial_locks(state, {(1, 1)}), [])

    def test_in_progress_subtask_is_a_serial_lock(self):
        state = _state(
            {"name": "A", "status": "completed",
             "subtasks": [{"name": "A1", "status": "in_progress"}]}
        )
        self.assertEqual(stale_serial_locks(state, set()), ["P1.T1.1"])

    def test_clean_state_empty(self):
        state = _state({"name": "A", "status": "completed"})
        self.assertEqual(stale_serial_locks(state, set()), [])


# --------------------------------------------------------------------------- #
# plan_drift_errors
# --------------------------------------------------------------------------- #
class PlanDriftErrorsTests(TestCase):
    def test_matched_structure_no_errors(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = Path(d)
            (tdir / "plan.md").write_text("## Phase 1\n\n- [ ] [Config] A\n")
            state = _state({"name": "A", "status": "pending"})
            self.assertEqual(plan_drift_errors(tdir, state), [])

    def test_task_count_mismatch_is_error(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = Path(d)
            (tdir / "plan.md").write_text(
                "## Phase 1\n\n- [ ] A\n- [ ] B\n"  # plan has 2
            )
            state = _state({"name": "A", "status": "pending"})  # state has 1
            errs = plan_drift_errors(tdir, state)
        self.assertTrue(errs)
        self.assertTrue(any("2 tasks" in e and "state has 1" in e for e in errs))

    def test_missing_plan_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(plan_drift_errors(Path(d), _state({"name": "A"})), [])


# --------------------------------------------------------------------------- #
# conductor_state_changes
# --------------------------------------------------------------------------- #
class ConductorStateChangesTests(TestCase):
    def test_flags_dirty_track_state_and_plan(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _init_git(root)
            td = "conductor/tracks/t1"
            (root / td).mkdir(parents=True)
            for f in ("track-state.json", "plan.md"):
                (root / td / f).write_text("{}")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
            # dirty both tracked files
            (root / td / "track-state.json").write_text('{"x":1}')
            (root / td / "plan.md").write_text("changed")
            dirty = conductor_state_changes(root, [td])
        self.assertEqual(sorted(dirty), [f"{td}/plan.md", f"{td}/track-state.json"])

    def test_clean_tree_empty(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _init_git(root)
            td = "conductor/tracks/t1"
            (root / td).mkdir(parents=True)
            (root / td / "track-state.json").write_text("{}")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
            self.assertEqual(conductor_state_changes(root, [td]), [])

    def test_unrelated_file_not_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _init_git(root)
            td = "conductor/tracks/t1"
            (root / td).mkdir(parents=True)
            (root / td / "track-state.json").write_text("{}")
            (root / "README.md").write_text("init")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
            (root / "README.md").write_text("dirty")  # unrelated edit
            self.assertEqual(conductor_state_changes(root, [td]), [])


# --------------------------------------------------------------------------- #
# audit_track integration
# --------------------------------------------------------------------------- #
class AuditTrackTests(TestCase):
    def test_terminal_track_not_audited(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = _write_track(Path(d), _state(
                {"name": "A", "status": "in_progress"}, status="completed"))
            self.assertEqual(audit_track(tdir), [])

    def test_flags_stale_lock(self):
        with tempfile.TemporaryDirectory() as d:
            tdir = _write_track(Path(d), _state({"name": "A", "status": "in_progress"}))
            issues = audit_track(tdir)
        self.assertEqual(len(issues), 1)
        self.assertIn("stale in_progress lock", issues[0])


# --------------------------------------------------------------------------- #
# main() — block / allow / loop-break contract via subprocess
# --------------------------------------------------------------------------- #
class MainContractTests(TestCase):
    def test_stale_lock_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _registry(root, "t1")
            _write_track(root, _state({"name": "A", "status": "in_progress"}))
            out = _run_hook(root, {"cwd": str(root)})
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("stale in_progress lock", out.get("reason", ""))

    def test_clean_track_allows(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _init_git(root)
            _registry(root, "t1")
            _write_track(
                root,
                _state({"name": "A", "status": "completed"}, status="completed"),
            )
            # commit the registry + state so the tree is clean
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
            out = _run_hook(root, {"cwd": str(root)})
        self.assertNotIn("decision", out)  # allow = no decision field

    def test_stop_hook_active_allows_unconditionally(self):
        # Even with a glaring stale lock, stop_hook_active must let it stop
        # (second attempt after a prior block — the loop-break / HALT escape).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _registry(root, "t1")
            _write_track(root, _state({"name": "A", "status": "in_progress"}))
            out = _run_hook(root, {"cwd": str(root), "stop_hook_active": True})
        self.assertNotIn("decision", out)

    def test_no_registry_allows(self):
        with tempfile.TemporaryDirectory() as d:
            out = _run_hook(Path(d), {"cwd": d})
        self.assertNotIn("decision", out)


if __name__ == "__main__":
    main()
