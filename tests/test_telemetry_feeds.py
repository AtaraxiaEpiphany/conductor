"""Tests for the telemetry substrate + its three feeds (any-job Track 3).

One substrate (the probes registry — no new mechanism), three feeds:

- feed 1 ``label-accuracy`` — cross-track declared-vs-signals agreement, the
  durable consumer of ``quality._persist_label_telemetry``'s init-time store.
- feed 2 ``skill-fires`` — dispatch starts per executor agent (the lifecycle
  ``start`` event's existing ``agent=`` field self-records a persona) joined
  with ``wrapper_skill_for`` so counts read as skill fires.
- feed 3 ``gate-outcomes`` — per-(class, gate) verdict tallies; the store is
  appended at BOTH arms of ``cmd_phase_checkpoint_review`` via
  ``misc._persist_gate_outcomes`` (integration pinned here alongside the
  unit tests).

Env fixtures mirror ``test_probe._EnvIsolated`` (CLAUDE_PROJECT_DIR steers
both the registry overlay walk and ``get_logs_dir``; CLAUDE_PLUGIN_DATA is
popped because it OVERRIDES the project dir in the data-dir ladder).
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import probes
from scripts.track_state.misc import _persist_gate_outcomes
from scripts.track_state.probes import run_probe, probe_names

# Integration fixtures: the git-backed "phase 1 complete, no checkpoint" track
# (same reuse as test_phase_checkpoint_handshake).
from tests.test_step import _phase_complete_track, _head_short
from scripts.track_state.dispatch import (
    cmd_phase_verdict, cmd_phase_checkpoint_review)


def _run(fn, *args):
    """Capture a stamp-only command's stdout JSON."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


class _EnvIsolated(TestCase):
    """Isolate env-driven resolution + clear the probes loader cache."""

    def setUp(self):
        self._old_env = {k: os.environ.get(k)
                         for k in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT",
                                   "CLAUDE_PLUGIN_DATA")}
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        probes._load.cache_clear()

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        probes._load.cache_clear()


def _mk_project():
    """A bare project root with the conductor/tracks/ tree the feeds walk."""
    tmp = tempfile.mkdtemp()
    Path(tmp, "conductor", "tracks").mkdir(parents=True)
    return tmp, Path(tmp)


def _write_store(project: Path, track: str, name: str, payload):
    d = project / "conductor" / "tracks" / track / ".conductor"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_store(track_dir, name):
    return json.loads(
        (Path(track_dir) / ".conductor" / name).read_text(encoding="utf-8"))


# --- feed 3 persistence (unit) ---------------------------------------------------


class PersistGateOutcomesTests(TestCase):
    def _state(self, tasks):
        return {"track_id": "t3", "phases": [{"name": "Phase 1",
                                              "tasks": tasks}]}

    def test_rows_per_class_and_gate(self):
        state = self._state([{"name": "[Docs] Fix typo"},
                             {"name": "Wire the adapter"}])
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _persist_gate_outcomes(d, state, 1, "passed")
        rows = _read_store(d, "gate-outcomes.json")["rows"]
        # Docs owes only checkpoint; untagged resolves the default class
        # (tdd + coverage + checkpoint). 4 rows, all verdict passed.
        self.assertEqual(len(rows), 4)
        by_class = {}
        for r in rows:
            by_class.setdefault(r["class"], []).append(r["gate"])
        self.assertEqual(by_class["docs"], ["checkpoint"])
        self.assertEqual(sorted(by_class["default"]),
                         ["checkpoint", "coverage", "tdd"])
        self.assertTrue(all(r["verdict"] == "passed" for r in rows))
        self.assertTrue(all(r["phase"] == 1 for r in rows))

    def test_append_not_overwrite(self):
        # A FAILED-then-PASSED cycle is two real observations, not a correction.
        state = self._state([{"name": "Patch the parser"}])
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _persist_gate_outcomes(d, state, 1, "failed")
        _persist_gate_outcomes(d, state, 1, "passed")
        doc = _read_store(d, "gate-outcomes.json")
        self.assertEqual(doc["track_id"], "t3")
        self.assertEqual(len(doc["rows"]), 6)  # 3 default gates × 2 verdicts
        verdicts = sorted(r["verdict"] for r in doc["rows"])
        self.assertEqual(verdicts, ["failed", "failed", "failed",
                                    "passed", "passed", "passed"])

    def test_phase_out_of_range_noop(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _persist_gate_outcomes(d, self._state([]), 7, "passed")
        self.assertFalse((Path(d) / ".conductor" / "gate-outcomes.json").exists())

    def test_fail_open_on_unwritable_tree(self):
        # Telemetry must never raise into the checkpoint advance.
        state = self._state([{"name": "Anything"}])
        _persist_gate_outcomes("/nonexistent/track/dir", state, 1, "failed")

    def test_fail_open_on_bad_prior_store(self):
        # A corrupt prior store restarts fresh rather than blocking.
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        cdir = Path(d, ".conductor")
        cdir.mkdir(parents=True)
        (cdir / "gate-outcomes.json").write_text("{not json", encoding="utf-8")
        _persist_gate_outcomes(d, self._state([{"name": "X"}]), 1, "passed")
        rows = _read_store(d, "gate-outcomes.json")["rows"]
        self.assertEqual(len(rows), 3)


# --- feed 3 persistence (integration through the review command) -----------------


class ReviewIntegrationTests(TestCase):
    def _reviewed(self, status):
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        sha = _head_short(d) if status == "PASSED" else None
        return d, _run(cmd_phase_checkpoint_review, d, status, sha, None)

    def test_passed_persists(self):
        d, o = self._reviewed("PASSED")
        self.assertTrue(o["ok"])
        rows = _read_store(d, "gate-outcomes.json")["rows"]
        self.assertEqual(len(rows), 3)  # "Task A" untagged → default class
        self.assertTrue(all(r["verdict"] == "passed" for r in rows))

    def test_failed_persists(self):
        d, o = self._reviewed("FAILED")
        self.assertTrue(o["ok"])
        rows = _read_store(d, "gate-outcomes.json")["rows"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["verdict"] == "failed" for r in rows))

    def test_rejected_stamp_writes_nothing(self):
        # A bad SHA errors before any telemetry — no store, no stray rows.
        d = _phase_complete_track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_phase_verdict, d, "passed", None, None, "passed", "pytest -q")
        o = _run(cmd_phase_checkpoint_review, d, "PASSED", "notahex", None)
        self.assertIn("error", o)
        self.assertFalse((Path(d) / ".conductor" / "gate-outcomes.json").exists())


# --- feed 1: label-accuracy ------------------------------------------------------


class LabelAccuracyProbeTests(_EnvIsolated):
    def _fixture(self):
        tmp, root = _mk_project()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _write_store(root, "alpha", "label-telemetry.json", {
            "track_id": "alpha", "samples": [
                {"task": "P1.T1", "declared": "docs", "suggested": "docs"},
                {"task": "P1.T2", "declared": "docs", "suggested": "config"},
                {"task": "P1.T3", "declared": "untagged", "suggested": "docs"},
            ]})
        _write_store(root, "beta", "label-telemetry.json", {
            "track_id": "beta", "samples": [
                {"task": "P1.T1", "declared": "docs", "suggested": "docs"},
            ]})
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        return root

    def test_aggregates_across_tracks(self):
        self._fixture()
        r = run_probe("label-accuracy")
        self.assertTrue(r["ok"])
        self.assertEqual(r["tracks"], 2)
        self.assertEqual(r["samples"], 4)
        self.assertEqual(r["agree"], 2)
        self.assertEqual(r["disagree"], 2)
        self.assertEqual(r["agreement_rate"], 0.5)
        self.assertEqual(r["per_tag"]["docs"], {"agree": 2, "disagree": 1})
        self.assertEqual(r["per_tag"]["untagged"], {"agree": 0, "disagree": 1})
        # declared untagged + suggested tagged = the false-untagged miss
        self.assertEqual(r["false_untagged"], 1)

    def test_malformed_store_skipped_not_fatal(self):
        root = self._fixture()
        _write_store(root, "gamma", "label-telemetry.json", {"nope": True})
        r = run_probe("label-accuracy")
        self.assertTrue(r["ok"])
        self.assertEqual(r["tracks"], 2)  # gamma contributed nothing

    def test_no_telemetry_fail_open(self):
        tmp, _root = _mk_project()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        r = run_probe("label-accuracy")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no label telemetry recorded")

    def test_no_project_root_fail_open(self):
        r = run_probe("label-accuracy")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no project root located")


# --- feed 2: skill-fires ---------------------------------------------------------


class SkillFiresProbeTests(_EnvIsolated):
    LINES = [
        '2026-09-04T10:00:00Z dispatch_lifecycle event=start session=s1 '
        'agent=task-executor phase=1 task=1 subtask=- marker=- in_flight=- '
        'decision=- head=- had_result=- gen=1',
        '2026-09-04T10:05:00Z dispatch_lifecycle event=stop session=s1 '
        'agent=task-executor phase=1 task=1 subtask=- marker=- in_flight=- '
        'decision=- head=abc1234 had_result=true gen=1',
        '2026-09-04T11:00:00Z dispatch_lifecycle event=start session=s2 '
        'agent=data-wrangler phase=1 task=2 subtask=- marker=- in_flight=- '
        'decision=- head=- had_result=- gen=1',
        '2026-09-04T11:03:00Z dispatch_lifecycle event=start session=s3 '
        'agent=data-wrangler phase=1 task=2 subtask=- marker=- in_flight=- '
        'decision=- head=- had_result=- gen=2',
    ]

    def _fixture(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        logs = Path(tmp, ".conductor", "logs")
        logs.mkdir(parents=True)
        (logs / "dispatch-lifecycle.log").write_text(
            "\n".join(self.LINES) + "\n", encoding="utf-8")
        return tmp

    def test_counts_starts_per_agent(self):
        # Only start events count; stop events are ignored.
        self._fixture()
        r = run_probe("skill-fires")
        self.assertTrue(r["ok"])
        self.assertEqual(r["total_starts"], 3)
        self.assertEqual(r["agents"]["task-executor"]["starts"], 1)
        self.assertEqual(r["agents"]["data-wrangler"]["starts"], 2)

    def test_wrapper_skill_join(self):
        tmp = self._fixture()
        # A project wrapper whose frontmatter preloads a skill — the join the
        # GC rule reads (wrapper fires = skill fires).
        agents = Path(tmp, ".claude", "agents")
        agents.mkdir(parents=True)
        (agents / "data-wrangler.md").write_text(
            "---\nname: data-wrangler\nskills: [data-pipeline]\n---\nbody\n",
            encoding="utf-8")
        r = run_probe("skill-fires")
        self.assertEqual(r["agents"]["data-wrangler"]["skill"], "data-pipeline")
        self.assertIsNone(r["agents"]["task-executor"]["skill"])

    def test_no_log_fail_open(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        r = run_probe("skill-fires")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no dispatch events recorded")

    def test_log_without_starts_fail_open(self):
        tmp = self._fixture()
        (Path(tmp, ".conductor", "logs") / "dispatch-lifecycle.log").write_text(
            "2026-09-04T10:00:00Z unrelated noise\n", encoding="utf-8")
        r = run_probe("skill-fires")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no dispatch start events recorded")


# --- feed 3 probe: gate-outcomes -------------------------------------------------


class GateOutcomesProbeTests(_EnvIsolated):
    def _fixture(self):
        tmp, root = _mk_project()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _write_store(root, "alpha", "gate-outcomes.json", {
            "track_id": "alpha", "rows": [
                {"phase": 1, "class": "default", "gate": "tdd",
                 "verdict": "passed"},
                {"phase": 1, "class": "default", "gate": "coverage",
                 "verdict": "failed"},
            ]})
        _write_store(root, "beta", "gate-outcomes.json", {
            "track_id": "beta", "rows": [
                {"phase": 2, "class": "default", "gate": "tdd",
                 "verdict": "passed"},
                {"phase": 2, "class": "docs", "gate": "checkpoint",
                 "verdict": "passed"},
                # Malformed row skipped by shape, not fatal to the walk.
                {"phase": 2, "gate": "tdd", "verdict": "passed"},
            ]})
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        return root

    def test_aggregates_per_class_gate(self):
        self._fixture()
        r = run_probe("gate-outcomes")
        self.assertTrue(r["ok"])
        self.assertEqual(r["tracks"], 2)
        self.assertEqual(r["rows"], 4)  # malformed row not counted
        self.assertEqual(r["gates"]["default"]["tdd"], {"passed": 2, "failed": 0})
        self.assertEqual(r["gates"]["default"]["coverage"],
                         {"passed": 0, "failed": 1})
        self.assertEqual(r["gates"]["docs"]["checkpoint"],
                         {"passed": 1, "failed": 0})

    def test_no_outcomes_fail_open(self):
        tmp, _root = _mk_project()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        r = run_probe("gate-outcomes")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no gate outcomes recorded")

    def test_no_project_root_fail_open(self):
        r = run_probe("gate-outcomes")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no project root located")


# --- registry wiring ------------------------------------------------------------


class RegistryWiringTests(_EnvIsolated):
    def test_all_three_feeds_registered(self):
        names = probe_names()
        for n in ("label-accuracy", "skill-fires", "gate-outcomes"):
            self.assertIn(n, names)

    def test_baseline_rows_lint_clean(self):
        # The shipped probes.json must lint — dead builtins are load-bearing
        # errors (validate_probes_doc cross-checks probes._BUILTINS).
        from scripts.track_state.registry_validate import validate_probes_doc
        doc = json.loads(
            (Path(__file__).resolve().parent.parent
             / "templates" / "workflow" / "probes.json").read_text(
                 encoding="utf-8"))
        self.assertEqual(validate_probes_doc(doc), [])


if __name__ == "__main__":
    main()
