"""Tests for fix/explorer-doc-placement (Design 3, generator-only).

- handoff --type explore renders the full explorer schema (Files Inventory,
  Out-of-Scope, Graduation Candidates) — the sanctioned channel the explorer
  now writes to instead of tracks/<track>/exploration.md.
- _init_core writes .conductor/.gitignore so transient result.json isn't swept
  into conductor commits.
- lint check_misplaced_docs flags stray .md in the track dir (contract: Spec/
  Plan/Meta only) and passes the meta allowlist + .conductor/ scratch.
"""
import io
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.handoff import cmd_append_handoff
from scripts.track_state.quality import _init_core


def _out_captured(fn, *args, **kwargs):
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue()), sys.stderr.getvalue()
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _make_track():
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text("# Plan\n\n## Phase 1: Build\n- [ ] Task A\n")
    save(d, {
        "track_id": "test", "type": "feature", "status": "in_progress",
        "description": "test", "current_phase_index": 1, "current_task_index": 1,
        "updated_at": "2026-06-19T00:00:00+00:00",
        "phases": [{"name": "Phase 1", "status": "pending",
                    "tasks": [{"name": "Task A", "status": "pending"}]}],
    })
    return d


def _load_lint():
    """lint-track-state.py has a hyphenated name — load by path with scripts/ on
    sys.path so its `from lib.*` imports resolve (matches production auto-path[0])."""
    repo = Path(__file__).resolve().parent.parent
    scripts_dir = str(repo / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(
        "lint_track_state", str(repo / "scripts" / "lint-track-state.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestExploreHandoffFullSchema(TestCase):
    def test_renders_files_inventory_outofscope_graduation(self):
        d = _make_track()
        content = json.dumps({
            "summary": "Auth module wires JWT middleware into the request pipeline.",
            "findings": ["f1"], "architecture": "A",
            "gotchas": ["g1"],
            "files_inventory": [{"path": "src/a.ts", "purpose": "P",
                                 "key_exports": "x",
                                 "related_docs": "conductor/design/architecture/foo"}],
            "recommended": "R", "out_of_scope": ["o1"],
            "graduation_candidates": ["durable finding"],
        })
        res, _ = _out_captured(cmd_append_handoff, d, 1, 1, "explore", content, None)
        self.assertTrue(res["ok"])
        text = (Path(d) / ".conductor" / "handoff" / "P1T1.md").read_text()
        self.assertIn("### Files Inventory", text)
        self.assertIn("src/a.ts", text)
        self.assertIn("### Out-of-Scope Notes", text)
        self.assertIn("o1", text)
        self.assertIn("### Graduation Candidates", text)
        self.assertIn("durable finding", text)

    def test_minimal_payload_renders_placeholders(self):
        d = _make_track()
        # Gate-passing core (summary + findings + files_inventory) with every
        # OPTIONAL field empty — those must still render placeholder sections.
        content = json.dumps({
            "summary": "Substantive exploration summary for the test track.",
            "findings": ["f1"],
            "files_inventory": [{"path": "src/a.ts", "purpose": "P"}],
        })
        res, _ = _out_captured(cmd_append_handoff, d, 1, 1, "explore", content, None)
        self.assertTrue(res["ok"])
        text = (Path(d) / ".conductor" / "handoff" / "P1T1.md").read_text()
        # All sections present even when optional fields are empty (uniform structure).
        self.assertIn("### Files Inventory", text)
        self.assertIn("### Graduation Candidates", text)

    def test_consulted_docs_rendered_with_provenance(self):
        # O1: explorer records which corpus docs it consulted (Layer-0 provenance)
        # so the task-executor + doc-syncer know which documented knowledge the
        # findings extend.
        d = _make_track()
        content = json.dumps({
            "summary": "Substantive exploration summary for the test track.",
            "findings": ["f1"],
            "files_inventory": [{"path": "src/a.ts", "purpose": "P"}],
            "consulted_docs": [
                {"path": "conductor/design/architecture/system-architecture.md",
                 "relevance": "documented the auth boundary this task extends"},
            ],
        })
        res, _ = _out_captured(cmd_append_handoff, d, 1, 1, "explore", content, None)
        self.assertTrue(res["ok"])
        text = (Path(d) / ".conductor" / "handoff" / "P1T1.md").read_text()
        self.assertIn("### Corpus Consulted", text)
        self.assertIn("conductor/design/architecture/system-architecture.md", text)
        self.assertIn("auth boundary this task extends", text)

    def test_consulted_docs_absent_warns_not_silent(self):
        # Omission must be *visible* (the consult step's absence is conspicuous),
        # not silently dropped — this is the structural pressure to run Layer 0.
        d = _make_track()
        content = json.dumps({
            "summary": "Substantive exploration summary for the test track.",
            "findings": ["f1"],
            "files_inventory": [{"path": "src/a.ts", "purpose": "P"}],
        })
        res, _ = _out_captured(cmd_append_handoff, d, 1, 1, "explore", content, None)
        self.assertTrue(res["ok"])
        text = (Path(d) / ".conductor" / "handoff" / "P1T1.md").read_text()
        self.assertIn("### Corpus Consulted", text)
        self.assertIn("None recorded", text)


class TestInitCoreGitignore(TestCase):
    def test_writes_conductor_gitignore(self):
        d = tempfile.mkdtemp()
        plan = {"phases": [{"name": "P1", "tasks": [{"name": "T1"}]}]}
        result = _init_core(d, plan, "test_20260626", "feature", "desc")
        self.assertTrue(result["ok"])
        gi = Path(d) / ".conductor" / ".gitignore"
        self.assertTrue(gi.exists(), ".conductor/.gitignore must be written at init")
        content = gi.read_text()
        self.assertIn("result.json", content)
        self.assertIn(".result.tmp.*", content)


class TestMisplacedDocsLint(TestCase):
    def test_flags_stray_and_passes_meta(self):
        d = tempfile.mkdtemp()
        for name in ["spec.md", "plan.md", "handoff.md", "index.md"]:
            Path(d, name).write_text("x")
        lint = _load_lint()
        self.assertEqual(lint.check_misplaced_docs(Path(d)), [])

        # Stray docs the old design produced — must be flagged.
        Path(d, "exploration.md").write_text("bloated")
        Path(d, "migration-options-analysis.md").write_text("x")
        stray = lint.check_misplaced_docs(Path(d))
        self.assertIn("exploration.md", stray)
        self.assertIn("migration-options-analysis.md", stray)

    def test_ignores_conductor_scratch_and_non_md(self):
        d = tempfile.mkdtemp()
        Path(d, "spec.md").write_text("x")
        Path(d, "track-state.json").write_text("{}")          # non-md, ignored
        Path(d, ".conductor").mkdir()                          # sanctioned scratch
        Path(d, ".conductor", "scratch.md").write_text("x")    # not top-level, ignored
        lint = _load_lint()
        self.assertEqual(lint.check_misplaced_docs(Path(d)), [])


if __name__ == "__main__":
    main()
