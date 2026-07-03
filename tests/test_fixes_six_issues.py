"""Regression tests for the six-issue fix batch.

Covers:
- Issue 4: explore completeness gate rejects a sparse handoff (exit non-zero).
- Issue 5: _do_fail_parent marks a parent failed ([!], not [x]) with retry_count
  pinned to MAX_RETRIES so recover surfaces it for a retry/skip/block decision.
- Issue 6: plan_parse errors on a task/subtask line missing its [ ] checkbox
  (previously silently dropped).
"""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import core
from scripts.track_state.constants import MAX_RETRIES
from scripts.track_state.handoff import cmd_append_handoff
from scripts.track_state.mutations import _do_fail_parent
from scripts.track_state.plan_parse import parse_plan


def _capture(fn, *args, **kwargs):
    """Run fn capturing stdout/stderr; return (parsed_stdout_or_None, stderr, exit_code)."""
    old_o, old_e = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    code = 0
    out_val = None
    try:
        fn(*args, **kwargs)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    raw_o, raw_e = sys.stdout.getvalue(), sys.stderr.getvalue()
    sys.stdout, sys.stderr = old_o, old_e
    try:
        out_val = json.loads(raw_o) if raw_o.strip() else None
    except json.JSONDecodeError:
        out_val = None
    return out_val, raw_e, code


def _track_with_parent():
    d = tempfile.mkdtemp()
    state = {
        "track_id": "t_1", "type": "feature", "status": "in_progress",
        "current_phase_index": 1, "current_task_index": 1,
        "updated_at": "2026-06-24T00:00:00+00:00",
        "phases": [{"name": "P1", "status": "in_progress", "tasks": [{
            "name": "Parent", "status": "in_progress",
            "subtasks": [
                {"name": "sub A", "status": "completed", "commit_sha": "abc1234"},
                {"name": "sub B", "status": "failed", "retry_count": MAX_RETRIES},
            ],
        }]}],
    }
    core.save(d, state)
    return d


# ── Issue 4: explore completeness gate ─────────────────────────────────

class ExploreCompletenessGateTests(TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        core.save(self.dir, {
            "track_id": "t", "type": "feature", "status": "in_progress",
            "current_phase_index": 1, "current_task_index": 1,
            "phases": [{"name": "P1", "status": "in_progress",
                        "tasks": [{"name": "Explore X", "status": "pending"}]}],
        })

    def test_sparse_payload_rejected_nonzero(self):
        sparse = json.dumps({"summary": "looks fine", "findings": [],
                             "files_inventory": []})
        out, err, code = _capture(cmd_append_handoff, self.dir, 1, 1, "explore", sparse)
        self.assertNotEqual(code, 0)
        self.assertIsNotNone(out)
        self.assertEqual(out["error"], "sparse_explore_handoff")
        # Names every missing minimum so the retry explorer knows what to add.
        joined = " ".join(out["missing"])
        self.assertIn("summary", joined)
        self.assertIn("findings", joined)
        self.assertIn("files_inventory", joined)

    def test_rich_payload_accepted(self):
        rich = json.dumps({
            "summary": "The auth module wires JWT middleware in src/auth/.",
            "findings": ["JWT middleware at src/auth/jwt.ts"],
            "files_inventory": [{"path": "src/auth/jwt.ts", "purpose": "sign/verify"}],
        })
        out, _err, code = _capture(cmd_append_handoff, self.dir, 1, 1, "explore", rich)
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        self.assertTrue((Path(self.dir) / ".conductor" / "handoff" / "P1T1.md").exists())


# ── Issue 5: _do_fail_parent ───────────────────────────────────────────

class FailParentTests(TestCase):
    def test_parent_marked_failed_not_completed(self):
        d = _track_with_parent()
        _do_fail_parent(d, 1, 1, "", None)
        parent = core.load(d)["phases"][0]["tasks"][0]
        self.assertEqual(parent["status"], "failed")  # renders [!], not [x]
        # Pinned to MAX so recover routes to skip-analyst / user decision,
        # not re-dispatch.
        self.assertEqual(parent["retry_count"], MAX_RETRIES)
        self.assertIn("sub B", parent["last_failure_summary"])
        # Traceability preserved from the last completed subtask.
        self.assertEqual(parent["commit_sha"], "abc1234")
        # Subtasks keep their individual statuses.
        subs = {s["name"]: s["status"] for s in parent["subtasks"]}
        self.assertEqual(subs, {"sub A": "completed", "sub B": "failed"})

    def test_current_indices_point_at_failed_parent(self):
        d = _track_with_parent()
        _do_fail_parent(d, 1, 1, "", None)
        s = core.load(d)
        self.assertEqual(s["current_phase_index"], 1)
        self.assertEqual(s["current_task_index"], 1)
        self.assertIsNone(s.get("current_subtask_index"))


# ── Issue 6: plan_parse missing-checkbox error ─────────────────────────

class PlanParseMissingCheckboxTests(TestCase):
    def _parse(self, plan_text):
        f = tempfile.NamedTemporaryFile("w", suffix="plan.md", delete=False)
        f.write(plan_text)
        f.close()
        try:
            return parse_plan(f.name)
        finally:
            Path(f.name).unlink()

    def test_missing_checkbox_subtask_is_error(self):
        r = self._parse(
            "## Phase 1: Build\n- [ ] Task: a\n  - subtask: no bracket\n"
            "- [ ] [Manual] Task: verify Phase 1\n")
        self.assertTrue(any("missing its '[ ]' checkbox" in e and "line 3" in e
                            for e in r["errors"]), r["errors"])

    def test_tag_without_checkbox_is_error(self):
        r = self._parse(
            "## Phase 1: Build\n- [Explore] Task: tagged but no checkbox\n"
            "- [ ] [Manual] Task: verify Phase 1\n")
        self.assertTrue(any("missing its '[ ]' checkbox" in e for e in r["errors"]),
                        r["errors"])

    def test_well_formed_plan_has_no_missing_checkbox_errors(self):
        r = self._parse(
            "## Phase 1: Build\n- [ ] Task: a\n  - [ ] Subtask: b\n"
            "- [ ] [Manual] Task: verify Phase 1\n")
        missing = [e for e in r["errors"] if "missing its '[ ]' checkbox" in e]
        self.assertEqual(missing, [])


# ── Keyword-independent safety net: annotated bullet missing its checkbox ──
# An author who drops the Task:/Subtask: keyword but keeps the AC/TC/deps
# annotation (``- implement login <!-- AC-1 -->``) would have the line silently
# dropped. The HTML comment is the "this was meant to be a task" signal that
# catches it without needing the keyword.

class PlanParseAnnotatedMissingCheckboxTests(TestCase):
    def _parse(self, plan_text):
        f = tempfile.NamedTemporaryFile("w", suffix="plan.md", delete=False)
        f.write(plan_text)
        f.close()
        try:
            return parse_plan(f.name)
        finally:
            Path(f.name).unlink()

    def test_annotated_bullet_without_keyword_or_checkbox_is_error(self):
        r = self._parse(
            "## Phase 1: Build\n- implement login <!-- AC-1 -->\n"
            "- [ ] [Manual] Task: verify Phase 1\n")
        self.assertTrue(
            any("annotation but is missing its '[ ]' checkbox" in e and "line 2" in e
                for e in r["errors"]), r["errors"])

    def test_annotated_indented_bullet_treated_as_subtask(self):
        r = self._parse(
            "## Phase 1: Build\n- [ ] Task: a\n"
            "  - nested step <!-- AC-1, TC-1.1 -->\n"
            "- [ ] [Manual] Task: verify Phase 1\n")
        # Indented → reported as a subtask.
        self.assertTrue(
            any("subtask line carries an annotation" in e for e in r["errors"]),
            r["errors"])

    def test_tagged_annotated_bullet_without_keyword_is_error(self):
        # [Explore] tag + annotation but no checkbox and no keyword.
        r = self._parse(
            "## Phase 1: Build\n- [Explore] map the module <!-- AC-1 -->\n"
            "- [ ] [Manual] Task: verify Phase 1\n")
        self.assertTrue(
            any("annotation but is missing its '[ ]' checkbox" in e for e in r["errors"]),
            r["errors"])

    def test_well_formed_annotated_task_not_flagged(self):
        # ``- [ ] implement login <!-- AC-1 -->`` is well-formed (keyword optional).
        r = self._parse(
            "## Phase 1: Build\n- [ ] implement login <!-- AC-1 -->\n"
            "- [ ] [Manual] Task: verify Phase 1\n")
        annotated = [e for e in r["errors"]
                     if "annotation but is missing" in e]
        self.assertEqual(annotated, [], r["errors"])

    def test_plain_prose_bullet_without_annotation_not_flagged(self):
        # No keyword, no checkbox, no annotation → still ignored prose (the
        # irreducible ambiguity; the net only catches the annotated case).
        r = self._parse(
            "## Phase 1: Build\n- [ ] Task: a\n- a plain prose note\n"
            "- [ ] [Manual] Task: verify Phase 1\n")
        annotated = [e for e in r["errors"]
                     if "annotation but is missing" in e]
        self.assertEqual(annotated, [], r["errors"])


if __name__ == "__main__":
    main()
