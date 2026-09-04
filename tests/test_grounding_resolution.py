"""Track-1 grounding inversion — the resolution layer and validator forms.

Commit 1 of the inversion: the JSON baseline stays legacy-form (exemption
booleans), but both forms already resolve. These tests pin:

- ``_resolve_row`` normalization in both directions (gates → derived booleans;
  legacy booleans → synthesized gates), row-level precedence, malformed-input
  fail-open floors;
- ``gates_of``/``grounding_of`` accessors over the REAL legacy baseline
  (new-form overlay rows work TODAY, before the baseline flips) — including
  the derivation-exactness invariant: for every baseline tag,
  ``is_tdd_exempt([t]) == ("tdd" not in gates_of(t))``;
- ``validate_tag_row``'s new ``gates``/``grounding`` forms + guards 1–2
  (tdd/coverage gates require a test grounding when the row declares one).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase


class _ProjectEnv(TestCase):
    """Env-snapshot discipline (mirror of test_task_type_field.OverrideLayerTests):
    snapshot/restore ``CLAUDE_PROJECT_DIR`` + cache_clear per test.
    """

    def setUp(self):
        from scripts.track_state import task_profiles
        self.tp = task_profiles
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        self.tp._load.cache_clear()

    def _mk_project(self):
        """A temp project tree with conductor/tracks/ (the real-project signal)."""
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        return proj

    def _set_project(self, proj):
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self.tp._load.cache_clear()

    def _write_overlay(self, proj, data):
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps(data), encoding="utf-8",
        )


class ResolveRowTests(TestCase):
    """Unit-normalization: one row in, canonical form out, input never mutated."""

    def setUp(self):
        from scripts.track_state.task_profiles import _resolve_row
        self.resolve = _resolve_row

    def test_gates_row_materializes_derived_booleans(self):
        self.assertEqual(
            self.resolve({"gates": ["tdd", "coverage", "checkpoint"]}),
            {"gates": ["tdd", "coverage", "checkpoint"],
             "tdd_exempt": False, "coverage_exempt": False})
        # Explore's shape: tdd dropped, coverage kept.
        self.assertEqual(
            self.resolve({"gates": ["coverage", "checkpoint"]}),
            {"gates": ["coverage", "checkpoint"],
             "tdd_exempt": True, "coverage_exempt": False})
        # Both exempt: checkpoint-only.
        self.assertEqual(
            self.resolve({"gates": ["checkpoint"]}),
            {"gates": ["checkpoint"], "tdd_exempt": True, "coverage_exempt": True})

    def test_legacy_row_synthesizes_gates_checkpoint_always_owed(self):
        # The legacy form had no checkpoint exemption to encode — a legacy row
        # always owes the checkpoint gate.
        self.assertEqual(
            self.resolve({"tdd_exempt": True, "coverage_exempt": False}),
            {"tdd_exempt": True, "coverage_exempt": False,
             "gates": ["coverage", "checkpoint"]})
        self.assertEqual(
            self.resolve({"tdd_exempt": True, "coverage_exempt": True}),
            {"tdd_exempt": True, "coverage_exempt": True, "gates": ["checkpoint"]})
        # A partial legacy row: missing boolean reads False (not exempt).
        self.assertEqual(
            self.resolve({"coverage_exempt": True}),
            {"coverage_exempt": True,
             "gates": ["tdd", "checkpoint"]})

    def test_row_declaring_neither_form_passes_through(self):
        row = {"route": "executor"}
        self.assertEqual(self.resolve(row), {"route": "executor"})

    def test_input_row_never_mutated(self):
        row = {"gates": ["checkpoint"]}
        self.resolve(row)
        self.assertEqual(row, {"gates": ["checkpoint"]})
        legacy = {"tdd_exempt": True}
        self.resolve(legacy)
        self.assertEqual(legacy, {"tdd_exempt": True})

    def test_malformed_gates_fail_open_to_full_set(self):
        # Non-list and out-of-vocab entries must never silently grant an
        # exemption to a typo — the floor is owing everything.
        for bad in ("tdd", ["tdd", "bogus"], 3, None):
            resolved = self.resolve({"gates": bad})
            self.assertEqual(resolved["gates"], ["tdd", "coverage", "checkpoint"])
            self.assertFalse(resolved["tdd_exempt"])
            self.assertFalse(resolved["coverage_exempt"])

    def test_mixed_forms_gates_wins_at_runtime(self):
        # The validator rejects a row carrying both forms at write time; the
        # runtime read is deterministic: gates win, booleans derived.
        resolved = self.resolve({"gates": ["checkpoint"],
                                 "tdd_exempt": False, "coverage_exempt": False})
        self.assertTrue(resolved["tdd_exempt"])
        self.assertTrue(resolved["coverage_exempt"])


class BaselineResolutionTests(_ProjectEnv):
    """gates_of/grounding_of over the REAL (still legacy) plugin baseline:
    the positive accessors answer correctly TODAY — the baseline flip later
    changes the declared form, not the answers.
    """

    def test_every_baseline_tag_derivation_exact(self):
        # The compat invariant: for every baseline tag the synthesized gates
        # reproduce the legacy booleans exactly (and []-tags default too).
        for tag in self.tp.TAG_VOCAB():
            gates = self.tp.gates_of(tag)
            self.assertEqual(
                self.tp.is_tdd_exempt([tag]), "tdd" not in gates,
                f"{tag}: tdd derivation mismatch")
            self.assertEqual(
                self.tp.is_coverage_exempt([tag]), "coverage" not in gates,
                f"{tag}: coverage derivation mismatch")
        self.assertEqual(
            self.tp.is_tdd_exempt([]), "tdd" not in self.tp.gates_of("NoSuchTag"))
        self.assertEqual(
            self.tp.is_coverage_exempt([]),
            "coverage" not in self.tp.gates_of("NoSuchTag"))

    def test_baseline_gates_match_pre_inversion_semantics(self):
        self.assertEqual(self.tp.gates_of("Docs"), ("checkpoint",))
        self.assertEqual(self.tp.gates_of("Explore"), ("coverage", "checkpoint"))
        self.assertEqual(self.tp.gates_of("Refactor"),
                         ("tdd", "coverage", "checkpoint"))
        # Unknown tag → resolved default → full set (legacy default not exempt).
        self.assertEqual(self.tp.gates_of("NoSuchTag"),
                         ("tdd", "coverage", "checkpoint"))

    def test_grounding_fail_open_derives_from_gates(self):
        # Baseline rows declare no grounding yet: the fail-open derivation
        # keys off the gates, never inventing an attestation.
        self.assertEqual(self.tp.grounding_of("Docs"), "review")      # checkpoint-only
        self.assertEqual(self.tp.grounding_of("Refactor"), "test")    # owes tdd
        self.assertEqual(self.tp.grounding_of("NoSuchTag"), "test")   # full gates

    def test_grounding_declared_wins(self):
        proj = self._mk_project()
        self._write_overlay(proj, {"tags": {"Audited": {
            "gates": ["checkpoint"], "grounding": "human-attest"}}})
        self._set_project(proj)
        self.assertEqual(self.tp.grounding_of("Audited"), "human-attest")


class OverlayFormInteractionTests(_ProjectEnv):
    """Row-level precedence: what the ROW declares beats the default's form —
    both directions. This is the seam that lets the baseline flip without
    touching a single legacy project overlay.
    """

    def test_legacy_overlay_row_on_positive_default_keeps_booleans(self):
        # A positive-form default (the post-flip baseline shape) must not
        # clobber a legacy overlay row's explicit booleans with inherited gates.
        proj = self._mk_project()
        self._write_overlay(proj, {
            "default": {"route": "executor",
                        "gates": ["tdd", "coverage", "checkpoint"],
                        "grounding": "test"},
            "tags": {"OldForm": {"route": "executor",
                                 "tdd_exempt": True, "coverage_exempt": True}},
        })
        self._set_project(proj)

        self.assertTrue(self.tp.is_tdd_exempt(["OldForm"]))
        self.assertTrue(self.tp.is_coverage_exempt(["OldForm"]))
        # The synthesized gates (not the default's inherited full set) answer.
        self.assertEqual(self.tp.gates_of("OldForm"), ("checkpoint",))

    def test_positive_overlay_row_on_legacy_default(self):
        # TODAY's situation: legacy baseline, new-form overlay row. The row's
        # gates drive every predicate — new-form rows already work pre-flip.
        proj = self._mk_project()
        self._write_overlay(proj, {"tags": {"DataPipeline": {
            "route": "executor", "gates": ["checkpoint"],
            "grounding": "data-check"}}})
        self._set_project(proj)

        self.assertEqual(self.tp.gates_of("DataPipeline"), ("checkpoint",))
        self.assertTrue(self.tp.is_tdd_exempt(["DataPipeline"]))
        self.assertTrue(self.tp.is_coverage_exempt(["DataPipeline"]))
        self.assertEqual(self.tp.grounding_of("DataPipeline"), "data-check")
        # The phase fan-out's code-free predicate follows the gates.
        self.assertTrue(self.tp._task_is_code_free(["DataPipeline"]))

    def test_row_declaring_neither_inherits_resolved_default(self):
        proj = self._mk_project()
        self._write_overlay(proj, {
            "default": {"route": "executor", "gates": ["checkpoint"],
                        "grounding": "review"},
            "tags": {"Bare": {"route": "executor"}},
        })
        self._set_project(proj)

        self.assertEqual(self.tp.gates_of("Bare"), ("checkpoint",))
        self.assertTrue(self.tp.is_tdd_exempt(["Bare"]))
        self.assertEqual(self.tp.grounding_of("Bare"), "review")

    def test_multi_tag_any_exemption_composes_from_gates(self):
        # Task-level semantics unchanged: a gate is dropped when ANY tag lacks
        # it (the ANY-exemption rule) — [Config][Refactor] is exempt from both
        # gates via Config, yet NOT code-free (Refactor produces code).
        self.assertTrue(self.tp.is_coverage_exempt(["Config", "Refactor"]))
        self.assertTrue(self.tp.is_tdd_exempt(["Config", "Refactor"]))
        self.assertFalse(self.tp._task_is_code_free(["Config", "Refactor"]))


class ValidateTagRowFormsTests(TestCase):
    """validate_tag_row: the new gates/grounding fields + guards 1–2."""

    def setUp(self):
        from scripts.track_state.registry_validate import validate_tag_row
        self.validate = validate_tag_row

    def test_positive_forms_valid(self):
        self.assertEqual(self.validate("X", {
            "gates": ["tdd", "coverage", "checkpoint"], "grounding": "test"}), [])
        self.assertEqual(self.validate("X", {
            "gates": ["checkpoint"], "grounding": "review"}), [])
        self.assertEqual(self.validate("X", {
            "gates": ["checkpoint"], "grounding": "human-attest"}), [])
        self.assertEqual(self.validate("X", {
            "gates": ["checkpoint"], "grounding": "data-check"}), [])

    def test_legacy_rows_still_valid(self):
        # Overlay compat: the legacy booleans remain a valid row form.
        self.assertEqual(self.validate("X", {
            "route": "executor", "tdd_exempt": True, "coverage_exempt": True}), [])

    def test_unknown_grounding_rejected(self):
        errs = self.validate("X", {"grounding": "vibes"})
        self.assertTrue(any("grounding" in e for e in errs))

    def test_gates_shape_rejected(self):
        self.assertTrue(self.validate("X", {"gates": "tdd"}))
        errs = self.validate("X", {"gates": ["tdd", "bogus"]})
        self.assertTrue(any("not in" in e and "bogus" in e for e in errs))

    def test_guards_test_witnessing_gates_need_test_grounding(self):
        # Guard 1: tdd gate + non-test grounding declared on the row.
        errs = self.validate("X", {
            "gates": ["tdd", "checkpoint"], "grounding": "review"})
        self.assertEqual(len(errs), 1)
        self.assertIn("witness a test-grounded deliverable", errs[0])
        # Guard 2: coverage gate + non-test grounding.
        errs = self.validate("X", {
            "gates": ["coverage", "checkpoint"], "grounding": "data-check"})
        self.assertEqual(len(errs), 1)
        # tdd/coverage with grounding test: clean.
        self.assertEqual(self.validate("X", {
            "gates": ["coverage", "checkpoint"], "grounding": "test"}), [])

    def test_guards_only_fire_when_row_declares_grounding(self):
        # A gates-only row may inherit a consistent grounding from the
        # default — the raw-row guard stays silent; the merged-level check
        # (added with the tag_add writer) catches violating inheritance.
        self.assertEqual(self.validate("X", {"gates": ["tdd", "checkpoint"]}), [])


if __name__ == "__main__":
    unittest.main()
