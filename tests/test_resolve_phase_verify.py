"""Tests for ``track-state resolve-phase-verify`` — the deterministic phase-verify
directive resolver the planner calls instead of re-encoding the resolution
procedure as a prose ladder.

Composes the exact contract precedence (explicit > goal-derived > tag-derived >
full gate) by calling the existing resolver functions — no new resolution logic
is invented, only exposed. Read-only: no track-dir, no writes.

Load-bearing invariants under test:

- **Precedence**: explicit > goal > tag > full gate, in that strict order.
- **Read-only**: no ``track_dir`` arg, no writes anywhere (same posture as
  ``registry-doc``). Lets it sit in the sanctioned subcommand set.
- **No track-dir positional**: its inputs are ``--goal``/``--tags``/``--explicit``
  flags; the first flag must not be eaten as a phantom track-dir.
- **Fail-open**: any resolver error → the safe full-gate line, never a raised
  exception into the planner's one-shot CLI call.
- **Sanctioned**: in ``_SANCTIONED_TS_SUBCOMMANDS`` (else pre-command-check's
  broad-verb scan could false-positive on the ``--goal`` text).
"""
import os
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


def _run(goal=None, tags=None, explicit=None, env=None):
    """Run ``resolve-phase-verify`` and return (returncode, stdout, stderr)."""
    argv = [sys.executable, str(_CLI), "resolve-phase-verify"]
    if goal is not None:
        argv += ["--goal", goal]
    if tags is not None:
        argv += ["--tags", tags]
    if explicit is not None:
        argv += ["--explicit", explicit]
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout.strip(), proc.stderr


class ResolvePhaseVerifyPrecedence(TestCase):
    """explicit > goal-derived > tag-derived > full gate, in that order."""

    def test_goal_derived_none_for_deps_bump(self):
        # The canonical debt-carry case: a pure dependency mutation → none.
        rc, out, err = _run(goal="bump the spring-boot parent")
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertEqual(out, "verify: none")

    def test_goal_derived_test_start_for_boot(self):
        rc, out, err = _run(goal="the app boots", tags="Migrate")
        self.assertEqual(rc, 0, f"failed: {err}")
        # Goal-before-tag is load-bearing: a boot goal is test,start for ANY tag.
        self.assertEqual(out, "verify: test,start")

    def test_goal_derived_compile_for_migration_build(self):
        rc, out, err = _run(goal="migrate and make it build")
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertEqual(out, "verify: compile")

    def test_goal_derived_anchor_for_refactor_with_frozen_anchor(self):
        rc, out, err = _run(goal="refactor against the frozen anchor")
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertEqual(out, "verify: anchor")

    def test_explicit_overrides_goal(self):
        # Explicit wins over a goal that would otherwise derive a directive.
        rc, out, err = _run(goal="the app boots", explicit="verify: anchor")
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertEqual(out, "verify: anchor")

    def test_explicit_passed_through_verbatim(self):
        # The explicit directive is carried over a retry verbatim — no
        # re-classification, no normalization.
        rc, out, err = _run(goal="whatever", explicit="verify: compile,test,start")
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertEqual(out, "verify: compile,test,start")

    def test_tag_derived_when_goal_classifier_empty(self):
        # No goal signal + a Migrate tag → the tag's default_verify (compile).
        rc, out, err = _run(tags="Migrate")
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertEqual(out, "verify: compile")

    def test_full_gate_when_nothing_resolves(self):
        # Plain feature work, no tag → no directive (the default full gate).
        rc, out, err = _run(goal="add a login form")
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertEqual(out, "(no directive — default full gate)")

    def test_empty_goal_and_no_tag_is_full_gate(self):
        rc, out, err = _run(goal="")
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertEqual(out, "(no directive — default full gate)")

    def test_goal_takes_precedence_over_tag(self):
        # A boot goal with a Migrate tag is still test,start (goal wins), NOT
        # the tag's compile default. This is the load-bearing precedence rule.
        rc_boot, out_boot, _ = _run(goal="the app boots", tags="Migrate")
        self.assertEqual(out_boot, "verify: test,start")
        # And a plain (non-boot) goal with a Migrate tag falls through to the
        # tag default only because the goal classifier returned [].
        rc_plain, out_plain, _ = _run(goal="reorganize the modules", tags="Migrate")
        self.assertEqual(out_plain, "verify: compile")


class ResolvePhaseVerifyReadOnly(TestCase):
    """The safety contract: never writes, never needs a track-dir."""

    def test_writes_nothing_in_temp_dir(self):
        # Run in a temp dir with NO conductor/ — no project overlay discoverable,
        # no track-state.json. Snapshot the tree before/after; nothing changes.
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "CLAUDE_PROJECT_DIR": d}
            before = sorted(Path(d).rglob("*"))
            rc, out, err = _run(goal="bump the spring-boot parent", env=env)
            self.assertEqual(rc, 0, f"failed in temp dir: {err}")
            after = sorted(Path(d).rglob("*"))
            self.assertEqual(before, after, "resolve-phase-verify wrote/deleted a file")

    def test_first_flag_not_eaten_as_track_dir(self):
        # The no-track-dir guard: --goal must not be mis-parsed as a phantom
        # track-dir positional (the bug that bit the first wiring). Confirmed by
        # a clean resolve, not the "not an existing directory" error.
        rc, out, err = _run(goal="bump the spring-boot parent")
        self.assertEqual(rc, 0)
        self.assertNotIn("not an existing directory", err)
        self.assertNotIn("no_registry", err)


class ResolvePhaseVerifySanctioned(TestCase):
    """resolve-phase-verify is in the sanctioned subcommand set (read-only)."""

    def test_is_sanctioned(self):
        self.assertIn("resolve-phase-verify", _pcc._SANCTIONED_TS_SUBCOMMANDS)


class ResolvePhaseVerifyFailOpen(TestCase):
    """Any resolver error → the safe full-gate line, never a raised exception."""

    def test_unknown_tag_does_not_raise(self):
        # An unknown tag in the tag-derived fallback: default_verify_for_phase
        # is fail-open (returns [] for unknown tags), so we get the full gate,
        # not a traceback.
        rc, out, err = _run(goal="", tags="NoSuchTag")
        self.assertEqual(rc, 0, f"failed: {err}")
        self.assertEqual(out, "(no directive — default full gate)")


if __name__ == "__main__":
    main()
