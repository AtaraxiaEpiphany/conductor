"""Tests for ``track-state setup`` — resolve + preflight in one call.

Collapses the skill §1.0 resolve+preflight pair so the model never hand-carries
``<td>`` between them (the path-mishandling class that motivated resolve-track).
Always exits 0 — outcome in JSON (mirrors ``cmd_preflight`` / ``cmd_resolve_track``).
The resolve defenses (literal ``$ARGUMENTS``, full-path) and the path-doubling
fix live in ``_resolve_core`` and are covered in ``test_resolve_track``; here we
cover the composition and the ``reason:"preflight"`` branch.
"""
import io
import json
import os
import contextlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import core
from scripts.track_state.misc import cmd_setup

_scripts = Path(__file__).resolve().parent.parent / "scripts"
_CLI = _scripts / "track-state"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class _Project:
    """A temp project layout: <root>/conductor/{tracks.md, tracks/<id>/, workflow/}.

    Workflow files are created so preflight's ``missing_workflow`` check passes by
    default; the preflight-branch tests delete them to exercise the failure path.
    """

    def __init__(self, root):
        self.root = Path(root)
        self.cond = self.root / "conductor"
        self.tracks_dir = self.cond / "tracks"
        self.tracks_dir.mkdir(parents=True, exist_ok=True)
        self.registry = self.cond / "tracks.md"
        wf = self.cond / "workflow"
        wf.mkdir(exist_ok=True)
        (wf / "index.md").write_text("i")
        (wf / "post-loop.md").write_text("p")

    def add_track(self, track_id, status, marker=None, link=None):
        td = self.tracks_dir / track_id
        td.mkdir(parents=True, exist_ok=True)
        for f in ("spec.md", "plan.md"):
            (td / f).write_text("x")
        core.save(str(td), {"track_id": track_id, "status": status, "phases": []})
        m = marker if marker is not None else {"new": " ", "in_progress": "~",
                                               "completed": "x"}.get(status, " ")
        lp = link if link is not None else f"conductor/tracks/{track_id}/"
        lines = self.registry.read_text().splitlines() if self.registry.exists() else []
        lines.append(f"- [{m}] {track_id} ({lp})")
        self.registry.write_text("\n".join(lines) + "\n")
        return td

    def setup(self, query=None, registry_path=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_setup(query=query, registry_path=registry_path or str(self.registry))
        return json.loads(buf.getvalue())


class SetupOkTests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_resolves_and_preflights_ok(self):
        td = self.p.add_track("auth_20260706", "in_progress")
        r = self.p.setup()
        self.assertTrue(r["ok"])
        self.assertEqual(r["td"], str(td))
        self.assertEqual(r["track_id"], "auth_20260706")
        self.assertEqual(r["status"], "in_progress")
        self.assertEqual(r["via"], "auto_single")

    def test_no_arg_auto_select(self):
        self.p.add_track("feat_20260101", "in_progress")
        r = self.p.setup()
        self.assertTrue(r["ok"])
        self.assertEqual(r["via"], "auto_single")

    def test_literal_placeholder_auto_selects(self):
        self.p.add_track("feat_20260101", "in_progress")
        r = self.p.setup("$ARGUMENTS")
        self.assertTrue(r["ok"])
        self.assertEqual(r["via"], "auto_single")


class SetupPreflightBranchTests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_reason_preflight_when_spec_missing(self):
        td = self.p.add_track("feat_20260101", "in_progress")
        (td / "spec.md").unlink()
        r = self.p.setup("feat_20260101")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "preflight")
        self.assertEqual(r["td"], str(td))
        self.assertEqual(r["missing"], ["spec.md"])
        self.assertIn("hint", r)

    def test_reason_preflight_when_workflow_missing(self):
        self.p.add_track("feat_20260101", "in_progress")
        (self.p.cond / "workflow" / "index.md").unlink()
        r = self.p.setup("feat_20260101")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "preflight")
        self.assertIn("workflow/index.md", r["missing_workflow"])


class SetupPassThroughTests(TestCase):
    """cmd_setup passes the _resolve_core envelope through when resolve fails."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        self.p = _Project(self.d)

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_passes_through_ambiguous(self):
        self.p.add_track("a_20260101", "in_progress")
        self.p.add_track("b_20260102", "in_progress")
        r = self.p.setup()
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "ambiguous")
        self.assertEqual(len(r["candidates"]), 2)

    def test_passes_through_no_match(self):
        self.p.add_track("feat_20260101", "in_progress")
        r = self.p.setup("nope")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_match")

    def test_passes_through_no_non_terminal(self):
        self.p.add_track("done_20260101", "completed")
        r = self.p.setup()
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_non_terminal")

    def test_passes_through_no_registry(self):
        bare = tempfile.mkdtemp()
        try:
            os.chdir(bare)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_setup()  # no registry_path → _find_registry finds nothing
            r = json.loads(buf.getvalue())
            self.assertFalse(r["ok"])
            self.assertEqual(r["reason"], "no_registry")
        finally:
            shutil.rmtree(bare, ignore_errors=True)


class SetupCLITests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        self.p = _Project(self.d)
        self.p.add_track("feat_20260101", "in_progress")

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_no_args_exits_zero(self):
        # The len<3 guard whitelist lets setup run with no positional.
        proc = _run([sys.executable, str(_CLI), "setup"])
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_help_lists_setup(self):
        proc = _run([sys.executable, str(_CLI), "help", "setup"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("setup", proc.stdout)

    def test_cli_auto_select_from_cwd(self):
        os.chdir(self.d)
        proc = _run([sys.executable, str(_CLI), "setup"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = json.loads(proc.stdout)
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_id"], "feat_20260101")


class SetupLinkParsingTests(TestCase):
    """The skill §1.0 invokes ``track-state setup "$ARGUMENTS"`` — often with an
    empty arg when the user runs ``/conductor:parallel-step`` directly. Two
    link-parsing defects made that path fail on otherwise-valid registries:

    - A checkbox description containing parens (``Add SSO (OAuth2) login``)
      was mis-parsed so the description's paren content became the link ->
      ``setup ""`` auto-selected a bogus dir -> ``reason: "preflight"`` HALT.
    - A checkbox with an empty link ``()`` was emitted as ``{track_dir: None}``;
      as the sole live entry it made ``setup`` crash (``Path(None)``) instead of
      exiting 0.
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_setup_auto_select_with_parens_in_description(self):
        td = self.p.add_track("sso-oauth2_20260706", "in_progress")
        self.p.registry.write_text(
            "- [~] Add SSO (OAuth2) login (conductor/tracks/sso-oauth2_20260706/)\n")
        r = self.p.setup()
        self.assertTrue(r["ok"])
        self.assertEqual(r["td"], str(td))
        self.assertEqual(r["track_id"], "sso-oauth2_20260706")
        self.assertNotIn("OAuth2", r["td"])

    def test_setup_empty_link_only_does_not_crash(self):
        # The literal reported failure: ``setup ""`` where the registry's only
        # live entry is a null-dir ghost. Must exit 0 with a reason, not raise.
        self.p.registry.write_text("- [~] ghost track ()\n")
        r = self.p.setup()
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_non_terminal")

    def test_setup_cli_empty_arg_with_parens_description(self):
        # End-to-end via the CLI entry point the skill actually calls, with the
        # literal empty-string arg (``setup "$ARGUMENTS"`` with no args).
        self.p.add_track("sso-oauth2_20260706", "in_progress")
        self.p.registry.write_text(
            "- [~] Add SSO (OAuth2) login (conductor/tracks/sso-oauth2_20260706/)\n")
        proc = _run([sys.executable, str(_CLI), "setup", "",
                     "--registry", str(self.p.registry)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = json.loads(proc.stdout)
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_id"], "sso-oauth2_20260706")


class SetupActionDirectiveTests(TestCase):
    """The skill switches on ``action`` (proceed/ask/halt), not on a parsed
    ``reason`` — this is what keeps the 5 §1.0 blocks a 3-arm switch instead of a
    run-on branch sentence. The legacy ``ok``/``reason``/``td``/``candidates``
    fields stay alongside ``action`` (backward-compatible superset).
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_proceed_emits_action_and_announce(self):
        self.p.add_track("auth_20260706", "in_progress")
        r = self.p.setup()
        self.assertEqual(r["action"], "proceed")
        self.assertEqual(r["ok"], True)  # legacy field preserved
        self.assertEqual(r["track_id"], "auth_20260706")
        self.assertIn("announce", r)
        # announce names the track + status + how (auto-selected here)
        self.assertIn("auth_20260706", r["announce"])
        self.assertIn("in_progress", r["announce"])
        self.assertIn("auto-selected", r["announce"])

    def test_proceed_via_arg_says_resolved(self):
        self.p.add_track("auth_20260706", "in_progress")
        r = self.p.setup("auth_20260706")
        self.assertEqual(r["action"], "proceed")
        self.assertIn("resolved", r["announce"])

    def test_ask_emits_action_and_candidates(self):
        self.p.add_track("a_20260101", "in_progress")
        self.p.add_track("b_20260102", "in_progress")
        r = self.p.setup()
        self.assertEqual(r["action"], "ask")
        self.assertEqual(r["reason"], "ambiguous")  # legacy preserved
        self.assertEqual(len(r["candidates"]), 2)

    def test_halt_preflight_emits_message(self):
        td = self.p.add_track("feat_20260101", "in_progress")
        (td / "spec.md").unlink()
        r = self.p.setup("feat_20260101")
        self.assertEqual(r["action"], "halt")
        self.assertEqual(r["reason"], "preflight")
        self.assertIn("message", r)
        self.assertEqual(r["missing"], ["spec.md"])  # legacy preserved

    def test_halt_no_non_terminal_emits_message(self):
        self.p.add_track("done_20260101", "completed")
        r = self.p.setup()
        self.assertEqual(r["action"], "halt")
        self.assertEqual(r["reason"], "no_non_terminal")
        self.assertIn("message", r)

    def test_halt_no_registry_emits_message(self):
        bare = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        try:
            os.chdir(bare)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_setup()  # no registry_path -> _find_registry finds nothing
            r = json.loads(buf.getvalue())
            self.assertEqual(r["action"], "halt")
            self.assertEqual(r["reason"], "no_registry")
            self.assertIn("/conductor:setup", r["message"])
        finally:
            os.chdir(self._cwd)
            shutil.rmtree(bare, ignore_errors=True)


if __name__ == "__main__":
    main()
