"""Tests for ``track-state resolve-track`` — the code-driven track-dir resolver.

Promotes the skill §1.0 "locate track" step from prose to code, killing the bug
class where a small-window model hands ``conductor/tracks.md`` (the registry
file) to ``preflight`` instead of the track directory. ``resolve-track`` always
exits 0 — ambiguity/no-match are skill-handled branches surfaced in the JSON,
not errors (mirrors ``cmd_preflight``).
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
from scripts.track_state.misc import cmd_resolve_track, _iter_registry_entries

_scripts = Path(__file__).resolve().parent.parent / "scripts"
_CLI = _scripts / "track-state"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class _Project:
    """A temp project layout: <root>/conductor/{tracks.md, tracks/<id>/...}."""

    def __init__(self, root):
        self.root = Path(root)
        self.cond = self.root / "conductor"
        self.tracks_dir = self.cond / "tracks"
        self.tracks_dir.mkdir(parents=True, exist_ok=True)
        self.registry = self.cond / "tracks.md"

    def add_track(self, track_id, status, marker=None, line_fmt="checkbox", link=None):
        """Create a track dir with track-state.json and a registry line.

        ``link`` overrides the checkbox link path (default canonical
        ``conductor/tracks/<id>/`` — the form ``cmd_derive_name`` writes, and the
        one that exposed the path-doubling bug). The historic ``tracks/<id>/``
        form and absolute paths are exercised by passing ``link`` explicitly.
        """
        td = self.tracks_dir / track_id
        td.mkdir(parents=True, exist_ok=True)
        for f in ("spec.md", "plan.md"):
            (td / f).write_text("x")
        core.save(str(td), {"track_id": track_id, "status": status, "phases": []})
        # append a registry line
        lines = self.registry.read_text().splitlines() if self.registry.exists() else []
        if line_fmt == "checkbox":
            m = marker if marker is not None else {"new": " ", "in_progress": "~",
                                                    "completed": "x"}.get(status, " ")
            lp = link if link is not None else f"conductor/tracks/{track_id}/"
            lines.append(f"- [{m}] {track_id} ({lp})")
        elif line_fmt == "section":
            lines.append(f"### {track_id}")
            lines.append(f"- **Status:** {status}")
            lines.append(f"- **Path:** [link](tracks/{track_id}/)")
        elif line_fmt == "table":
            lines.append(f"| {track_id} | feature | {status} | desc |")
        self.registry.write_text("\n".join(lines) + "\n")
        return td

    def resolve(self, query=None, registry_path=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_resolve_track(query=query, registry_path=registry_path or str(self.registry))
        return json.loads(buf.getvalue())


class ResolveTrackQueryTests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_query_exact_track_id_short_circuits(self):
        self.p.add_track("auth_login_20260101", "in_progress")
        self.p.add_track("auth_logout_20260101", "in_progress")  # shortname collides
        r = self.p.resolve("auth_login_20260101")
        self.assertTrue(r["ok"])
        self.assertEqual(r["via"], "arg")
        self.assertEqual(r["track_id"], "auth_login_20260101")

    def test_query_shortname_prefix_unique(self):
        self.p.add_track("auth_login_20260101", "in_progress")
        self.p.add_track("docs_20260102", "completed")
        r = self.p.resolve("auth_log")  # shortname 'auth_login' prefix-matches
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_id"], "auth_login_20260101")

    def test_query_shortname_prefix_ambiguous(self):
        self.p.add_track("auth_login_20260101", "in_progress")
        self.p.add_track("auth_logout_20260101", "in_progress")
        r = self.p.resolve("auth")  # both shortnames start with 'auth'
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "ambiguous")
        self.assertEqual(len(r["candidates"]), 2)

    def test_query_path_basename_substring(self):
        self.p.add_track("feat_20260101", "in_progress")
        r = self.p.resolve("feat")  # tier-2 shortname 'feat' matches first, but verify ok
        self.assertTrue(r["ok"])

    def test_query_no_match(self):
        self.p.add_track("feat_20260101", "in_progress")
        r = self.p.resolve("nope")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_match")
        self.assertEqual(r["query"], "nope")
        self.assertIn("hint", r)

    def test_empty_query_string_treated_as_auto(self):
        self.p.add_track("feat_20260101", "in_progress")
        r = self.p.resolve("")
        self.assertTrue(r["ok"])
        self.assertEqual(r["via"], "auto_single")

    def test_query_case_insensitive(self):
        self.p.add_track("feat_20260101", "in_progress")
        r = self.p.resolve("FEAT_20260101")
        self.assertTrue(r["ok"])


class ResolveTrackAutoSelectTests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_auto_single_in_progress(self):
        self.p.add_track("live_20260101", "in_progress")
        self.p.add_track("done_20260102", "completed")
        r = self.p.resolve()
        self.assertTrue(r["ok"])
        self.assertEqual(r["via"], "auto_single")
        self.assertEqual(r["track_id"], "live_20260101")

    def test_auto_single_new_status(self):
        self.p.add_track("fresh_20260101", "new")
        r = self.p.resolve()
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "new")

    def test_auto_two_live_is_ambiguous(self):
        self.p.add_track("a_20260101", "in_progress")
        self.p.add_track("b_20260102", "in_progress")
        r = self.p.resolve()
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "ambiguous")
        self.assertEqual(len(r["candidates"]), 2)

    def test_auto_all_terminal_no_non_terminal(self):
        self.p.add_track("done_20260101", "completed")
        self.p.add_track("arch_20260102", "archived")
        r = self.p.resolve()
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_non_terminal")
        self.assertIn("hint", r)

    def test_query_supplied_even_when_all_terminal(self):
        # no_non_terminal is the no-query path; a query must still match.
        self.p.add_track("done_20260101", "completed")
        r = self.p.resolve("done_20260101")
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "completed")


class ResolveTrackAuthorityTests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_track_state_authoritative_over_registry_marker(self):
        # Registry says [ ] (new) but track-state.json says in_progress.
        td = self.p.add_track("feat_20260101", "in_progress", marker=" ")
        r = self.p.resolve()
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "in_progress")  # state wins over marker

    def test_missing_track_state_falls_back_to_marker(self):
        # Registry entry points at a track dir whose track-state.json is gone.
        self.p.add_track("orphan_20260101", "in_progress", marker="~")
        # delete the state file → load() fails → fall back to marker projection
        (self.p.tracks_dir / "orphan_20260101" / "track-state.json").unlink()
        r = self.p.resolve()
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "in_progress")  # marker '~' → in_progress


class ResolveTrackRegistryFormatsTests(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_section_format_resolves(self):
        self.p.add_track("sec_20260101", "in_progress", line_fmt="section")
        r = self.p.resolve()
        self.assertTrue(r["ok"])
        self.assertTrue(r["track_dir"].endswith("tracks/sec_20260101"))

    def test_table_format_resolves(self):
        self.p.add_track("tab_20260101", "in_progress", line_fmt="table")
        r = self.p.resolve()
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_id"], "tab_20260101")

    def test_iter_registry_entries_all_three_formats(self):
        self.p.add_track("cb_20260101", "in_progress", line_fmt="checkbox")
        self.p.add_track("se_20260102", "completed", line_fmt="section")
        self.p.add_track("ta_20260103", "in_progress", line_fmt="table")
        entries = _iter_registry_entries(self.p.registry.read_text(), str(self.p.cond))
        ids = {e["track_id"] for e in entries}
        self.assertEqual(ids, {"cb_20260101", "se_20260102", "ta_20260103"})


class ResolveTrackPathFormTests(TestCase):
    """The checkbox link-path forms — the path-doubling regression."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_checkbox_canonical_path_not_doubled(self):
        # The canonical form cmd_derive_name writes (conductor/tracks/<id>/) must
        # resolve to a SINGLE conductor/ segment — the regression that made
        # preflight HALT "Conductor environment incomplete" on a non-existent
        # conductor/conductor/tracks/<id> path.
        td = self.p.add_track("auth_20260706", "in_progress")  # default canonical
        r = self.p.resolve("auth_20260706")
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_dir"], str(td))
        self.assertNotIn("conductor/conductor", r["track_dir"])

    def test_checkbox_legacy_conductor_relative_path(self):
        # Historic form (tracks/<id>/) still resolves to the same real dir.
        td = self.p.add_track("leg_20260101", "in_progress",
                              link=f"tracks/leg_20260101/")
        r = self.p.resolve("leg_20260101")
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_dir"], str(td))

    def test_absolute_link_path(self):
        td = self.p.tracks_dir / "ab_20260101"
        self.p.add_track("ab_20260101", "in_progress", link=f"{td}/")
        r = self.p.resolve("ab_20260101")
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_dir"], str(td))


class ResolveTrackPlaceholderTests(TestCase):
    """Small-window-model defenses: literal $ARGUMENTS and full-path args."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)
        self.td = self.p.add_track("auth_20260706", "in_progress")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_literal_arguments_placeholder_auto_selects(self):
        # A model that emits $ARGUMENTS unsubstituted must not get no_match.
        for placeholder in ("$ARGUMENTS", "${ARGUMENTS}"):
            r = self.p.resolve(placeholder)
            self.assertTrue(r["ok"], placeholder)
            self.assertEqual(r["via"], "auto_single", placeholder)

    def test_full_path_arg_reduces_to_basename(self):
        # The done → post-loop-step hand-off passes <td> (the resolved dir)
        # verbatim — a full path whose basename IS the track_id.
        r = self.p.resolve(str(self.td))
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_id"], "auth_20260706")
        self.assertEqual(r["via"], "arg")


class ResolveTrackLocatorTests(TestCase):
    """_find_registry from CWD — the core of the bug scenario."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        self.p = _Project(self.d)
        self.p.add_track("feat_20260101", "in_progress")

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_cwd_at_project_root_finds_registry(self):
        os.chdir(self.d)  # project root — tracks.md is a CHILD at conductor/
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_resolve_track()  # no registry_path → _find_registry from CWD
        r = json.loads(buf.getvalue())
        self.assertTrue(r["ok"])
        self.assertEqual(r["via"], "auto_single")

    def test_cwd_in_track_dir_finds_registry(self):
        os.chdir(self.p.tracks_dir / "feat_20260101")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_resolve_track()
        r = json.loads(buf.getvalue())
        self.assertTrue(r["ok"])

    def test_cwd_in_nested_subdir_finds_registry(self):
        nested = Path(self.d) / "src" / "auth"
        nested.mkdir(parents=True)
        os.chdir(nested)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_resolve_track()
        r = json.loads(buf.getvalue())
        self.assertTrue(r["ok"])

    def test_no_registry_in_ancestors_returns_no_registry(self):
        bare = tempfile.mkdtemp()
        try:
            os.chdir(bare)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_resolve_track()
            r = json.loads(buf.getvalue())
            self.assertFalse(r["ok"])
            self.assertEqual(r["reason"], "no_registry")
        finally:
            shutil.rmtree(bare, ignore_errors=True)


class ResolveTrackCLITests(TestCase):
    """End-to-end through the real `track-state` binary."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._cwd = os.getcwd()
        self.p = _Project(self.d)
        self.p.add_track("feat_20260101", "in_progress")

    def tearDown(self):
        os.chdir(self._cwd)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_no_args_exits_zero(self):
        # The len<3 guard whitelist lets resolve-track run with no positional.
        proc = _run([sys.executable, str(_CLI), "resolve-track"])
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_help_lists_resolve_track(self):
        proc = _run([sys.executable, str(_CLI), "help", "resolve-track"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("resolve-track", proc.stdout)

    def test_cli_auto_select_from_cwd(self):
        os.chdir(self.d)
        proc = _run([sys.executable, str(_CLI), "resolve-track"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = json.loads(proc.stdout)
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_id"], "feat_20260101")

    def test_cli_query_arg(self):
        os.chdir(self.d)
        proc = _run([sys.executable, str(_CLI), "resolve-track", "feat_20260101"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = json.loads(proc.stdout)
        self.assertTrue(r["ok"])
        self.assertEqual(r["via"], "arg")

    def test_cli_registry_flag_overrides_locator(self):
        # Run from a CWD with no registry; --registry points at the real one.
        bare = tempfile.mkdtemp()
        try:
            os.chdir(bare)
            proc = _run([sys.executable, str(_CLI), "resolve-track",
                         "--registry", str(self.p.registry)])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            r = json.loads(proc.stdout)
            self.assertTrue(r["ok"])
        finally:
            shutil.rmtree(bare, ignore_errors=True)

    def test_cli_registry_flag_no_query(self):
        # resolve-track --registry X (no query) must not eat the flag into the
        # query slot — the arg-consumption bug the Plan agent flagged.
        os.chdir(self.d)
        proc = _run([sys.executable, str(_CLI), "resolve-track",
                     "--registry", str(self.p.registry)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = json.loads(proc.stdout)
        self.assertEqual(r.get("via"), "auto_single")  # not treated as query="--registry"


class ResolveTrackDescriptionParensTests(TestCase):
    """A checkbox description containing parens must not be mis-read as the link.

    The link is the TRAILING parenthetical; a non-greedy ``.*?\\(`` would capture
    the description's own parens (``Add SSO (OAuth2) login`` -> link ``OAuth2``)
    and resolve to a bogus track_dir — the realistic trigger for the reported
    ``setup ""`` "directly failed" (preflight HALT on a valid track).
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_parens_in_description_resolves_auto(self):
        td = self.p.add_track("sso-oauth2_20260706", "in_progress")
        # Overwrite the registry with a description that itself has parens.
        self.p.registry.write_text(
            "- [~] Add SSO (OAuth2) login (conductor/tracks/sso-oauth2_20260706/)\n")
        r = self.p.resolve()
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_id"], "sso-oauth2_20260706")
        self.assertEqual(r["track_dir"], str(td))  # not ".../OAuth2"

    def test_parens_in_description_shortname_query(self):
        self.p.add_track("sso-oauth2_20260706", "in_progress")
        self.p.registry.write_text(
            "- [~] Add SSO (OAuth2) login (conductor/tracks/sso-oauth2_20260706/)\n")
        r = self.p.resolve("sso")
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_id"], "sso-oauth2_20260706")

    def test_multiple_paren_groups_dont_corrupt_link(self):
        td = self.p.add_track("kv_20260706", "in_progress")
        self.p.registry.write_text(
            "- [~] Cache (redis) layer (v2) (conductor/tracks/kv_20260706/)\n")
        r = self.p.resolve()
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_dir"], str(td))


class ResolveTrackEmptyLinkTests(TestCase):
    """A checkbox with an empty link ``()`` carries no track identity and is skipped.

    Without the skip, such an entry emits ``{track_dir: None}``, which (a) makes
    ``_resolve_core`` auto-select a null track_dir -> ``cmd_setup`` crashes in
    ``_preflight_result`` (``Path(None)``), and (b) pollutes ``ambiguous``
    candidates with nulls the skill can't render. Real (parseable) entries must
    be unaffected.
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_empty_link_entry_not_emitted(self):
        self.p.registry.write_text(
            "- [~] ghost track ()\n"
            "- [~] real_20260706 (conductor/tracks/real_20260706/)\n")
        entries = _iter_registry_entries(self.p.registry.read_text(), str(self.p.cond))
        ids = [e["track_id"] for e in entries]
        self.assertEqual(ids, ["real_20260706"])  # ghost dropped, no None
        self.assertTrue(all(e["track_dir"] for e in entries))

    def test_empty_link_only_no_crash(self):
        # Sole live entry being a null-dir ghost must not auto-select null ->
        # no_non_terminal, not a TypeError.
        self.p.registry.write_text("- [~] ghost track ()\n")
        r = self.p.resolve()
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_non_terminal")

    def test_empty_link_ghost_excluded_from_ambiguous(self):
        self.p.add_track("a_20260101", "in_progress")
        self.p.registry.write_text(
            self.p.registry.read_text() + "- [~] ghost ()\n")
        r = self.p.resolve()
        # ghost dropped -> single live track auto-selects (not ambiguous)
        self.assertTrue(r["ok"])
        self.assertEqual(r["track_id"], "a_20260101")


class ResolveTrackLinklessFormatsTests(TestCase):
    """``new-track`` §2.6 historically said only "Append entry to tracks.md"
    with NO format constraint, so the model wrote freeform lines the parser
    silently dropped — breaking both auto-select (0 live entries parsed ->
    ``no_non_terminal``) and explicit ``setup <track>`` (``no_match``). The
    universal ``_\\d{8}`` token fallback recovers them. derive-name always
    stamps ``_YYYYMMDD``, so every real track_id is caught regardless of the
    surrounding line shape.
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = _Project(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _seed(self, track_id, status):
        # Creates the track dir + track-state.json (authoritative status) via the
        # canonical checkbox line, then the test overwrites tracks.md with the
        # freeform line under test. The dir/state persist across the overwrite.
        return self.p.add_track(track_id, status)

    def test_checkbox_without_link_resolves(self):
        self._seed("auth_20260706", "in_progress")
        self.p.registry.write_text("- [~] auth_20260706\n")
        r = self.p.resolve("auth_20260706")
        self.assertTrue(r["ok"])
        self.assertEqual(r["via"], "arg")
        self.assertEqual(r["track_id"], "auth_20260706")
        # auto-select too
        self.assertTrue(self.p.resolve()["ok"])

    def test_plain_bullet_resolves(self):
        self._seed("auth_20260706", "in_progress")
        self.p.registry.write_text("- auth_20260706 — Add SSO login\n")
        r = self.p.resolve("auth_20260706")
        self.assertTrue(r["ok"])
        self.assertTrue(self.p.resolve()["ok"])

    def test_bold_id_resolves(self):
        self._seed("auth_20260706", "in_progress")
        self.p.registry.write_text("- [~] **auth_20260706**: Add SSO login\n")
        self.assertTrue(self.p.resolve("auth_20260706")["ok"])

    def test_section_path_line_does_not_duplicate(self):
        # A section's "- **Path:** [link](tracks/<id>/)" body line also contains
        # the dated id -> the universal fallback would re-emit it. Dedup must
        # collapse to a single entry.
        self._seed("auth_20260706", "in_progress")
        self.p.registry.write_text(
            "### auth_20260706\n"
            "- **Status:** in_progress\n"
            "- **Path:** [link](tracks/auth_20260706/)\n")
        entries = _iter_registry_entries(
            self.p.registry.read_text(), str(self.p.cond))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["track_id"], "auth_20260706")

    def test_prefer_in_progress_over_multiple_new(self):
        # 2 new + 1 in_progress -> resume the single in_progress rather than ask.
        self._seed("a_new_20260101", "new")
        self._seed("b_new_20260102", "new")
        self._seed("c_prog_20260103", "in_progress")
        r = self.p.resolve()
        self.assertTrue(r["ok"])
        self.assertEqual(r["via"], "auto_prefer_in_progress")
        self.assertEqual(r["track_id"], "c_prog_20260103")

    def test_two_in_progress_still_ambiguous(self):
        self._seed("a_20260101", "in_progress")
        self._seed("b_20260102", "in_progress")
        r = self.p.resolve()
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "ambiguous")
        self.assertEqual(len(r["candidates"]), 2)


if __name__ == "__main__":
    main()
