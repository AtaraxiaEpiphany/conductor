"""Tests for the graduation harvest (#2): cmd_harvest_candidates + _extract_candidates.

doc-syncer graduates durable findings (explorer `graduation_candidates` + decision
entries) from the sanctioned `.conductor/handoff/` channel into the wiki corpus.
These cover the extraction half (script-side, testable); the merge half lives in
the doc-syncer agent prompt and is exercised end-to-end, not unit-tested.
"""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.handoff import (
    cmd_append_handoff, cmd_harvest_candidates, _extract_candidates,
)


def _out_captured(fn, *args, **kwargs):
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
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


def _explore_payload(graduation=None, **extra):
    payload = {"summary": "S", "findings": [], "architecture": "A", "gotchas": [],
               "files_inventory": [], "recommended": "", "out_of_scope": []}
    payload["graduation_candidates"] = graduation or []
    payload.update(extra)
    return json.dumps(payload)


class HarvestGraduationTests(TestCase):
    def test_extracts_graduation_candidates(self):
        d = _make_track()
        cmd_append_handoff(d, 1, 1, "explore",
                           _explore_payload(["binary v1.0.115 verified",
                                             "OFFICECLI_SKIP_UPDATE=1 needed"]))
        res = _out_captured(cmd_harvest_candidates, d)
        self.assertEqual(res["count"], 2)
        texts = [g["text"] for g in res["graduation"]]
        self.assertIn("binary v1.0.115 verified", texts)
        self.assertIn("OFFICECLI_SKIP_UPDATE=1 needed", texts)
        self.assertEqual([g["source"] for g in res["graduation"]], ["P1T1", "P1T1"])

    def test_none_section_contributes_nothing(self):
        d = _make_track()
        # Empty graduation_candidates renders `_None_` — must yield zero findings.
        cmd_append_handoff(d, 1, 1, "explore", _explore_payload([]))
        res = _out_captured(cmd_harvest_candidates, d)
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["graduation"], [])

    def test_multi_file_aggregate_and_dedup(self):
        d = _make_track()
        cmd_append_handoff(d, 1, 1, "explore", _explore_payload(["shared finding"]))
        cmd_append_handoff(d, 1, 2, "explore",
                           _explore_payload(["shared finding", "unique to T2"]))
        res = _out_captured(cmd_harvest_candidates, d)
        texts = [g["text"] for g in res["graduation"]]
        self.assertEqual(sorted(texts), ["shared finding", "unique to T2"])
        # Dedup: "shared finding" appears in both files but is harvested once.
        self.assertEqual(len(texts), len(set(texts)))

    def test_multiple_sections_per_file_all_collected(self):
        """A single handoff can carry multiple Graduation Candidates sections
        (e.g. one per subtask) — all must be collected (regression for the
        real P1T2.md shape: main notes + subtask-4 section)."""
        d = _make_track()
        cmd_append_handoff(d, 1, 1, "explore", _explore_payload(["from main"]))
        cmd_append_handoff(d, 1, 1, "explore",
                           _explore_payload(["from subtask"]), subtask=2)
        res = _out_captured(cmd_harvest_candidates, d)
        texts = [g["text"] for g in res["graduation"]]
        self.assertIn("from main", texts)
        self.assertIn("from subtask", texts)


class HarvestDecisionTests(TestCase):
    def test_extracts_decision_entries(self):
        d = _make_track()
        cmd_append_handoff(d, 1, 1, "decision", json.dumps({
            "title": "Use Option A", "options": "A/B", "chosen": "Option A",
            "reasoning": "removes docxtpl", "tradeoffs": "template redesign",
        }))
        res = _out_captured(cmd_harvest_candidates, d)
        self.assertEqual(len(res["decisions"]), 1)
        dec = res["decisions"][0]
        self.assertEqual(dec["title"], "Use Option A")
        self.assertEqual(dec["chosen"], "Option A")
        self.assertEqual(dec["reasoning"], "removes docxtpl")
        self.assertEqual(dec["source"], "P1T1")


class HarvestEdgeCaseTests(TestCase):
    def test_empty_handoff_dir_count_zero(self):
        d = _make_track()
        # handoff dir doesn't exist yet (no appends) — must not crash.
        res = _out_captured(cmd_harvest_candidates, d)
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["graduation"], [])
        self.assertEqual(res["decisions"], [])

    def test_extract_candidates_missing_dir(self):
        # Direct helper call on a non-existent dir — graceful empty result.
        r = _extract_candidates(Path(tempfile.mkdtemp()) / "nope" / "handoff")
        self.assertEqual(r, {"graduation": [], "decisions": []})

    def test_ignores_non_handoff_md_files(self):
        """Only P*T*.md files are handoffs; stray .md in the dir is ignored."""
        d = _make_track()
        handoff_dir = Path(d) / ".conductor" / "handoff"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        (handoff_dir / "README.md").write_text(
            "### Graduation Candidates (durable → corpus; for doc-syncer harvest)\n- should be ignored\n")
        res = _out_captured(cmd_harvest_candidates, d)
        self.assertEqual(res["count"], 0)


if __name__ == "__main__":
    main()
