"""Tests for ``track-state plan-profile`` and ``check-conflicts`` — the two
read-only plan-audit CLIs that share one extracted conflict composer.

``check-conflicts`` is the clean gate surface a hook or skill reads to decide
whether a generated plan needs another pass; ``plan-profile`` is the resolved
phase-verify view (the input the conflict audit runs over). Both parse
``<track-dir>/plan.md`` and delegate to the SAME functions ``init-from-plan``
uses — :func:`verify_mode_profiles.collect_plan_conflicts` (the single
whole-plan composer) and :func:`verify_mode_profiles.resolve_phase_verify_modes`
— so the CLI surface and init cannot drift. This is the structural capability
the ``spec-planner`` drift incident motivated: the planner writes goals blind
(it has no Bash); these CLIs report the resolver's verdict to the one
Bash-having orchestrator in the loop (``new-track``).

Load-bearing invariants under test:

- **Wiring (5 sites)**: both commands are registered at every site the CLI
  requires (COMMAND_HELP, _COMMAND_GROUPS, the dispatch branch, and
  _SANCTIONED_TS_SUBCOMMANDS). The dispatch branch is the one site no
  cross-cutting test guards — these end-to-end subprocess calls prove it routes.
- **Shared composer**: ``check-conflicts`` emits exactly what
  ``collect_plan_conflicts`` returns; ``init-from-plan`` folds the same list
  (filtered to harmful_undergating) into its warnings.
- **Conflict kinds**: ``harmful_undergating`` (authored directive gates a
  build/debt phase on the red suite), ``all_exempt_suite_gated`` (every task
  suite-exempt yet suite-gated). A directive-less clean plan emits none.
- **Read-only + fail-open**: writes nothing; a missing plan.md →
  ``{"ok": false, ...}``, never a raised exception.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))
_CLI = _scripts / "track-state"

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "pre_command_check", _scripts / "pre-command-check.py")
_pcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pcc)


def _run(subcmd, track_dir):
    """Run ``track-state <subcmd> <track_dir>``; return (rc, stdout-obj, stderr).

    stdout is parsed as JSON (both CLIs emit JSON); ``None`` if unparseable.
    """
    argv = [sys.executable, str(_CLI), subcmd, str(track_dir)]
    proc = subprocess.run(argv, capture_output=True, text=True)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
    return proc.returncode, payload, proc.stderr


def _write_plan(body):
    """Write ``plan.md`` into a fresh temp track dir; return the dir path."""
    tmp = tempfile.mkdtemp()
    Path(tmp, "plan.md").write_text(body)
    return tmp


# A plan with one of each interesting phase: a clean directive-less Migrate
# phase, an all-suite-exempt Docs phase, a HARMFUL authored directive, and a
# legitimate adversarial directive.
_PLAN = (
    "# Implementation Plan: demo\n"
    "## Phase 1: Write docs\n"
    "- [ ] [Docs] notes <!-- AC-1 -->\n"
    "- [ ] [Manual] verify\n"
    "## Phase 2: Migrate dependencies <!-- verify: test -->\n"
    "- [ ] [Migrate] bump parent <!-- AC-2 -->\n"
    "- [ ] [Manual] verify\n"
    "## Phase 3: Harden authN          <!-- verify: test,adversarial -->\n"
    "- [ ] harden it <!-- AC-3 -->\n"
    "- [ ] [Manual] verify\n"
)


class PlanAuditWiring(TestCase):
    """Both commands are registered at every site the CLI requires.

    The cross-cutting coverage test (test_extract_track_dirs) already asserts
    ``_SANCTIONED_TS_SUBCOMMANDS`` ⊇ ``_COMMAND_GROUPS`` for every command; this
    class pins the remaining sites (COMMAND_HELP, the dispatch branch) for these
    two specifically. The dispatch branch is proven by the subprocess calls in
    the behavior classes below returning ``rc == 0`` with valid JSON — an
    unwired dispatch would fall through to the help/usage path, not JSON.
    """

    def test_both_are_sanctioned(self):
        self.assertIn("plan-profile", _pcc._SANCTIONED_TS_SUBCOMMANDS)
        self.assertIn("check-conflicts", _pcc._SANCTIONED_TS_SUBCOMMANDS)

    def test_both_have_command_help(self):
        from track_state.cli import COMMAND_HELP
        self.assertIn("plan-profile", COMMAND_HELP)
        self.assertIn("check-conflicts", COMMAND_HELP)

    def test_both_are_in_command_groups(self):
        from track_state.cli import _COMMAND_GROUPS
        grouped = {c for _name, cmds in _COMMAND_GROUPS for c in cmds}
        self.assertIn("plan-profile", grouped)
        self.assertIn("check-conflicts", grouped)

    def test_both_are_track_dir_not_no_track_dir(self):
        # These take a <track-dir> positional, so they must NOT be in the
        # no-track-dir set (that set uses the argv[2:] flag-slice form).
        from track_state.cli import _NO_TRACK_DIR_COMMANDS
        self.assertNotIn("plan-profile", _NO_TRACK_DIR_COMMANDS)
        self.assertNotIn("check-conflicts", _NO_TRACK_DIR_COMMANDS)


class CheckConflictsBehavior(TestCase):
    """``check-conflicts`` emits the structured conflict set, kind by kind."""

    def test_harmful_undergating_and_all_exempt_both_fire(self):
        td = _write_plan(_PLAN)
        rc, payload, err = _run("check-conflicts", td)
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertTrue(payload["ok"])
        kinds = {c["kind"] for c in payload["conflicts"]}
        # Phase 2 (authored `test` on a Migrate phase) → harmful_undergating.
        self.assertIn("harmful_undergating", kinds)
        # Phase 1 (all-suite-exempt Docs) → all_exempt_suite_gated.
        self.assertIn("all_exempt_suite_gated", kinds)

    def test_harmful_undergating_detail_is_verbatim_init_wording(self):
        # init-from-plan folds this exact detail string into its warnings; the
        # shared composer guarantees the two cannot drift. Pin the phrasing.
        td = _write_plan(_PLAN)
        rc, payload, _ = _run("check-conflicts", td)
        harmful = [c for c in payload["conflicts"]
                   if c["kind"] == "harmful_undergating"]
        self.assertEqual(len(harmful), 1)
        self.assertEqual(harmful[0]["phase"], 2)
        self.assertEqual(harmful[0]["derived"], ["compile"])
        self.assertEqual(harmful[0]["authored"], ["test"])
        self.assertIn("under-gates a build/debt phase", harmful[0]["detail"])

    def test_all_exempt_suggests_verify_none(self):
        td = _write_plan(_PLAN)
        rc, payload, _ = _run("check-conflicts", td)
        all_exempt = [c for c in payload["conflicts"]
                      if c["kind"] == "all_exempt_suite_gated"]
        self.assertEqual(len(all_exempt), 1)
        self.assertEqual(all_exempt[0]["phase"], 1)
        self.assertEqual(all_exempt[0]["suggestion"], "verify: none")

    def test_legitimate_adversarial_emits_no_conflict(self):
        # Phase 3 (test,adversarial on a feature/harden phase) is NOT harmful —
        # adversarial is additive on a full-gate phase. Phase 3 must not appear.
        td = _write_plan(_PLAN)
        rc, payload, _ = _run("check-conflicts", td)
        phase3 = [c for c in payload["conflicts"] if c.get("phase") == 3]
        self.assertEqual(phase3, [])

    def test_directive_less_clean_plan_emits_no_conflicts(self):
        # The omit-by-default norm: a plan whose Migrate phases carry no
        # authored directive is clean — the resolver owns every phase.
        td = _write_plan(
            "# Implementation Plan: migrate\n"
            "## Phase 1: Migrate dependencies\n"
            "- [ ] [Migrate] bump parent <!-- AC-1 -->\n"
            "- [ ] [Manual] verify\n"
        )
        rc, payload, _ = _run("check-conflicts", td)
        self.assertEqual(rc, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["conflicts"], [])

    def test_migrate_phase_is_not_all_exempt(self):
        # An all-[Migrate] phase is build-gated, not suite-over-gated: Migrate
        # is tdd_exempt YET carries default_verify=[compile], so it must NOT
        # trip all_exempt_suite_gated (condition 2 of the predicate).
        td = _write_plan(
            "# Implementation Plan: migrate\n"
            "## Phase 1: Migrate dependencies\n"
            "- [ ] [Migrate] bump parent <!-- AC-1 -->\n"
            "- [ ] [Migrate] bump child <!-- AC-2 -->\n"
            "- [ ] [Manual] verify\n"
        )
        rc, payload, _ = _run("check-conflicts", td)
        kinds = {c["kind"] for c in payload["conflicts"]}
        self.assertNotIn("all_exempt_suite_gated", kinds)


class PlanProfileBehavior(TestCase):
    """``plan-profile`` emits the resolved modes + source per phase."""

    def test_authored_directive_shows_as_explicit(self):
        td = _write_plan(_PLAN)
        rc, payload, err = _run("plan-profile", td)
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertTrue(payload["ok"])
        by_phase = {p["phase"]: p for p in payload["phases"]}
        # Phase 2: authored `test` → explicit, with the directive captured.
        self.assertEqual(by_phase[2]["verify_modes"], ["test"])
        self.assertEqual(by_phase[2]["source"], "explicit")
        self.assertEqual(by_phase[2]["authored_directive"], "test")
        # Phase 1: directive-less Docs phase → full gate, no authored directive.
        self.assertEqual(by_phase[1]["verify_modes"], [])
        self.assertEqual(by_phase[1]["source"], "full_gate")
        self.assertIsNone(by_phase[1]["authored_directive"])

    def test_goal_strips_verify_comment(self):
        # goal is clean prose; the directive lives in authored_directive, not
        # duplicated into the goal string.
        td = _write_plan(_PLAN)
        rc, payload, _ = _run("plan-profile", td)
        by_phase = {p["phase"]: p for p in payload["phases"]}
        self.assertNotIn("<!-- verify:", by_phase[2]["goal"])
        self.assertEqual(by_phase[2]["goal"], "Migrate dependencies")

    def test_per_task_extracted_and_derived_tags(self):
        td = _write_plan(_PLAN)
        rc, payload, _ = _run("plan-profile", td)
        by_phase = {p["phase"]: p for p in payload["phases"]}
        docs_task = by_phase[1]["tasks"][0]
        self.assertEqual(docs_task["name"], "[Docs] notes")
        self.assertEqual(docs_task["tag"], "Docs")
        self.assertEqual(docs_task["derived"], "Docs")


class PlanAuditSafety(TestCase):
    """Read-only + fail-open: writes nothing; missing plan → ok:false."""

    def test_writes_nothing(self):
        td = _write_plan(_PLAN)
        before = sorted(Path(td).rglob("*"))
        _run("check-conflicts", td)
        _run("plan-profile", td)
        after = sorted(Path(td).rglob("*"))
        self.assertEqual(before, after, "plan-audit CLI wrote/deleted a file")

    def test_missing_plan_is_ok_false_not_exception(self):
        with tempfile.TemporaryDirectory() as empty:
            rc, payload, err = _run("check-conflicts", empty)
        self.assertEqual(rc, 0, f"raised instead of failing open: {err}")
        self.assertFalse(payload["ok"])
        self.assertIn("plan.md", payload["error"])


if __name__ == "__main__":
    main()
