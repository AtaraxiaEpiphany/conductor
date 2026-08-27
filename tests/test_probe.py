"""Tests for the tier-B probe layer (probes registry + ``probe`` CLI).

Pins: loader merge (baseline ⊕ overlay, row-level replace) + fail-open;
the builtin ``test-state`` snapshot over a fixture ledger (verdicts, recent
cap, absent-log); command-kind probes (argv, timeout family, bounded stdout);
the registry lint (unknown kind / orphaned command / dead builtin); CLI
wiring (help/group/sanctioned/branch/no-track-dir sets auto-enforced by
test_command_surface + explicit here).
"""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import probes
from scripts.track_state.probes import run_probe, probe_names
from scripts.track_state.registry_validate import (
    validate_probes_row, validate_probes_doc,
)

ROOT = Path(__file__).resolve().parent.parent


class _EnvIsolated(TestCase):
    """Isolate env-driven overlay resolution + clear the loader cache."""

    def setUp(self):
        # CLAUDE_PLUGIN_DATA too: it OVERRIDES the project dir in the data-dir
        # ladder, and other suites set it (env leak) — pop it so the fixture's
        # CLAUDE_PROJECT_DIR governs where the ledger is read from.
        self._old_env = {k: os.environ.get(k)
                         for k in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT",
                                   "CLAUDE_PLUGIN_DATA")}
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        probes._load.cache_clear()

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        probes._load.cache_clear()


def _write_overlay(root, rows):
    d = root / "conductor" / "workflow"
    d.mkdir(parents=True, exist_ok=True)
    (d / "probes.json").write_text(json.dumps({"probes": rows}),
                                   encoding="utf-8")


class LoaderTests(_EnvIsolated):
    def test_baseline_builtin_registered(self):
        self.assertIn("test-state", probe_names())

    def test_overlay_adds_and_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "conductor" / "tracks").mkdir(parents=True)
            _write_overlay(root, {
                "ci-status": {"description": "CI", "kind": "command",
                              "command": "true"},
            })
            os.environ["CLAUDE_PROJECT_DIR"] = str(root)
            probes._load.cache_clear()
            names = probe_names()
            self.assertIn("test-state", names)   # baseline survives
            self.assertIn("ci-status", names)    # overlay added

    def test_malformed_overlay_fails_open_to_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "conductor" / "tracks").mkdir(parents=True)
            d = root / "conductor" / "workflow"
            d.mkdir(parents=True)
            (d / "probes.json").write_text("{not json", encoding="utf-8")
            os.environ["CLAUDE_PROJECT_DIR"] = str(root)
            probes._load.cache_clear()
            self.assertIn("test-state", probe_names())


class TestStateProbeTests(_EnvIsolated):
    def _fixture_ledger(self, n=3):
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        os.environ["CLAUDE_PROJECT_DIR"] = tmp  # get_data_dir lands under it
        lines = [
            '2026-08-27T10:00:00+00:00 [INFO] 2026-08-27T10:00:00Z '
            'test_command="pytest -q" result=passed',
            '2026-08-27T10:05:00+00:00 [INFO] 2026-08-27T10:05:00Z '
            'test_command="pytest -q" result=failed',
            '2026-08-27T10:09:00+00:00 [INFO] 2026-08-27T10:09:00Z '
            'test_command="pytest tests/test_x.py" result=interrupted',
        ]
        if n == 3:
            ledger = lines
        else:
            ledger = [lines[0].replace("passed", "passed")] * 0
            for i in range(n):
                verdict = ["passed", "failed", "interrupted"][i % 3]
                ledger.append(
                    f'2026-08-27T1{i:02d}:00:00+00:00 [INFO] t '
                    f'test_command="pytest -q" result={verdict}')
        logs = Path(tmp) / ".conductor" / "logs"
        logs.mkdir(parents=True)
        (logs / "on-test-run.log").write_text("\n".join(ledger) + "\n",
                                              encoding="utf-8")

    def test_snapshot_verdicts(self):
        self._fixture_ledger(3)
        r = run_probe("test-state")
        self.assertTrue(r["ok"])
        self.assertEqual(r["last"]["result"], "interrupted")
        self.assertEqual(r["last"]["command"], "pytest tests/test_x.py")
        self.assertEqual(len(r["recent"]), 3)
        self.assertEqual(r["summary"], {"passed": 1, "failed": 1,
                                        "interrupted": 1})

    def test_recent_capped_at_20(self):
        self._fixture_ledger(30)
        r = run_probe("test-state")
        self.assertEqual(len(r["recent"]), 20)

    def test_absent_ledger(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        r = run_probe("test-state")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no test runs recorded")

    def test_ledger_without_test_lines(self):
        self._fixture_ledger(3)
        # Overwrite with non-test noise → same absent-run response.
        tmp = os.environ["CLAUDE_PROJECT_DIR"]
        (Path(tmp) / ".conductor" / "logs" / "on-test-run.log").write_text(
            "2026-08-27T10:00:00+00:00 [INFO] something else\n",
            encoding="utf-8")
        r = run_probe("test-state")
        self.assertFalse(r["ok"])


class CommandProbeTests(_EnvIsolated):
    def _register(self, command):
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        (Path(tmp) / "conductor" / "tracks").mkdir(parents=True)
        _write_overlay(Path(tmp), {
            "stub": {"description": "stub", "kind": "command",
                     "command": command},
        })
        os.environ["CLAUDE_PROJECT_DIR"] = tmp
        probes._load.cache_clear()

    def test_command_probe_runs(self):
        # The argv is shlex-split (no shell), so the program must arrive as
        # ONE quoted token — double-quote it in the registered command.
        self._register(f'''{sys.executable} -c "print('hello-probe')"''')
        r = run_probe("stub")
        self.assertTrue(r["ok"])
        self.assertEqual(r["exit"], 0)
        self.assertIn("hello-probe", r["stdout"])

    def test_command_probe_failure_exit(self):
        self._register(f'''{sys.executable} -c "import sys; sys.exit(3)"''')
        r = run_probe("stub")
        self.assertFalse(r["ok"])
        self.assertEqual(r["exit"], 3)

    def test_command_probe_timeout(self):
        self._register(f'''{sys.executable} -c "import time; time.sleep(30)"''')
        r = run_probe("stub")
        self.assertFalse(r["ok"])
        self.assertIn("timeout", r["reason"])

    def test_unknown_probe_names_registered_vocabulary(self):
        r = run_probe("nope")
        self.assertFalse(r["ok"])
        self.assertIn("test-state", r["reason"])


class ProbeLintTests(_EnvIsolated):
    def test_baseline_doc_lints_clean(self):
        doc = json.loads(
            (ROOT / "templates" / "workflow" / "probes.json").read_text(
                encoding="utf-8"))
        self.assertEqual(validate_probes_doc(doc), [])

    def test_bad_rows_rejected(self):
        cases = [
            ({"kind": "builtin"}, "description"),                       # no description
            ({"description": "x"}, "kind"),                             # no kind
            ({"description": "x", "kind": "wizard"}, "kind"),           # unknown kind
            ({"description": "x", "kind": "command"}, "command"),       # command w/o command
            ({"description": "x", "kind": "builtin"}, "builtin"),       # dead builtin
            ({"description": "x", "kind": "builtin", "command": "ls"},
             "orphaned"),                                               # command on builtin
        ]
        for row, expect in cases:
            errs = validate_probes_row("bad", row)
            self.assertTrue(errs, f"row must be rejected: {row}")
            self.assertTrue(any(expect in e for e in errs),
                            f"{row}: expected {expect!r} in {errs}")

    def test_valid_command_row(self):
        errs = validate_probes_row("ci", {"description": "CI",
                                          "kind": "command", "command": "true"})
        self.assertEqual(errs, [])

    def test_check_lint_surfaces_bad_overlay(self):
        from scripts.track_state.misc import _probe_lint_findings
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "conductor" / "tracks").mkdir(parents=True)
            _write_overlay(root, {
                "ghost": {"description": "dead", "kind": "builtin"},
            })
            os.environ["CLAUDE_PROJECT_DIR"] = str(root)
            probes._load.cache_clear()
            findings = _probe_lint_findings()
            self.assertTrue(any("ghost" in f for f in findings),
                            findings)


class ProbeCliWiringTests(TestCase):
    def test_probe_listed_in_help(self):
        from scripts.track_state.cli import COMMAND_HELP
        self.assertIn("probe", COMMAND_HELP)

    def test_probe_group_exists(self):
        from scripts.track_state.cli import _COMMAND_GROUPS
        groups = {name: cmds for name, cmds in _COMMAND_GROUPS}
        self.assertIn("Probes", groups)
        self.assertIn("probe", groups["Probes"])

    def test_probe_sanctioned(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pcc_probe", ROOT / "scripts" / "pre-command-check.py")
        pcc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pcc)
        self.assertIn("probe", pcc._SANCTIONED_TS_SUBCOMMANDS)

    def test_probe_dispatch_branch_exists(self):
        src = (ROOT / "scripts" / "track_state" / "cli.py").read_text()
        self.assertIn('cmd == "probe"', src)

    def test_probe_takes_no_track_dir(self):
        from scripts.track_state import cli
        self.assertIn("probe", cli._NO_TRACK_DIR_COMMANDS)
        self.assertIn("probe", cli._TD_NO_RESOLUTION_COMMANDS)

    def test_probe_via_cli(self):
        import io
        old_argv, old_out, old_env = (
            sys.argv, sys.stdout, os.environ.get("CLAUDE_PROJECT_DIR"))
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        os.environ["CLAUDE_PROJECT_DIR"] = tmp  # absent ledger → ok:false
        buf = io.StringIO()
        sys.argv = ["track-state", "probe", "test-state"]
        sys.stdout = buf
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
        self.assertFalse(o["ok"])
        self.assertEqual(o["reason"], "no test runs recorded")


if __name__ == "__main__":
    main()
