"""Tests for ``track_state.shape_studio`` — the stdlib HTTP server.

The server is the WRITE surface a browser hits, so the test pins the security +
correctness contract the plan locked:

- **127.0.0.1 binding** (never 0.0.0.0).
- **GET** paths: ``/`` (HTML), ``/api/registry``, ``/api/resolve?shape=``,
  ``/api/resolve?track=`` (carries a studio-only ``studio.task_cards``
  enrichment the dashboard ignores), ``/api/tracks``, ``/api/nodes`` (the
  6-agent legend), ``/api/task-profile?tag=`` (one tag's resolved profile,
  fail-soft on unknown), ``/api/state`` (carries ``default_target`` + ``theme``).
- **POST** paths: ``/api/registry/save`` (valid → ok, invalid → 400 + writes
  nothing), ``/api/track/shape`` (valid → ok, bad/traversal track_dir → 400).
- A save round-trips: after a valid overlay save, ``/api/registry`` reflects it
  with origin ``overlay``.
- The track-binding endpoint refuses a path-traversal ``track_dir`` and a dir
  that is not under the project's ``conductor/tracks/`` tree.
"""
import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import shape_studio as ss
from scripts.track_state.core import save


def _track_state(track_id="auth", workflow_shape="default"):
    return {
        "track_id": track_id, "type": "feature", "status": "in_progress",
        "workflow_shape": workflow_shape, "current_phase_index": 1,
        "current_task_index": 1,
        "phases": [{"name": "Phase 1", "status": "pending",
                    "tasks": [{"name": "Task A", "status": "pending"}]}],
    }


class _Server:
    """A studio server bound to 127.0.0.1:0 in a temp project, on a daemon thread."""

    def __init__(self, project_dir):
        self.project_dir = project_dir
        self.httpd = ss._ThreadingServer(("127.0.0.1", 0), ss._Handler)
        self.httpd.studio = ss._StudioState(project_dir)
        self.host, self.port = self.httpd.server_address[:2]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self):
        return f"http://{self.host}:{self.port}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


def _get(base, path):
    # urllib raises HTTPError on 4xx/5xx rather than returning; collapse both
    # paths so a 400 (the rejection-under-test) reads like a 200 would.
    try:
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def _get_json(base, path):
    status, body = _get(base, path)
    return status, json.loads(body)


def _post(base, path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class Binding(TestCase):
    def test_binds_loopback_not_wildcard(self):
        with _Server(tempfile.mkdtemp()) as srv:  # type: ignore
            self.assertEqual(srv.host, "127.0.0.1")


class GetPaths(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv = _Server(self.tmp)

    def tearDown(self):
        self.srv.stop()

    def test_root_returns_html(self):
        status, body = _get(self.srv.base, "/")
        self.assertEqual(status, 200)
        self.assertIn("Workflow Studio", body)

    def test_registry_shapes_snapshot(self):
        status, snap = _get_json(self.srv.base, "/api/registry?which=shapes")
        self.assertEqual(status, 200)
        self.assertEqual(snap["which"], "shapes")
        self.assertIn("merged", snap)
        self.assertIn("origins", snap)
        self.assertIn("vocab", snap)
        self.assertIn("nodes", snap["vocab"]["list_fields"])

    def test_registry_bad_which_is_400(self):
        status, body = _get_json(self.srv.base, "/api/registry?which=bogus")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_resolve_shape_default(self):
        status, g = _get_json(self.srv.base, "/api/resolve?shape=default")
        self.assertEqual(status, 200)
        self.assertEqual(g["nodes"], ["spec-planner", "task-executor", "phase-checker"])
        self.assertIn("ac-tracer", g["verifiers"])

    def test_resolve_requires_param(self):
        status, body = _get_json(self.srv.base, "/api/resolve")
        self.assertEqual(status, 400)

    def test_tracks_empty(self):
        status, body = _get_json(self.srv.base, "/api/tracks")
        self.assertEqual(status, 200)
        self.assertEqual(body["tracks"], [])

    def test_state_endpoint(self):
        status, body = _get_json(self.srv.base, "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(body["default_target"], "overlay")
        self.assertEqual(body["theme"], "system")


class ResolveTrack(TestCase):
    def test_track_resolve_and_validation(self):
        tmp = tempfile.mkdtemp()
        tdir = Path(tmp, "conductor", "tracks", "auth")
        tdir.mkdir(parents=True)
        save(str(tdir), _track_state(workflow_shape="migration"))
        with _Server(tmp) as srv:
            status, env = _get_json(srv.base,
                                    "/api/resolve?track=" + str(tdir))
            self.assertEqual(status, 200)
            self.assertEqual(env["resolved_workflow"]["shape"], "migration")

    def test_track_resolve_rejects_nonexistent(self):
        tmp = tempfile.mkdtemp()
        with _Server(tmp) as srv:
            status, body = _get_json(srv.base,
                                     "/api/resolve?track=" +
                                     str(Path(tmp, "conductor", "tracks", "nope")))
            self.assertEqual(status, 400)

    def test_track_resolve_carries_studio_task_cards(self):
        # The studio-only enrichment: every task carries its leading tag's
        # resolved profile (workflow prose + exemptions) inline, so the
        # whole-track map renders without a per-task round-trip.
        tmp = tempfile.mkdtemp()
        tdir = Path(tmp, "conductor", "tracks", "auth")
        tdir.mkdir(parents=True)
        state = {
            "track_id": "auth", "type": "feature", "status": "in_progress",
            "workflow_shape": "migration", "current_phase_index": 1,
            "current_task_index": 1,
            "phases": [{"name": "Phase 1", "status": "in_progress", "tasks": [
                {"name": "[Migrate] Rename foo to bar", "status": "in_progress"},
                {"name": "Plain untagged task", "status": "pending"},
            ]}],
        }
        save(str(tdir), state)
        with _Server(tmp) as srv:
            status, env = _get_json(srv.base,
                                    "/api/resolve?track=" + str(tdir))
            self.assertEqual(status, 200)
            cards = env["studio"]["task_cards"]
            self.assertEqual(len(cards), 2)
            migrate = cards[0]
            self.assertEqual(migrate["tag"], "Migrate")
            self.assertTrue(migrate["known"])
            # The bespoke workflow's single home is the steps-library docfile
            # (workflow-as-data): the card carries the docfile NAME, and the
            # inline `workflow` string stays empty (two homes = drift).
            self.assertEqual(migrate["workflow_doc"], "migrate.md")
            self.assertEqual(migrate["workflow"], "")
            self.assertFalse(migrate["coverage_exempt"])
            self.assertEqual(migrate["phase"], 1)
            self.assertEqual(migrate["task"], 1)
            self.assertIsNone(migrate["subtask"])
            plain = cards[1]
            self.assertIsNone(plain["tag"])          # no brackets → no tag
            self.assertFalse(plain["known"])
            self.assertEqual(plain["workflow"], "")   # default TDD
            self.assertEqual(plain["workflow_doc"], "")


class SaveEndpoint(TestCase):
    def test_valid_overlay_save_round_trips(self):
        tmp = tempfile.mkdtemp()
        frag = {"shapes": {"studio-e2e": {
            "nodes": ["explorer", "spec-planner"],
            "verifiers": ["ac-tracer"], "gates": ["checkpoint"],
            "ac_grounding": "review"}}}
        with _Server(tmp) as srv:
            status, res = _post(srv.base, "/api/registry/save",
                                {"which": "shapes", "target": "overlay", "doc": frag})
            self.assertEqual(status, 200)
            self.assertTrue(res["ok"], res)
            # The next registry read reflects it, attributed overlay.
            _, snap = _get_json(srv.base, "/api/registry?which=shapes")
            self.assertEqual(snap["origins"]["studio-e2e"], "overlay")
            self.assertIn("studio-e2e", snap["merged"]["shapes"])

    def test_invalid_overlay_save_is_400_and_writes_nothing(self):
        tmp = tempfile.mkdtemp()
        bad = {"shapes": {"x": {"nodes": ["bogus-agent"]}}}
        with _Server(tmp) as srv:
            status, res = _post(srv.base, "/api/registry/save",
                                {"which": "shapes", "target": "overlay", "doc": bad})
            self.assertEqual(status, 400)
            self.assertFalse(res["ok"])
            self.assertTrue(res["errors"])
            # Nothing written.
            self.assertFalse(
                Path(tmp, "conductor", "workflow", "workflow-shapes.json").exists())

    def test_save_rejects_bad_target(self):
        tmp = tempfile.mkdtemp()
        with _Server(tmp) as srv:
            status, res = _post(srv.base, "/api/registry/save",
                                {"which": "shapes", "target": "bogus",
                                 "doc": {"shapes": {}}})
            self.assertEqual(status, 400)

    def test_save_rejects_non_dict_doc(self):
        tmp = tempfile.mkdtemp()
        with _Server(tmp) as srv:
            status, res = _post(srv.base, "/api/registry/save",
                                {"which": "shapes", "target": "overlay", "doc": []})
            self.assertEqual(status, 400)


class TrackShapeBinding(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tdir = Path(self.tmp, "conductor", "tracks", "auth")
        self.tdir.mkdir(parents=True)
        save(str(self.tdir), _track_state())
        self.srv = _Server(self.tmp)

    def tearDown(self):
        self.srv.stop()

    def test_bind_known_shape(self):
        status, res = _post(self.srv.base, "/api/track/shape",
                            {"track_dir": str(self.tdir), "shape": "migration"})
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])
        self.assertEqual(res["workflow_shape"], "migration")

    def test_bind_unknown_shape_rejected(self):
        status, res = _post(self.srv.base, "/api/track/shape",
                            {"track_dir": str(self.tdir), "shape": "bogus"})
        self.assertEqual(status, 400)
        self.assertFalse(res["ok"])

    def test_traversal_track_dir_rejected(self):
        status, res = _post(self.srv.base, "/api/track/shape",
                            {"track_dir": str(Path(self.tmp, "..", "..", "etc")),
                             "shape": "default"})
        self.assertEqual(status, 400)

    def test_track_dir_outside_project_rejected(self):
        # A real track-state.json, but in a DIFFERENT project tree.
        other = tempfile.mkdtemp()
        odir = Path(other, "conductor", "tracks", "x")
        odir.mkdir(parents=True)
        save(str(odir), _track_state())
        status, res = _post(self.srv.base, "/api/track/shape",
                            {"track_dir": str(odir), "shape": "default"})
        self.assertEqual(status, 400)


class AutoDetectContainment(TestCase):
    """When ``--project-dir`` is omitted, ``_validate_track_dir`` must STILL
    enforce project containment — it resolves the project root via the same
    ``workflow_shapes._project_root`` ladder the registry ops use ($CLAUDE_PROJECT_DIR
    → cwd-with-tracks). Without this, a studio started with no --project-dir would
    accept any track-state.json-bearing dir on the host (the containment promised
    in the docstring was enforced only for the explicit-dir case)."""

    def _make_track(self, project):
        tdir = Path(project, "conductor", "tracks", "x")
        tdir.mkdir(parents=True)
        save(str(tdir), _track_state())
        return tdir

    def test_auto_detect_rejects_track_outside_resolved_project(self):
        proj_a = tempfile.mkdtemp()
        proj_b = tempfile.mkdtemp()
        track_in_a = self._make_track(proj_a)
        track_in_b = self._make_track(proj_b)  # in a DIFFERENT project tree
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": proj_a}):
            # project_dir=None → auto-detect resolves to proj_a.
            self.assertIsNone(ss._validate_track_dir(str(track_in_b), None))
            # A track under the auto-detected project is still accepted.
            self.assertEqual(ss._validate_track_dir(str(track_in_a), None),
                             track_in_a.resolve())

    def test_explicit_project_dir_still_governs(self):
        # Explicit project_dir wins and is NOT overridden by CLAUDE_PROJECT_DIR.
        proj_a = tempfile.mkdtemp()
        proj_b = tempfile.mkdtemp()
        track_in_b = self._make_track(proj_b)
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": proj_a}):
            self.assertIsNone(
                ss._validate_track_dir(str(track_in_b), proj_a))


class NodesEndpoint(TestCase):
    """``/api/nodes`` — the 6-agent legend (display prose only)."""

    def setUp(self):
        self.srv = _Server(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.stop()

    def test_returns_all_seven_agents(self):
        status, body = _get_json(self.srv.base, "/api/nodes")
        self.assertEqual(status, 200)
        nodes = body["nodes"]
        self.assertEqual(set(nodes), {"spec-planner", "explorer",
                                      "task-executor", "phase-checker",
                                      "ac-tracer", "build-runner", "test-runner"})
        for name, d in nodes.items():
            self.assertIn(d["kind"], ("spine", "verifier"), name)
            self.assertTrue(d["role"], name)
            self.assertTrue(d["produces"], name)
        # the four spine nodes are kind=spine; the three verifiers are kind=verifier
        self.assertEqual(sorted(n for n, d in nodes.items() if d["kind"] == "spine"),
                         ["explorer", "phase-checker", "spec-planner", "task-executor"])
        self.assertEqual(sorted(n for n, d in nodes.items() if d["kind"] == "verifier"),
                         ["ac-tracer", "build-runner", "test-runner"])


class TaskProfileEndpoint(TestCase):
    """``/api/task-profile?tag=`` — one tag's resolved profile, fail-soft."""

    def setUp(self):
        self.srv = _Server(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.stop()

    def test_known_tag_migrate_resolves(self):
        status, prof = _get_json(self.srv.base, "/api/task-profile?tag=Migrate")
        self.assertEqual(status, 200)
        self.assertTrue(prof["known"])
        self.assertEqual(prof["route"], "executor")
        self.assertFalse(prof["tdd_exempt"])
        self.assertFalse(prof["coverage_exempt"])
        # The bespoke workflow's single home is the docfile (workflow-as-data);
        # the inline `workflow` string stays empty.
        self.assertEqual(prof["workflow_doc"], "migrate.md")
        self.assertEqual(prof["workflow"], "")
        # [Migrate] is opt-in (authored, never goal-detected).
        self.assertFalse(prof["auto_propose"])

    def test_unknown_tag_fails_soft_not_500(self):
        status, prof = _get_json(self.srv.base,
                                 "/api/task-profile?tag=NoSuchTag")
        self.assertEqual(status, 200)            # fail-soft, never a 500
        self.assertFalse(prof["known"])
        self.assertEqual(prof["workflow"], "")   # default fallback → no prose
        self.assertEqual(prof["route"], "executor")

    def test_blank_tag_fails_soft(self):
        status, prof = _get_json(self.srv.base, "/api/task-profile?tag=")
        self.assertEqual(status, 200)
        self.assertFalse(prof["known"])
        self.assertEqual(prof["workflow"], "")


class CheckpointPolicyControlSurfaceTests(TestCase):
    """Track C3 — ``checkpoint_policy`` is the 3rd drives-dispatch field (after
    verifiers + gates). The Shape Studio's honest control surface must surface
    it as load-bearing (so an editor sees it changes dispatch), expose its vocab
    in the dynamic form, and resolve it per-shape. Direct unit tests — no server."""

    def test_effects_marks_checkpoint_policy_as_drives(self):
        cls, _ = ss._SHAPE_FIELD_EFFECTS["checkpoint_policy"]
        self.assertEqual(cls, "drives")

    def test_load_bearing_auto_derives_checkpoint_policy(self):
        # The load_bearing list is derived from _SHAPE_FIELD_EFFECTS (cls ==
        # "drives") — adding checkpoint_policy AND ac_grounding as drives MUST
        # surface them here without a second edit (the no-drift taxonomy).
        # ac_grounding is load-bearing: the build-tier cross-field guard keys on
        # it (test-grounded REQUIRES build-runner), and it is the declared
        # substitute that lets a skip-if-declared freedom waive the checkpoint.
        self.assertIn("checkpoint_policy", ss._vocab()["shapes"]["load_bearing"])
        self.assertIn("ac_grounding", ss._vocab()["shapes"]["load_bearing"])
        # verifiers + gates + checkpoint_policy + ac_grounding + planning_doc +
        # signals + max_retries = the seven drives. planning_doc
        # (planning-as-data) drives the planning layer; signals drives it too
        # since Phase B — propose-shape ranks the description against them
        # (new-track §2.1's selection step); max_retries drives the retry
        # chain (task > shape > global).
        self.assertEqual(
            set(ss._vocab()["shapes"]["load_bearing"]),
            {"verifiers", "gates", "checkpoint_policy", "ac_grounding",
             "planning_doc", "signals", "max_retries"})

    def test_vocab_exposes_checkpoint_policy_scalar(self):
        self.assertEqual(
            ss._vocab()["shapes"]["scalar_fields"]["checkpoint_policy"],
            ["run", "skip-if-declared"])

    def test_shape_graph_resolves_checkpoint_policy(self):
        g = ss._shape_graph("default")
        self.assertEqual(g["checkpoint_policy"], "run")

    def test_field_guide_names_all_drives_fields(self):
        # The Field Guide HTML is a module-level string literal — scan the source
        # so the assertion survives the HTML living in any module string. The
        # guide must name ALL drives fields: verifiers + gates + checkpoint_policy
        # + ac_grounding (the OLD "ONLY verifiers and gates" claim is gone, and
        # ac_grounding is no longer mislabeled display/reference).
        import inspect
        src = inspect.getsource(ss)
        for field in ("checkpoint_policy", "ac_grounding"):
            self.assertIn(field, src)
        # The pre-C3 misleading fragment (verifiers "and <code>gates</code>"
        # followed by "ONLY ... fields that change behavior") is gone.
        self.assertNotIn(
            "run at the checkpoint) and <code>gates</code>", src)
        # ac_grounding must NOT be lumped into the display/reference list anymore.
        self.assertNotIn(
            "ac_grounding</code> are display/reference", src)


class MaxRetriesControlSurfaceTests(TestCase):
    """The shape-level ``max_retries`` field's studio surface: an int field
    (number input, not a vocab dropdown), resolved per-shape in the graph, and
    honest-effects-badged as drives (it changes the retry chain). Direct unit
    tests — no server."""

    def test_effects_marks_max_retries_as_drives(self):
        cls, _ = ss._SHAPE_FIELD_EFFECTS["max_retries"]
        self.assertEqual(cls, "drives")

    def test_vocab_exposes_int_fields(self):
        self.assertEqual(ss._vocab()["shapes"]["int_fields"], ["max_retries"])

    def test_shape_graph_carries_max_retries(self):
        # 0 = inherit the global (the frontend renders the effective value
        # with that fallback).
        self.assertEqual(ss._shape_graph("default")["max_retries"], 0)

    def test_save_cache_survives_endpoint_read(self):
        # The staleness regression the plan suspected: a save must be visible
        # to the same process's subsequent accessor reads. save_registry owns
        # the cache clear (rs._cache_clear) — pin it end-to-end through the
        # studio server: save an overlay budget, then resolve the shape.
        # _shape_graph reads the ws accessors, whose project ladder is
        # $CLAUDE_PROJECT_DIR-first — pin it (the studio's --project-dir pin
        # feeds rs.* which take it explicitly; the accessor ladder needs env).
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        prior = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        def _restore():
            if prior is None:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = prior
            from scripts.track_state import workflow_shapes as _ws
            _ws._load.cache_clear()
        self.addCleanup(_restore)
        with _Server(proj) as srv:
            status, body = _post(srv.base, "/api/registry/save", {
                "which": "shapes", "target": "overlay",
                "doc": {"shapes": {"k8s-rollout": {
                    "nodes": ["task-executor"], "max_retries": 1}}},
            })
            self.assertEqual(status, 200, body)
            self.assertTrue(body["ok"], body)
            status, g = _get_json(srv.base, "/api/resolve?shape=k8s-rollout")
            self.assertEqual(status, 200)
            self.assertEqual(g["max_retries"], 1,
                             "post-save resolve must see the saved budget "
                             "(lru_cache staleness)")

    def test_save_rejects_bad_max_retries(self):
        # The strict-write gate: a non-int/negative budget is rejected at
        # save, writes nothing.
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        with _Server(proj) as srv:
            status, body = _post(srv.base, "/api/registry/save", {
                "which": "shapes", "target": "overlay",
                "doc": {"shapes": {"bad-budget": {
                    "nodes": ["task-executor"], "max_retries": "lots"}}},
            })
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
            self.assertTrue(any("max_retries" in e for e in body.get("errors", [])),
                            body)


if __name__ == "__main__":
    main()
