"""Tests for ``track-state status`` — the code-owned status-report backend.

Retires /conductor:status's hand-parse + prose-aggregation drift: track/phase
status are the authoritative STORED values (never re-derived from tasks — that
derivation WAS the drift), and summary counts / issues / deferred / position are
computed here, never by the model. The skill renders this envelope; it does not
read track-state.json.
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import core
from scripts.track_state.misc import cmd_status


class _Project:
    """Temp project layout: <root>/conductor/{tracks.md, tracks/<id>/...}.

    Mirrors the fixture in test_resolve_track.py so the registry parsing +
    dir-resolution path is exercised identically.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.cond = self.root / "conductor"
        self.tracks_dir = self.cond / "tracks"
        self.tracks_dir.mkdir(parents=True, exist_ok=True)
        self.registry = self.cond / "tracks.md"

    def add_track(self, track_id, state, marker=None, link=None):
        """Create a track dir with track-state.json + a checkbox registry line."""
        td = self.tracks_dir / track_id
        td.mkdir(parents=True, exist_ok=True)
        for f in ("spec.md", "plan.md"):
            (td / f).write_text("x")
        state.setdefault("track_id", track_id)
        state.setdefault("status", "in_progress")
        state.setdefault("phases", [])
        core.save(str(td), state)
        self._line(track_id, state["status"], marker, link)
        return td

    def add_uninit(self, track_id, status="new", marker=None):
        """Dir + spec/plan but NO track-state.json → classify as uninit."""
        td = self.tracks_dir / track_id
        td.mkdir(parents=True, exist_ok=True)
        (td / "spec.md").write_text("x")
        self._line(track_id, status, marker, link=f"conductor/tracks/{track_id}/")

    def add_missing(self, track_id, status="completed"):
        """Registry line whose dir does not exist → classify as missing."""
        self._line(track_id, status, link=f"conductor/tracks/{track_id}/")

    def _line(self, track_id, status, marker=None, link=None):
        m = marker if marker is not None else {
            "new": " ", "in_progress": "~", "completed": "x"}.get(status, " ")
        lp = link or f"conductor/tracks/{track_id}/"
        lines = self.registry.read_text().splitlines() if self.registry.exists() else []
        lines.append(f"- [{m}] {track_id} ({lp})")
        self.registry.write_text("\n".join(lines) + "\n")

    def status(self, query=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_status(query=query, registry_path=str(self.registry))
        return json.loads(buf.getvalue())


def _task(name, status, **extra):
    t = {"name": name, "status": status}
    t.update(extra)
    return t


class TestStatusAllTracks(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_enumerates_and_classifies_every_entry(self):
        self.p.add_track("auth_20260101", {"status": "in_progress"})
        self.p.add_track("docs_20260102", {"status": "completed"})
        self.p.add_uninit("fresh_20260103", status="new")
        self.p.add_missing("ghost_20260104", status="archived")
        r = self.p.status()
        self.assertTrue(r["ok"])
        by_id = {t["track_id"]: t for t in r["tracks"]}
        self.assertEqual(by_id["auth_20260101"]["state"], "loadable")
        self.assertEqual(by_id["auth_20260101"]["status"], "in_progress")
        self.assertEqual(by_id["docs_20260102"]["state"], "loadable")
        self.assertEqual(by_id["fresh_20260103"]["state"], "uninit")
        self.assertEqual(by_id["ghost_20260104"]["state"], "missing")
        # uninit/missing carry no phase detail.
        self.assertEqual(by_id["fresh_20260103"]["phases"], [])
        self.assertEqual(by_id["ghost_20260104"]["phases"], [])

    def test_summary_aggregates_in_code(self):
        self.p.add_track("a_20260101", {
            "status": "in_progress",
            "phases": [{"name": "P1", "status": "in_progress", "tasks": [
                _task("t1", "completed"), _task("t2", "pending")]}]})
        self.p.add_track("b_20260102", {
            "status": "completed",
            "phases": [{"name": "P1", "status": "completed", "tasks": [
                _task("t1", "deferred", defer_reason="manual check")]}]})
        r = self.p.status()
        s = r["summary"]
        self.assertEqual(s["total_tracks"], 2)
        self.assertEqual(s["by_status"], {"in_progress": 1, "completed": 1})
        # 1 completed task of 3 total across both tracks.
        self.assertEqual(s["overall_progress"], {"completed": 1, "total": 3, "pct": 33.3})
        self.assertEqual(s["deferred_count"], 1)


class TestStatusStoredNotDerived(TestCase):
    """The headline drift-retirement: status is the STORED value, never
    re-derived from tasks (the skill's first-match-wins table WAS the drift)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_all_tasks_completed_but_stored_in_progress_stays_in_progress(self):
        self.p.add_track("stale_20260101", {
            "status": "in_progress",  # stored — authoritative
            "phases": [{"name": "P1", "status": "in_progress", "tasks": [
                _task("t1", "completed"), _task("t2", "completed")]}]})
        r = self.p.status()
        t = r["tracks"][0]
        self.assertEqual(t["status"], "in_progress")  # NOT re-derived to "completed"
        self.assertEqual(t["progress"], {"completed": 2, "total": 2})


class TestStatusPerTrackDetail(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)
        self.p.add_track("feat_20260101", {
            "track_id": "feat_20260101",
            "status": "in_progress",
            "type": "feature",
            "workflow_shape": "migration",
            "phases": [
                {"name": "Phase 1", "status": "in_progress", "tasks": [
                    _task("1.1 done", "completed", commit_sha="a1b2c3d"),
                    _task("1.2 failed", "failed", retry_count=1, max_retries=3,
                          last_failure_summary="boom")]},
                {"name": "Phase 2", "status": "pending", "tasks": [
                    _task("2.1 deferred", "deferred", defer_reason="needs manual"),
                    _task("2.2 active", "in_progress"),
                    _task("2.3 blocked", "blocked", skip_analysis="skip-analyst: risky")]},
            ],
        })
        self.t = self.p.status()["tracks"][0]

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_position_points_at_in_progress_task(self):
        pos = self.t["position"]
        self.assertEqual((pos["phase"], pos["task"]), (2, 2))
        self.assertEqual(pos["name"], "2.2 active")

    def test_issues_collect_failed_and_blocked_with_reasons(self):
        kinds = {i["kind"]: i for i in self.t["issues"]}
        self.assertEqual(set(kinds), {"failed", "blocked"})
        self.assertEqual(kinds["failed"]["last_failure_summary"], "boom")
        self.assertEqual(kinds["failed"]["retry_count"], 1)
        self.assertEqual(kinds["failed"]["max_retries"], 3)
        self.assertEqual(kinds["blocked"]["skip_analysis"], "skip-analyst: risky")

    def test_deferred_collects_reason(self):
        self.assertEqual(len(self.t["deferred"]), 1)
        self.assertEqual(self.t["deferred"][0]["reason"], "needs manual")

    def test_progress_and_shape_surface(self):
        self.assertEqual(self.t["progress"], {"completed": 1, "total": 5})
        self.assertEqual(self.t["shape"], "migration")

    def test_phases_carry_reason_fields(self):
        failed = self.t["phases"][0]["tasks"][1]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["last_failure_summary"], "boom")
        blocked = self.t["phases"][1]["tasks"][2]
        self.assertEqual(blocked["skip_analysis"], "skip-analyst: risky")


class TestStatusQueryAndNoRegistry(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_query_reports_single_track(self):
        self.p.add_track("a_20260101", {"status": "in_progress"})
        self.p.add_track("b_20260102", {"status": "completed"})
        r = self.p.status(query="a_20260101")
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["tracks"]), 1)
        self.assertEqual(r["tracks"][0]["track_id"], "a_20260101")

    def test_query_no_match_exits_zero_with_error_envelope(self):
        self.p.add_track("a_20260101", {"status": "in_progress"})
        r = self.p.status(query="nonexistent")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_match")

    def test_no_registry(self):
        # No add_track → conductor/tracks.md was never written; the missing path
        # triggers no_registry.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_status(registry_path=str(self.p.registry))
        r = json.loads(buf.getvalue())
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_registry")


if __name__ == "__main__":
    main()
