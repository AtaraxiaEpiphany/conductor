"""Tests for ``track-state task-context`` — the read-only per-task plan↔spec join.

task-executor's Layer 1+2 used to hand-extract a task's AC text + TC rows across
plan.md and spec.md. This CLI owns that join deterministically (plan_parse +
spec_parse composed), so the extraction can't drift from the parsers' grammar.

Pinned: the happy-path join (AC text + TC rows + leading-tag profile),
subtask-inherits-parent-refs, an untagged default task, a trailing tag, dangling
AC refs, a missing spec, a missing task, and the CLI surface.
"""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

from track_state.task_context import compute_task_context  # noqa: E402

_CLI = _scripts / "track-state"


@contextlib.contextmanager
def _track(plan_body, spec_body=None):
    """A tmp track dir with plan.md (+ optional spec.md); yields track_dir."""
    with tempfile.TemporaryDirectory() as d:
        track_dir = Path(d) / "conductor" / "tracks" / "demo"
        track_dir.mkdir(parents=True)
        (track_dir / "plan.md").write_text(plan_body)
        if spec_body is not None:
            (track_dir / "spec.md").write_text(spec_body)
        yield track_dir


_PLAN_WITH_SUBTASKS = (
    "# Implementation Plan: Demo\n\n"
    "## Phase 1: Foundation\n"
    "- [ ] [Config] wire up settings <!-- AC-1, TC-1.1 -->\n"
    "  - [ ] create the config loader\n"
    "  - [ ] add validation\n"
    "- [ ] [Manual] verify Phase 1\n"
)

_SPEC = (
    "## Requirements\n"
    "### Functional Requirements\n"
    "- FR-1: load settings\n"
    "## Acceptance Criteria\n"
    "- AC-1: The system loads settings from .env\n"
    "- AC-2: An unrelated AC no task references\n"
    "## Test Scenarios\n"
    "| ID | AC Ref | Scenario | Expected |\n"
    "| TC-1.1 | AC-1 | env present | settings loaded |\n"
    "| TC-1.2 | AC-1 | env missing | defaults used |\n"
)


class ComputeTaskContextTests(TestCase):
    def test_happy_path_join_and_profile(self):
        # The task carries AC-1 + TC-1.1; spec defines AC-1's text and TWO TCs
        # tracing to it. The join resolves AC-1's text, surfaces BOTH TCs (the
        # declared TC-1.1 first, then the extra TC-1.2), and resolves the
        # leading [Config] tag's profile. Subtasks present do not interfere —
        # the (phase, task) address is the parent, whose refs subtasks inherit.
        with _track(_PLAN_WITH_SUBTASKS, _SPEC) as td:
            ctx = compute_task_context(td, 1, 1)
        self.assertEqual(ctx["name"], "[Config] wire up settings")
        self.assertEqual(ctx["ac_refs"], ["AC-1"])
        self.assertEqual(ctx["tc_refs"], ["TC-1.1"])
        self.assertEqual(ctx["acs"], [{"id": "AC-1",
                                       "text": "The system loads settings from .env"}])
        self.assertEqual([t["id"] for t in ctx["tcs"]], ["TC-1.1", "TC-1.2"])
        self.assertEqual(ctx["tag_profile"]["tag"], "Config")
        self.assertTrue(ctx["tag_profile"]["coverage_exempt"])
        self.assertEqual(ctx["tag_profile"]["workflow"], "absent")
        self.assertEqual(ctx["errors"], [])
        self.assertEqual(ctx["warnings"], [])

    def test_untagged_default_task_has_no_profile(self):
        plan = ("## Phase 1: P\n- [ ] build the thing <!-- AC-1, TC-1.1 -->\n"
                "- [ ] [Manual] verify\n")
        with _track(plan, _SPEC) as td:
            ctx = compute_task_context(td, 1, 1)
        self.assertEqual(ctx["tags"], [])
        self.assertIsNone(ctx["tag_profile"])
        self.assertEqual(ctx["acs"][0]["id"], "AC-1")

    def test_trailing_tag_resolves_profile(self):
        # A trailing [Refactor] name marker (the per-task escape hatch for a
        # task whose leading tag is something else) is picked up by extract_tags
        # and resolves the refactor flag.
        plan = ("## Phase 1: P\n"
                "- [ ] [Config] tighten env loader [Refactor] <!-- AC-1 -->\n"
                "- [ ] [Manual] verify\n")
        with _track(plan, _SPEC) as td:
            ctx = compute_task_context(td, 1, 1)
        self.assertIn("Config", ctx["tags"])
        self.assertTrue(ctx["tag_profile"]["coverage_exempt"])

    def test_dangling_ac_ref_warns(self):
        # AC in the plan annotation but NOT in spec.md → a warning, and the AC
        # is absent from the resolved acs list (only resolvable ACs appear).
        plan = ("## Phase 1: P\n- [ ] thing <!-- AC-1, AC-99 -->\n"
                "- [ ] [Manual] verify\n")
        with _track(plan, _SPEC) as td:
            ctx = compute_task_context(td, 1, 1)
        self.assertEqual([a["id"] for a in ctx["acs"]], ["AC-1"])  # AC-99 unresolved
        self.assertTrue(any("AC-99" in w for w in ctx["warnings"]),
                        ctx["warnings"])

    def test_missing_spec_warns_when_refs_present(self):
        plan = ("## Phase 1: P\n- [ ] thing <!-- AC-1, TC-1.1 -->\n"
                "- [ ] [Manual] verify\n")
        with _track(plan, spec_body=None) as td:
            ctx = compute_task_context(td, 1, 1)
        self.assertEqual(ctx["acs"], [])
        self.assertEqual(ctx["tcs"], [])
        self.assertTrue(any("spec.md absent" in w for w in ctx["warnings"]),
                        ctx["warnings"])

    def test_missing_task_returns_error(self):
        with _track(_PLAN_WITH_SUBTASKS, _SPEC) as td:
            ctx = compute_task_context(td, 1, 9)  # out of range
        self.assertIsNone(ctx["name"])
        self.assertEqual(ctx["acs"], [])
        self.assertTrue(ctx["errors"], "expected a not-found error")


def _seed_handoff_artifacts(track_dir, stem, produced=()):
    """Write a handoff file whose ## Task Artifacts block declares the given
    produced paths — the harvest source for the produced bucket."""
    h = Path(track_dir) / ".conductor" / "handoff"
    h.mkdir(parents=True, exist_ok=True)
    bullets = "".join(f"- {p}\n" for p in produced)
    (h / f"{stem}.md").write_text(
        f"# {stem}\n\n## Task Artifacts | ts\n\n### Produced\n{bullets}")


class ArtifactJoinTests(TestCase):
    """The artifacts delivery join (findings/artifact edge): ``uses`` resolves
    this task's plan edges against the project root; ``produced`` filters the
    handoff ledger to strictly-earlier (phase, task). Fail-open everywhere."""

    _PLAN = ("## Phase 1: P\n"
             "- [ ] baseline <!-- AC-1 --> <!-- produces: reports/b.md -->\n"
             "- [ ] [Manual] verify 1\n"
             "## Phase 2: Q\n"
             "- [ ] early <!-- AC-2 --> <!-- produces: reports/early.md -->\n"
             "- [ ] consumer <!-- AC-3 --> <!-- uses: reports/b.md -->\n"
             "- [ ] later <!-- AC-4 -->\n"
             "- [ ] [Manual] verify 2\n")

    def test_uses_resolved_against_project_root(self):
        # The track lives at {root}/conductor/tracks/demo → the repo-relative
        # uses ref resolves against {root}; existence is reported honestly.
        with _track(self._PLAN, _SPEC) as td:
            ctx = compute_task_context(td, 2, 2)
        self.assertEqual(len(ctx["artifacts"]["uses"]), 1)
        u = ctx["artifacts"]["uses"][0]
        self.assertEqual(u["path"], "reports/b.md")
        self.assertTrue(str(u["resolved"]).endswith("reports/b.md"))
        self.assertIn("/conductor/tracks/demo", str(td))  # root derivation held
        self.assertFalse(u["exists"])  # never created in this fixture

    def test_uses_existence_true_when_file_present(self):
        with _track(self._PLAN, _SPEC) as td:
            root = Path(td).parents[2]
            (root / "reports").mkdir()
            (root / "reports" / "b.md").write_text("baseline\n")
            ctx = compute_task_context(td, 2, 2)
        self.assertTrue(ctx["artifacts"]["uses"][0]["exists"])

    def test_produced_filtered_to_strictly_earlier(self):
        # P1T1 (earlier phase), P2T1 (same-phase serial) reach the P2T2
        # consumer; P2T2 (self), P2T3 (same-phase higher), P3T1 (future) never.
        with _track(self._PLAN, _SPEC) as td:
            _seed_handoff_artifacts(td, "P1T1", ["reports/b.md — baseline"])
            _seed_handoff_artifacts(td, "P2T1", ["reports/early.md — early"])
            _seed_handoff_artifacts(td, "P2T2", ["reports/self.md"])
            _seed_handoff_artifacts(td, "P2T3", ["reports/higher.md"])
            _seed_handoff_artifacts(td, "P3T1", ["reports/future.md"])
            ctx = compute_task_context(td, 2, 2)
        got = [(a["path"], a["role"], a["source"])
               for a in ctx["artifacts"]["produced"]]
        self.assertEqual(got, [
            ("reports/b.md", "baseline", "P1T1"),
            ("reports/early.md", "early", "P2T1"),
        ])

    def test_malformed_stem_skipped(self):
        with _track(self._PLAN, _SPEC) as td:
            _seed_handoff_artifacts(td, "P1T", ["reports/odd.md"])
            ctx = compute_task_context(td, 2, 2)
        self.assertEqual(ctx["artifacts"]["produced"], [])

    def test_fail_open_no_plan_no_handoffs(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d) / "conductor" / "tracks" / "demo"
            td.mkdir(parents=True)
            ctx = compute_task_context(td, 2, 2)
        self.assertEqual(ctx["artifacts"]["uses"], [])
        self.assertEqual(ctx["artifacts"]["produced"], [])

    def test_bare_track_dir_leaves_paths_repo_relative(self):
        # No conductor/tracks layout and no $CLAUDE_PROJECT_DIR → resolved and
        # exists are None; the repo-relative path is still delivered.
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            (td / "plan.md").write_text(self._PLAN)
            old = os.environ.get("CLAUDE_PROJECT_DIR")
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            try:
                ctx = compute_task_context(td, 2, 2)
            finally:
                if old is not None:
                    os.environ["CLAUDE_PROJECT_DIR"] = old
        u = ctx["artifacts"]["uses"][0]
        self.assertEqual(u["path"], "reports/b.md")
        self.assertIsNone(u["resolved"])
        self.assertIsNone(u["exists"])


class TaskContextCLITests(TestCase):
    def _run(self, track_dir, phase, task):
        proc = subprocess.run(
            [sys.executable, str(_CLI), "task-context", str(track_dir),
             "--phase", str(phase), "--task", str(task)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, f"CLI failed: {proc.stderr}")
        return json.loads(proc.stdout)

    def test_cli_emits_joined_json(self):
        with _track(_PLAN_WITH_SUBTASKS, _SPEC) as td:
            out = self._run(td, 1, 1)
        self.assertEqual(out["acs"], [{"id": "AC-1",
                                       "text": "The system loads settings from .env"}])
        self.assertEqual([t["id"] for t in out["tcs"]], ["TC-1.1", "TC-1.2"])
        self.assertEqual(out["tag_profile"]["tag"], "Config")

    def test_cli_accepts_positional_indices(self):
        # Mirror wave-finalize: both positional and --phase/--task forms resolve.
        with _track(_PLAN_WITH_SUBTASKS, _SPEC) as td:
            proc = subprocess.run(
                [sys.executable, str(_CLI), "task-context", str(td), "1", "1"],
                capture_output=True, text=True,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["ac_refs"], ["AC-1"])


if __name__ == "__main__":
    main()
