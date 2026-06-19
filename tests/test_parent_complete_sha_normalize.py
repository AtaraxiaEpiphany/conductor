"""Tests for _do_complete parent auto-complete SHA normalization.

Regression: the parent auto-complete branch wrote the raw `sha` argument
while the subtask itself was normalized via _normalize_sha, so a 40-char
SHA produced an un-normalized parent record (plan.md [sha] marker drop +
broken sibling-dedup).
"""
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save
from scripts.track_state.mutations import _do_complete


def _state():
    return {
        "track_id": "t",
        "type": "feature",
        "status": "in_progress",
        "description": "d",
        "current_phase_index": 1,
        "current_task_index": 1,
        "current_subtask_index": 1,
        "updated_at": "2026-06-19T00:00:00Z",
        "phases": [{
            "name": "P1",
            "status": "in_progress",
            "tasks": [{
                "name": "Parent",
                "status": "in_progress",
                "subtasks": [
                    {"name": "S1", "status": "pending"},
                    {"name": "S2", "status": "pending"},
                ],
            }],
        }],
    }


class ParentCompleteShaNormalizeTests(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        save(self.dir, _state())

    def test_parent_inherits_normalized_sha(self):
        # Completing the last subtask triggers parent auto-complete.
        _do_complete(self.dir, 1, 1, 1, sha="a" * 40)
        _do_complete(self.dir, 1, 1, 2, sha="b" * 40)
        parent = load(self.dir)["phases"][0]["tasks"][0]
        self.assertEqual(parent["status"], "completed")
        # Normalized to the 7-char form — not the raw 40-char SHA.
        self.assertEqual(parent["commit_sha"], "bbbbbbb")
        self.assertEqual(len(parent["commit_sha"]), 7)

    def test_parent_falls_back_to_last_subtask_sha(self):
        # Empty sha on the final subtask → inherit last subtask's SHA (normalized).
        _do_complete(self.dir, 1, 1, 1, sha="a" * 40)
        _do_complete(self.dir, 1, 1, 2, sha="")
        parent = load(self.dir)["phases"][0]["tasks"][0]
        self.assertEqual(parent["status"], "completed")
        self.assertEqual(parent["commit_sha"], "aaaaaaa")

    def test_subtask_sha_normalized(self):
        # Sanity check: the subtask itself is normalized (pre-existing behavior).
        _do_complete(self.dir, 1, 1, 1, sha="c" * 40)
        sub = load(self.dir)["phases"][0]["tasks"][0]["subtasks"][0]
        self.assertEqual(sub["status"], "completed")
        self.assertEqual(sub["commit_sha"], "ccccccc")


if __name__ == "__main__":
    main()
