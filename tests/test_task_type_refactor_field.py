"""Wiring tests for the task-type ``refactor`` field (the ``[Refactor]``
generalization).

Tactical refactor was opt-in by name-marker only — the ``[Refactor]`` inline
marker or ``CONDUCTOR_TASK_REFACTOR=1`` env. The ``refactor`` field promotes it
to a declarative task-type property: a row sets ``refactor: true`` and a whole
class of tasks opts into the §3.6c tactical-refactor seam with zero plugin edits.
The ``[Refactor]`` name marker and env remain as escape hatches.

These tests pin:
- ``refactor_for`` resolves the field, inheriting the default (False);
- ``[Refactor]`` is now a real tag (recognized, routed to executor, NOT
  TDD/coverage-exempt — it still owes a working test, only the refactor flag is
  set);
- the field flows through the project-overlay layer with zero plugin edits.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

from track_state import task_profiles as tp  # noqa: E402


class RefactorForAccessorTests(TestCase):
    """``refactor_for(tag)`` is the single switch §3.6c reads — mirrors
    ``workflow_for``. Absent/False = no tactical refactor (default)."""

    def test_refactor_tag_is_true(self):
        self.assertTrue(tp.refactor_for("Refactor"))

    def test_exempt_tags_inherit_default_false(self):
        # The TDD/coverage-exempt tags do NOT get a refactor pass — refactor
        # owes a working test, so an exempt tag is the wrong pairing.
        for tag in ("Config", "Docs", "Chore"):
            self.assertFalse(tp.refactor_for(tag), f"{tag} must be refactor=False")

    def test_explore_is_false(self):
        self.assertFalse(tp.refactor_for("Explore"))

    def test_default_is_false(self):
        # The untagged-task profile inherits default.refactor = False.
        self.assertFalse(tp.refactor_for("default"))

    def test_unknown_tag_inherits_default_false(self):
        self.assertFalse(tp.refactor_for("does-not-exist"))


class RefactorIsRealTagTests(TestCase):
    """``[Refactor]`` is promoted from a name marker to a real registry tag.
    It must be recognized, routed to the executor, and — crucially — NOT
    TDD/coverage-exempt (a refactor task still owes a working test; only the
    tactical-refactor flag is set)."""

    def test_refactor_in_vocab(self):
        self.assertIn("Refactor", tp.TAG_VOCAB())

    def test_refactor_routes_to_executor(self):
        self.assertEqual(tp.route_for(["Refactor"]), "executor")

    def test_refactor_not_tdd_exempt(self):
        self.assertFalse(tp.is_tdd_exempt(["Refactor"]))

    def test_refactor_not_coverage_exempt(self):
        self.assertFalse(tp.is_coverage_exempt(["Refactor"]))

    def test_refactor_tag_accepted_by_parser(self):
        # End-to-end: a [Refactor] task name parses with no unknown-tag error.
        from scripts.track_state.plan_parse import _find_unknown_tags
        self.assertEqual(_find_unknown_tags("[Refactor] extract the helper"), [])

    def test_refactor_tag_has_no_signals(self):
        # [Refactor] is a deliberate opt-in, NOT a goal-detected tag. It carries
        # NO `signals` so derive_task_tag never auto-proposes it — otherwise any
        # description containing "refactor"/"extract"/"simplify" would silently
        # opt into the tactical refactorer. Pinned in test_derive_task_tag too.
        self.assertFalse(tp._profile("Refactor").get("signals"),
                         "[Refactor] must not carry signals (it is opt-in, not auto-derived)")


class RefactorOverlayTests(TestCase):
    """The project-overlay layer: a project drops
    ``conductor/workflow/task-type-profiles.json`` declaring ``refactor: true``
    on a project tag and it flows through with ZERO plugin edits — the
    ``[Refactor]`` generalization."""

    def setUp(self):
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")
        self._cwd = os.getcwd()

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        os.chdir(self._cwd)
        tp._load.cache_clear()

    def _mk_project(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        return proj

    def test_overlay_project_tag_carries_refactor(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps({"tags": {"TechDebt": {
                "route": "executor", "refactor": True,
                "when_to_use": "Pay down tech debt with a refactor pass."}}}),
            encoding="utf-8",
        )
        tp._load.cache_clear()

        # Zero plugin edits: the project tag's refactor flag flows.
        self.assertIn("TechDebt", tp.TAG_VOCAB())
        self.assertTrue(tp.refactor_for("TechDebt"))
        # Built-in [Refactor] still present (merge, not replace).
        self.assertTrue(tp.refactor_for("Refactor"))

    def test_overlay_keeps_builtins_when_adding_refactor_tag(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps({"tags": {"TechDebt": {"route": "executor", "refactor": True}}}),
            encoding="utf-8",
        )
        tp._load.cache_clear()

        self.assertEqual(tp.route_for(["Chore"]), "executor")  # built-in untouched
        self.assertFalse(tp.refactor_for("Chore"))


if __name__ == "__main__":
    main()
