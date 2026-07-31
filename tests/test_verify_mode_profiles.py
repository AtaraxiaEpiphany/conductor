"""Wiring tests for the phase-verify-mode registry.

The verify-mode axis graduated to a data-driven registry
(``verify-mode-profiles.json`` + ``verify_mode_profiles.py``, baseline ⊕ project
overlay) — mirroring the task-type registry. Adding a verify-mode is now one JSON
row with zero Python edits and zero agent-prose edits: the phase-checker's Step-3
directive is a mode-agnostic loop that reads each mode's ``protocol`` from the
registry. These tests pin that contract:

- the registry data file exists and carries every mode's semantics + protocol;
- ``MODE_VOCAB``/``runs_for``/``fix_policy_for``/``protocol_for`` flow;
- the project overlay layer (``conductor/workflow/verify-mode-profiles.json``)
  adds/overrides a mode with ZERO plugin edits, fail-open on malformed overlay;
- the registry vocab is the single source (no drift vs the in-code vocab).

Mirrors ``test_task_type_field.py::OverrideLayerTests`` (overlay discipline) and
``test_plan_format_contract_wiring.py::RegistryDriftTests`` (drift guard).
"""
import json
import os
import tempfile
from io import StringIO
from contextlib import redirect_stderr
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import verify_mode_profiles as vmp

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "templates" / "workflow" / "verify-mode-profiles.json"

# The baseline modes the registry ships. Drift between this list, the
# contract table, and the registry is what RegistryDriftTests guards.
BASELINE_MODES = ("compile", "test", "start", "adversarial", "anchor", "none")


class RegistryShapeTests(TestCase):
    def setUp(self):
        self.assertTrue(REGISTRY.exists(), "verify-mode-profiles.json must exist")

    def test_registry_carries_baseline_modes(self):
        for mode in BASELINE_MODES:
            self.assertIn(mode, vmp.MODE_VOCAB(),
                          f"baseline mode {mode!r} missing from registry")

    def test_every_mode_carries_protocol_and_runs(self):
        # Each registered mode must declare runs + fix_policy + protocol + the
        # report_field (the fields the phase-checker loop reads). A row missing
        # a key silently inherits the default — assert intent is explicit so a
        # future editor sees the full picture per row.
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for mode, prof in data["modes"].items():
            for field in ("runs", "fix_policy", "report_field", "protocol"):
                self.assertIn(
                    field, prof,
                    f"registry mode [{mode}] missing required field '{field}'",
                )

    def test_protocol_for_returns_registry_prose(self):
        # The single-source invariant: protocol_for() returns the prose that
        # USED to live inline in phase-checker.md. Pin a load-bearing literal
        # per mode so a regression (prose deleted from the registry) is caught.
        self.assertIn("Ignore the `L1_VERIFY_STATUS`", vmp.protocol_for("compile"))
        self.assertIn("BUILD: passed", vmp.protocol_for("compile"))
        self.assertIn("boot smoke", vmp.protocol_for("start"))
        self.assertIn("frozen_anchor_pass_rate", vmp.protocol_for("anchor"))
        self.assertIn("frozen_anchor_drift_rate", vmp.protocol_for("anchor"))
        self.assertIn("no frozen anchor", vmp.protocol_for("anchor"))
        # The debt-carrying migration mode: gates on nothing, passes on intent.
        self.assertIn("debt-carrying phase", vmp.protocol_for("none"))
        self.assertIn("PASSES on the operator's declared intent",
                      vmp.protocol_for("none"))

    def test_runs_and_fix_policy_flow(self):
        self.assertEqual(vmp.runs_for("compile"), ["build"])
        self.assertEqual(vmp.fix_policy_for("compile"), "none")
        self.assertEqual(vmp.runs_for("anchor"), ["frozen-subset"])
        self.assertEqual(vmp.fix_policy_for("start"), "fail-fast")
        self.assertEqual(vmp.fix_policy_for("test"), "fix-and-retry")
        # The none mode gates on nothing: no runs, no fix-and-retry.
        self.assertEqual(vmp.runs_for("none"), [])
        self.assertEqual(vmp.fix_policy_for("none"), "none")

    def test_vocab_matches_registry_keys(self):
        # The in-code MODE_VOCAB() is derived from the registry keys — they must
        # be identical (the dedup contract: one source of truth).
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(set(vmp.MODE_VOCAB()), set(data["modes"].keys()))


class OverrideLayerTests(TestCase):
    """The project-local override layer: a project drops
    ``conductor/workflow/verify-mode-profiles.json`` and its modes flow through
    the full pipeline with ZERO plugin edits — plugin baseline ⊕ project overlay,
    project wins conflicts.

    Mirrors test_task_type_field.py::OverrideLayerTests.
    """

    def setUp(self):
        # Snapshot the env + cwd we mutate so every test restores them.
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")
        self._cwd = os.getcwd()

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        os.chdir(self._cwd)
        vmp._load.cache_clear()

    def _mk_project(self):
        """A temp project tree with conductor/tracks/ (the real-project signal)."""
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        return proj

    def _write_overlay(self, proj, data):
        Path(proj, "conductor", "workflow", "verify-mode-profiles.json").write_text(
            json.dumps(data), encoding="utf-8",
        )

    def test_project_override_adds_mode(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"modes": {"lint": {
            "runs": ["lint"], "fix_policy": "none", "report_field": "LINT",
            "protocol": "Run the linter once; no fix-and-retry."}}})
        vmp._load.cache_clear()

        # Zero plugin edits: the new mode flows through every consumer.
        self.assertIn("lint", vmp.MODE_VOCAB())
        self.assertEqual(vmp.runs_for("lint"), ["lint"])
        self.assertEqual(vmp.fix_policy_for("lint"), "none")
        self.assertEqual(vmp.protocol_for("lint"), "Run the linter once; no fix-and-retry.")

    def test_project_overlay_merges_keeps_builtins(self):
        # Overlay declares ONLY a new mode — built-ins must survive (merge, not replace).
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"modes": {"lint": {
            "runs": ["lint"], "fix_policy": "none", "report_field": "LINT",
            "protocol": "lint"}}})
        vmp._load.cache_clear()

        self.assertIn("compile", vmp.MODE_VOCAB())  # built-in still present
        self.assertEqual(vmp.runs_for("compile"), ["build"])

    def test_project_overlay_overrides_builtin(self):
        # Project re-declares compile with a different fix_policy → project wins.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"modes": {"compile": {
            "runs": ["build"], "fix_policy": "fail-fast", "report_field": "BUILD",
            "protocol": "overridden"}}})
        vmp._load.cache_clear()

        self.assertEqual(vmp.fix_policy_for("compile"), "fail-fast")  # overridden
        self.assertEqual(vmp.protocol_for("compile"), "overridden")
        # Other built-ins untouched.
        self.assertEqual(vmp.fix_policy_for("anchor"), "none")

    def test_malformed_overlay_falls_back_to_baseline(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        Path(proj, "conductor", "workflow", "verify-mode-profiles.json").write_text(
            "{ not valid json", encoding="utf-8",
        )
        vmp._load.cache_clear()

        # No crash, built-in vocab intact.
        self.assertEqual(set(vmp.MODE_VOCAB()), set(BASELINE_MODES))
        self.assertEqual(vmp.fix_policy_for("compile"), "none")

    def test_malformed_shape_overlay_falls_back_to_baseline(self):
        # Structurally-wrong overlay (not an object) → baseline alone.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"default": {"fix_policy": "none"}})  # no 'modes' key
        vmp._load.cache_clear()

        self.assertEqual(set(vmp.MODE_VOCAB()), set(BASELINE_MODES))

    def test_no_override_file_no_change(self):
        # CLAUDE_PROJECT_DIR set to a project tree with NO overlay file → baseline.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        vmp._load.cache_clear()

        self.assertEqual(set(vmp.MODE_VOCAB()), set(BASELINE_MODES))

    def test_warning_fires_on_malformed_overlay(self):
        # The fail-open contract logs loudly so a malformed overlay is diagnosable.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        Path(proj, "conductor", "workflow", "verify-mode-profiles.json").write_text(
            "{ bad", encoding="utf-8",
        )
        buf = StringIO()
        with redirect_stderr(buf):
            vmp._load.cache_clear()
            vmp._load()
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("verify-mode-profiles.json", buf.getvalue())

    def test_overlay_mode_accepted_by_parser(self):
        # End-to-end: a plan with verify: lint is accepted (no warning) once the
        # overlay registers the mode — mirroring the task-tag overlay E2E test.
        # NOTE: no ``plan_parse`` internals are patched here. The parser resolves
        # the vocab live via MODE_VOCAB() per call (mirrors helpers.extract_tags),
        # so the overlay alone — via the cache_clear below — must suffice. (This
        # test USED to monkeypatch ``pp._VERIFY_MODES`` to work around the
        # import-time snapshot; that snapshot no longer exists.)
        from scripts.track_state.plan_parse import parse_plan
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"modes": {"lint": {
            "runs": ["lint"], "fix_policy": "none", "report_field": "LINT",
            "protocol": "lint"}}})
        vmp._load.cache_clear()

        import tempfile as _tf
        f = _tf.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        f.write("# Plan\n\n## Phase 1: Lint <!-- verify: lint -->\n\n"
                "- [ ] [Chore] lint <!-- AC-1 -->\n- [ ] [Manual] verify\n")
        f.flush()
        result = parse_plan(f.name)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["phases"][0]["verify_modes"], ["lint"])
        self.assertFalse(any("unrecognized verify mode" in w for w in result["warnings"]))

    def test_verify_mode_overlay_recognized_without_snapshot(self):
        # Regression guard for the import-time-freeze bug: an overlay mode added
        # AFTER ``plan_parse`` is imported must be recognized with no monkeypatch
        # of parser internals. Pre-fix this warned "unrecognized verify mode" and
        # dropped the mode (the silent-drop asymmetry the tag side was already
        # fixed to avoid). Mirrors the tag-side E2E shape (test_registry_add).
        import scripts.track_state.plan_parse as pp  # noqa: F401  — import first
        from scripts.track_state.plan_parse import parse_plan
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"modes": {"lint": {
            "runs": ["lint"], "fix_policy": "none", "report_field": "LINT",
            "protocol": "Run the linter once."}}})
        vmp._load.cache_clear()
        self.assertIn("lint", vmp.MODE_VOCAB())  # overlay took

        import tempfile as _tf
        f = _tf.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        f.write("# Plan\n\n## Phase 1: Lint <!-- verify: lint -->\n\n"
                "- [ ] [Chore] lint <!-- AC-1 -->\n- [ ] [Manual] verify\n")
        f.flush()
        result = parse_plan(f.name)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["phases"][0]["verify_modes"], ["lint"])
        self.assertFalse(any("unrecognized verify mode" in w for w in result["warnings"]))


if __name__ == "__main__":
    main()
