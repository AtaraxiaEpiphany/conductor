"""Tests for ``track_state.registry_studio`` — the workflow-studio data layer.

Load-bearing invariants under test:

- **Origins are source-attributed.** A row the overlay FILE declares is
  ``"overlay"``; everything else is ``"baseline"``. The editor's B/O badge and
  the "which file am I editing?" intent both derive from this — if attribution
  drifts, an editor saves to the wrong file.
- **save_registry is the strict gate.** It rejects an invalid fragment AND an
  otherwise-valid fragment whose merge would break the merged result; it never
  writes on rejection. On acceptance it writes atomically, leaves a ``.bak``,
  preserves ``_comment``/``_fields`` doc blocks, and the next read reflects it.
- **list_tracks / set_workflow_shape** are the studio's track-binding surface.
"""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import registry_studio as rs
from scripts.track_state.core import load, save


def _capture(fn, *args, **kwargs):
    """Capture a single-JSON-object stdout. Returns parsed dict."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _proj(tmp, *parts):
    """A path under a temp project's conductor/ tree (creating parent dirs)."""
    p = Path(tmp, *parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _track_dir(tmp, track_id):
    """A conductor/tracks/<id>/ directory (creating it) for list_tracks tests."""
    d = Path(tmp, "conductor", "tracks", track_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_overlay_shapes(tmp, doc):
    """Write a project overlay workflow-shapes.json under the temp project."""
    path = _proj(tmp, "conductor", "workflow", "workflow-shapes.json")
    path.write_text(json.dumps(doc))
    return path


def _make_track_state(track_id="auth", workflow_shape="default", status="in_progress"):
    return {
        "track_id": track_id,
        "type": "feature",
        "status": status,
        "workflow_shape": workflow_shape,
        "current_phase_index": 1,
        "current_task_index": 1,
        "phases": [{"name": "Phase 1", "status": "pending",
                    "tasks": [{"name": "Task A", "status": "pending"}]}],
    }


class NormalizeWhich(TestCase):
    def test_aliases_canonicalize(self):
        self.assertEqual(rs.normalize_which("shapes"), "shapes")
        self.assertEqual(rs.normalize_which("workflow-shapes"), "shapes")
        self.assertEqual(rs.normalize_which("task-types"), "task-types")
        self.assertEqual(rs.normalize_which("tags"), "task-types")
        self.assertEqual(rs.normalize_which("TASK_TYPES"), "task-types")

    def test_none_and_unknown_return_none(self):
        self.assertIsNone(rs.normalize_which(None))
        self.assertIsNone(rs.normalize_which("bogus"))


class LoadWithOrigins(TestCase):
    def test_no_overlay_all_baseline(self):
        tmp = tempfile.mkdtemp()
        snap = rs.load_with_origins("shapes", project_dir=tmp)
        self.assertEqual(snap["which"], "shapes")
        # merged carries only {default, shapes} (the conductor's resolved view —
        # it intentionally drops the _comment/_fields doc blocks the baseline
        # file carries), so compare the two data substructures, not the whole
        # dict.
        self.assertEqual(snap["merged"]["default"], snap["baseline"]["default"])
        self.assertEqual(snap["merged"]["shapes"], snap["baseline"]["shapes"])
        self.assertEqual(snap["overlay"], {})
        # The shipped baseline has a `default` shape; every origin is baseline.
        self.assertIn("default", snap["merged"]["shapes"])
        for key, origin in snap["origins"].items():
            self.assertEqual(origin, "baseline", f"{key} wrongly attributed")

    def test_overlay_shape_is_attributed_overlay(self):
        tmp = tempfile.mkdtemp()
        _write_overlay_shapes(tmp, {
            "shapes": {"my-proj-shape": {
                "nodes": ["explorer", "spec-planner"],
                "verifiers": ["ac-tracer"],
                "gates": ["checkpoint"]}},
        })
        snap = rs.load_with_origins("shapes", project_dir=tmp)
        self.assertEqual(snap["origins"]["my-proj-shape"], "overlay")
        # A baseline shape the overlay did NOT touch stays baseline.
        self.assertEqual(snap["origins"]["default"], "baseline")
        # merged carries the overlay shape.
        self.assertIn("my-proj-shape", snap["merged"]["shapes"])
        # default origin is baseline (overlay did not declare a default).
        self.assertEqual(snap["origins"]["default"], "baseline")

    def test_overlay_default_block_flips_default_origin(self):
        tmp = tempfile.mkdtemp()
        _write_overlay_shapes(tmp, {
            "default": {"verify_policy": "none"},
        })
        snap = rs.load_with_origins("shapes", project_dir=tmp)
        self.assertEqual(snap["origins"]["default"], "overlay")
        # The per-key merge: overlay default wins verify_policy.
        self.assertEqual(snap["merged"]["default"]["verify_policy"], "none")

    def test_unknown_which_raises(self):
        with self.assertRaises(ValueError):
            rs.load_with_origins("bogus")


class SaveRegistryValidation(TestCase):
    def test_rejects_invalid_fragment_writes_nothing(self):
        tmp = tempfile.mkdtemp()
        bad = {"shapes": {"x": {"nodes": ["bogus-agent"]}}}  # unknown node
        res = rs.save_registry("shapes", "overlay", bad, project_dir=tmp)
        self.assertFalse(res["ok"])
        self.assertTrue(res["errors"])
        # Nothing written.
        overlay = Path(tmp, "conductor", "workflow", "workflow-shapes.json")
        self.assertFalse(overlay.exists())

    def test_rejects_overlay_with_no_project_dir(self):
        # No project_dir and (in the plugin repo) no conductor/tracks → no root.
        res = rs.save_registry("shapes", "overlay",
                               {"shapes": {"x": {"nodes": ["explorer"]}}})
        self.assertFalse(res["ok"])
        self.assertTrue(any("project dir" in e for e in res["errors"]))

    def test_rejects_unknown_target(self):
        tmp = tempfile.mkdtemp()
        res = rs.save_registry("shapes", "bogus-target",
                               {"shapes": {}}, project_dir=tmp)
        self.assertFalse(res["ok"])

    def test_rejects_non_object_doc(self):
        tmp = tempfile.mkdtemp()
        res = rs.save_registry("shapes", "overlay", ["not", "an", "object"],
                               project_dir=tmp)
        self.assertFalse(res["ok"])


class SaveRegistryAccept(TestCase):
    def test_accepts_valid_overlay_and_is_reflected_in_read(self):
        tmp = tempfile.mkdtemp()
        frag = {"shapes": {"proj-default": {
            "nodes": ["spec-planner", "task-executor", "phase-checker"],
            "verifiers": ["ac-tracer", "build-runner", "test-runner"],
            "gates": ["tdd", "coverage", "checkpoint"]}}}
        res = rs.save_registry("shapes", "overlay", frag, project_dir=tmp)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["target"], "overlay")
        # File written.
        overlay = Path(tmp, "conductor", "workflow", "workflow-shapes.json")
        self.assertTrue(overlay.exists())
        # The next read reflects it (cache cleared, origin attributed overlay).
        snap = rs.load_with_origins("shapes", project_dir=tmp)
        self.assertEqual(snap["origins"]["proj-default"], "overlay")
        self.assertIn("proj-default", snap["merged"]["shapes"])

    def test_bak_created_on_second_write(self):
        tmp = tempfile.mkdtemp()
        first = {"shapes": {"a-shape": {"nodes": ["explorer"]}}}
        second = {"shapes": {"b-shape": {"nodes": ["explorer"]}}}
        rs.save_registry("shapes", "overlay", first, project_dir=tmp)
        rs.save_registry("shapes", "overlay", second, project_dir=tmp)
        bak = Path(tmp, "conductor", "workflow", "workflow-shapes.json.bak")
        self.assertTrue(bak.exists(), ".bak should exist after second write")
        # .bak holds the FIRST write (the pre-overwrite content).
        bak_doc = json.loads(bak.read_text())
        self.assertIn("a-shape", bak_doc["shapes"])
        self.assertNotIn("b-shape", bak_doc["shapes"])

    def test_preserves_comment_and_fields_when_omitted(self):
        tmp = tempfile.mkdtemp()
        with_comment = {
            "_comment": "project overlay",
            "shapes": {"a-shape": {"nodes": ["explorer"]}},
        }
        rs.save_registry("shapes", "overlay", with_comment, project_dir=tmp)
        # A subsequent save that OMITS _comment must not strip it.
        without_comment = {"shapes": {"b-shape": {"nodes": ["explorer"]}}}
        rs.save_registry("shapes", "overlay", without_comment, project_dir=tmp)
        overlay = Path(tmp, "conductor", "workflow", "workflow-shapes.json")
        doc = json.loads(overlay.read_text())
        self.assertEqual(doc["_comment"], "project overlay")
        self.assertIn("b-shape", doc["shapes"])

    def test_accepts_valid_task_types_overlay(self):
        tmp = tempfile.mkdtemp()
        frag = {"tags": {"K8sRollout": {
            "route": "executor", "tdd_exempt": True, "coverage_exempt": True}}}
        res = rs.save_registry("task-types", "overlay", frag, project_dir=tmp)
        self.assertTrue(res["ok"], res)
        snap = rs.load_with_origins("task-types", project_dir=tmp)
        self.assertEqual(snap["origins"]["K8sRollout"], "overlay")


class ListTracks(TestCase):
    def test_lists_tracks_in_project(self):
        tmp = tempfile.mkdtemp()
        tdir = _track_dir(tmp, "auth")
        save(str(tdir), _make_track_state(track_id="auth",
                                          workflow_shape="migration"))
        tracks = rs.list_tracks(project_dir=tmp)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["track_id"], "auth")
        self.assertEqual(tracks[0]["workflow_shape"], "migration")
        self.assertEqual(tracks[0]["status"], "in_progress")

    def test_no_project_returns_empty(self):
        self.assertEqual(rs.list_tracks(project_dir=None), [])

    def test_skips_unreadable_track_state(self):
        tmp = tempfile.mkdtemp()
        good = _track_dir(tmp, "good")
        save(str(good), _make_track_state(track_id="good"))
        bad = _track_dir(tmp, "bad")
        (bad / "track-state.json").write_text("{not json")
        tracks = rs.list_tracks(project_dir=tmp)
        ids = [t["track_id"] for t in tracks]
        self.assertIn("good", ids)
        self.assertNotIn("bad", ids)


class SetWorkflowShape(TestCase):
    def _track(self):
        d = tempfile.mkdtemp()
        Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
        save(d, _make_track_state())
        return d

    def test_rejects_unknown_shape(self):
        d = self._track()
        res = rs.set_workflow_shape(d, "bogus-shape")
        self.assertFalse(res["ok"])
        self.assertIn("error", res)
        # State unchanged.
        self.assertEqual(load(d).get("workflow_shape", "default"), "default")

    def test_accepts_known_shape_and_persists(self):
        d = self._track()
        res = rs.set_workflow_shape(d, "migration")
        self.assertTrue(res["ok"])
        self.assertEqual(res["previous"], "default")
        self.assertEqual(res["workflow_shape"], "migration")
        # Persisted to disk.
        self.assertEqual(load(d)["workflow_shape"], "migration")


class CmdWrappers(TestCase):
    def test_cmd_registry_json_emits_snapshot(self):
        tmp = tempfile.mkdtemp()
        out = _capture(rs.cmd_registry_json, "shapes", project_dir=tmp)
        self.assertEqual(out["which"], "shapes")
        self.assertIn("merged", out)

    def test_cmd_registry_json_unknown_which_emits_error(self):
        out = _capture(rs.cmd_registry_json, "bogus")
        self.assertFalse(out["ok"])

    def test_cmd_registry_save_reads_stdin(self):
        tmp = tempfile.mkdtemp()
        frag = {"shapes": {"z": {"nodes": ["explorer"]}}}
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(frag))
        try:
            out = _capture(rs.cmd_registry_save, "shapes", "overlay",
                           project_dir=tmp)
        finally:
            sys.stdin = old
        self.assertTrue(out["ok"], out)

    def test_cmd_registry_save_rejects_bad_json(self):
        tmp = tempfile.mkdtemp()
        old = sys.stdin
        sys.stdin = io.StringIO("{not json")
        try:
            out = _capture(rs.cmd_registry_save, "shapes", "overlay",
                           project_dir=tmp)
        finally:
            sys.stdin = old
        self.assertFalse(out["ok"])


class ReadOnlyRegistries(TestCase):
    """B2 — agent-roster + probes render as studio VIEWERS.

    Reads flow through the same origins machinery (B/O badges, merged view);
    writes are rejected naming the sanctioned mutation surface. Their
    baselines carry no top-level ``default`` block, which must not trip the
    structural baseline read (the ``default_required`` gate is registry-aware).
    """

    def test_normalize_which_aliases(self):
        for alias in ("agent-roster", "agent_roster", "roster", "agents", "agent"):
            self.assertEqual(rs.normalize_which(alias), "agent-roster")
        for alias in ("probes", "probe"):
            self.assertEqual(rs.normalize_which(alias), "probes")

    def test_load_agent_roster_no_default_block_required(self):
        # The shipped baseline has no top-level default; the read must accept
        # the FILE (not fail-open to _FALLBACK) — the viewer shows real rows.
        tmp = tempfile.mkdtemp()
        snap = rs.load_with_origins("agent-roster", project_dir=tmp)
        self.assertEqual(snap["which"], "agent-roster")
        self.assertIn("phase-checker", snap["baseline"]["agents"])
        self.assertIn("phase-checker", snap["merged"]["agents"])
        self.assertEqual(snap["origins"]["phase-checker"], "baseline")
        self.assertEqual(snap["overlay"], {})

    def test_load_probes_with_origins(self):
        tmp = tempfile.mkdtemp()
        snap = rs.load_with_origins("probes", project_dir=tmp)
        self.assertEqual(snap["which"], "probes")
        self.assertIn("gate-outcomes", snap["merged"]["probes"])
        self.assertEqual(snap["origins"]["gate-outcomes"], "baseline")

    def test_overlay_agent_row_attributed_overlay(self):
        tmp = tempfile.mkdtemp()
        path = _proj(tmp, "conductor", "workflow", "agent-roster.json")
        path.write_text(json.dumps({"agents": {"my-probe-agent": {
            "class": "verifier", "fence": "none", "recovery": "fail"}}}))
        snap = rs.load_with_origins("agent-roster", project_dir=tmp)
        self.assertEqual(snap["origins"]["my-probe-agent"], "overlay")
        self.assertIn("my-probe-agent", snap["merged"]["agents"])
        # A baseline row the overlay did not touch stays baseline.
        self.assertEqual(snap["origins"]["phase-checker"], "baseline")

    def test_save_registry_rejects_read_only_with_hint(self):
        tmp = tempfile.mkdtemp()
        # The read_only gate fires before validation, target, and doc checks —
        # an attacker-ish payload must still only ever see the rejection.
        cases = (("agent-roster", {"agents": {"x": {"class": "spine"}}}, "roster add"),
                 ("probes", {"probes": {"x": {"kind": "test"}}}, "probes.json"))
        for which, doc, hint_frag in cases:
            res = rs.save_registry(which, "overlay", doc, project_dir=tmp)
            self.assertFalse(res["ok"], which)
            self.assertTrue(any("read-only" in e for e in res["errors"]), which)
            self.assertTrue(any(hint_frag in e for e in res["errors"]), which)
        # Nothing written for either registry.
        self.assertFalse(
            Path(tmp, "conductor", "workflow", "agent-roster.json").exists())
        self.assertFalse(
            Path(tmp, "conductor", "workflow", "probes.json").exists())

    def test_cmd_registry_json_serves_read_only_registries(self):
        # The read path is generic — the CLI wrapper gets both for free.
        tmp = tempfile.mkdtemp()
        for which, key, probe_row in (("agent-roster", "agents", "phase-checker"),
                                      ("probes", "probes", "gate-outcomes")):
            snap = _capture(rs.cmd_registry_json, which, project_dir=tmp)
            self.assertEqual(snap["which"], which, which)
            self.assertIn(probe_row, snap["merged"][key], which)


if __name__ == "__main__":
    main()
