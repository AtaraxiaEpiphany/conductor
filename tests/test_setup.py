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
from scripts.track_state import agent_roster as ar
from scripts.track_state.misc import cmd_setup, cmd_check, _resolve_core

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

    def check(self, query=None, registry_path=None):
        # Canonical entry point (``cmd_setup`` is the pre-rename alias for it).
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_check(query=query, registry_path=registry_path or str(self.registry))
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


class SetupRosterLintTests(TestCase):
    """``check``'s agent-roster halt arm (design D4): runtime hooks are
    fail-open by design, so a broken project overlay surfaces HERE — reason
    ``"roster"`` with the validation findings — while a GOOD overlay row for a
    project agent proceeds untouched (the campaign's happy path)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)
        self.p.add_track("feat_20260101", "in_progress")
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.p.root)
        ar._load.cache_clear()

    def tearDown(self):
        if self._prior_proj is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        else:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        shutil.rmtree(self.d, ignore_errors=True)
        ar._load.cache_clear()

    def _write_overlay(self, doc):
        (self.p.cond / "workflow" / "agent-roster.json").write_text(
            json.dumps(doc), encoding="utf-8")
        ar._load.cache_clear()

    def test_broken_overlay_halts_with_reason_roster(self):
        self._write_overlay({"agents": {
            "proj-agent": {"class": "executorr", "fence": "x"},
        }})
        r = self.p.check("feat_20260101")
        self.assertFalse(r["ok"])
        self.assertEqual(r["action"], "halt")
        self.assertEqual(r["reason"], "roster")
        self.assertTrue(r["roster_errors"], "the findings must be in the payload")
        self.assertIn("registry-doc --roster", r["message"])

    def test_dead_name_halts_with_reason_roster(self):
        # Declared-names-exist is a halt too: a roster row naming an agent
        # with no definition file in any harness home is a typo, and letting
        # the track proceed would dispatch into nothing.
        self._write_overlay({"agents": {
            "ghost-agent": {"class": "advisory", "fence": "x"},
        }})
        r = self.p.check("feat_20260101")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "roster")
        self.assertTrue(any("ghost-agent" in e for e in r["roster_errors"]))

    def test_good_project_overlay_row_proceeds(self):
        # ONE overlay row + a project agent file = full scaffold, and check
        # stays green — adding a project agent must NOT cost track work.
        proj_agents = self.p.root / ".claude" / "agents"
        proj_agents.mkdir(parents=True, exist_ok=True)
        (proj_agents / "proj-agent.md").write_text("---\n---\nbody\n",
                                                   encoding="utf-8")
        self._write_overlay({"agents": {
            "proj-agent": {"class": "executor",
                           "fence": "---PROJ RESULT--- ... ---END RESULT---"},
        }})
        r = self.p.check("feat_20260101")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["action"], "proceed")
        self.assertEqual(r.get("roster_errors", []), [])


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

    def test_help_lists_check(self):
        proc = _run([sys.executable, str(_CLI), "help", "check"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("check", proc.stdout)
        # ``setup`` is the pre-rename alias — no longer in the help listing.
        alias = _run([sys.executable, str(_CLI), "help", "setup"])
        self.assertEqual(alias.returncode, 1)

    def test_cli_auto_select_from_cwd(self):
        os.chdir(self.d)
        proc = _run([sys.executable, str(_CLI), "check"])
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


class CheckDiagnosticsTests(TestCase):
    """The diagnostic-collapse fix: a track that exists on disk but lacks state,
    or whose registry dir doesn't exist, used to collapse to the useless
    ``no_non_terminal`` ("No track with status new/in_progress. Pass a track_id
    query."). Each now surfaces a precise, actionable reason.
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_check_track_not_initialized(self):
        # Scenario 1: dir exists with spec/plan but NO track-state.json — the
        # track was scaffolded but init-from-plan never ran.
        td = self.p.tracks_dir / "feat_20260706"
        td.mkdir(parents=True, exist_ok=True)
        (td / "spec.md").write_text("x")
        (td / "plan.md").write_text("x")
        self.p.registry.write_text(
            "- [ ] feat_20260706 (conductor/tracks/feat_20260706/)\n")
        r = self.p.check()
        self.assertEqual(r["action"], "halt")
        self.assertEqual(r["reason"], "track_not_initialized")
        self.assertEqual(r["track_id"], "feat_20260706")
        self.assertEqual(r["td"], str(td))
        self.assertIn("track-state.json", r["missing"])
        self.assertIn("recover", r)
        self.assertIn("init-from-plan", r["recover"])

    def test_check_track_dir_missing(self):
        # Scenario 3: registry lists a track_id whose dir doesn't exist
        # (bare-line entry, dir-name mismatch / orphan).
        self.p.registry.write_text("ghost_20260706 some description\n")
        r = self.p.check()
        self.assertEqual(r["action"], "halt")
        self.assertEqual(r["reason"], "track_dir_missing")
        self.assertIn("ghost_20260706", r["track_ids"])

    def test_check_registry_at_root_resolves(self):
        # Scenario 2: tracks.md at the PROJECT ROOT (not conductor/). The
        # candidate-root probe must still find <root>/conductor/tracks/<id>.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        td = root / "conductor" / "tracks" / "feat_20260706"
        td.mkdir(parents=True)
        (td / "spec.md").write_text("x")
        (td / "plan.md").write_text("x")
        core.save(str(td), {"track_id": "feat_20260706",
                            "status": "in_progress", "phases": []})
        wf = root / "conductor" / "workflow"
        wf.mkdir(parents=True)
        (wf / "index.md").write_text("i")
        (root / "tracks.md").write_text("feat_20260706 desc\n")  # at root
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_check(query=None, registry_path=str(root / "tracks.md"))
        r = json.loads(buf.getvalue())
        self.assertEqual(r["action"], "proceed")
        self.assertEqual(r["track_id"], "feat_20260706")
        self.assertEqual(r["td"], str(td))

    def test_resolve_core_auto_selects_uninit_by_identity(self):
        # The "smart auto-select": a single on-disk-but-uninitialized track is
        # selected by identity (via:auto_uninit) so preflight can diagnose it,
        # instead of being dropped as a non-terminal-status miss.
        td = self.p.tracks_dir / "feat_20260706"
        td.mkdir(parents=True, exist_ok=True)
        (td / "spec.md").write_text("x")
        (td / "plan.md").write_text("x")
        self.p.registry.write_text(
            "- [ ] feat_20260706 (conductor/tracks/feat_20260706/)\n")
        core_out = _resolve_core(self.p.registry, None)
        self.assertTrue(core_out["ok"])
        self.assertEqual(core_out["via"], "auto_uninit")
        self.assertEqual(core_out["track_id"], "feat_20260706")

    def test_check_alias_setup_identical(self):
        # The rename keeps ``setup`` as a hidden alias with identical output.
        self.p.add_track("auth_20260706", "in_progress")
        a = _run([sys.executable, str(_CLI), "check", "",
                  "--registry", str(self.p.registry)])
        b = _run([sys.executable, str(_CLI), "setup", "",
                  "--registry", str(self.p.registry)])
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(b.returncode, 0, b.stderr)
        self.assertEqual(json.loads(a.stdout), json.loads(b.stdout))


if __name__ == "__main__":
    main()
