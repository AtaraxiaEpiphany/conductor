"""Wiring tests for the workflow-shape registry (the third axis).

The workflow-shape axis is a data-driven registry
(``workflow-shapes.json`` + ``workflow_shapes.py``, baseline ⊕ project overlay)
— mirroring the task-type registry. Adding a shape is now one JSON row with zero
Python edits: ``SHAPES_VOCAB``/``nodes_for``/``verify_policy_for``/
``resolve_shape`` all derive from it, dispatch surfaces a ``shape_violation``
for an off-topology action, and ``registry-doc`` renders it. These tests pin
that contract:

- the registry data file exists and carries every shape's topology;
- ``SHAPES_VOCAB``/``nodes_for``/``verify_policy_for``/``planning_doc_for`` flow;
- the project overlay layer (``conductor/workflow/workflow-shapes.json``)
  adds/overrides a shape with ZERO plugin edits, fail-open on malformed overlay;
- ``resolve_shape`` fails open to ``default`` on unknown/absent;
- the dispatch constraint surfaces a ``shape_violation`` off-topology.

Mirrors ``test_task_type_field.py`` (overlay discipline + drift guard).
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
BASELINE_SHAPES = ("default", "research-first", "migration", "deliverable")


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

    def test_checkpoint_policy_for_defaults_to_run_everywhere(self):
        # Track C1: checkpoint_policy is the 3rd load-bearing drives-dispatch
        # field (after verifiers + gates). Byte-identical behavior: EVERY shipped
        # shape resolves "run" (the checkpoint always ran before this field
        # existed), so default-inheritance from the registry's `default` is the
        # no-op that keeps today's behavior. Unknown shape fails open to "run".
        for shape in BASELINE_SHAPES:
            self.assertEqual(ws.checkpoint_policy_for(shape), "run",
                             f"shape [{shape}] must resolve checkpoint_policy=run")
        self.assertEqual(ws.checkpoint_policy_for("typo-shape"), "run")

    def test_research_first_runs_explorer_before_planner(self):
        # The proof shape: explorer FIRST, then planner, then executor; no
        # checkpoint gate (exploration is not a committable artifact).
        self.assertEqual(ws.nodes_for("research-first"),
                         ("explorer", "spec-planner", "task-executor"))
        self.assertEqual(ws.verify_policy_for("research-first"), "none")

    def test_planning_doc_for_returns_docfile_pointer(self):
        # planning_doc_for is the mirror of task-type workflow_doc_for: the
        # registry-driven pointer into the planning library. Every shipped
        # shape declares its docfile EXPLICITLY (honest data — the same pin
        # every-shape-declares-verifiers makes); an unknown shape fails open
        # to the default row's pointer.
        for shape, doc in (("default", "default.md"),
                           ("research-first", "research-first.md"),
                           ("migration", "migration.md"),
                           ("deliverable", "deliverable.md")):
            self.assertEqual(ws.planning_doc_for(shape), doc)
        self.assertEqual(ws.planning_doc_for("typo-shape"), "default.md")

    def test_shipped_shapes_carry_no_instruction(self):
        # D5 relocation: the shape `instruction` fields MOVED home into the
        # planning docfiles (Delete→Point rung). No shipped row carries the
        # legacy inline form anymore — a row carrying BOTH instruction and
        # planning_doc is a two-homes drift the strict-write validator rejects
        # (test_registry_validate.PlanningDocField pins that guard).
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for shape, prof in data["shapes"].items():
            self.assertNotIn(
                "instruction", prof,
                f"shape [{shape}] still carries legacy `instruction` — the "
                f"planning procedure lives in its planning docfile")

    def test_research_first_planning_doc_carries_explorer_prelude(self):
        # The D4 mechanism: research-first goes live at the PLANNING layer —
        # its docfile Prelude has the orchestrator dispatch explorer BEFORE
        # spec-planner (planning-side ordering; nodes stays advisory).
        doc = ws.resolve_planning_doc("research-first").read_text(
            encoding="utf-8")
        self.assertIn("Prelude", doc)
        self.assertIn("explorer", doc)
        self.assertIn("BEFORE", doc)
        self.assertIn("RESEARCH_NOTES", doc)

    def test_vocab_matches_registry_keys(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(set(ws.SHAPES_VOCAB()), set(data["shapes"].keys()))


class VerifiersForTests(TestCase):
    """``verifiers_for(shape)`` is the LOAD-BEARING seam — the dispatch
    checkpoint fan-out iterates it (the third and fourth axes joined at the
    checkpoint). Distinct from ``nodes_for`` (the SPINE topology): verifiers are
    checkpoint *children*, never spine nodes."""

    def test_default_shape_fans_out_standard_pair(self):
        # Byte-identical to the pre-#4 hardcoded triple — now build-runner is the
        # cheapest-first compile floor between ac-tracer and test-runner.
        self.assertEqual(ws.verifiers_for("default"),
                         ("ac-tracer", "build-runner", "test-runner"))

    def test_research_first_also_fans_out_standard_pair(self):
        # research-first swaps the SPINE (explorer-first) but fans out the SAME
        # verifiers at its checkpoint.
        self.assertEqual(ws.verifiers_for("research-first"),
                         ("ac-tracer", "build-runner", "test-runner"))

    def test_unknown_shape_fails_open_to_default_pair(self):
        # A typo / unregistered shape must NOT block the fan-out — fall back to
        # the default shape's verifiers (the standard triple).
        self.assertEqual(ws.verifiers_for("typo-shape"),
                         ("ac-tracer", "build-runner", "test-runner"))

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


class GatesForTests(TestCase):
    """``gates_for(shape)`` is the track-level ON/OFF for each quality gate. A
    gate fires for a task iff (gate in gates_for(shape)) AND (not task_exempt) —
    composed at the F2 commit hook (``tdd``) and the F3 advisory gate
    (``coverage``) from Stage 2b. Distinct from ``verifiers_for`` (checkpoint
    children) and ``checkpoint_policy_for`` (whether the checkpoint actually
    RUNS — the load-bearing switch); ``verify_policy_for`` is declared display
    intent only (no code consults it to gate progress)."""

    def test_default_shape_enforces_all_three_gates(self):
        # Byte-identical to today: default runs the tdd, coverage, and checkpoint
        # gates. The regression pin Stage 2b composes against.
        self.assertEqual(ws.gates_for("default"),
                         ("tdd", "coverage", "checkpoint"))

    def test_research_first_inherits_default_gates(self):
        # research-first overrides nodes/verify_policy but inherits gates from
        # the default baseline (its executor tasks still owe tdd/coverage).
        self.assertEqual(ws.gates_for("research-first"),
                         ("tdd", "coverage", "checkpoint"))

    def test_unknown_shape_fails_open_to_default_gates(self):
        # A typo / unregistered shape must NOT silently drop a gate — fall back
        # to the default shape's gates (fail-open, never blocks dispatch).
        self.assertEqual(ws.gates_for("typo-shape"),
                         ("tdd", "coverage", "checkpoint"))

    def test_ac_grounding_for_defaults_to_test(self):
        self.assertEqual(ws.ac_grounding_for("default"), "test")
        self.assertEqual(ws.ac_grounding_for("typo-shape"), "test")

    def test_fields_documents_new_axes(self):
        # The new fields are documented in the registry's _fields block.
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for field in ("gates", "ac_grounding"):
            self.assertIn(field, data["_fields"])

    def test_fields_documents_max_retries(self):
        # The shape-level retry budget is documented (the _fields coverage
        # pin for the newest axis).
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertIn("max_retries", data["_fields"])
        self.assertIn("task.max_retries", data["_fields"]["max_retries"])


class MigrationShapeTests(TestCase):
    """Stage 2c: the built-in ``migration`` shape — the first non-code shape.
    Behavior-preservation tracks (framework upgrade, API rename, cross-module
    refactor) where correctness is witnessed by the EXISTING suite, not new
    tests. The shape drops the tdd/coverage gates at the track level
    (gates=[checkpoint]); its executor guidance lives on the [Migrate]
    task-type tag (``workflow`` prose), NOT on the shape — the conductor's
    two-registry separation (task-types own node behavior, shapes own
    topology + gates)."""

    def test_migration_resolves_with_canonical_topology(self):
        self.assertEqual(ws.resolve_shape("migration"), "migration")
        # Same planner→executor→checker spine as default — only the gates differ.
        self.assertEqual(ws.nodes_for("migration"),
                         ("spec-planner", "task-executor", "phase-checker"))

    def test_migration_reuses_existing_verifiers(self):
        # The existing suite is the behavior-preservation signal; the build tier
        # catches any module the suite never imports.
        self.assertEqual(ws.verifiers_for("migration"),
                         ("ac-tracer", "build-runner", "test-runner"))

    def test_migration_drops_tdd_and_coverage_gates(self):
        # The track-level gate set is checkpoint-only: tdd/coverage OFF here,
        # composed with the per-task exemption at the F2/F3 enforcers (Stage 2b).
        self.assertEqual(ws.gates_for("migration"), ("checkpoint",))

    def test_migration_ac_grounding_is_test(self):
        # Existing tests ground the ACs (ac_grounding=test), so the spec_integrity
        # grounding scan takes its test branch for a migration track (Rate 1 = AC→TC
        # coverage) rather than insisting on review anchors a migration doesn't use.
        self.assertEqual(ws.ac_grounding_for("migration"), "test")

    def test_migration_when_to_use_pairs_with_migrate_tag(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        when = data["shapes"]["migration"].get("when_to_use", "")
        self.assertIn("PRESERVATION", when)
        self.assertIn("[Migrate]", when)


class DeliverableShapeTests(TestCase):
    """Track B1: the built-in ``deliverable`` shape — the first REVIEW-grounded
    non-code shape. A deliverable track (a design doc, research report, spec,
    runbook, data deliverable) produces an artifact whose correctness is
    witnessed by an artifact anchor + a review attestation, not by automated
    tests. The shape drops the test-runner verifier (no tests to run — ac-tracer
    alone fans out) and grounds ACs by ``review`` (``ac_grounding=review``). The
    planner→executor→checker spine is unchanged; only the verifier set + the
    grounding paradigm differ. (Track B2 wires the integrity scan to honor
    ``ac_grounding``; until then the shape is resolvable and its verifiers/gates
    drive dispatch, which is what these tests pin.)"""

    def test_deliverable_resolves_with_canonical_topology(self):
        self.assertEqual(ws.resolve_shape("deliverable"), "deliverable")
        # Same planner→executor→checker spine as default.
        self.assertEqual(ws.nodes_for("deliverable"),
                         ("spec-planner", "task-executor", "phase-checker"))

    def test_deliverable_drops_test_runner_verifier(self):
        # THE load-bearing B1 claim: a deliverable shape fans out ac-tracer ONLY
        # (test-runner has nothing to run — no tests). verifiers_for reads the
        # row, so the checkpoint wave is ac-tracer alone.
        self.assertEqual(ws.verifiers_for("deliverable"), ("ac-tracer",))

    def test_deliverable_drops_tdd_and_coverage_gates(self):
        # tdd/coverage are test-grounded gates; a review-grounded shape drops
        # them at the track level — only the checkpoint gate remains.
        self.assertEqual(ws.gates_for("deliverable"), ("checkpoint",))

    def test_deliverable_ac_grounding_is_review(self):
        # The grounding axis: review, not test. B2's integrity scan reads this
        # so a deliverable shape is NOT required to ground its ACs in test_TC_*.
        self.assertEqual(ws.ac_grounding_for("deliverable"), "review")

    def test_deliverable_verify_policy_runs_checkpoint(self):
        # Declared intent: a deliverable states verify_policy=checkpoint. It DOES
        # run a checkpoint — but the load-bearing switch is checkpoint_policy=run
        # (inherited), NOT this field (verify_policy is display-only). Distinct
        # from research-first, whose verify_policy=none records that exploration
        # produces no committable artifact.
        self.assertEqual(ws.verify_policy_for("deliverable"), "checkpoint")

    def test_deliverable_when_to_use_names_review_grounding(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        when = data["shapes"]["deliverable"].get("when_to_use", "")
        self.assertIn("DELIVERABLE", when)
        self.assertIn("review", when.lower())

    def test_ac_grounding_vocab_includes_review(self):
        # The closed vocab widened for B1: review is now a valid scalar.
        from scripts.track_state.registry_validate import AC_GROUNDINGS
        self.assertIn("review", AC_GROUNDINGS)


class ResolveShapeTests(TestCase):
    """resolve_shape is the fail-open chokepoint the dispatch spine reads."""

    def test_known_shape_resolves(self):
        self.assertEqual(ws.resolve_shape("default"), "default")
        self.assertEqual(ws.resolve_shape("research-first"), "research-first")
        self.assertEqual(ws.resolve_shape("migration"), "migration")

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

    Mirrors test_task_type_field.py::OverrideLayerTests.
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
        # `verifiers` list omits the code tiers (build-runner + test-runner). The
        # fan-out reads verifiers_for, so this shape fans out ONLY ac-tracer —
        # zero plugin edits. This is where the shape becomes load-bearing on the
        # verifier axis. Review-grounded (ac_grounding=review) so the omit is
        # valid: a review shape owes no compile/test (the review attestation is
        # its substitute); omitting the code tiers on a test-grounded shape would
        # be rejected by validate_merged_shapes at save.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"shapes": {"lint-only": {
            "nodes": ["spec-planner", "task-executor", "phase-checker"],
            "verifiers": ["ac-tracer"],  # omit the code tiers
            "ac_grounding": "review",
            "verify_policy": "checkpoint",
            "stop_condition": "all_nodes_done"}}})
        ws._load.cache_clear()

        self.assertIn("lint-only", ws.SHAPES_VOCAB())
        self.assertEqual(ws.verifiers_for("lint-only"), ("ac-tracer",))
        # The default shape is untouched (still the standard triple).
        self.assertEqual(ws.verifiers_for("default"),
                         ("ac-tracer", "build-runner", "test-runner"))

    def test_project_shape_dropping_a_gate_controls_composition(self):
        # THE load-bearing portability case: a project overlay declares a shape
        # whose `gates` omits tdd/coverage. gates_for reads it, so this shape
        # drops F2/F3 at the track level — zero plugin edits. Stage 2b composes
        # `"tdd" in gates_for(shape)` at the F2 hook, so this is where the shape
        # becomes load-bearing on the gate axis. (Mirrors the built-in migration
        # shape's gate set; uses a fresh name to prove the OVERLAY adds a shape,
        # not merely re-declares the built-in migration.)
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"shapes": {"release-cut": {
            "nodes": ["spec-planner", "task-executor", "phase-checker"],
            "verifiers": ["ac-tracer", "build-runner", "test-runner"],
            "gates": ["checkpoint"],
            "verify_policy": "checkpoint",
            "stop_condition": "all_nodes_done"}}})
        ws._load.cache_clear()

        self.assertIn("release-cut", ws.SHAPES_VOCAB())
        self.assertEqual(ws.gates_for("release-cut"), ("checkpoint",))
        # default is untouched (still all three gates).
        self.assertEqual(ws.gates_for("default"),
                         ("tdd", "coverage", "checkpoint"))

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


class RailAShapeDisclosureTests(TestCase):
    """``cmd_dispatch_next`` (Rail A — the implement skill's primary §3.1 flow)
    surfaces the same ``shape_violation`` disclosure Rail B's ``step`` spine
    does, so the no-silent-caps constraint holds on BOTH rails — not only the
    step spine. Without this, an off-topology dispatch via dispatch-next was
    silently unflagged while the same dispatch via ``step`` attached the
    violation."""

    def _recent_iso(self):
        from datetime import datetime, timezone, timedelta
        return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def _track(self, task_name, shape="default"):
        from scripts.track_state.core import save
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        Path(d, "plan.md").write_text(
            f"# Plan\n\n## Phase 1: Build\n- [ ] {task_name}\n")
        save(d, {
            "track_id": "sd", "type": "feature", "status": "in_progress",
            "current_phase_index": 1, "current_task_index": 1,
            "workflow_shape": shape, "updated_at": self._recent_iso(),
            "phases": [{"name": "P1", "status": "pending",
                        "tasks": [{"name": task_name, "status": "pending"}]}],
        })
        return d

    def _dispatch_next(self, d):
        import io, sys
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            from scripts.track_state.dispatch import cmd_dispatch_next
            cmd_dispatch_next(d)
            return json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old

    def test_off_topology_dispatch_surfaces_violation_on_rail_a(self):
        # explorer is not in the default shape's nodes → an [Explore] task
        # dispatch surfaces the advisory on Rail A too (mirrors Rail B's step).
        d = self._track("[Explore] map the module")
        out = self._dispatch_next(d)
        self.assertEqual(out["action"], "dispatch_explorer")
        self.assertEqual(out["agent"], "explorer")
        self.assertIn("shape_violation", out)
        self.assertIn("explorer", out["shape_violation"])
        self.assertEqual(out["workflow_shape"], "default")

    def test_on_topology_executor_dispatch_no_violation_on_rail_a(self):
        # task-executor IS in default's nodes → no disclosure.
        d = self._track("build the feature")
        out = self._dispatch_next(d)
        self.assertEqual(out["action"], "dispatch_executor")
        self.assertNotIn("shape_violation", out)


class SetWorkflowShapeTests(TestCase):
    """``cmd_set_workflow_shape`` mutates the topology declaration on an existing
    track. Unlike ``task_type`` (re-derived from the name), ``workflow_shape`` is
    a declaration with no upstream source — so it MUST be mutable, and via a
    validating command (not a free JSON edit). Mirrors the validate-then-mutate
    pattern: validate-then-mutate, emit ``previous`` so the change is visible.
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

    def test_set_migration_shape_round_trips_and_drops_gates(self):
        # Stage 2c: the §2.6 new-track path sets `--shape migration` after
        # init-from-plan writes default. The set value round-trips through
        # resolve_shape AND composes to checkpoint-only gates (Stage 2b).
        from scripts.track_state.quality import cmd_set_workflow_shape, load
        d = self._mk_track(shape="default")
        emitted = self._capture(cmd_set_workflow_shape, d, "migration")
        self.assertTrue(emitted["ok"])
        self.assertEqual(emitted["previous"], "default")
        self.assertEqual(ws.resolve_shape(load(d).get("workflow_shape")),
                         "migration")
        self.assertEqual(ws.gates_for(ws.resolve_shape(
            load(d).get("workflow_shape"))), ("checkpoint",))

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


class PhaseCodeFreeTests(TestCase):
    """``phase_is_code_free`` narrows the checkpoint fan-out: a phase of pure
    coverage_exempt tasks ([Config]/[Docs]/[Chore]/[Manual]) produces no code →
    no tests, so test-runner is dropped from the wave. Auto-detected from the
    live task tags; no directive, no authoring — the lightweight alternative to
    the per-phase verify apparatus. Keys on ``coverage_exempt`` (the F2/F3
    predicate), NOT ``tdd_exempt``: [Explore] is tdd_exempt but stays out of the
    code-free set (explore-heavy tracks use research-first, which runs no
    checkpoint at all)."""

    def _state(self, tasks, shape="default"):
        return {"workflow_shape": shape,
                "phases": [{"name": "P1", "tasks": tasks}]}

    def test_all_exempt_phase_is_code_free(self):
        from scripts.track_state.task_profiles import phase_is_code_free
        st = self._state([{"name": "[Config] tweak"}, {"name": "[Docs] write"},
                          {"name": "[Manual] verify"}])
        self.assertTrue(phase_is_code_free(st, 1))

    def test_mixed_phase_is_not_code_free(self):
        from scripts.track_state.task_profiles import phase_is_code_free
        # An untagged task = default TDD (real code) → not code-free.
        st = self._state([{"name": "[Config] tweak"}, {"name": "build the API"}])
        self.assertFalse(phase_is_code_free(st, 1))

    def test_mixed_tag_task_is_not_code_free(self):
        # A task carrying an exempt tag AND a code-producing tag
        # ([Config][Refactor]) is NOT code-free — phase_is_code_free composes
        # ALL-exempt per task, NOT is_coverage_exempt's ANY (which would call
        # [Config][Refactor] exempt). The Refactor half produces code, so such
        # a phase keeps test-runner.
        from scripts.track_state.task_profiles import phase_is_code_free
        st = self._state([{"name": "[Config][Refactor] extract validation"},
                          {"name": "[Docs][Feature] ship it"}])
        self.assertFalse(phase_is_code_free(st, 1))

    def test_explore_only_phase_is_not_code_free(self):
        # [Explore] is tdd_exempt but NOT coverage_exempt — excluded from the
        # code-free set, so an explore-only phase keeps test-runner.
        from scripts.track_state.task_profiles import phase_is_code_free
        st = self._state([{"name": "[Explore] map the module"}])
        self.assertFalse(phase_is_code_free(st, 1))

    def test_empty_phase_is_not_code_free(self):
        # An empty phase is malformed, not code-free — keep test-runner.
        from scripts.track_state.task_profiles import phase_is_code_free
        self.assertFalse(phase_is_code_free(self._state([]), 1))

    def test_out_of_range_phase_is_not_code_free(self):
        from scripts.track_state.task_profiles import phase_is_code_free
        self.assertFalse(phase_is_code_free(self._state([{"name": "[Config] x"}]), 99))

    def test_code_free_phase_drops_test_runner_from_wave(self):
        # THE narrowing: a code-free phase fans out ONLY ac-tracer (test-runner
        # has nothing to run). Both rails share _build_verifier_wave, so this
        # propagates everywhere.
        from scripts.track_state.dispatch import _build_verifier_wave
        st = self._state([{"name": "[Config] tweak"}, {"name": "[Manual] verify"}])
        agents = [m["agent"] for m in _build_verifier_wave("/td", st, 1)]
        self.assertNotIn("test-runner", agents)
        self.assertIn("ac-tracer", agents)  # ac-tracer still runs (ACs declared)

    def test_mixed_phase_keeps_both_verifiers(self):
        from scripts.track_state.dispatch import _build_verifier_wave
        st = self._state([{"name": "[Config] tweak"}, {"name": "build feature"}])
        agents = [m["agent"] for m in _build_verifier_wave("/td", st, 1)]
        self.assertIn("ac-tracer", agents)
        self.assertIn("test-runner", agents)

    def test_narrowing_applies_when_verifiers_passed_in(self):
        # Rail A (cmd_dispatch_next) passes verifiers= from resolve_phase_gate;
        # the narrowing must apply to the passed-in set too, not only the
        # resolved-here path.
        from scripts.track_state.dispatch import _build_verifier_wave
        from scripts.track_state.workflow_shapes import verifiers_for
        st = self._state([{"name": "[Config] tweak"}])
        agents = [m["agent"] for m in _build_verifier_wave(
            "/td", st, 1, verifiers=verifiers_for("default"))]
        self.assertNotIn("test-runner", agents)

    def test_code_free_does_not_empty_the_wave(self):
        # A pathological overlay shape declaring verifiers=['test-runner'] (no
        # ac-tracer) on a code-free phase must NOT narrow to an empty wave —
        # keep test-runner (it runs, finds nothing) rather than stalling the
        # checkpoint with zero verifiers. Shipped shapes always pair the two,
        # so only a custom overlay trips this; the guard keeps it safe.
        from scripts.track_state.dispatch import _build_verifier_wave
        st = self._state([{"name": "[Config] tweak"}])
        members = _build_verifier_wave("/td", st, 1, verifiers=("test-runner",))
        self.assertEqual([m["agent"] for m in members], ["test-runner"])

    def test_phase_checker_emits_skipped_for_code_free_phase(self):
        # No L1 verdict transcribed (test-runner didn't run) on a code-free
        # phase → the dispatch fills an explicit "skipped" so the checker
        # records it instead of treating the empty verdict as a failure.
        from scripts.track_state.dispatch import _build_phase_checker
        st = {"track_id": "t", "execution_mode": "interactive",
              "phases": [{"name": "P1", "tasks": [{"name": "[Config] tweak"}]}]}
        prompt = _build_phase_checker("/td", st, 1, {"ac_verdict": "passed"})
        self.assertIn("L1_VERIFY_STATUS=skipped (no code-producing tasks)", prompt)

    def test_phase_checker_keeps_empty_verdict_for_code_phase(self):
        # A NOT-code-free phase with no L1 verdict keeps the empty status — the
        # checker surfaces that as FAILURE (a dispatch defect), NOT "skipped".
        from scripts.track_state.dispatch import _build_phase_checker
        st = {"track_id": "t", "execution_mode": "interactive",
              "phases": [{"name": "P1", "tasks": [{"name": "build feature"}]}]}
        prompt = _build_phase_checker("/td", st, 1, {"ac_verdict": "passed"})
        self.assertNotIn("skipped", prompt)

    def test_phase_checker_none_marker_is_empty_not_literal_none(self):
        # cmd_phase_verdict writes l1_status=None when --l1-status is omitted
        # (a code-free phase, or a transcription defect). The builder must coerce
        # None to "" — NOT emit the literal "L1_VERIFY_STATUS=None", a token
        # phase-checker.md has no branch for. On a code phase the empty status is
        # the intended FAILURE-dispatch-defect signal.
        from scripts.track_state.dispatch import _build_phase_checker
        st = {"track_id": "t", "execution_mode": "interactive",
              "phases": [{"name": "P1", "tasks": [{"name": "build feature"}]}]}
        prompt = _build_phase_checker("/td", st, 1,
                                      {"ac_verdict": "passed", "l1_status": None})
        self.assertIn("L1_VERIFY_STATUS=", prompt)
        self.assertNotIn("L1_VERIFY_STATUS=None", prompt)

    def test_phase_checker_marker_verdict_wins_over_code_free(self):
        # If test-runner/build-runner DID run (marker carries the verdicts), those
        # verdicts are honored even on a code-free phase — the narrowing only
        # fills the EMPTY case, it never clobbers a real verdict.
        from scripts.track_state.dispatch import _build_phase_checker
        st = {"track_id": "t", "execution_mode": "interactive",
              "phases": [{"name": "P1", "tasks": [{"name": "[Config] tweak"}]}]}
        prompt = _build_phase_checker(
            "/td", st, 1,
            {"ac_verdict": "passed", "l1_status": "passed", "build_status": "passed"})
        self.assertIn("L1_VERIFY_STATUS=passed", prompt)
        self.assertIn("BUILD_VERIFY_STATUS=passed", prompt)
        self.assertNotIn("skipped", prompt)


class MaxRetriesTests(OverrideLayerTests):
    """The shape-level ``max_retries`` accessor — 0 = inherit the global.

    Subclasses OverrideLayerTests for the env/cache harness (a real overlay
    file per case, `_load.cache_clear` in tearDown); the accessor reads the
    merged registry exactly as production does.
    """

    def test_shipped_shapes_inherit(self):
        # No shipped shape declares a budget — every one resolves 0 (inherit
        # the global MAX_RETRIES; constants.task_max_reties' chain).
        for shape in ("default", "migration", "research-first", "deliverable"):
            self.assertEqual(ws.max_retries_for(shape), 0, shape)

    def test_overlay_budget_resolves(self):
        # A project shape declares a per-family ceiling with zero plugin
        # edits — flows through the merged read like every other field.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"shapes": {"k8s-rollout": {
            "nodes": ["task-executor"], "max_retries": 1}}})
        ws._load.cache_clear()
        self.assertEqual(ws.max_retries_for("k8s-rollout"), 1)

    def test_malformed_budget_fails_open_to_inherit(self):
        # A corrupt overlay value must not corrupt the budget: 0 / negative /
        # string / bool / float / null all resolve 0 (inherit) — the same
        # defensiveness task_max_retries holds one tier down.
        for bad in (0, -2, "3", True, 1.5, None):
            proj = self._mk_project()
            os.environ["CLAUDE_PROJECT_DIR"] = proj
            self._write_overlay(proj, {"shapes": {"risky": {
                "nodes": ["task-executor"], "max_retries": bad}}})
            ws._load.cache_clear()
            self.assertEqual(ws.max_retries_for("risky"), 0, bad)

    def test_unknown_shape_inherits(self):
        # Fail-open shape: a typo resolves default's (absent) budget → 0.
        self.assertEqual(ws.max_retries_for("typo-shape"), 0)


if __name__ == "__main__":
    main()
