"""Tests for the data-driven task-type registry + the ``task_type`` field.

Covers the three-part change that makes task-type portable and closes the
silent-drift defect the user diagnosed:

1. **``task_type`` field** — a typed mirror of the plan.md name tag, written into
   track-state.json at init (quality._init_core). The name string stays the
   authoritative source; the field is a cache.
2. **Unknown-tag hard error** — a task name with an unrecognized tag-shaped
   bracket (typo ``[Migration]``, invented ``[Springboot3]``) blocks
   ``init-from-plan`` instead of silently defaulting to TDD.
3. **Registry-driven semantics** — adding a tag is one registry row; the test in
   test_plan_format_contract_wiring + test_migrate_tag_wiring pin the parity.

These tests use the canonical track-state harness (``_make_state`` /
``_make_track_dir`` / ``_out_captured`` from test_track_state) so init runs
through the real code path.
"""
import json
import os
import tempfile
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.plan_parse import _find_unknown_tags, parse_plan
from scripts.track_state.task_profiles import derive_task_type
from scripts.track_state.quality import cmd_init_from_plan
from scripts.track_state.helpers import _reset_task
from test_track_state import _make_track_dir, _out_captured


def _write_plan(track_dir, body):
    Path(str(track_dir), "plan.md").write_text(body, encoding="utf-8")


# A plan with a mix of tagged + untagged tasks, all using KNOWN tags.
_PLAN_OK = """# Plan

## Phase 1: Build

- [ ] [Migrate] Add jakarta package <!-- AC-1, TC-1.1 -->
- [ ] [Chore] Bump deps <!-- AC-2, TC-1.2 -->
- [ ] Add a plain button <!-- AC-3, TC-1.3 -->
- [ ] [Manual] verify it boots
"""


class DeriveTaskTypeTests(TestCase):
    def test_tagged_lowercased(self):
        self.assertEqual(derive_task_type("[Migrate] Add jakarta"), "migrate")
        self.assertEqual(derive_task_type("[Chore] Bump deps"), "chore")
        self.assertEqual(derive_task_type("[Docs] Write README"), "docs")

    def test_untagged_is_default(self):
        self.assertEqual(derive_task_type("Add a plain button"), "default")

    def test_first_tag_wins(self):
        # A name carrying two tags derives from the first (extract_tags order).
        self.assertEqual(derive_task_type("[Chore] [Migrate] bump"), "chore")


class UnknownTagHelperTests(TestCase):
    def test_typo_detected(self):
        # [Migration] is the canonical typo for [Migrate].
        self.assertEqual(_find_unknown_tags("[Migration] Add jakarta"), ["[Migration]"])

    def test_invented_alphanumeric_tag_detected(self):
        # An unregistered tag with digits — exactly the "new tag nobody added"
        # case. The regex must catch these, not just all-alpha tokens.
        self.assertEqual(_find_unknown_tags("[Springboot3] upgrade"), ["[Springboot3]"])
        self.assertEqual(_find_unknown_tags("[K8sRollout] deploy"), ["[K8sRollout]"])

    def test_known_tags_pass(self):
        for tag in ("[Migrate]", "[Manual]", "[Chore]", "[Docs]", "[Config]",
                    "[Explore]", "[migrate]"):  # case-insensitive
            self.assertEqual(_find_unknown_tags(f"{tag} do work"), [], f"{tag} should be known")

    def test_trailing_markers_not_flagged(self):
        # SHA, [N/A], [verified] are legitimate trailing markers, not tags.
        self.assertEqual(_find_unknown_tags("Add x [abcdef1]"), [])
        self.assertEqual(_find_unknown_tags("Add x [N/A]"), [])
        self.assertEqual(_find_unknown_tags("Add x [verified]"), [])

    def test_untagged_name_clean(self):
        self.assertEqual(_find_unknown_tags("Add a plain button"), [])


class InitTaskTypeFieldTests(TestCase):
    """init-from-plan writes task_type derived from the name tag."""

    def setUp(self):
        self.td = _make_track_dir()
        _write_plan(self.td, _PLAN_OK)
        _out_captured(
            cmd_init_from_plan,
            str(self.td), track_id="tt_20260101", track_type="feature",
            description="task_type field test",
        )
        self.state = json.loads(
            Path(str(self.td), "track-state.json").read_text()
        )

    def test_field_present_and_derived(self):
        tasks = self.state["phases"][0]["tasks"]
        self.assertEqual(tasks[0]["task_type"], "migrate")
        self.assertEqual(tasks[1]["task_type"], "chore")
        self.assertEqual(tasks[2]["task_type"], "default")  # untagged
        self.assertEqual(tasks[3]["task_type"], "manual")

    def test_field_described_in_schema(self):
        # additionalProperties:false means the field must be declared in the
        # schema or state files carrying it would fail JSON-schema validation.
        plugin_root = Path(__file__).resolve().parent.parent
        schema = json.loads((plugin_root / "schemas" /
                             "track-state.schema.json").read_text())
        self.assertIn("task_type", schema["$defs"]["task"]["properties"])


class UnknownTagBlocksInitTests(TestCase):
    """The silent-drift fix: an unrecognized tag is a hard error at parse time,
    so init-from-plan refuses to start the track."""

    def test_typo_in_plan_blocks_parse(self):
        td = _make_track_dir()
        _write_plan(td, """# Plan

## Phase 1: Build

- [ ] [Migration] Add jakarta <!-- AC-1, TC-1.1 -->
- [ ] [Manual] verify
""")
        # parse_plan is what init-from-plan runs; its errors block init.
        result = parse_plan(str(Path(td) / "plan.md"))
        unknown_errs = [e for e in result["errors"] if "unrecognized tag" in e]
        self.assertTrue(unknown_errs, f"expected an unrecognized-tag error; got {result['errors']}")
        self.assertIn("[Migration]", unknown_errs[0])

    def test_invented_tag_blocks_parse(self):
        td = _make_track_dir()
        _write_plan(td, """# Plan

## Phase 1: Build

- [ ] [Springboot3] upgrade <!-- AC-1, TC-1.1 -->
- [ ] [Manual] verify
""")
        result = parse_plan(str(Path(td) / "plan.md"))
        unknown_errs = [e for e in result["errors"] if "unrecognized tag" in e]
        self.assertTrue(unknown_errs, f"expected an unrecognized-tag error; got {result['errors']}")
        self.assertIn("[Springboot3]", unknown_errs[0])

    def test_known_tags_do_not_block(self):
        td = _make_track_dir()
        _write_plan(td, _PLAN_OK)
        result = parse_plan(str(Path(td) / "plan.md"))
        unknown_errs = [e for e in result["errors"] if "unrecognized tag" in e]
        self.assertEqual(unknown_errs, [])


class ResetPreservesTaskTypeTests(TestCase):
    """task_type is structural identity (derived from the name), not progress —
    so reset must NOT clear it. _RESET_FIELDS must not list task_type."""

    def test_reset_keeps_task_type(self):
        from scripts.track_state.constants import _RESET_FIELDS
        self.assertNotIn("task_type", _RESET_FIELDS,
                         "task_type is structural; reset must preserve it")

        # And behaviorally: a reset task keeps its task_type.
        task = {"name": "[Migrate] x", "status": "completed",
                "task_type": "migrate", "commit_sha": "abcdef1",
                "completed_at": "2026-01-01T00:00:00Z", "retry_count": 2}
        # _reset_task mutates in place and returns None; read the mutated dict.
        target = dict(task)
        _reset_task(target)
        self.assertEqual(target.get("task_type"), "migrate")
        # while progress fields ARE cleared:
        self.assertNotIn("commit_sha", target)
        self.assertNotIn("completed_at", target)


class OverrideLayerTests(TestCase):
    """The project-local override layer: a project drops
    ``conductor/workflow/task-type-profiles.json`` and its tags flow through the
    full pipeline with ZERO plugin edits — plugin baseline ⊕ project overlay,
    project wins conflicts.

    Mirrors test_env.py's env-snapshot discipline (snapshot/restore
    ``CLAUDE_PROJECT_DIR`` + cwd, cache_clear per test, tempdir cleanup).
    """

    def setUp(self):
        from scripts.track_state import task_profiles
        self.tp = task_profiles
        # Snapshot the env + cwd we mutate so every test restores them.
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")
        self._cwd = os.getcwd()

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        os.chdir(self._cwd)
        self.tp._load.cache_clear()

    def _mk_project(self):
        """A temp project tree with conductor/tracks/ (the real-project signal)."""
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        return proj

    def _write_overlay(self, proj, data):
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps(data), encoding="utf-8",
        )

    def test_project_override_adds_tag(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"tags": {"K8sRollout": {
            "route": "executor", "tdd_exempt": True, "coverage_exempt": True}}})
        self.tp._load.cache_clear()

        # Zero plugin edits: the new tag flows through every consumer.
        self.assertIn("K8sRollout", self.tp.TAG_VOCAB())
        self.assertEqual(self.tp.route_for(["K8sRollout"]), "executor")
        self.assertTrue(self.tp.is_tdd_exempt(["K8sRollout"]))
        self.assertTrue(self.tp.is_coverage_exempt(["K8sRollout"]))
        from scripts.track_state.helpers import extract_tags
        self.assertEqual(extract_tags("[K8sRollout] deploy"), ["K8sRollout"])
        self.assertEqual(self.tp.derive_task_type("[K8sRollout] deploy"), "k8srollout")

    def test_project_overlay_merges_keeps_builtins(self):
        # Overlay declares ONLY a new tag — built-ins must survive (merge, not replace).
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"tags": {"K8sRollout": {
            "route": "executor", "tdd_exempt": True, "coverage_exempt": True}}})
        self.tp._load.cache_clear()

        self.assertIn("Migrate", self.tp.TAG_VOCAB())  # built-in still present
        self.assertEqual(self.tp.route_for(["Migrate"]), "executor")
        self.assertTrue(self.tp.is_tdd_exempt(["Migrate"]))
        self.assertTrue(self.tp.is_coverage_exempt(["Migrate"]))

    def test_project_overlay_overrides_builtin(self):
        # Project re-declares [Manual] with route:executor → project wins the conflict.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"tags": {"Manual": {
            "route": "executor", "tdd_exempt": True, "coverage_exempt": True}}})
        self.tp._load.cache_clear()

        self.assertEqual(self.tp.route_for(["Manual"]), "executor")  # overridden
        # [Explore] untouched (manual precedence is registry-driven, not hardcoded).
        self.assertEqual(self.tp.route_for(["Explore"]), "explore")

    def test_malformed_overlay_falls_back_to_baseline(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            "{ not valid json", encoding="utf-8",
        )
        self.tp._load.cache_clear()

        # No crash, built-in vocab intact, baseline routing restored.
        self.assertEqual(set(self.tp.TAG_VOCAB()),
                         {"Explore", "Docs", "Config", "Chore", "Manual", "Migrate"})
        self.assertEqual(self.tp.route_for(["Manual"]), "manual")

    def test_malformed_shape_overlay_falls_back_to_baseline(self):
        # Structurally-wrong overlay (not an object / missing tags) → baseline alone.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"default": {"route": "executor"}})  # no 'tags' key
        self.tp._load.cache_clear()

        self.assertEqual(set(self.tp.TAG_VOCAB()),
                         {"Explore", "Docs", "Config", "Chore", "Manual", "Migrate"})

    def test_no_override_file_no_change(self):
        # CLAUDE_PROJECT_DIR set to a project tree with NO overlay file → baseline.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self.tp._load.cache_clear()

        self.assertEqual(set(self.tp.TAG_VOCAB()),
                         {"Explore", "Docs", "Config", "Chore", "Manual", "Migrate"})

    def test_unknown_project_tag_blocks_init_then_passes_when_registered(self):
        # End-to-end: a project tag the validator rejects until the overlay registers it.
        from scripts.track_state.plan_parse import parse_plan
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        plan_body = """# Plan

## Phase 1: Build

- [ ] [Foo] do a thing <!-- AC-1, TC-1.1 -->
- [ ] [Manual] verify
"""

        # Without the overlay: [Foo] is an unrecognized tag → parse error.
        td = _make_track_dir()
        _write_plan(td, plan_body)
        result = parse_plan(str(Path(td) / "plan.md"))
        self.assertTrue(any("unrecognized tag" in e for e in result["errors"]),
                        f"expected unrecognized-tag error; got {result['errors']}")

        # After registering [Foo] in the project overlay: same plan parses clean.
        self._write_overlay(proj, {"tags": {"Foo": {
            "route": "executor", "tdd_exempt": True, "coverage_exempt": True}}})
        self.tp._load.cache_clear()
        td2 = _make_track_dir()
        _write_plan(td2, plan_body)
        result2 = parse_plan(str(Path(td2) / "plan.md"))
        self.assertEqual(
            [e for e in result2["errors"] if "unrecognized tag" in e], [],
            f"[Foo] should be recognized once registered; got {result2['errors']}",
        )

    def test_override_via_cwd_heuristic(self):
        # CLAUDE_PROJECT_DIR UNSET, cwd is a project tree with conductor/tracks/.
        proj = self._mk_project()
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        os.chdir(proj)
        self._write_overlay(proj, {"tags": {"K8sRollout": {
            "route": "executor", "tdd_exempt": True, "coverage_exempt": True}}})
        self.tp._load.cache_clear()

        self.assertIn("K8sRollout", self.tp.TAG_VOCAB())  # cwd-heuristic tier works
        self.assertEqual(self.tp.route_for(["K8sRollout"]), "executor")

    def test_warning_fires_on_malformed_overlay(self):
        # The fail-open contract logs loudly so a malformed overlay is diagnosable.
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            "{ bad", encoding="utf-8",
        )
        buf = StringIO()
        with redirect_stderr(buf):
            self.tp._load.cache_clear()
            self.tp._load()
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("task-type-profiles.json", buf.getvalue())


if __name__ == "__main__":
    main()
