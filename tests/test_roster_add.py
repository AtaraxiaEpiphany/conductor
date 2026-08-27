"""Tests for ``roster add`` — the adopt-a-skill generator (agent_roster.roster_add).

The D3 recipe as one command: wrapper agent + overlay roster row, both
validated before write. Pins: files written with task-executor-default
scaffold; existing overlay rows/doc blocks preserved; row-level replace by
name; the merged-roster lint clean end-to-end; overwrite refused without
--force; no-project error; unknown --class/--recovery rejected; CLI wiring
(mirrors test_brief_cli wiring tests).
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import agent_roster as ar
from scripts.track_state.agent_roster import roster_add

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
        ar._load.cache_clear()

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        ar._load.cache_clear()
        import shutil
        for d in getattr(self, "_dirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def _track_dir(self):
        d = _project()
        if not hasattr(self, "_dirs"):
            self._dirs = []
        self._dirs.append(d)
        return d


class RosterAddWritesTests(_EnvIsolated):
    def test_wrapper_and_row_written_with_defaults(self):
        d = self._track_dir()
        r = roster_add("my-adapter", "doc-gen", project_dir=d)
        self.assertTrue(r["ok"], r)
        wrapper = Path(r["agent_path"])
        self.assertEqual(wrapper, d / ".claude" / "agents" / "my-adapter.md")
        body = wrapper.read_text(encoding="utf-8")
        self.assertIn("skills: [doc-gen]", body)
        self.assertIn("name: my-adapter", body)
        # The conductor result contract rides the body (write-result + fence).
        self.assertIn("track-state write-result", body)
        self.assertIn("---TASK RESULT---", body)
        # Defaults = task-executor scaffold.
        overlay = json.loads(Path(r["roster_path"]).read_text(encoding="utf-8"))
        row = overlay["agents"]["my-adapter"]
        self.assertEqual(row["class"], "executor")
        self.assertEqual(row["fence"], "---TASK RESULT--- ... ---END RESULT---")
        self.assertEqual(row["recovery"], "result-file")
        self.assertTrue(row["recovery_instruction"])

    def test_existing_overlay_rows_and_doc_blocks_preserved(self):
        d = self._track_dir()
        roster = d / "conductor" / "workflow" / "agent-roster.json"
        roster.parent.mkdir(parents=True, exist_ok=True)
        roster.write_text(json.dumps({
            "_comment": "project overlay",
            "agents": {"prior-agent": {"class": "advisory",
                                       "fence": "---X--- ... ---END---"}},
        }), encoding="utf-8")
        r = roster_add("new-one", "doc-gen", project_dir=d)
        self.assertTrue(r["ok"], r)
        overlay = json.loads(roster.read_text(encoding="utf-8"))
        self.assertEqual(overlay["_comment"], "project overlay")
        self.assertIn("prior-agent", overlay["agents"])
        self.assertIn("new-one", overlay["agents"])

    def test_same_name_row_replaced(self):
        d = self._track_dir()
        roster_add("my-adapter", "doc-gen", project_dir=d,
                   agent_class="advisory")
        # Row-level replace: a second add with a different class swaps the row.
        r = roster_add("my-adapter", "other-skill", project_dir=d,
                       agent_class="reviewer", force=True)
        self.assertTrue(r["ok"], r)
        os.environ["CLAUDE_PROJECT_DIR"] = str(d)
        ar._load.cache_clear()
        self.assertEqual(ar.class_for("my-adapter"), "reviewer")

    def test_overwrite_refused_without_force(self):
        d = self._track_dir()
        self.assertTrue(roster_add("my-adapter", "doc-gen", project_dir=d)["ok"])
        r = roster_add("my-adapter", "doc-gen", project_dir=d)
        self.assertFalse(r["ok"])
        self.assertTrue(any("already exists" in e for e in r["errors"]))

    def test_bak_kept_on_second_add(self):
        d = self._track_dir()
        roster_add("a-one", "doc-gen", project_dir=d)
        roster_add("b-two", "doc-gen", project_dir=d)
        bak = d / "conductor" / "workflow" / "agent-roster.json.bak"
        self.assertTrue(bak.exists(), "second write must keep a .bak")
        # The .bak holds the pre-second-write state (a-one, no b-two).
        self.assertIn("a-one", bak.read_text(encoding="utf-8"))
        self.assertNotIn("b-two", bak.read_text(encoding="utf-8"))

    def test_resolved_scaffold_after_add(self):
        # End-to-end through the loader: with the project resolved via env,
        # the adopted agent gets the full task-executor scaffold.
        d = self._track_dir()
        roster_add("my-adapter", "doc-gen", project_dir=d)
        os.environ["CLAUDE_PROJECT_DIR"] = str(d)
        ar._load.cache_clear()
        self.assertEqual(ar.reminder_for("my-adapter"),
                         ar.REMINDER_LEAD + "---TASK RESULT--- ... ---END RESULT---")
        self.assertIn("my-adapter", ar.single_writers())  # executor class
        self.assertIn("my-adapter", ar.result_file_agents())
        self.assertIn("my-adapter", ar.agent_file_names())  # declared-names lint


class RosterAddLintTests(_EnvIsolated):
    def test_lint_clean_end_to_end(self):
        # The `check` roster lint over the post-add state: validity + every
        # declared name resolvable to a definition file. The wrapper file the
        # generator writes is what makes the declared-names check pass.
        from scripts.track_state.misc import _roster_lint_findings
        d = self._track_dir()
        roster_add("my-adapter", "doc-gen", project_dir=d)
        os.environ["CLAUDE_PROJECT_DIR"] = str(d)
        ar._load.cache_clear()
        self.assertEqual(_roster_lint_findings(), [])


class RosterAddErrorTests(_EnvIsolated):
    def test_no_project_error(self):
        r = roster_add("x", "y", project_dir=Path(tempfile.mkdtemp()) / "nope")
        self.assertFalse(r["ok"])
        self.assertTrue(any("does not exist" in e for e in r["errors"]))

    def test_unknown_class_rejected(self):
        d = self._track_dir()
        r = roster_add("x", "y", agent_class="wizard", project_dir=d)
        self.assertFalse(r["ok"])
        self.assertTrue(any("--class" in e for e in r["errors"]))
        # Rejected before any write: no wrapper, no overlay.
        self.assertFalse((d / ".claude" / "agents" / "x.md").exists())
        self.assertFalse((d / "conductor" / "workflow" / "agent-roster.json").exists())

    def test_unknown_recovery_rejected(self):
        d = self._track_dir()
        r = roster_add("x", "y", recovery="prayer", project_dir=d)
        self.assertFalse(r["ok"])
        self.assertTrue(any("--recovery" in e for e in r["errors"]))

    def test_orphaned_recovery_instruction_rejected(self):
        d = self._track_dir()
        r = roster_add("x", "y", recovery="none",
                       recovery_instruction="do things", project_dir=d)
        self.assertFalse(r["ok"])

    def test_recovery_none_row_has_no_instruction(self):
        d = self._track_dir()
        r = roster_add("x", "y", recovery="none", project_dir=d)
        self.assertTrue(r["ok"], r)
        overlay = json.loads(Path(r["roster_path"]).read_text(encoding="utf-8"))
        self.assertNotIn("recovery", overlay["agents"]["x"])
        self.assertNotIn("recovery_instruction", overlay["agents"]["x"])

    def test_bad_name_rejected(self):
        d = self._track_dir()
        for bad in ("../escape", "has space", ""):
            r = roster_add(bad, "y", project_dir=d)
            self.assertFalse(r["ok"], f"{bad!r} must be rejected")


class RosterCliWiringTests(TestCase):
    """The roster command is registered in the CLI surface (help, group,
    sanctioned allowlist, dispatch branch, no-track-dir sets). Mirrors
    test_brief_cli wiring tests."""

    def test_roster_listed_in_help(self):
        from scripts.track_state.cli import COMMAND_HELP
        self.assertIn("roster", COMMAND_HELP)

    def test_roster_group_exists(self):
        from scripts.track_state.cli import _COMMAND_GROUPS
        groups = {name: cmds for name, cmds in _COMMAND_GROUPS}
        self.assertIn("Roster", groups)
        self.assertIn("roster", groups["Roster"])

    def test_roster_sanctioned(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pcc_roster", ROOT / "scripts" / "pre-command-check.py")
        pcc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pcc)
        self.assertIn("roster", pcc._SANCTIONED_TS_SUBCOMMANDS)

    def test_roster_dispatch_branch_exists(self):
        src = (ROOT / "scripts" / "track_state" / "cli.py").read_text()
        self.assertIn('cmd == "roster"', src)

    def test_roster_takes_no_track_dir(self):
        # A `roster add` invocation has no <track-dir> positional — it must be
        # in the arity allowlist (else main() rejects it with a usage error)
        # and skipped by short-id resolution.
        from scripts.track_state import cli
        self.assertIn("roster", cli._NO_TRACK_DIR_COMMANDS)
        self.assertIn("roster", cli._TD_NO_RESOLUTION_COMMANDS)

    def test_roster_via_cli(self):
        d = _project()
        self.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
        old_argv, old_out, old_env = (
            sys.argv, sys.stdout, os.environ.get("CLAUDE_PROJECT_DIR"))
        import io
        buf = io.StringIO()
        sys.argv = ["track-state", "roster", "add", "cli-agent",
                    "--skill", "doc-gen", "--project-dir", str(d)]
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
        self.assertTrue((d / ".claude" / "agents" / "cli-agent.md").exists())

    def test_roster_missing_skill_errors(self):
        d = _project()
        self.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
        old_argv, old_out = sys.argv, sys.stdout
        import io
        buf = io.StringIO()
        sys.argv = ["track-state", "roster", "add", "cli-agent",
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
