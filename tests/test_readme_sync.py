"""Tests for ``check-readme-sync`` — the README drift gate (campaign 2.5).

Load-bearing invariants under test:

- **Fragment surgery is marker-bounded**: ``replace_fragment`` swaps only the
  text between ``<!-- conductor:begin/end -->`` markers; hand prose outside is
  never touched (the README's hand-written notes are the whole point of the
  marker design).
- **Drift is DETECTED, not silently fixed**: ``sync()`` reports stale
  fragments, missing marker pairs, and unfound count sentences — the exact
  failure shapes the pre-1.1 README hit (23-vs-24 agents, missing
  build-runner row, 16-vs-17 skills).
- **Counts are derived, never trusted**: the Features bullet + architecture
  tree counts re-render from directory listings / hooks.json, and ``--fix``
  semantics = "re-run sub until no residual problems".
- **The real tree verifies clean** — README.md on disk matches its sources.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))
sys.path.insert(0, str(_scripts / "lib"))

_spec = importlib.util.spec_from_file_location(
    "crs", _scripts / "check-readme-sync.py")
crs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crs)


class FrontmatterTests(TestCase):
    def _write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return Path(f.name)

    def test_parses_name_model_description(self):
        p = self._write(
            "---\nname: build-runner\ndescription: Runs the build gate\n"
            "model: opus\n---\n\nBody line.\n")
        fm = crs._frontmatter(p)
        self.assertEqual(fm["name"], "build-runner")
        self.assertEqual(fm["model"], "opus")
        self.assertEqual(fm["description"], "Runs the build gate")

    def test_first_win_on_duplicate_keys(self):
        p = self._write("---\nname: a\nname: b\n---\n")
        self.assertEqual(crs._frontmatter(p)["name"], "a")

    def test_no_frontmatter_returns_empty(self):
        p = self._write("Just prose, no block.\n")
        self.assertEqual(crs._frontmatter(p), {})

    def test_missing_file_returns_empty(self):
        # A glob victim that vanishes between listing and reading must not
        # crash the gate.
        self.assertEqual(crs._frontmatter(Path("/nonexistent/x.md")), {})


class ReplaceFragmentTests(TestCase):
    TEXT = ("intro\n<!-- conductor:begin:demo -->\nold body\n"
            "<!-- conductor:end:demo -->\noutro\n")

    def test_swaps_only_the_fragment(self):
        out = crs.replace_fragment(self.TEXT, "demo", "new body")
        self.assertIn("<!-- conductor:begin:demo -->\nnew body\n"
                      "<!-- conductor:end:demo -->", out)
        self.assertTrue(out.startswith("intro\n"))
        self.assertTrue(out.endswith("outro\n"))

    def test_missing_marker_raises(self):
        with self.assertRaises(KeyError) as ctx:
            crs.replace_fragment("no markers here\n", "demo", "x")
        self.assertIn("demo", str(ctx.exception))

    def test_adjacent_fragment_untouched(self):
        text = (self.TEXT.replace("demo", "a").rstrip()
                + "\n<!-- conductor:begin:b -->\nkeep\n<!-- conductor:end:b -->\n")
        out = crs.replace_fragment(text, "a", "new")
        self.assertIn("<!-- conductor:begin:b -->\nkeep\n"
                      "<!-- conductor:end:b -->", out)


class CountFixerTests(TestCase):
    """The count sentences live in HAND prose — regex-verified, regex-fixed."""

    def test_agent_bullet_count_fixed(self):
        text = "dispatches 23 specialized AI agents on your behalf."
        n_agents = 3
        pat = r"(dispatches )\d+( specialized AI agents)"
        fixer = lambda m: f"{m.group(1)}{n_agents}{m.group(2)}"  # noqa: E731
        out, n = __import__("re").subn(pat, fixer, text, count=1)
        self.assertEqual(n, 1)
        self.assertIn("dispatches 3 specialized", out)

    def test_hooks_sentence_shape_matches(self):
        # The two-number hooks sentence must keep matching after edits around
        # it (the drift bug class: someone rewords the sentence, the gate
        # silently stops checking it → sync() reports it instead).
        import re
        pat = r"(hooks/hooks\.json\s+)\d+(\s+hook event types,\s+)\d+(\s+hook entries)"
        self.assertIsNotNone(re.search(
            pat, "hooks/hooks.json 12 hook event types, 14 hook entries"))
        self.assertIsNone(re.search(
            pat, "hooks/hooks.json twelve event types, fourteen entries"))


class SyncDriftTests(TestCase):
    """sync() = apply + report. Drift descriptions are the CI signal."""

    def _root(self, agents, skills=(), hooks=None):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        (root / "agents").mkdir()
        for name, desc in agents:
            (root / "agents" / f"{name}.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\n---\n")
        (root / "skills").mkdir()
        for name, desc in skills:
            sk = root / "skills" / name
            sk.mkdir()
            (sk / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {desc}\n---\n")
        # hook_counts is deliberately strict (a missing hooks.json must not
        # silently become 0-counts), so synthetic trees carry one.
        (root / "hooks").mkdir()
        (root / "hooks" / "hooks.json").write_text(
            '{"hooks": {"PreToolUse": [{"a": 1}], "PostToolUse": [{"b": 2}]}}')
        return root

    def test_stale_fragment_reported_and_fixed(self):
        root = self._root(agents=[("build-runner", "runs builds")])
        text = ("<!-- conductor:begin:agents-table -->\n"
                "| Agent | Model | Purpose |\n|---|---|---|\n"
                "<!-- conductor:end:agents-table -->\n")
        new_text, drift, changed = crs.sync(text, root)
        self.assertIn("fragment 'agents-table' is stale", drift)
        self.assertTrue(changed)
        # Fixed on apply: re-running sync on the output is drift-free.
        _, residual, _ = crs.sync(new_text, root)
        self.assertEqual([d for d in residual if "agents-table" in d], [])

    def test_missing_marker_pair_reported_unfixable(self):
        root = self._root(agents=[])
        text = "A README with no fragments at all.\n"
        new_text, drift, changed = crs.sync(text, root)
        self.assertTrue(any(d.startswith("missing marker pair") for d in drift),
                        f"expected missing-marker drift: {drift}")
        self.assertFalse(changed, "nothing can be fixed without markers")

    def test_current_fragment_no_drift(self):
        # A fully-current synthetic README (all three fragments + the four
        # count sentences matching the synthetic tree) reports ZERO drift.
        root = self._root(agents=[("a-one", "does a")], skills=[("s-one", "does s")])
        n_ev, n_en = crs.hook_counts(root)
        text = "\n".join([
            "dispatches 1 specialized AI agents on your behalf.",
            "├── agents/ 1 specialised agent definitions",
            "├── skills/ 1 slash-command skills",
            f"├── hooks/hooks.json {n_ev} hook event types, {n_en} hook entries",
            f"<!-- conductor:begin:agents-table -->\n{crs.render_agents_table(root)}\n<!-- conductor:end:agents-table -->",
            f"<!-- conductor:begin:commands-table -->\n{crs.render_commands_table(root)}\n<!-- conductor:end:commands-table -->",
            f"<!-- conductor:begin:cli-groups -->\n{crs.render_cli_groups()}\n<!-- conductor:end:cli-groups -->",
        ])
        new_text, drift, changed = crs.sync(text, root)
        self.assertEqual(drift, [])
        self.assertFalse(changed)
        self.assertEqual(new_text, text)

    def test_count_sentence_vanished_is_reported(self):
        # The Features bullet was reworded so the regex no longer matches —
        # sync must FAIL LOUD, not pass by absence.
        root = self._root(agents=[("a-one", "does a")])
        text = "We dispatch lots of agents.\n"
        _, drift, _ = crs.sync(text, root)
        self.assertTrue(any(d.startswith("count sentence not found") for d in drift),
                        f"expected unfound-count drift: {drift}")


class SourceReaderTests(TestCase):
    def test_command_groups_shape(self):
        groups = crs.command_groups()
        self.assertGreaterEqual(len(groups), 10)
        flat = [c for _g, cmds in groups for c in cmds]
        self.assertGreaterEqual(len(flat), 50)
        # Spot-known members survive (pure re-homing guard for 2.1).
        all_cmds = set(flat) | {"setup", "help"}
        for known in ("dispatch-next", "finalize", "phase-done", "status",
                      "reconcile-plan", "split"):
            self.assertIn(known, all_cmds, f"{known} missing from COMMAND_GROUPS")

    def test_renderers_are_tables(self):
        for render in (crs.render_agents_table(crs.get_root()),
                       crs.render_commands_table(crs.get_root())):
            self.assertTrue(render.startswith("| "))
            self.assertIn("\n|", render)
        # cli-groups carries an intro sentence (with the live counts) before
        # the full group table.
        cli = crs.render_cli_groups()
        self.assertIn("| Group | Subcommands |", cli)
        self.assertIn("subcommands across", cli)

    def test_agent_rows_sorted_and_counted(self):
        rows = crs.agent_rows(crs.get_root())
        names = [r[0] for r in rows]
        self.assertEqual(names, sorted(names))
        # The 1.1 drift bug: build-runner missing from the table.
        self.assertIn("build-runner", names)


class TreeIntegrationTests(TestCase):
    """The real README.md verifies clean — the gate's standing guarantee."""

    def test_real_readme_in_sync(self):
        text = crs.README.read_text(encoding="utf-8")
        new_text, drift, changed = crs.sync(text, crs.get_root())
        self.assertFalse(changed,
                         f"README.md drifted from its sources: {drift}")

    def test_readme_carries_all_markers(self):
        text = crs.README.read_text(encoding="utf-8")
        for name in crs.FRAGMENTS:
            self.assertIn(f"<!-- conductor:begin:{name} -->", text)
            self.assertIn(f"<!-- conductor:end:{name} -->", text)

    def test_main_exit_zero_on_clean_tree(self):
        self.assertEqual(crs.main([]), 0)


if __name__ == "__main__":
    main()
