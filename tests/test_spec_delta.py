"""Tests for ``track-state spec-delta`` — the spec-edit blast-radius engine.

Headline: ``test_changed_ac_surfaces_at_risk_sha`` — a completed task claiming
AC-3 with a ``commit_sha``, where AC-3's body was edited, must surface in
``at_risk_tasks`` with its SHA. An unchanged-AC task must NOT. This is the
guarantee the ``re-spec`` skill relies on to surface (never auto-reset) the
SHAs a spec edit puts at risk.
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import cli
from scripts.track_state.spec_delta import compute_spec_delta, cmd_spec_delta

from tests.test_step import _make_state, _git_track_dir


_SPEC_BEFORE = """\
# Spec

## Functional Requirements
- FR-1: do the thing

## Non-Functional Requirements
- NFR-1: fast enough

## Acceptance Criteria
- AC-1: thing works
- AC-2: edge handled
- AC-3: concurrent up to 1k

## Test Scenarios
| TC-1.1 | AC-1 | happy path |
| TC-3.1 | AC-3 | load test |
"""


def _task(coord, name, ac_refs, status="completed", sha="abc1234"):
    return {"coord": coord, "name": name, "ac_refs": ac_refs,
            "status": status, "commit_sha": sha if status in ("completed", "skipped") else None}


class ComputeSpecDelta(TestCase):
    def test_changed_ac_surfaces_at_risk_sha(self):
        after = _SPEC_BEFORE.replace("concurrent up to 1k", "concurrent up to 100k")
        tasks = [_task("P1.T1", "impl concurrency", ["AC-3"], status="completed")]
        d = compute_spec_delta(_SPEC_BEFORE, after, tasks)
        self.assertEqual([c["id"] for c in d["changed_acs"]], ["AC-3"])
        self.assertEqual(len(d["at_risk_tasks"]), 1)
        risk = d["at_risk_tasks"][0]
        self.assertEqual(risk["coord"], "P1.T1")
        self.assertEqual(risk["commit_sha"], "abc1234")
        self.assertEqual(risk["acs"], ["AC-3"])

    def test_unchanged_ac_not_at_risk(self):
        tasks = [_task("P1.T1", "impl", ["AC-1"], status="completed")]
        d = compute_spec_delta(_SPEC_BEFORE, _SPEC_BEFORE, tasks)
        self.assertEqual(d["changed_acs"], [])
        self.assertEqual(d["at_risk_tasks"], [])

    def test_changed_ac_no_claimer_empty_at_risk(self):
        after = _SPEC_BEFORE.replace("edge handled", "edge handled robustly")
        d = compute_spec_delta(_SPEC_BEFORE, after, plan_tasks=[])
        self.assertEqual([c["id"] for c in d["changed_acs"]], ["AC-2"])
        self.assertEqual(d["at_risk_tasks"], [])

    def test_non_terminal_claimer_not_at_risk(self):
        after = _SPEC_BEFORE.replace("thing works", "thing works correctly")
        # pending task claiming AC-1 — no SHA to lose, so not at risk.
        tasks = [_task("P1.T1", "impl", ["AC-1"], status="pending")]
        d = compute_spec_delta(_SPEC_BEFORE, after, tasks)
        self.assertEqual([c["id"] for c in d["changed_acs"]], ["AC-1"])
        self.assertEqual(d["at_risk_tasks"], [])

    def test_added_removed_ac_structural(self):
        # Remove AC-2, add AC-4 (still under the AC section, before Test Scenarios).
        after = _SPEC_BEFORE.replace("- AC-2: edge handled\n", "")
        after = after.replace("## Test Scenarios", "- AC-4: new criterion\n\n## Test Scenarios")
        d = compute_spec_delta(_SPEC_BEFORE, after, plan_tasks=[])
        self.assertEqual(sorted(d["added_acs"]), ["AC-4"])
        self.assertEqual(sorted(d["removed_acs"]), ["AC-2"])
        self.assertEqual(d["changed_acs"], [])
        # FR/NFR set deltas too.
        self.assertEqual(d["added_frs"], [])
        self.assertEqual(d["removed_frs"], [])

    def test_terminal_without_sha_not_at_risk(self):
        after = _SPEC_BEFORE.replace("concurrent up to 1k", "concurrent up to 100k")
        # skipped but no commit_sha → nothing concrete to invalidate.
        tasks = [{"coord": "P1.T1", "name": "x", "ac_refs": ["AC-3"],
                  "status": "skipped", "commit_sha": None}]
        d = compute_spec_delta(_SPEC_BEFORE, after, tasks)
        self.assertEqual(d["at_risk_tasks"], [])


class CmdSpecDelta(TestCase):
    def _track(self, before_spec, after_spec, plan, state):
        d = _git_track_dir(state, plan_content=plan)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        env = {
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        }
        # Commit the BEFORE spec, then write+commit the AFTER spec so HEAD~1 is BEFORE.
        Path(d, "spec.md").write_text(before_spec)
        subprocess.run(["git", "-C", d, "add", "spec.md"], check=True, capture_output=True)
        subprocess.run(["git", "-C", d, "commit", "-q", "-m", "spec before"],
                       check=True, capture_output=True, env=env)
        Path(d, "spec.md").write_text(after_spec)
        subprocess.run(["git", "-C", d, "add", "spec.md"], check=True, capture_output=True)
        subprocess.run(["git", "-C", d, "commit", "-q", "-m", "spec after"],
                       check=True, capture_output=True, env=env)
        return d

    def _run(self, track_dir, **kwargs):
        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            cmd_spec_delta(track_dir, **kwargs)
            return json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old

    def test_cli_default_before_is_head_prior(self):
        after = _SPEC_BEFORE.replace("concurrent up to 1k", "concurrent up to 100k")
        plan = ("# Plan\n\n## Phase 1: Build\n"
                "- [x] impl concurrency <!-- AC-3 -->\n")
        state = _make_state(phases=[{"name": "Phase 1", "status": "in_progress",
                                     "tasks": [{"name": "impl concurrency", "status": "completed",
                                                "commit_sha": "abc1234"}]}])
        d = self._track(_SPEC_BEFORE, after, plan, state)
        out = self._run(d)
        self.assertTrue(out["ok"])
        self.assertEqual(out["before"], "HEAD~1:spec.md")
        self.assertEqual([c["id"] for c in out["changed_acs"]], ["AC-3"])
        self.assertEqual(len(out["at_risk_tasks"]), 1)
        self.assertEqual(out["at_risk_tasks"][0]["commit_sha"], "abc1234")

    def test_cli_before_flag_overrides_git(self):
        # Same track, but pass an explicit --before pointing at a 3rd version.
        third = _SPEC_BEFORE.replace("concurrent up to 1k", "concurrent up to 5k")
        after = _SPEC_BEFORE.replace("concurrent up to 1k", "concurrent up to 100k")
        plan = "# Plan\n\n## Phase 1: Build\n- [ ] t <!-- AC-3 -->\n"
        state = _make_state()
        d = self._track(_SPEC_BEFORE, after, plan, state)
        tmp = Path(d, "baseline.md")
        tmp.write_text(third)
        out = self._run(d, before=str(tmp))
        self.assertEqual(out["before"], str(tmp))
        # third→after still differs on AC-3
        self.assertEqual([c["id"] for c in out["changed_acs"]], ["AC-3"])

    def test_cli_missing_spec_exits_zero_with_error(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        out = self._run(d)
        self.assertFalse(out["ok"])
        self.assertTrue(out["errors"])


if __name__ == "__main__":
    main()
