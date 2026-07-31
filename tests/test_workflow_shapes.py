"""Wiring tests for the workflow-shape registry (the third axis).

The workflow-shape axis graduated to a data-driven registry
(``workflow-shapes.json`` + ``workflow_shapes.py``, baseline ⊕ project overlay)
— mirroring the task-type and verify-mode registries. Adding a shape is now one
JSON row with zero Python edits: ``SHAPES_VOCAB``/``nodes_for``/
``verify_policy_for``/``resolve_shape`` all derive from it, dispatch surfaces a
``shape_violation`` for an off-topology action, and ``registry-doc`` renders it.
These tests pin that contract:

- the registry data file exists and carries every shape's topology;
- ``SHAPES_VOCAB``/``nodes_for``/``verify_policy_for``/``instruction_for`` flow;
- the project overlay layer (``conductor/workflow/workflow-shapes.json``)
  adds/overrides a shape with ZERO plugin edits, fail-open on malformed overlay;
- ``resolve_shape`` fails open to ``default`` on unknown/absent;
- the dispatch constraint surfaces a ``shape_violation`` off-topology.

Mirrors ``test_verify_mode_profiles.py`` (overlay discipline + drift guard).
"""
import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import workflow_shapes as ws

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "templates" / "workflow" / "workflow-shapes.json"

# The baseline shapes the registry ships.
BASELINE_SHAPES = ("default", "research-first")


class RegistryShapeTests(TestCase):
    def setUp(self):
        self.assertTrue(REGISTRY.exists(), "workflow-shapes.json must exist")

    def test_registry_carries_baseline_shapes(self):
        for shape in BASELINE_SHAPES:
            self.assertIn(shape, ws.SHAPES_VOCAB(),
                          f"baseline shape {shape!r} missing from registry")

    def test_every_shape_carries_topology(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for shape, prof in data["shapes"].items():
            for field in ("nodes", "verify_policy", "stop_condition"):
                self.assertIn(
                    field, prof,
                    f"registry shape [{shape}] missing required field '{field}'",
                )
            self.assertIsInstance(prof["nodes"], list,
                                  f"shape [{shape}] nodes must be a list")
            self.assertGreaterEqual(len(prof["nodes"]), 1,
                                    f"shape [{shape}] needs >=1 node")

    def test_default_topology_is_the_canonical_loop(self):
        # The default shape IS the loop the conductor has always run — declared,
        # not hardcoded. Pin the exact node sequence so a regression is caught.
        self.assertEqual(ws.nodes_for("default"),
                         ("spec-planner", "task-executor", "phase-checker"))
        self.assertEqual(ws.verify_policy_for("default"), "checkpoint")
        self.assertEqual(ws.stop_condition_for("default"), "all_nodes_done")

    def test_research_first_runs_explorer_before_planner(self):
        # The proof shape: explorer FIRST, then planner, then executor; no
        # checkpoint gate (exploration is not a committable artifact).
        self.assertEqual(ws.nodes_for("research-first"),
                         ("explorer", "spec-planner", "task-executor"))
        self.assertEqual(ws.verify_policy_for("research-first"), "none")

    def test_instruction_for_returns_registry_prose(self):
        # instruction_for is the mirror of task-type workflow_for / verify-mode
        # protocol_for: shape-level prose. ADVISORY TODAY — it is rendered by
        # registry-doc --shape for reference but NOT injected into the orchestrator
        # prompt, and dispatch does not reorder to honor it. Pin the honest
        # literals: the intent (explorer before planner) + the advisory caveat.
        instr = ws.instruction_for("research-first")
        self.assertIn("explorer", instr)
        self.assertIn("before spec-planner", instr)
        self.assertIn("ADVISORY", instr)
        self.assertIn("NOT injected", instr)

    def test_vocab_matches_registry_keys(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(set(ws.SHAPES_VOCAB()), set(data["shapes"].keys()))


class VerifiersForTests(TestCase):
    """``verifiers_for(shape)`` is the LOAD-BEARING seam — the dispatch
    checkpoint fan-out iterates it (the third and fourth axes joined at the
    checkpoint). Distinct from ``nodes_for`` (the SPINE topology): verifiers are
    checkpoint *children*, never spine nodes."""

    def test_default_shape_fans_out_standard_pair(self):
        # Byte-identical to the pre-#4 hardcoded [ac-tracer, test-runner] pair.
        self.assertEqual(ws.verifiers_for("default"),
                         ("ac-tracer", "test-runner"))

    def test_research_first_also_fans_out_standard_pair(self):
        # research-first swaps the SPINE (explorer-first) but fans out the SAME
        # verifiers at its checkpoint.
        self.assertEqual(ws.verifiers_for("research-first"),
                         ("ac-tracer", "test-runner"))

    def test_unknown_shape_fails_open_to_default_pair(self):
        # A typo / unregistered shape must NOT block the fan-out — fall back to
        # the default shape's verifiers (the standard pair).
        self.assertEqual(ws.verifiers_for("typo-shape"),
                         ("ac-tracer", "test-runner"))

    def test_fields_documents_verifiers(self):
        # The `verifiers` field is documented in the registry's _fields block
        # (distinct from `nodes`) — the contract it makes with project overlays.
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertIn("verifiers", data["_fields"])
        self.assertIn("checkpoint CHILDREN", data["_fields"]["verifiers"])

    def test_every_shape_carries_verifiers(self):
        # Both shipped shapes declare `verifiers` explicitly (honest data, not
        # implicit) — pin the topology rows carry the field.
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for shape, prof in data["shapes"].items():
            self.assertIn("verifiers", prof,
                          f"shape [{shape}] must declare its `verifiers`")


class ResolveShapeTests(TestCase):
    """resolve_shape is the fail-open chokepoint the dispatch spine reads."""

    def test_known_shape_resolves(self):
        self.assertEqual(ws.resolve_shape("default"), "default")
        self.assertEqual(ws.resolve_shape("research-first"), "research-first")

    def test_unknown_shape_falls_back_to_default(self):
        # A typo / an unregistered project shape must NOT block dispatch.
        self.assertEqual(ws.resolve_shape("nonexistent"), "default")

    def test_absent_falls_back_to_default(self):
        self.assertEqual(ws.resolve_shape(None), "default")
        self.assertEqual(ws.resolve_shape(""), "default")


class OverrideLayerTests(TestCase):
    """The project-local override layer: a project drops
    ``conductor/workflow/workflow-shapes.json`` and its shapes flow through the
    full pipeline with ZERO plugin edits — plugin baseline ⊕ project overlay,
    project wins conflicts.

    Mirrors test_verify_mode_profiles.py::OverrideLayerTests.
    """

    def setUp(self):
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")
        self._cwd = os.getcwd()

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        os.chdir(self._cwd)
        ws._load.cache_clear()

    def _mk_project(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        return proj

    def _write_overlay(self, proj, data):
        Path(proj, "conductor", "workflow", "workflow-shapes.json").write_text(
            json.dumps(data), encoding="utf-8",
        )

    def test_project_override_adds_shape(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"shapes": {"k8s-rollout": {
            "nodes": ["explorer", "task-executor"], "verify_policy": "none",
            "stop_condition": "all_nodes_done",
            "instruction": "Roll out via kubectl, no checkpoint gate."}}})
        ws._load.cache_clear()

        # Zero plugin edits: the new shape flows through every consumer.
        self.assertIn("k8s-rollout", ws.SHAPES_VOCAB())
        self.assertEqual(ws.nodes_for("k8s-rollout"),
                         ("explorer", "task-executor"))
        self.assertEqual(ws.verify_policy_for("k8s-rollout"), "none")
        self.assertIn("kubectl", ws.instruction_for("k8s-rollout"))

    def test_project_shape_omitting_a_verifier_controls_fanout(self):
        # THE load-bearing #4 case: a project overlay declares a shape whose
        # `verifiers` list omits test-runner. The fan-out reads verifiers_for,
        # so this shape fans out ONLY ac-tracer — zero plugin edits. This is
        # where the shape becomes load-bearing on the verifier axis.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"shapes": {"lint-only": {
            "nodes": ["spec-planner", "task-executor", "phase-checker"],
            "verifiers": ["ac-tracer"],  # omit test-runner
            "verify_policy": "checkpoint",
            "stop_condition": "all_nodes_done"}}})
        ws._load.cache_clear()

        self.assertIn("lint-only", ws.SHAPES_VOCAB())
        self.assertEqual(ws.verifiers_for("lint-only"), ("ac-tracer",))
        # The default shape is untouched (still the standard pair).
        self.assertEqual(ws.verifiers_for("default"),
                         ("ac-tracer", "test-runner"))

    def test_project_overlay_merges_keeps_builtins(self):
        # Overlay declares ONLY a new shape — built-ins must survive.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"shapes": {"k8s-rollout": {
            "nodes": ["task-executor"], "verify_policy": "none",
            "stop_condition": "all_nodes_done"}}})
        ws._load.cache_clear()

        self.assertIn("default", ws.SHAPES_VOCAB())  # built-in still present
        self.assertEqual(ws.nodes_for("default"),
                         ("spec-planner", "task-executor", "phase-checker"))

    def test_project_overlay_overrides_builtin(self):
        # Project re-declares default with a different verify_policy → project wins.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"shapes": {"default": {
            "nodes": ["spec-planner", "task-executor", "phase-checker"],
            "verify_policy": "none", "stop_condition": "all_nodes_done"}}})
        ws._load.cache_clear()

        self.assertEqual(ws.verify_policy_for("default"), "none")  # overridden
        # Other built-ins untouched.
        self.assertEqual(ws.verify_policy_for("research-first"), "none")

    def test_malformed_overlay_falls_back_to_baseline(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        Path(proj, "conductor", "workflow", "workflow-shapes.json").write_text(
            "{ not valid json", encoding="utf-8",
        )
        ws._load.cache_clear()

        # No crash, built-in vocab intact.
        self.assertEqual(set(ws.SHAPES_VOCAB()), set(BASELINE_SHAPES))
        self.assertEqual(ws.verify_policy_for("default"), "checkpoint")

    def test_malformed_shape_overlay_falls_back_to_baseline(self):
        # Structurally-wrong overlay (not an object) → baseline alone.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"default": {"verify_policy": "none"}})  # no 'shapes' key
        ws._load.cache_clear()

        self.assertEqual(set(ws.SHAPES_VOCAB()), set(BASELINE_SHAPES))


class DispatchConstraintTests(TestCase):
    """The shape is load-bearing: dispatch surfaces a shape_violation when an
    agent is off-topology, and stays quiet when on-topology."""

    def test_default_shape_allows_executor(self):
        # A normal default-shape track dispatching task-executor is on-topology.
        from scripts.track_state.dispatch import shape_allows
        state = {"workflow_shape": "default"}
        allowed, shape = shape_allows("/td", "task-executor", state=state)
        self.assertTrue(allowed)
        self.assertEqual(shape, "default")

    def test_default_shape_allows_phase_checker(self):
        from scripts.track_state.dispatch import shape_allows
        state = {"workflow_shape": "default"}
        allowed, _ = shape_allows("/td", "phase-checker", state=state)
        self.assertTrue(allowed)

    def test_research_first_allows_explorer(self):
        from scripts.track_state.dispatch import shape_allows
        state = {"workflow_shape": "research-first"}
        allowed, shape = shape_allows("/td", "explorer", state=state)
        self.assertTrue(allowed)
        self.assertEqual(shape, "research-first")

    def test_research_first_flags_phase_checker_off_topology(self):
        # research-first's topology has no phase-checker → dispatching one is a
        # shape_violation. Advisory today: shape_allows returns (False, shape) but
        # the dispatch emit site attaches that as a disclosure, not a block — the
        # constraint is surfaced, not enforced (see workflow-shape-is-advisory-only).
        from scripts.track_state.dispatch import shape_allows
        state = {"workflow_shape": "research-first"}
        allowed, shape = shape_allows("/td", "phase-checker", state=state)
        self.assertFalse(allowed)
        self.assertEqual(shape, "research-first")

    def test_unknown_shape_fails_open_to_default(self):
        # An unknown workflow_shape resolves to default (fail-open) — so an
        # executor dispatch stays allowed rather than deadlocking the track.
        from scripts.track_state.dispatch import shape_allows
        state = {"workflow_shape": "typo-shape"}
        allowed, shape = shape_allows("/td", "task-executor", state=state)
        self.assertTrue(allowed)
        self.assertEqual(shape, "default")


class SetWorkflowShapeTests(TestCase):
    """``cmd_set_workflow_shape`` mutates the topology declaration on an existing
    track. Unlike ``task_type`` (re-derived from the name) and verify-mode
    (re-parsed from the heading), ``workflow_shape`` is a declaration with no
    upstream source — so it MUST be mutable, and via a validating command (not a
    free JSON edit). Mirrors ``cmd_set_mode``: validate-then-mutate, emit
    ``previous`` so the change is visible.
    """

    def _mk_track(self, shape="default"):
        """A minimal track dir with a track-state.json carrying ``shape``."""
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        Path(d, "track-state.json").write_text(json.dumps({
            "track_id": "test_20260729", "type": "feature", "status": "in_progress",
            "description": "set-workflow-shape test", "current_phase_index": 1,
            "current_task_index": 1, "updated_at": "2026-07-29T00:00:00+00:00",
            "workflow_shape": shape,
            "phases": [{"name": "P1", "status": "in_progress",
                        "tasks": [{"name": "t", "status": "pending"}]}],
        }), encoding="utf-8")
        return d

    def _capture(self, fn, *a, **kw):
        """Run a handler that calls ``out(...)`` and return the emitted dict."""
        import scripts.track_state.quality as q
        captured = {}
        orig = q.out
        q.out = lambda payload: captured.update(payload)
        try:
            fn(*a, **kw)
        finally:
            q.out = orig
        return captured

    def test_set_known_shape_writes_field_and_emits_previous(self):
        from scripts.track_state.quality import cmd_set_workflow_shape, load
        d = self._mk_track(shape="default")
        emitted = self._capture(cmd_set_workflow_shape, d, "research-first")

        self.assertTrue(emitted["ok"])
        self.assertEqual(emitted["workflow_shape"], "research-first")
        self.assertEqual(emitted["previous"], "default")
        self.assertEqual(load(d)["workflow_shape"], "research-first")

    def test_set_idempotent_default(self):
        from scripts.track_state.quality import cmd_set_workflow_shape, load
        d = self._mk_track(shape="default")
        emitted = self._capture(cmd_set_workflow_shape, d, "default")
        self.assertTrue(emitted["ok"])
        self.assertEqual(load(d)["workflow_shape"], "default")

    def test_set_then_resolve_round_trips(self):
        # The set value is what dispatch reads via resolve_shape — no skew.
        from scripts.track_state.quality import cmd_set_workflow_shape, load
        d = self._mk_track(shape="default")
        self._capture(cmd_set_workflow_shape, d, "research-first")
        self.assertEqual(ws.resolve_shape(load(d).get("workflow_shape")),
                         "research-first")

    def test_set_unknown_shape_rejected_and_field_unchanged(self):
        # Validate-before-mutate: a typo must hard-reject (NOT fail-open to
        # default — that's for reads). The on-disk field must be untouched.
        from scripts.track_state.quality import cmd_set_workflow_shape, load
        d = self._mk_track(shape="default")
        emitted = self._capture(cmd_set_workflow_shape, d, "nope")

        self.assertFalse(emitted["ok"])
        self.assertIn("unknown", emitted["error"])
        self.assertIn("known shapes", emitted["hint"])
        # Source of truth never left holding an unrecognized name.
        self.assertEqual(load(d)["workflow_shape"], "default")


if __name__ == "__main__":
    main()
