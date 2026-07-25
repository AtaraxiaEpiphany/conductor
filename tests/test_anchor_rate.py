"""Tests for the Goodhart counter-metric (``compute_frozen_anchor_rate``).

The counter-metric is the antagonistic pair to ``coverage_pct``: it RUNS the
frozen subset and records pass/fail independently of the executor's
self-report. These tests exercise the four signals it surfaces — pass, fail,
skip, drift — plus the structural-only mode (``run=False``) the quality
snapshot uses and the empty/no-anchor degradation.

Pytest is invoked as a subprocess by the runner, so these tests create real
test files and real locators and assert the measured verdicts.
"""
import json
import tempfile
from pathlib import Path
from unittest import TestCase, main

from track_state import anchor


def _spec_md():
    return (
        "# S\n\n## Acceptance Criteria\n\n- AC-1: ok\n\n"
        "## Test Scenarios\n\n| TC | AC | S |\n|---|---|---|\n| TC-1.1 | AC-1 | ok |\n"
    )


def _make_track(td, test_body):
    track = Path(td)
    (track / "spec.md").write_text(_spec_md())
    (track / "tests").mkdir(parents=True)
    (track / "tests" / "test_users.py").write_text(test_body)
    (track / "track-state.json").write_text(json.dumps({"track_id": "demo"}))
    anchor.cmd_freeze(str(track))
    return track


class RateTests(TestCase):
    def test_no_anchor_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            r = anchor.compute_frozen_anchor_rate(td, run=True)
            self.assertIsNone(r["frozen_anchor_pass_rate"])
            self.assertEqual(r["reason"], "no_anchor")
            self.assertEqual(r["runnable"], 0)

    def test_structural_mode_does_not_run_tests(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td, "def test_TC_1_1_ok():\n    assert 1 == 1\n")
            r = anchor.compute_frozen_anchor_rate(td, run=False)
            # run=False: drift + skip measured, pass NOT measured.
            self.assertIsNone(r["frozen_anchor_pass_rate"])
            self.assertEqual(r["frozen_anchor_drift_rate"], 0.0)
            self.assertEqual(r["frozen_anchor_skip_rate"], 0.0)

    def test_passing_frozen_test_measures_100(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td, "def test_TC_1_1_ok():\n    assert 1 == 1\n")
            r = anchor.compute_frozen_anchor_rate(td, run=True)
            self.assertEqual(r["frozen_anchor_pass_rate"], 100.0)
            self.assertEqual(r["passed"], 1)
            self.assertEqual(r["failed"], 0)

    def test_failing_frozen_test_measures_0_the_goodhart_alarm(self):
        # The headline case: the executor's coverage_pct may still be high,
        # but the frozen anchor test actually breaks → pass_rate 0.0. This is
        # the divergence the counter-metric exists to surface.
        with tempfile.TemporaryDirectory() as td:
            _make_track(td, "def test_TC_1_1_ok():\n    assert 1 == 2\n")
            r = anchor.compute_frozen_anchor_rate(td, run=True)
            self.assertEqual(r["frozen_anchor_pass_rate"], 0.0)
            self.assertEqual(r["failed"], 1)

    def test_drift_detected_when_locator_resolves_no_more(self):
        with tempfile.TemporaryDirectory() as td:
            track = _make_track(td, "def test_TC_1_1_ok():\n    assert 1 == 1\n")
            # Delete the grounding test → the frozen locator now points at nothing.
            (track / "tests" / "test_users.py").unlink()
            r = anchor.compute_frozen_anchor_rate(td, run=True)
            self.assertEqual(r["frozen_anchor_drift_rate"], 100.0)
            self.assertEqual(r["drifted"], 1)

    def test_skip_detected_on_frozen_function(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(
                td,
                "import pytest\n\n@pytest.mark.skip(reason='silenced')\n"
                "def test_TC_1_1_ok():\n    assert 1 == 1\n",
            )
            r = anchor.compute_frozen_anchor_rate(td, run=False)
            self.assertEqual(r["frozen_anchor_skip_rate"], 100.0)
            self.assertEqual(r["skipped"], 1)

    def test_never_raises_on_bad_state(self):
        # A track whose feature-list.json is corrupt must degrade, not crash.
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / ".conductor").mkdir(parents=True)
            (Path(td) / ".conductor" / "feature-list.json").write_text("{ broken")
            r = anchor.compute_frozen_anchor_rate(td, run=True)  # must not raise
            self.assertIsNone(r["frozen_anchor_pass_rate"])


if __name__ == "__main__":
    main()
