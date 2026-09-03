"""Tests for the new-track resume-marker CLI (``scripts/track_state/new_track.py``).

Promotes the §0.5 / §2.x prose JSON bookkeeping into code: the marker
``<track_dir>/.conductor/new-track-progress.json`` was previously 100% model
hand-edit (init, four ``steps_done`` appends, set-mode, finalize-and-delete,
and the §0.5 glob+jump detection) with no code and no test. These pin the code
contract: idempotent init, idempotent + order-preserving step appends, validated
set-mode, idempotent finalize, a tolerant reader (corrupt/missing → no crash),
and the resume directive (§0.5 detect+jump promoted to code).

The cmd functions print JSON via ``out``; a capture helper parses it. Resume
needs a registry on disk and resolves it from CWD (``_find_registry``), so those
tests chdir into a temp project and restore CWD in tearDown.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import new_track as nt

ROOT = Path(__file__).resolve().parent.parent


def run(fn, *args, **kwargs):
    """Call a cmd fn, return its parsed stdout JSON."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    return json.loads(buf.getvalue())


class _MarkerTestCase(TestCase):
    """Each test gets a temp track dir; .conductor/new-track-progress.json helpers."""

    def setUp(self):
        self._prev_cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="nt-test-")
        self.td = str(Path(self.tmp) / "conductor" / "tracks" / "foo_20260710")

    def tearDown(self):
        os.chdir(self._prev_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def marker_path(self):
        return Path(self.td) / ".conductor" / nt._NT_MARKER

    def read_marker_file(self):
        return json.loads(self.marker_path().read_text())


class NewTrackInitTests(_MarkerTestCase):
    def test_init_creates_marker_with_correct_shape(self):
        out = run(nt.cmd_new_track_init, self.td, "foo_20260710", "Foo thing", "feature")
        self.assertTrue(out["ok"])
        self.assertEqual(out["action"], "created")
        data = self.read_marker_file()
        self.assertEqual(data["track_id"], "foo_20260710")
        self.assertEqual(data["description"], "Foo thing")
        self.assertEqual(data["type"], "feature")
        self.assertIsNone(data["execution_mode"])
        self.assertIsNone(data["workflow_shape"])
        self.assertEqual(data["steps_done"], [])
        self.assertFalse(data["committed"])

    def test_init_creates_track_dir_and_conductor_dir(self):
        self.assertFalse(Path(self.td).exists())
        run(nt.cmd_new_track_init, self.td, "foo_20260710", "d", "feature")
        self.assertTrue(self.marker_path().exists())

    def test_init_is_idempotent_preserves_progress(self):
        run(nt.cmd_new_track_init, self.td, "foo_20260710", "d", "feature")
        run(nt.cmd_new_track_step, self.td, "spec_planned")  # advance first
        out = run(nt.cmd_new_track_init, self.td, "foo_20260710", "d", "feature")
        self.assertEqual(out["action"], "exists")  # NOT created
        self.assertEqual(out["steps_done"], ["spec_planned"])  # progress preserved
        self.assertEqual(self.read_marker_file()["steps_done"], ["spec_planned"])


class NewTrackStepTests(_MarkerTestCase):
    def setUp(self):
        super().setUp()
        run(nt.cmd_new_track_init, self.td, "foo_20260710", "d", "feature")

    def test_step_appends_key(self):
        out = run(nt.cmd_new_track_step, self.td, "spec_planned")
        self.assertTrue(out["ok"])
        self.assertEqual(out["steps_done"], ["spec_planned"])

    def test_step_is_idempotent(self):
        run(nt.cmd_new_track_step, self.td, "spec_planned")
        out = run(nt.cmd_new_track_step, self.td, "spec_planned")
        self.assertEqual(out["steps_done"], ["spec_planned"])  # no duplicate

    def test_step_preserves_order(self):
        run(nt.cmd_new_track_step, self.td, "spec_planned")
        out = run(nt.cmd_new_track_step, self.td, "reviewed")
        self.assertEqual(out["steps_done"], ["spec_planned", "reviewed"])

    def test_step_rejects_unknown_key_without_mutation(self):
        before = self.read_marker_file()["steps_done"]
        out = run(nt.cmd_new_track_step, self.td, "bogus_key")
        self.assertIn("error", out)
        self.assertEqual(self.read_marker_file()["steps_done"], before)  # unchanged

    def test_step_errors_when_no_marker(self):
        bare = str(Path(self.tmp) / "nope")
        out = run(nt.cmd_new_track_step, bare, "spec_planned")
        self.assertIn("error", out)


class NewTrackSetModeTests(_MarkerTestCase):
    def setUp(self):
        super().setUp()
        run(nt.cmd_new_track_init, self.td, "foo_20260710", "d", "feature")

    def test_set_mode_writes_execution_mode(self):
        out = run(nt.cmd_new_track_set_mode, self.td, "continuous")
        self.assertTrue(out["ok"])
        self.assertEqual(self.read_marker_file()["execution_mode"], "continuous")

    def test_set_mode_rejects_invalid_mode(self):
        prev = self.read_marker_file()["execution_mode"]
        out = run(nt.cmd_new_track_set_mode, self.td, "bogus")
        self.assertIn("error", out)
        self.assertEqual(self.read_marker_file()["execution_mode"], prev)

    def test_set_mode_errors_when_no_marker(self):
        bare = str(Path(self.tmp) / "nope")
        out = run(nt.cmd_new_track_set_mode, bare, "continuous")
        self.assertIn("error", out)


class NewTrackSetShapeTests(_MarkerTestCase):
    def setUp(self):
        super().setUp()
        run(nt.cmd_new_track_init, self.td, "foo_20260710", "d", "feature")

    def test_set_shape_writes_workflow_shape(self):
        out = run(nt.cmd_new_track_set_shape, self.td, "migration")
        self.assertTrue(out["ok"])
        self.assertEqual(self.read_marker_file()["workflow_shape"], "migration")

    def test_set_shape_rejects_unknown_without_mutation(self):
        # The hard-gate contract: "migrate" is the shape's keyword SIGNAL, not
        # the shape name — a deliberate declaration never silently no-ops.
        run(nt.cmd_new_track_set_shape, self.td, "default")
        out = run(nt.cmd_new_track_set_shape, self.td, "migrate")
        self.assertIn("error", out)
        self.assertIn("migration", out["hint"])
        self.assertEqual(self.read_marker_file()["workflow_shape"], "default")

    def test_set_shape_errors_when_no_marker(self):
        bare = str(Path(self.tmp) / "nope")
        out = run(nt.cmd_new_track_set_shape, bare, "migration")
        self.assertIn("error", out)


class NewTrackFinalizeTests(_MarkerTestCase):
    def setUp(self):
        super().setUp()
        run(nt.cmd_new_track_init, self.td, "foo_20260710", "d", "feature")

    def test_finalize_deletes_marker(self):
        self.assertTrue(self.marker_path().exists())
        out = run(nt.cmd_new_track_finalize, self.td)
        self.assertTrue(out["ok"])
        self.assertTrue(out["finalized"])
        self.assertTrue(out["removed"])
        self.assertFalse(self.marker_path().exists())

    def test_finalize_is_idempotent(self):
        run(nt.cmd_new_track_finalize, self.td)
        out = run(nt.cmd_new_track_finalize, self.td)  # again — no crash
        self.assertTrue(out["ok"])
        self.assertFalse(out["removed"])  # already gone


class CorruptionToleranceTests(_MarkerTestCase):
    def test_corrupt_marker_does_not_crash_step(self):
        # A half-written / corrupt marker must not raise; step surfaces an error
        # instead of crashing the resume flow.
        cdir = Path(self.td) / ".conductor"
        cdir.mkdir(parents=True, exist_ok=True)
        self.marker_path().write_text("{ not valid json")
        out = run(nt.cmd_new_track_step, self.td, "spec_planned")
        self.assertIn("error", out)  # tolerant reader → None → clean error

    def test_corrupt_marker_skipped_by_resume(self):
        self._make_project()
        cdir = Path(self.td) / ".conductor"
        cdir.mkdir(parents=True, exist_ok=True)
        self.marker_path().write_text("{ not valid json")
        out = run(nt.cmd_new_track_resume)
        self.assertEqual(out["action"], "none")  # corrupt → not resumable

    def _make_project(self):
        """Stand up conductor/tracks.md so _find_registry resolves from CWD."""
        os.chdir(self.tmp)
        (Path(self.tmp) / "conductor").mkdir(parents=True, exist_ok=True)
        (Path(self.tmp) / "conductor" / "tracks.md").write_text("# Tracks\n")


class NewTrackResumeTests(_MarkerTestCase):
    def _make_project(self):
        os.chdir(self.tmp)
        (Path(self.tmp) / "conductor").mkdir(parents=True, exist_ok=True)
        (Path(self.tmp) / "conductor" / "tracks.md").write_text("# Tracks\n")

    def test_resume_none_when_clean(self):
        self._make_project()
        out = run(nt.cmd_new_track_resume)
        self.assertEqual(out["action"], "none")
        self.assertEqual(out["candidates"], [])

    def test_resume_none_when_no_registry(self):
        os.chdir(tempfile.mkdtemp(prefix="nt-empty-"))  # no tracks.md anywhere up
        out = run(nt.cmd_new_track_resume)
        self.assertEqual(out["action"], "none")
        self.assertEqual(out["reason"], "no_registry")

    def test_resume_directive_for_partial_track(self):
        self._make_project()
        run(nt.cmd_new_track_init, self.td, "foo_20260710", "Foo thing", "feature")
        run(nt.cmd_new_track_step, self.td, "spec_planned")
        run(nt.cmd_new_track_step, self.td, "reviewed")
        run(nt.cmd_new_track_set_mode, self.td, "continuous")
        run(nt.cmd_new_track_set_shape, self.td, "migration")
        out = run(nt.cmd_new_track_resume)
        self.assertEqual(out["action"], "resume")
        self.assertEqual(len(out["candidates"]), 1)
        c = out["candidates"][0]
        self.assertEqual(c["track_id"], "foo_20260710")
        self.assertEqual(c["description"], "Foo thing")
        self.assertEqual(c["execution_mode"], "continuous")
        # The stamped shape rides the resume directive so a new session
        # re-records $WORKFLOW_SHAPE without re-running the §2.1 matcher.
        self.assertEqual(c["workflow_shape"], "migration")
        self.assertEqual(c["steps_done"], ["spec_planned", "reviewed"])
        self.assertEqual(c["last_step"], "reviewed")
        self.assertEqual(c["first_missing_step"], "state_created")
        self.assertEqual(c["resume_target"], "§2.6")

    def test_resume_target_mapping_per_first_missing(self):
        cases = [
            ([], "spec_planned", "§2.3"),
            (["spec_planned"], "reviewed", "§2.4"),
            (["spec_planned", "reviewed"], "state_created", "§2.6"),
            (["spec_planned", "reviewed", "state_created"], "registry_updated", "§2.6"),
        ]
        self._make_project()
        for done, expected_missing, expected_target in cases:
            with self.subTest(done=done):
                td2 = str(Path(self.tmp) / "conductor" / "tracks" / f"t_{len(done)}")
                run(nt.cmd_new_track_init, td2, f"t_{len(done)}", "d", "feature")
                for k in done:
                    run(nt.cmd_new_track_step, td2, k)
                out = run(nt.cmd_new_track_resume)
                cand = next(c for c in out["candidates"] if c["track_id"] == f"t_{len(done)}")
                self.assertEqual(cand["first_missing_step"], expected_missing, done)
                self.assertEqual(cand["resume_target"], expected_target, done)

    def test_resume_skips_committed_true_and_missing_field_markers(self):
        self._make_project()
        # committed:true (stale — finalize should have deleted it) → skipped
        run(nt.cmd_new_track_init, self.td, "stale", "d", "feature")
        data = self.read_marker_file()
        data["committed"] = True
        self.marker_path().write_text(json.dumps(data))
        # marker missing the committed field entirely (ambiguous) → skipped
        td2 = str(Path(self.tmp) / "conductor" / "tracks" / "ambig")
        nt._nt_write_marker(td2, {"track_id": "ambig", "steps_done": []})  # no committed key
        out = run(nt.cmd_new_track_resume)
        self.assertEqual(out["action"], "none")  # neither resumable

    def test_resume_emits_multiple_candidates(self):
        self._make_project()
        for i in (1, 2):
            td_i = str(Path(self.tmp) / "conductor" / "tracks" / f"m{i}")
            run(nt.cmd_new_track_init, td_i, f"m{i}", "d", "feature")
        out = run(nt.cmd_new_track_resume)
        self.assertEqual(out["action"], "resume")
        self.assertEqual(len(out["candidates"]), 2)

    def test_resume_none_after_finalize(self):
        self._make_project()
        run(nt.cmd_new_track_init, self.td, "foo_20260710", "d", "feature")
        run(nt.cmd_new_track_finalize, self.td)
        out = run(nt.cmd_new_track_resume)
        self.assertEqual(out["action"], "none")


class NewTrackCliWiringTests(TestCase):
    """Integration: the 5 subcommands dispatch through cli.py end-to-end."""

    def setUp(self):
        self._prev_cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="nt-cli-")
        (Path(self.tmp) / "conductor").mkdir(parents=True, exist_ok=True)
        (Path(self.tmp) / "conductor" / "tracks.md").write_text("# Tracks\n")
        os.chdir(self.tmp)
        self.shim = str(ROOT / "scripts" / "track-state")

    def tearDown(self):
        os.chdir(self._prev_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        r = subprocess.run([sys.executable, self.shim, *args],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        return json.loads(r.stdout)

    def test_help_lists_new_track_group(self):
        r = subprocess.run([sys.executable, self.shim, "help"],
                           capture_output=True, text=True)
        self.assertIn("New-Track Resume", r.stdout)
        for cmd in ("new-track-resume", "new-track-init", "new-track-step",
                    "new-track-set-mode", "new-track-set-shape",
                    "new-track-finalize"):
            self.assertIn(cmd, r.stdout)

    def test_full_lifecycle_round_trip(self):
        td = "conductor/tracks/lifecycle_20260710"
        self.assertEqual(self._run("new-track-resume")["action"], "none")
        init = self._run("new-track-init", td, "--track-id", "lifecycle_20260710",
                         "--description", "Life", "--type", "feature")
        self.assertEqual(init["action"], "created")
        self.assertEqual(self._run("new-track-step", td, "spec_planned")["steps_done"],
                         ["spec_planned"])
        self.assertEqual(self._run("new-track-set-mode", td, "--mode", "continuous")["execution_mode"],
                         "continuous")
        res = self._run("new-track-resume")
        self.assertEqual(res["action"], "resume")
        self.assertEqual(res["candidates"][0]["first_missing_step"], "reviewed")
        self.assertTrue(self._run("new-track-finalize", td)["finalized"])
        self.assertEqual(self._run("new-track-resume")["action"], "none")


if __name__ == "__main__":
    main()
