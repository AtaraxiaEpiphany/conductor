"""Tests for ``track-state tag add`` — the task-type overlay generator
(task_profiles.tag_add).

The task-type counterpart of ``roster add``: one validated upsert into the
project overlay ``conductor/workflow/task-type-profiles.json`` and the tag is
live (vocab, routing, exemptions, when_to_use, extract_tags) with zero Python
edits. Pins: safe defaults (executor route, both gates ON, ``auto_propose:
false`` written EXPLICIT — absent means True at read time, so the opt-out must
be on disk); overlay preservation; row-level replace with ``--force`` / refusal
without; shadowing a BASELINE tag needs no flag (it is the overlay mechanism);
the proposer ignores an opted-out tag even on its own signals; ``parse_plan``
accepts the new ``[Tag]`` marker; every rejection lands before any write; CLI
wiring incl. the ``_BOOL_FLAGS`` membership (without it ``positional()``
swallows the token after each boolean flag as its value).
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import task_profiles as tp
from scripts.track_state.task_profiles import tag_add

ROOT = Path(__file__).resolve().parent.parent


def _project():
    d = Path(tempfile.mkdtemp())
    (d / "conductor" / "tracks").mkdir(parents=True)
    return d


class _EnvIsolated(TestCase):
    """Isolate the env-driven overlay resolution + clear the read cache around
    every test (the loader is process-cached and _project_root() reads
    $CLAUDE_PROJECT_DIR)."""

    def setUp(self):
        self._old_env = {k: os.environ.get(k)
                         for k in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        tp._load.cache_clear()

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        tp._load.cache_clear()
        for d in getattr(self, "_dirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def _track_dir(self):
        d = _project()
        if not hasattr(self, "_dirs"):
            self._dirs = []
        self._dirs.append(d)
        return d


class TagAddWritesTests(_EnvIsolated):
    def test_row_written_with_safe_defaults(self):
        d = self._track_dir()
        r = tag_add("Lint", when_to_use="Run the repo linters",
                    signals="lint, linting", project_dir=d)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["tag"], "Lint")
        self.assertEqual(Path(r["registry_path"]),
                         d / "conductor" / "workflow" / "task-type-profiles.json")
        row = json.loads(Path(r["registry_path"]).read_text(
            encoding="utf-8"))["tags"]["Lint"]
        # Safe defaults: executor route, full gates + test grounding (the
        # positive form — the generator never writes legacy booleans),
        # auto_propose opt-out written EXPLICIT (absent means True at read
        # time — a generated tag must never join the proposer's candidates
        # by accident).
        self.assertEqual(row["route"], "executor")
        self.assertEqual(row["when_to_use"], "Run the repo linters")
        self.assertEqual(row["gates"], ["tdd", "coverage", "checkpoint"])
        self.assertEqual(row["grounding"], "test")
        self.assertNotIn("tdd_exempt", row)
        self.assertNotIn("coverage_exempt", row)
        self.assertIs(row["auto_propose"], False)
        # Opt-in-only extras stay OFF the row entirely.
        self.assertNotIn("over_tag_risk", row)
        self.assertNotIn("refactor", row)
        # Signals lowercased, split, deduped (whitespace tolerated).
        self.assertEqual(row["signals"], ["lint", "linting"])

    def test_exemptions_and_optin_flags_written_when_set(self):
        d = self._track_dir()
        r = tag_add("Deploy", when_to_use="Deploy the stack",
                    route="manual", tdd_exempt=True, coverage_exempt=True,
                    over_tag_risk=True, auto_propose=True,
                    signals="deployit", project_dir=d)
        self.assertTrue(r["ok"], r)
        row = json.loads(Path(r["registry_path"]).read_text(
            encoding="utf-8"))["tags"]["Deploy"]
        self.assertEqual(row["route"], "manual")
        # Both-exempt flags land as checkpoint-only gates + review grounding
        # (the derived fail-open value; an explicit --grounding can override).
        self.assertEqual(row["gates"], ["checkpoint"])
        self.assertEqual(row["grounding"], "review")
        self.assertNotIn("tdd_exempt", row)
        self.assertNotIn("coverage_exempt", row)
        self.assertIs(row["auto_propose"], True)
        self.assertTrue(row["over_tag_risk"])

    def test_existing_overlay_rows_and_doc_blocks_preserved(self):
        d = self._track_dir()
        reg = d / "conductor" / "workflow" / "task-type-profiles.json"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({
            "_comment": "project overlay",
            "default": {"coverage_exempt": False},
            "tags": {"Prior": {"route": "executor",
                               "when_to_use": "prior tag"}},
        }), encoding="utf-8")
        r = tag_add("New", when_to_use="new tag", project_dir=d)
        self.assertTrue(r["ok"], r)
        overlay = json.loads(reg.read_text(encoding="utf-8"))
        self.assertEqual(overlay["_comment"], "project overlay")
        self.assertEqual(overlay["default"], {"coverage_exempt": False})
        self.assertIn("Prior", overlay["tags"])
        self.assertIn("New", overlay["tags"])

    def test_same_name_row_replaced_with_force(self):
        d = self._track_dir()
        tag_add("Twin", when_to_use="first", project_dir=d)
        r = tag_add("Twin", when_to_use="second", route="manual",
                    force=True, project_dir=d)
        self.assertTrue(r["ok"], r)
        row = json.loads(Path(r["registry_path"]).read_text(
            encoding="utf-8"))["tags"]["Twin"]
        self.assertEqual(row["when_to_use"], "second")
        self.assertEqual(row["route"], "manual")

    def test_overwrite_refused_without_force(self):
        d = self._track_dir()
        self.assertTrue(tag_add("Twin", when_to_use="first",
                                project_dir=d)["ok"])
        r = tag_add("Twin", when_to_use="second", project_dir=d)
        self.assertFalse(r["ok"])
        self.assertTrue(any("already exists" in e for e in r["errors"]))

    def test_shadowing_baseline_tag_needs_no_flag(self):
        # Project-wins-conflicting-tag IS the overlay mechanism — replacing a
        # baseline tag's semantics is an override, not an accidental clobber.
        d = self._track_dir()
        r = tag_add("Docs", when_to_use="project-flavored docs handling",
                    project_dir=d)
        self.assertTrue(r["ok"], r)
        os.environ["CLAUDE_PROJECT_DIR"] = str(d)
        tp._load.cache_clear()
        self.assertEqual(tp.when_to_use_for("Docs"),
                         "project-flavored docs handling")

    def test_bak_kept_on_second_add(self):
        d = self._track_dir()
        tag_add("A", when_to_use="a", project_dir=d)
        tag_add("B", when_to_use="b", project_dir=d)
        bak = d / "conductor" / "workflow" / "task-type-profiles.json.bak"
        self.assertTrue(bak.exists(), "second write must keep a .bak")
        self.assertIn('"A"', bak.read_text(encoding="utf-8"))
        self.assertNotIn('"B"', bak.read_text(encoding="utf-8"))


class TagAddLiveTests(_EnvIsolated):
    """End-to-end through the loader — the point of the generator."""

    def _add_and_resolve(self, d):
        r = tag_add("Lint", when_to_use="Run the repo linters",
                    signals="lintx, lintingx", project_dir=d)
        self.assertTrue(r["ok"], r)
        os.environ["CLAUDE_PROJECT_DIR"] = str(d)
        tp._load.cache_clear()

    def test_tag_live_in_vocab_and_routing(self):
        d = self._track_dir()
        self._add_and_resolve(d)
        self.assertIn("Lint", tp.TAG_VOCAB())
        self.assertEqual(tp.route_for(["Lint"]), "executor")
        self.assertFalse(tp.is_tdd_exempt(["Lint"]))
        self.assertFalse(tp.is_coverage_exempt(["Lint"]))
        self.assertEqual(tp.when_to_use_for("Lint"),
                         "Run the repo linters")

    def test_extract_tags_recognizes_marker(self):
        # The vocab drives the plan-marker regex — a task named "[Lint] x"
        # must parse with the tag attached (init-from-plan hard-error relief).
        d = self._track_dir()
        self._add_and_resolve(d)
        from scripts.track_state import helpers
        self.assertEqual(helpers.extract_tags("[Lint] clean the checks"),
                         ["Lint"])
        self.assertEqual(helpers.strip_tags("[Lint] clean the checks"),
                         "clean the checks")

    def test_parse_plan_accepts_new_tag_task(self):
        d = self._track_dir()
        self._add_and_resolve(d)
        from scripts.track_state.plan_parse import parse_plan
        plan = d / "plan.md"
        plan.write_text(
            "# Plan\n\n## Phase 1: checks\n\n- [ ] [Lint] clean the checks\n"
            "- [ ] verify by hand\n", encoding="utf-8")
        r = parse_plan(str(plan))
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["phases"][0]["tasks"][0]["name"],
                         "[Lint] clean the checks")

    def test_opted_out_tag_never_proposed(self):
        # auto_propose: false honored by the mechanical proposer: even text
        # saturated with the tag's OWN signals never surfaces it (bespoke
        # signals chosen to avoid colliding with any baseline tag's).
        d = self._track_dir()
        self._add_and_resolve(d)
        self.assertNotIn("Lint", [c["tag"] for c in
                                  tp.rank_tags("lintx and lintingx everything")])
        self.assertIsNone(tp.derive_task_tag("lintx and lintingx everything"))

    def test_merged_registry_lints_clean(self):
        d = self._track_dir()
        self._add_and_resolve(d)
        from scripts.track_state.registry_validate import (
            validate_merged_task_types)
        self.assertEqual(validate_merged_task_types(tp._load()), [])


class TagAddErrorTests(_EnvIsolated):
    def _err(self, **kwargs):
        return tag_add(**kwargs)

    def test_no_project_error(self):
        r = tag_add("X", when_to_use="x",
                    project_dir=Path(tempfile.mkdtemp()) / "nope")
        self.assertFalse(r["ok"])
        self.assertTrue(any("does not exist" in e for e in r["errors"]))

    def test_no_project_dir_resolved(self):
        r = tag_add("X", when_to_use="x")  # cwd has no conductor/tracks
        self.assertFalse(r["ok"])
        self.assertTrue(any("no project dir" in e for e in r["errors"]))

    def test_missing_when_to_use_rejected(self):
        d = self._track_dir()
        for bad in (None, "", "   "):
            r = tag_add("X", when_to_use=bad, project_dir=d)
            self.assertFalse(r["ok"], f"{bad!r} must be rejected")
            self.assertTrue(any("--when-to-use" in e for e in r["errors"]))

    def test_unknown_route_rejected(self):
        d = self._track_dir()
        r = tag_add("X", when_to_use="x", route="wizard", project_dir=d)
        self.assertFalse(r["ok"])
        self.assertTrue(any("--route" in e for e in r["errors"]))

    def test_reserved_and_bad_names_rejected(self):
        d = self._track_dir()
        for bad in ("default", "../escape", "has space", "[Brackets]",
                    "", "-leading-dash", "under_score ok 1"):
            if bad == "under_score ok 1":
                continue
            r = tag_add(bad, when_to_use="x", project_dir=d)
            self.assertFalse(r["ok"], f"{bad!r} must be rejected")

    def test_underscore_name_accepted(self):
        # The plan-marker charset allows underscores; the generator must too.
        d = self._track_dir()
        self.assertTrue(tag_add("my_tag", when_to_use="x",
                                project_dir=d)["ok"])

    def test_two_homes_rejected(self):
        d = self._track_dir()
        r = tag_add("X", when_to_use="x", workflow="inline prose",
                    workflow_doc="x.md", project_dir=d)
        self.assertFalse(r["ok"])
        self.assertTrue(any("two homes" in e for e in r["errors"]))

    def test_bare_path_workflow_doc_rejected(self):
        d = self._track_dir()
        r = tag_add("X", when_to_use="x",
                    workflow_doc="steps/x.md", project_dir=d)
        self.assertFalse(r["ok"])
        self.assertTrue(any("workflow_doc" in e for e in r["errors"]))

    def test_rejected_before_write(self):
        # Every rejection above lands BEFORE disk: a bad row leaves no file.
        d = self._track_dir()
        self.assertFalse(tag_add("X", when_to_use="x", route="nope",
                                 project_dir=d)["ok"])
        self.assertFalse(
            (d / "conductor" / "workflow" / "task-type-profiles.json").exists())

    def test_malformed_existing_overlay_refused(self):
        d = self._track_dir()
        reg = d / "conductor" / "workflow" / "task-type-profiles.json"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text("{not json", encoding="utf-8")
        r = tag_add("X", when_to_use="x", project_dir=d)
        self.assertFalse(r["ok"])
        self.assertTrue(any("unreadable" in e for e in r["errors"]))
        # The write gate refused to clobber the malformed file.
        self.assertEqual(reg.read_text(encoding="utf-8"), "{not json")


class TagCliWiringTests(TestCase):
    """The tag command is registered in the CLI surface. The _BOOL_FLAGS
    membership is load-bearing: without it positional() treats the next token
    after each boolean flag as that flag's VALUE (e.g. `--tdd-exempt Foo`
    silently eats Foo)."""

    def test_tag_listed_in_help(self):
        from scripts.track_state.cli import COMMAND_HELP
        self.assertIn("tag", COMMAND_HELP)

    def test_task_types_group_exists(self):
        from scripts.track_state.cli import _COMMAND_GROUPS
        groups = {name: cmds for name, cmds in _COMMAND_GROUPS}
        self.assertIn("tag", groups["Task Types"])

    def test_tag_sanctioned(self):
        spec = importlib.util.spec_from_file_location(
            "pcc_tag", ROOT / "scripts" / "pre-command-check.py")
        pcc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pcc)
        self.assertIn("tag", pcc._SANCTIONED_TS_SUBCOMMANDS)

    def test_tag_dispatch_branch_exists(self):
        src = (ROOT / "scripts" / "track_state" / "cli.py").read_text(
            encoding="utf-8")
        self.assertIn('cmd == "tag"', src)

    def test_tag_takes_no_track_dir(self):
        from scripts.track_state import cli
        self.assertIn("tag", cli._NO_TRACK_DIR_COMMANDS)
        self.assertIn("tag", cli._TD_NO_RESOLUTION_COMMANDS)

    def test_generator_bool_flags_registered(self):
        from scripts.track_state import cli
        for f in ("--tdd-exempt", "--coverage-exempt", "--auto-propose",
                  "--over-tag-risk", "--refactor"):
            self.assertIn(f, cli._BOOL_FLAGS, f)

    def test_tag_via_cli(self):
        d = _project()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        old_argv, old_out, old_env = (
            sys.argv, sys.stdout, os.environ.get("CLAUDE_PROJECT_DIR"))
        buf = io.StringIO()
        sys.argv = ["track-state", "tag", "add", "Cli-Tag",
                    "--when-to-use", "cli tag", "--signals", "clix, clix",
                    "--project-dir", str(d)]
        sys.stdout = buf
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        try:
            from scripts.track_state import cli
            cli.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_out
            if old_env is None:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = old_env
        o = json.loads(buf.getvalue())
        self.assertTrue(o["ok"], o)
        row = json.loads((d / "conductor" / "workflow" /
                          "task-type-profiles.json").read_text(
                              encoding="utf-8"))["tags"]["Cli-Tag"]
        self.assertEqual(row["signals"], ["clix"])

    def test_tag_missing_when_to_use_errors(self):
        d = _project()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        old_argv, old_out = sys.argv, sys.stdout
        buf = io.StringIO()
        sys.argv = ["track-state", "tag", "add", "Cli-Tag",
                    "--project-dir", str(d)]
        sys.stdout = buf
        try:
            from scripts.track_state import cli
            with self.assertRaises(SystemExit):
                cli.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_out
        o = json.loads(buf.getvalue())
        self.assertFalse(o["ok"])


if __name__ == "__main__":
    main()
