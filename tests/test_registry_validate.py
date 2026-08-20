"""Tests for ``track_state.registry_validate`` — the strict-write gate.

Load-bearing invariants under test:

- **The shipped baselines validate clean**: ``templates/workflow/workflow-shapes.json``
  and ``task-type-profiles.json`` produce zero errors. This is the regression
  floor — if validation ever rejects the plugin's own registries, every save
  (and the editor itself) is broken.
- **Closed vocabularies are the expected sets** (the editor dropdowns + drift
  lint derive from these tuples).
- **Every error class is caught**: unknown vocab member, wrong type, unknown
  field, non-dict row, non-object top level.
- **Overlay fragments validate** (a ``{"shapes": {...}}`` with no ``default`` is
  valid here — it inherits at merge), but the MERGED result must declare
  ``default``.
"""
import json
import sys
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

from track_state import registry_validate as rv  # noqa: E402

_TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "workflow"


def _shapes_baseline():
    return json.loads((_TEMPLATES / "workflow-shapes.json").read_text())


def _tags_baseline():
    return json.loads((_TEMPLATES / "task-type-profiles.json").read_text())


class VocabConstants(TestCase):
    def test_spine_nodes(self):
        self.assertEqual(rv.SPINE_NODES,
                         ("spec-planner", "explorer", "task-executor", "phase-checker"))

    def test_verifiers_and_gates(self):
        self.assertEqual(rv.VERIFIERS, ("ac-tracer", "build-runner", "test-runner"))
        self.assertEqual(rv.GATES, ("tdd", "coverage", "checkpoint"))

    def test_policies_groundings_routes(self):
        self.assertEqual(rv.VERIFY_POLICIES, ("checkpoint", "none"))
        self.assertEqual(rv.AC_GROUNDINGS, ("test", "review"))
        self.assertEqual(rv.CHECKPOINT_POLICIES, ("run", "skip-if-declared"))
        self.assertEqual(rv.ROUTES, ("manual", "explore", "executor"))


class ShippedBaselinesValidate(TestCase):
    """The plugin's own registries MUST pass validation — the regression floor."""

    def test_shapes_baseline_is_clean(self):
        self.assertEqual(rv.validate_shapes(_shapes_baseline()), [],
                         "shipped workflow-shapes.json should validate clean")

    def test_tags_baseline_is_clean(self):
        self.assertEqual(rv.validate_task_types(_tags_baseline()), [],
                         "shipped task-type-profiles.json should validate clean")

    def test_merged_shapes_baseline_is_clean(self):
        self.assertEqual(rv.validate_merged_shapes(_shapes_baseline()), [])

    def test_merged_tags_baseline_is_clean(self):
        self.assertEqual(rv.validate_merged_task_types(_tags_baseline()), [])


class ShapeValidation(TestCase):
    def _base(self):
        # Minimal valid shape document. default includes build-runner so it is
        # valid under validate_merged_shapes too (a test-grounded shape must run
        # the build tier — the cross-field guard).
        return {
            "default": {"nodes": ["spec-planner", "task-executor", "phase-checker"],
                        "verifiers": ["ac-tracer", "build-runner", "test-runner"],
                        "gates": ["tdd", "coverage", "checkpoint"]},
            "shapes": {"default": {"nodes": ["spec-planner", "task-executor"]}},
        }

    def test_valid_shape_doc_has_no_errors(self):
        self.assertEqual(rv.validate_shapes(self._base()), [])

    def test_unknown_node_rejected(self):
        doc = self._base()
        doc["shapes"]["default"]["nodes"] = ["spec-planner", "bogus-agent"]
        errs = rv.validate_shapes(doc)
        self.assertTrue(any("nodes" in e and "bogus-agent" in e for e in errs), errs)

    def test_unknown_verifier_rejected(self):
        doc = self._base()
        doc["default"]["verifiers"] = ["ac-tracer", "lint-runner"]
        errs = rv.validate_shapes(doc)
        self.assertTrue(any("verifiers" in e and "lint-runner" in e for e in errs), errs)

    def test_unknown_gate_rejected(self):
        doc = self._base()
        doc["default"]["gates"] = ["tdd", "coverage", "checkpoint", "lint"]
        errs = rv.validate_shapes(doc)
        self.assertTrue(any("gates" in e and "lint" in e for e in errs), errs)

    def test_bad_verify_policy_rejected(self):
        doc = self._base()
        doc["default"]["verify_policy"] = "always"
        errs = rv.validate_shapes(doc)
        self.assertTrue(any("verify_policy" in e for e in errs), errs)

    def test_good_checkpoint_policy_accepted(self):
        # checkpoint_policy is an optional scalar vocab field; both values valid.
        doc = self._base()
        doc["default"]["checkpoint_policy"] = "run"
        self.assertEqual(rv.validate_shapes(doc), [])
        doc["default"]["checkpoint_policy"] = "skip-if-declared"
        self.assertEqual(rv.validate_shapes(doc), [])

    def test_bad_checkpoint_policy_rejected(self):
        doc = self._base()
        doc["default"]["checkpoint_policy"] = "sometimes"
        errs = rv.validate_shapes(doc)
        self.assertTrue(
            any("checkpoint_policy" in e and "sometimes" in e for e in errs), errs)

    def test_unknown_field_rejected(self):
        doc = self._base()
        doc["default"]["verifers"] = ["ac-tracer"]  # typo
        errs = rv.validate_shapes(doc)
        self.assertTrue(any("unknown field" in e and "verifers" in e for e in errs), errs)

    def test_non_list_nodes_rejected(self):
        doc = self._base()
        doc["default"]["nodes"] = "spec-planner"
        errs = rv.validate_shapes(doc)
        self.assertTrue(any("nodes must be a list" in e for e in errs), errs)

    def test_non_dict_shape_rejected(self):
        doc = self._base()
        doc["shapes"]["default"] = "oops"
        errs = rv.validate_shapes(doc)
        self.assertTrue(any("must be an object" in e for e in errs), errs)

    def test_non_object_top_level_rejected(self):
        self.assertEqual(rv.validate_shapes(["not", "an", "object"]),
                         ["shapes registry top-level must be an object"])

    def test_comment_and_fields_blocks_allowed(self):
        doc = self._base()
        doc["_comment"] = "doc block"
        doc["_fields"] = {"nodes": "topology"}
        self.assertEqual(rv.validate_shapes(doc), [])

    def test_unknown_top_level_key_rejected(self):
        doc = self._base()
        doc["shpaes"] = {}
        errs = rv.validate_shapes(doc)
        self.assertTrue(any("unknown top-level key" in e for e in errs), errs)

    def test_overlay_fragment_without_default_is_valid(self):
        # An overlay that only adds a shape — no `default` — inherits baseline.
        overlay = {"shapes": {"my-shape": {"nodes": ["explorer", "spec-planner"]}}}
        self.assertEqual(rv.validate_shapes(overlay), [])


class TaskTypeValidation(TestCase):
    def _base(self):
        return {
            "default": {"route": "executor", "tdd_exempt": False, "coverage_exempt": False},
            "tags": {"Docs": {"route": "executor", "tdd_exempt": True,
                              "coverage_exempt": True}},
        }

    def test_valid_tag_doc_has_no_errors(self):
        self.assertEqual(rv.validate_task_types(self._base()), [])

    def test_unknown_route_rejected(self):
        doc = self._base()
        doc["tags"]["Docs"]["route"] = "batch"
        errs = rv.validate_task_types(doc)
        self.assertTrue(any("route" in e for e in errs), errs)

    def test_non_bool_exempt_rejected(self):
        doc = self._base()
        doc["tags"]["Docs"]["tdd_exempt"] = "yes"
        errs = rv.validate_task_types(doc)
        self.assertTrue(any("tdd_exempt must be a boolean" in e for e in errs), errs)

    def test_signals_not_list_rejected(self):
        doc = self._base()
        doc["tags"]["Docs"]["signals"] = "docs"
        errs = rv.validate_task_types(doc)
        self.assertTrue(any("signals must be a list of strings" in e for e in errs), errs)

    def test_signals_non_string_member_rejected(self):
        doc = self._base()
        doc["tags"]["Docs"]["signals"] = ["docs", 3]
        errs = rv.validate_task_types(doc)
        self.assertTrue(any("signals must be a list of strings" in e for e in errs), errs)

    def test_unknown_field_rejected(self):
        doc = self._base()
        doc["tags"]["Docs"]["routee"] = "executor"
        errs = rv.validate_task_types(doc)
        self.assertTrue(any("unknown field" in e and "routee" in e for e in errs), errs)

    def test_overlay_fragment_without_default_is_valid(self):
        overlay = {"tags": {"K8sRollout": {"route": "executor", "tdd_exempt": True,
                                           "coverage_exempt": True}}}
        self.assertEqual(rv.validate_task_types(overlay), [])


class BuildTierCrossFieldGuard(TestCase):
    """Track 1's cross-field invariant (the PRIMARY catch, mirroring Track C2's
    checkpoint-skip guard): a test-grounded shape IS a code shape, so its
    resolved verifiers MUST include ``build-runner`` (the L0 compile tier). Only
    a review-grounded shape (the ``deliverable`` pattern) may drop it — the
    review attestation is the integrity substitute. Judged on the
    default-INHERITED value, mirroring runtime."""

    def _base(self):
        # default is a valid code shape (test-grounded, carries the build tier).
        return {
            "default": {"nodes": ["spec-planner", "task-executor", "phase-checker"],
                        "verifiers": ["ac-tracer", "build-runner", "test-runner"],
                        "gates": ["tdd", "coverage", "checkpoint"],
                        "ac_grounding": "test"},
            "shapes": {"default": {"nodes": ["spec-planner"]}},
        }

    def test_test_grounded_shape_without_build_runner_rejected(self):
        # THE critical rule: a code shape that silently drops the build tier
        # reopens the "unimported module" hole — reject at save.
        doc = self._base()
        doc["shapes"]["hole"] = {"ac_grounding": "test",
                                 "verifiers": ["ac-tracer", "test-runner"]}
        errs = rv.validate_merged_shapes(doc)
        self.assertTrue(any("'hole'" in e and "build-runner" in e
                            and "build tier" in e for e in errs), errs)

    def test_review_grounded_shape_may_omit_build_runner(self):
        # A non-code (review) shape owes no compile — the review attestation is
        # the substitute. ac-tracer alone is a valid verifier set (deliverable).
        doc = self._base()
        doc["shapes"]["doc-only"] = {"ac_grounding": "review",
                                     "verifiers": ["ac-tracer"]}
        self.assertEqual(rv.validate_merged_shapes(doc), [])

    def test_inherited_test_grounding_without_build_runner_rejected(self):
        # A row that INHERITS ac_grounding=test from default (does not declare
        # it) and drops build-runner is still a code shape missing its compile
        # tier — rejected (mirrors runtime inheritance).
        doc = self._base()
        doc["shapes"]["inher"] = {"verifiers": ["ac-tracer", "test-runner"]}
        errs = rv.validate_merged_shapes(doc)
        self.assertTrue(any("'inher'" in e and "build-runner" in e for e in errs),
                        errs)

    def test_default_dropping_build_runner_rejected(self):
        # Overlaying top-level default.verifiers to drop the build floor would
        # weaken EVERY track — the top-level default is checked too.
        doc = self._base()
        doc["default"]["verifiers"] = ["ac-tracer", "test-runner"]
        errs = rv.validate_merged_shapes(doc)
        self.assertTrue(any("'default'" in e and "build-runner" in e
                            for e in errs), errs)

    def test_shipped_baseline_clean_under_build_guard(self):
        # The shipped registries carry build-runner on every code shape and omit
        # it only on the review-grounded deliverable — the guard is a no-op there.
        self.assertEqual(rv.validate_merged_shapes(_shapes_baseline()), [])


class MergedRequiresDefault(TestCase):
    def test_merged_shapes_without_default_rejected(self):
        # A merged result must carry `default` — an overlay fragment alone is NOT
        # a valid merged result.
        errs = rv.validate_merged_shapes({"shapes": {"default": {"nodes": []}}})
        self.assertTrue(any("default" in e for e in errs), errs)

    def test_merged_tags_without_default_rejected(self):
        errs = rv.validate_merged_task_types({"tags": {"Docs": {}}})
        self.assertTrue(any("default" in e for e in errs), errs)


class WorkflowDocField(TestCase):
    """The ``workflow_doc`` registry field — the steps-library pointer.

    Strict-write rules: the value must be a STRING and a BARE ``.md`` filename
    (no path separators — a path-y value is a typo or traversal attempt). The
    EXISTENCE cross-check (a declared docfile must resolve in the plugin or
    project steps dir) is I/O-bound, so it lives at read time
    (``task_profiles.resolve_workflow_doc`` fail-open warning) plus the
    shipped-registry pin below, not in this pure module.
    """

    def _doc(self, doc_value):
        return {"default": {}, "tags": {"Custom": {"workflow_doc": doc_value}}}

    def test_valid_docfile_name_passes(self):
        self.assertEqual(
            rv.validate_task_types(self._doc("rollout.md")), [], )

    def test_path_separator_rejected(self):
        errs = rv.validate_task_types(self._doc("steps/rollout.md"))
        self.assertTrue(any("workflow_doc" in e and "bare" in e for e in errs),
                        errs)

    def test_parent_traversal_rejected(self):
        errs = rv.validate_task_types(self._doc("../../etc/passwd.md"))
        self.assertTrue(any("workflow_doc" in e for e in errs), errs)

    def test_non_string_rejected(self):
        errs = rv.validate_task_types({"default": {},
                                       "tags": {"Custom": {"workflow_doc": 7}}})
        self.assertTrue(any("workflow_doc must be a string" in e for e in errs),
                        errs)

    def test_shipped_declared_docfiles_resolve(self):
        # The existence gate for the SHIPPED registry (test-side I/O): every
        # declared workflow_doc must name a real file in the plugin steps dir,
        # and resolve_workflow_doc must point at it (not fail-open to default).
        from track_state import task_profiles as tp
        for tag, row in _tags_baseline()["tags"].items():
            declared = row.get("workflow_doc")
            if not declared:
                continue
            plugin_cand = (_TEMPLATES / "steps" / declared)
            self.assertTrue(
                plugin_cand.is_file(),
                f"{tag} declares workflow_doc {declared!r} but the plugin "
                f"steps dir has no such file")
            resolved = tp.resolve_workflow_doc(tag)
            self.assertEqual(resolved.name, declared)
            self.assertTrue(resolved.is_file())


if __name__ == "__main__":
    main()
