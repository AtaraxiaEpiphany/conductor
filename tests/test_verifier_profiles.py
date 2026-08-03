"""Wiring tests for the verifier registry (the fourth axis).

The verifier axis graduated to a data-driven registry
(``verifier-profiles.json`` + ``verifier_profiles.py``, baseline ⊕ project
overlay) — mirroring the task-type, verify-mode, and workflow-shape registries.
The checkpoint verifiers (``ac-tracer``, ``test-runner``, ``compile-runner``)
are now registry rows instead of hardcoded fan-out members:
``VERIFIER_VOCAB``/``field_set_for``/``agent_for``/``when_to_use_for`` all
derive from the registry, dispatch's ``_build_verifier`` reads each row's
``field_set`` (no hardcoded ``if agent == "test-runner"``), and
``registry-doc --verifier`` renders it. ``compile-runner`` is the build
verify-only tier — the mirror of test-runner fanned out on a build-gated
(``compile``/``none``) phase instead of test-runner (see
``workflow_shapes.verifiers_for``'s per-phase substitution).

These tests pin that contract:

- the registry data file exists and carries all three verifiers;
- ``VERIFIER_VOCAB``/``field_set_for``/``agent_for`` flow (test-runner AND
  compile-runner add ``PHASE_INDEX``, ac-tracer does not);
- the project overlay layer (``conductor/workflow/verifier-profiles.json``)
  adds/overrides a verifier with ZERO plugin edits, fail-open on malformed;
- accessors return copies / are fail-open on unknown.

Mirrors ``test_workflow_shapes.py`` (overlay discipline + drift guard).
"""
import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import verifier_profiles as vp

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "templates" / "workflow" / "verifier-profiles.json"

# The baseline verifiers the registry ships.
BASELINE_VERIFIERS = ("ac-tracer", "test-runner", "compile-runner")


class RegistryVerifierTests(TestCase):
    def setUp(self):
        self.assertTrue(REGISTRY.exists(), "verifier-profiles.json must exist")

    def test_registry_carries_baseline_verifiers(self):
        for v in BASELINE_VERIFIERS:
            self.assertIn(v, vp.VERIFIER_VOCAB(),
                          f"baseline verifier {v!r} missing from registry")

    def test_every_verifier_carries_agent_and_field_set(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for name, prof in data["verifiers"].items():
            self.assertIn("agent", prof,
                          f"verifier [{name}] missing 'agent'")
            self.assertIsInstance(prof["field_set"], list,
                                  f"verifier [{name}] field_set must be a list")

    def test_test_runner_field_set_adds_phase_index(self):
        # The load-bearing distinction: test-runner's §2.0 ASSIGNMENT adds
        # PHASE_INDEX; ac-tracer takes only TRACK_DIR/TRACK_ID. Pin it as a
        # registry row so _build_verifier is data-driven.
        self.assertEqual(vp.field_set_for("ac-tracer"),
                         ("TRACK_DIR", "TRACK_ID"))
        self.assertEqual(vp.field_set_for("test-runner"),
                         ("TRACK_DIR", "TRACK_ID", "PHASE_INDEX"))

    def test_compile_runner_mirrors_test_runner_field_set(self):
        # compile-runner is the build verify-only mirror of test-runner: same
        # field_set (TRACK_DIR/TRACK_ID/PHASE_INDEX), distinct agent, distinct
        # when_to_use (BUILD, not suite). The build floor in the `none`/`compile`
        # protocols reads BUILD_VERIFY_STATUS from this verifier's result block.
        self.assertEqual(vp.field_set_for("compile-runner"),
                         ("TRACK_DIR", "TRACK_ID", "PHASE_INDEX"))
        self.assertEqual(vp.agent_for("compile-runner"), "compile-runner")
        self.assertIn("build", vp.when_to_use_for("compile-runner").lower())

    def test_agent_for_defaults_to_name(self):
        # A verifier row's `agent` defaults to the verifier key itself.
        self.assertEqual(vp.agent_for("ac-tracer"), "ac-tracer")
        self.assertEqual(vp.agent_for("test-runner"), "test-runner")
        self.assertEqual(vp.agent_for("compile-runner"), "compile-runner")

    def test_when_to_use_returns_registry_prose(self):
        self.assertIn("AC", vp.when_to_use_for("ac-tracer"))
        self.assertIn("L1", vp.when_to_use_for("test-runner"))
        self.assertIn("BUILD", vp.when_to_use_for("compile-runner"))

    def test_vocab_matches_registry_keys(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(set(vp.VERIFIER_VOCAB()), set(data["verifiers"].keys()))

    def test_default_verifiers_constant_is_the_standard_pair(self):
        # The fail-open floor workflow_shapes.verifiers_for falls back to.
        self.assertEqual(vp.DEFAULT_VERIFIERS, ("ac-tracer", "test-runner"))


class FailOpenTests(TestCase):
    """Accessors are fail-open: an unknown/malformed verifier never raises."""

    def test_unknown_verifier_field_set_is_empty(self):
        self.assertEqual(vp.field_set_for("ghost"), ())

    def test_unknown_verifier_when_to_use_is_empty_string(self):
        self.assertEqual(vp.when_to_use_for("ghost"), "")

    def test_unknown_verifier_agent_defaults_to_name(self):
        self.assertEqual(vp.agent_for("ghost"), "ghost")


class OverrideLayerTests(TestCase):
    """The project-local override layer: a project drops
    ``conductor/workflow/verifier-profiles.json`` and its verifiers flow through
    with ZERO plugin edits — plugin baseline ⊕ project overlay, project wins
    conflicts. Mirrors test_workflow_shapes.py::OverrideLayerTests.
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
        vp._load.cache_clear()

    def _mk_project(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        return proj

    def _write_overlay(self, proj, data):
        Path(proj, "conductor", "workflow", "verifier-profiles.json").write_text(
            json.dumps(data), encoding="utf-8",
        )

    def test_project_override_adds_verifier(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"verifiers": {"lint-runner": {
            "agent": "test-runner",  # reuse an existing agent def for the test
            "field_set": ["TRACK_DIR", "TRACK_ID", "PHASE_INDEX"],
            "when_to_use": "Runs the linter."}}})
        vp._load.cache_clear()

        # Zero plugin edits: the new verifier flows through every consumer.
        self.assertIn("lint-runner", vp.VERIFIER_VOCAB())
        self.assertEqual(vp.field_set_for("lint-runner"),
                         ("TRACK_DIR", "TRACK_ID", "PHASE_INDEX"))
        self.assertIn("linter", vp.when_to_use_for("lint-runner"))

    def test_project_overlay_merges_keeps_builtins(self):
        # Overlay declares ONLY a new verifier — built-ins must survive.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"verifiers": {"lint-runner": {
            "field_set": ["TRACK_DIR", "TRACK_ID"]}}})
        vp._load.cache_clear()

        for v in BASELINE_VERIFIERS:
            self.assertIn(v, vp.VERIFIER_VOCAB())  # built-ins still present

    def test_project_overlay_overrides_builtin(self):
        # Project re-declares test-runner with a different field_set → project wins.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"verifiers": {"test-runner": {
            "field_set": ["TRACK_DIR", "TRACK_ID"]}}})  # drop PHASE_INDEX
        vp._load.cache_clear()

        self.assertEqual(vp.field_set_for("test-runner"),
                         ("TRACK_DIR", "TRACK_ID"))  # overridden
        # ac-tracer untouched.
        self.assertEqual(vp.field_set_for("ac-tracer"),
                         ("TRACK_DIR", "TRACK_ID"))

    def test_malformed_overlay_falls_back_to_baseline(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        Path(proj, "conductor", "workflow", "verifier-profiles.json").write_text(
            "{ not valid json", encoding="utf-8",
        )
        vp._load.cache_clear()

        # No crash, built-in vocab intact.
        self.assertEqual(set(vp.VERIFIER_VOCAB()), set(BASELINE_VERIFIERS))
        self.assertEqual(vp.field_set_for("test-runner"),
                         ("TRACK_DIR", "TRACK_ID", "PHASE_INDEX"))


if __name__ == "__main__":
    main()
