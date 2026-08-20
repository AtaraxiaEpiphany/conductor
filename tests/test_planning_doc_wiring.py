"""Wiring tests for the planning docfile seam (planning-as-data Phase A).

Mirrors ``test_workflow_doc_wiring`` / the registry-doc docfile tests one
layer up: the workflow-shape registry's ``planning_doc`` field points into a
planning library (``templates/planning/`` plugin side, ``conductor/planning/``
project side, project wins), ``resolve_planning_doc`` fails open to the
default docfile, ``registry-doc --shape`` renders the resolved docfile
verbatim, and every shipped shape's declared docfile actually exists.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import workflow_shapes as ws

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "templates" / "workflow" / "workflow-shapes.json"
PLANNING = ROOT / "templates" / "planning"
CLI = ROOT / "scripts" / "track-state"


class ShippedDocfileTests(TestCase):
    """Every shipped shape's declared planning docfile exists and resolves."""

    def test_every_shipped_shape_declares_a_existing_docfile(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for shape, prof in data["shapes"].items():
            declared = prof.get("planning_doc")
            self.assertTrue(
                declared,
                f"shape [{shape}] must declare its `planning_doc` explicitly")
            self.assertTrue(
                (PLANNING / declared).is_file(),
                f"shape [{shape}] declares planning_doc {declared!r} but the "
                f"plugin planning dir has no such file")

    def test_declared_docfiles_resolve_to_themselves(self):
        # resolve_planning_doc must point at the DECLARED file (not fail-open
        # to default) for every shipped shape.
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for shape, prof in data["shapes"].items():
            resolved = ws.resolve_planning_doc(shape)
            self.assertEqual(resolved.name, prof["planning_doc"])
            self.assertTrue(resolved.is_file())

    def test_planning_library_carries_the_four_docfiles(self):
        for name in ("default.md", "migration.md", "deliverable.md",
                     "research-first.md"):
            self.assertTrue((PLANNING / name).is_file(),
                            f"planning library missing {name}")

    def test_default_shape_signals_absent(self):
        # The default shape is the fail-open FALLBACK, never a competitor:
        # it deliberately declares no `signals` (only candidate shapes carry
        # them — mirrors opt-in task tags omitting signals).
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertNotIn("signals", data["shapes"]["default"])
        for shape in ("research-first", "migration", "deliverable"):
            self.assertIn("signals", data["shapes"][shape],
                          f"candidate shape [{shape}] must declare signals")


class ResolverFailOpenTests(TestCase):
    """``resolve_planning_doc`` fails open to the default docfile — the same
    contract ``resolve_workflow_doc`` holds one layer down."""

    def setUp(self):
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        ws._load.cache_clear()

    def test_unknown_shape_resolves_default_docfile(self):
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        ws._load.cache_clear()
        resolved = ws.resolve_planning_doc("typo-shape")
        self.assertEqual(resolved.name, "default.md")
        self.assertTrue(resolved.is_file())

    def test_malformed_docfile_name_fails_open(self):
        # A path-y planning_doc (typo / traversal) never resolves — warn and
        # fall back to the default docfile, never a raise.
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj,
                                                            ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        Path(proj, "conductor", "workflow", "workflow-shapes.json").write_text(
            json.dumps({"shapes": {"evil": {
                "nodes": ["spec-planner", "task-executor", "phase-checker"],
                "planning_doc": "../../etc/passwd.md"}}}), encoding="utf-8")
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        ws._load.cache_clear()
        resolved = ws.resolve_planning_doc("evil")
        self.assertEqual(resolved.name, "default.md")

    def test_project_planning_dir_wins(self):
        # Project overrides a shipped docfile (or adds a bespoke one) with
        # zero plugin edits: conductor/planning/<name> beats
        # templates/planning/<name>.
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj,
                                                            ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        Path(proj, "conductor", "workflow", "workflow-shapes.json").write_text(
            json.dumps({"shapes": {"rollout": {
                "nodes": ["spec-planner", "task-executor", "phase-checker"],
                "planning_doc": "rollout.md"}}}), encoding="utf-8")
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        ws._load.cache_clear()
        # No project docfile yet → fail-open to default.
        self.assertEqual(ws.resolve_planning_doc("rollout").name, "default.md")
        # Drop the project docfile in → project wins.
        Path(proj, "conductor", "planning").mkdir(parents=True)
        Path(proj, "conductor", "planning", "rollout.md").write_text(
            "# Planning: rollout\n\nproject overlay body\n",
            encoding="utf-8")
        resolved = ws.resolve_planning_doc("rollout")
        self.assertEqual(resolved.name, "rollout.md")
        self.assertIn("project overlay body",
                      resolved.read_text(encoding="utf-8"))


class RegistryDocShapeRenderTests(TestCase):
    """``registry-doc --shape`` renders the resolved planning docfile
    verbatim — the Tier-B fetch planners/orchestrators consume (mirrors the
    ``--tag`` workflow-docfile render)."""

    def _run(self, *args):
        env = {**os.environ}
        env.pop("CLAUDE_PROJECT_DIR", None)
        proc = subprocess.run(
            [sys.executable, str(CLI), "registry-doc", *args],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 0,
                         f"registry-doc {args} failed: {proc.stderr}\n"
                         f"{proc.stdout}")
        return proc.stdout

    def test_shape_migration_renders_planning_docfile(self):
        out = self._run("--shape", "migration")
        self.assertIn(
            "Planning docfile for `migration`: `migration.md`", out)
        # The docfile body renders verbatim (planner-facing procedure).
        self.assertIn("Prelude (orchestrator)", out)
        self.assertIn("BEHAVIOR-PRESERVATION", out)
        self.assertIn("`[Migrate]`", out)

    def test_shape_default_renders_default_docfile(self):
        out = self._run("--shape", "default")
        self.assertIn(
            "Planning docfile for `default`: `default.md`", out)
        self.assertIn("TESTED-CODE", out)

    def test_shape_render_carries_when_to_use_gloss(self):
        # when_to_use stays the human-facing rationale (the gloss for the
        # machine `signals`) — rendered alongside the docfile.
        out = self._run("--shape", "deliverable")
        self.assertIn("`when_to_use` for `deliverable`", out)
        self.assertIn("DELIVERABLE", out)


if __name__ == "__main__":
    main()
