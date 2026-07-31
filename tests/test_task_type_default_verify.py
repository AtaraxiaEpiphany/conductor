"""Wiring tests for the task-type ``default_verify`` field (tag-driven phase-verify default).

The phase-verify directive's authoring-time default has two independent sources:
the phase's GOAL text (``derive_verify_modes`` — goal-driven, already shipped) and
the phase's TASK TAGS (``default_verify`` — tag-driven, this field). The planner
composes them by precedence (explicit > tag-derived > goal-derived > full gate);
the contract is documented in plan-format-contract.md §"Phase Verify Directives
→ Default source".

This field is the ``[Migrate]`` generalization of "a migration phase's gate
defaults to compile": a task-type row declares ``default_verify`` and a phase
composed of that tag gets that directive proposed at plan generation, with zero
plugin edits (a project-overlay tag flows the same way).

These tests pin:
- ``default_verify_for`` resolves the field (Migrate→["compile"], others→[]);
- ``default_verify_for_phase`` reduces across a phase's tags (agreement passes,
  conflict → [] full gate, empty → []);
- unknown modes are DROPPED (fail-open, never raised) — the loader stays
  crash-free over a malformed row;
- the returned list is a copy (registry lists are shared module state);
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
from track_state import verify_mode_profiles as vmp  # noqa: E402


# --- accessor ---------------------------------------------------------------

class DefaultVerifyAccessorTests(TestCase):
    def test_migrate_carries_compile(self):
        # The canonical case: a migration phase's suite is expected red, the
        # build is the gate.
        self.assertEqual(tp.default_verify_for("Migrate"), ["compile"])

    def test_other_builtin_tags_have_no_default(self):
        # Only [Migrate] carries a default_verify today — the other tags are not
        # phase-gate-shaped (Config/Chore/Docs are exemption tags; Manual is
        # human; Refactor wants anchor only with a frozen subset; Explore
        # produces no committable artifact).
        for tag in ("Config", "Chore", "Docs", "Manual", "Refactor", "Explore"):
            self.assertEqual(tp.default_verify_for(tag), [],
                             f"{tag} must not carry a default_verify")

    def test_unknown_tag_is_empty(self):
        # An unrecognized tag inherits the default profile (no default_verify).
        self.assertEqual(tp.default_verify_for("NoSuchTag"), [])

    def test_returns_a_copy(self):
        # Registry lists are shared module state; a caller mutating the return
        # must not corrupt the cached profile.
        first = tp.default_verify_for("Migrate")
        first.append("mutated")
        second = tp.default_verify_for("Migrate")
        self.assertEqual(second, ["compile"],
                         "default_verify_for must return a fresh copy each call")


# --- reducer ----------------------------------------------------------------

class DefaultVerifyForPhaseTests(TestCase):
    def test_single_migrate_tag(self):
        self.assertEqual(vmp.default_verify_for_phase(["Migrate"]), ["compile"])

    def test_agreement_is_not_a_conflict(self):
        # Two [Migrate] tasks both proposing ["compile"] is agreement.
        self.assertEqual(vmp.default_verify_for_phase(["Migrate", "Migrate"]),
                         ["compile"])

    def test_mixed_with_no_default_tag_keeps_default(self):
        # [Migrate] + [Config] (Config has no default_verify): the only
        # contributor is Migrate → no conflict → compile.
        self.assertEqual(vmp.default_verify_for_phase(["Migrate", "Config"]),
                         ["compile"])

    def test_no_contributing_tag_is_empty(self):
        self.assertEqual(vmp.default_verify_for_phase(["Config", "Docs"]), [])
        self.assertEqual(vmp.default_verify_for_phase(["Manual"]), [])

    def test_empty_input_is_empty(self):
        self.assertEqual(vmp.default_verify_for_phase([]), [])

    def test_non_list_input_is_empty(self):
        # Fail-open: a None / non-list never raises.
        self.assertEqual(vmp.default_verify_for_phase(None), [])  # type: ignore[arg-type]


# --- conflict via overlay (construct without polluting the baseline) --------

class DefaultVerifyConflictTests(TestCase):
    """A phase whose tags propose DIFFERENT non-empty default_verify sets has no
    single gate semantics → the reducer returns [] (the full gate). Built with a
    project overlay so the baseline registry is untouched."""

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
        vmp._load.cache_clear()

    def _mk_project(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        return proj

    def test_conflicting_defaults_yield_full_gate(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        # Add a project tag whose default_verify differs from [Migrate]'s
        # ["compile"] — a phase mixing [Migrate] and [Bootloader] has no single
        # gate, so the safe full gate wins.
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps({"tags": {"Bootloader": {
                "route": "executor", "default_verify": ["test", "start"]}}}),
            encoding="utf-8",
        )
        tp._load.cache_clear()
        vmp._load.cache_clear()

        self.assertIn("Bootloader", tp.TAG_VOCAB())
        self.assertEqual(tp.default_verify_for("Bootloader"), ["test", "start"])
        # Conflict: Migrate=["compile"], Bootloader=["test","start"] → [] (full gate).
        self.assertEqual(
            vmp.default_verify_for_phase(["Migrate", "Bootloader"]), [])
        # Each alone is fine (no conflict within one tag).
        self.assertEqual(vmp.default_verify_for_phase(["Bootloader"]),
                         ["test", "start"])

    def test_overlay_tag_flows_as_default_source(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        # A project tag whose default_verify alone shapes a phase — zero plugin edits.
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps({"tags": {"E2E": {
                "route": "executor", "default_verify": ["test", "start"]}}}),
            encoding="utf-8",
        )
        tp._load.cache_clear()
        vmp._load.cache_clear()

        self.assertEqual(vmp.default_verify_for_phase(["E2E", "E2E"]),
                         ["test", "start"])


# --- fail-open: unknown modes dropped, never raised -------------------------

class DefaultVerifyFailOpenTests(TestCase):
    """An unknown mode in a default_verify row is DROPPED (treated as absent),
    not raised on — the loader stays crash-free over a malformed row (mirror of
    every registry field's fail-open posture). The plan originally said 'hard
    error at load'; that contradicted the established fail-open discipline, so
    the accessor filters to known modes instead."""

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
        vmp._load.cache_clear()

    def _mk_project(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        return proj

    def test_unknown_mode_dropped_not_raised(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        # A row with a bogus mode AND a real one: the bogus is dropped, the real
        # survives; no exception.
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps({"tags": {"Weird": {
                "route": "executor", "default_verify": ["bogus", "compile"]}}}),
            encoding="utf-8",
        )
        tp._load.cache_clear()
        vmp._load.cache_clear()

        # No raise; "bogus" filtered out, "compile" kept.
        self.assertEqual(tp.default_verify_for("Weird"), ["compile"])

    def test_all_unknown_modes_yield_empty(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps({"tags": {"Weird": {
                "route": "executor", "default_verify": ["bogus", "alsobogus"]}}}),
            encoding="utf-8",
        )
        tp._load.cache_clear()
        vmp._load.cache_clear()

        self.assertEqual(tp.default_verify_for("Weird"), [])


if __name__ == "__main__":
    main()
