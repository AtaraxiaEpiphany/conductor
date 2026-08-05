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
