"""Tests for ``scripts/track_state/anchor.py`` — freeze / thaw / set-contract / anchor-status.

The frozen anchor (``feature-list.json``) is the Goodhart counter-anchor for
F3: seeded from the spec's AC/TC inventory + measured grounding tests, amended
only through governed ``thaw`` (removal) or ``set-contract`` (filling the
exogenous-judgment field). These tests cover the full chain
``compute_ac_integrity`` → ``freeze`` → ``thaw``/``set-contract`` →
``anchor-status`` on a synthetic track, asserting the load-bearing semantics:

- freeze seeds one feature per AC, with measured locators for grounded TCs and
  ``ungrounded`` strength for TCs with no real test.
- freeze refuses an existing list without --force (no laundering a weakened
  spec into the anchor).
- thaw requires --reason, marks (not deletes), and appends an audit entry.
- anchor-status reports grounded/ungrounded/thawed/audit counts.
- the frozen file is valid JSON at ``<track>/.conductor/feature-list.json``.
"""
import json
import tempfile
from pathlib import Path
from unittest import TestCase, main

from track_state import anchor


def _spec_md():
    return (
        "# Spec\n\n## Acceptance Criteria\n\n"
        "- AC-1: GET /users returns 200\n"
        "- AC-2: POST /users validates email\n\n"
        "## Test Scenarios\n\n"
        "| TC | AC | Scenario |\n|---|---|---|\n"
        "| TC-1.1 | AC-1 | returns paginated list |\n"
        "| TC-2.1 | AC-2 | rejects bad email |\n"
    )


def _grounded_test():
    # Grounds TC-1.1 only — TC-2.1 deliberately has no test (ungrounded case).
    return "def test_TC_1_1_returns_paginated_list():\n    assert True\n"


def _make_track(td):
    track = Path(td)
    (track / "spec.md").write_text(_spec_md())
    (track / "tests").mkdir()
    (track / "tests" / "test_users.py").write_text(_grounded_test())
    (track / "track-state.json").write_text(json.dumps({"track_id": "demo_20260724"}))
    return track


class FreezeTests(TestCase):
    def test_freeze_seeds_one_feature_per_ac_with_measured_locators(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            fl = json.loads((Path(td) / ".conductor" / "feature-list.json").read_text())
            by_id = {f["id"]: f for f in fl["features"]}
            self.assertEqual(set(by_id), {"F-AC-1", "F-AC-2"})
            self.assertEqual(
                by_id["F-AC-1"]["test_locators"],
                ["tests/test_users.py::test_TC_1_1_returns_paginated_list"],
            )
            self.assertEqual(by_id["F-AC-1"]["strength"], "strong")
            self.assertEqual(by_id["F-AC-2"]["test_locators"], [])
            self.assertEqual(by_id["F-AC-2"]["ungrounded_tcs"], ["TC-2.1"])
            self.assertEqual(by_id["F-AC-2"]["strength"], "ungrounded")
            # The load-bearing semantic field starts blank (human fills it).
            self.assertEqual(by_id["F-AC-1"]["assertion_contract"], "")
            self.assertEqual(by_id["F-AC-1"]["passes"], "unknown")
            # Provenance + audit.
            self.assertIn("frozen_at", fl)
            self.assertEqual(fl["audit"][0]["action"], "freeze")

    def test_freeze_refuses_existing_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            # A second freeze with no --force must refuse (and leave the file
            # unchanged — no laundering a weakened spec into the anchor).
            first = (Path(td) / ".conductor" / "feature-list.json").read_text()
            anchor.cmd_freeze(td)
            self.assertEqual((Path(td) / ".conductor" / "feature-list.json").read_text(), first)

    def test_freeze_force_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            anchor.cmd_freeze(td, force=True)
            fl = json.loads((Path(td) / ".conductor" / "feature-list.json").read_text())
            # force re-freeze appends a second audit entry.
            self.assertEqual([a["action"] for a in fl["audit"]], ["freeze", "freeze"])
            self.assertTrue(fl["audit"][-1]["force"])


class ThawTests(TestCase):
    def test_thaw_requires_reason(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            anchor.cmd_thaw(td, feature="F-AC-1")  # no reason
            fl = json.loads((Path(td) / ".conductor" / "feature-list.json").read_text())
            self.assertFalse(any(f.get("thawed") for f in fl["features"]))

    def test_thaw_marks_not_deletes_and_audits(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            anchor.cmd_thaw(td, feature="F-AC-1", reason="rewriting contract")
            fl = json.loads((Path(td) / ".conductor" / "feature-list.json").read_text())
            f1 = next(f for f in fl["features"] if f["id"] == "F-AC-1")
            self.assertTrue(f1.get("thawed"))
            self.assertEqual(f1.get("thaw_reason"), "rewriting contract")
            # Still present (marked, not deleted) — audit graph stays complete.
            self.assertEqual(len(fl["features"]), 2)
            self.assertEqual(fl["audit"][-1]["action"], "thaw")
            self.assertEqual(fl["audit"][-1]["feature_ids"], ["F-AC-1"])

    def test_thaw_by_locator(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            loc = "tests/test_users.py::test_TC_1_1_returns_paginated_list"
            anchor.cmd_thaw(td, locator=loc, reason="locator match")
            fl = json.loads((Path(td) / ".conductor" / "feature-list.json").read_text())
            f1 = next(f for f in fl["features"] if f["id"] == "F-AC-1")
            self.assertTrue(f1.get("thawed"))

    def test_thaw_no_match_errors(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            anchor.cmd_thaw(td, feature="F-AC-99", reason="x")
            fl = json.loads((Path(td) / ".conductor" / "feature-list.json").read_text())
            self.assertFalse(any(f.get("thawed") for f in fl["features"]))


class AnchorStatusTests(TestCase):
    def test_status_no_list(self):
        with tempfile.TemporaryDirectory() as td:
            anchor.cmd_anchor_status(td)  # must not crash; frozen=False

    def test_status_reports_counts(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            anchor.cmd_thaw(td, feature="F-AC-2", reason="drop")
            # Read the status dict directly via the reader the command uses.
            data = anchor._anchor_read(td)
            active = [f for f in data["features"] if not f.get("thawed")]
            self.assertEqual(len(data["features"]), 2)
            self.assertEqual(len(active), 1)
            self.assertEqual(sum(1 for f in active if f.get("test_locators")), 1)
            self.assertEqual(len(data["audit"]), 2)


class SetContractTests(TestCase):
    def test_set_contract_requires_text(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            anchor.cmd_set_contract(td, feature="F-AC-1")  # no text
            f = anchor._anchor_read(td)["features"][0]
            self.assertEqual(f["assertion_contract"], "")  # unchanged

    def test_set_contract_fills_field_and_audits(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            anchor.cmd_set_contract(td, feature="F-AC-1", text="status==200")
            data = anchor._anchor_read(td)
            f1 = next(f for f in data["features"] if f["id"] == "F-AC-1")
            self.assertEqual(f1["assertion_contract"], "status==200")
            self.assertEqual(data["audit"][-1]["action"], "set-contract")
            self.assertFalse(data["audit"][-1]["cleared"])

    def test_set_contract_clear_with_empty_text(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            anchor.cmd_set_contract(td, feature="F-AC-1", text="status==200")
            anchor.cmd_set_contract(td, feature="F-AC-1", text="")  # deliberate clear
            data = anchor._anchor_read(td)
            self.assertEqual(
                next(f for f in data["features"] if f["id"] == "F-AC-1")["assertion_contract"],
                "",
            )
            self.assertTrue(data["audit"][-1]["cleared"])

    def test_set_contract_refuses_thawed_feature(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            anchor.cmd_thaw(td, feature="F-AC-1", reason="dropped")
            anchor.cmd_set_contract(td, feature="F-AC-1", text="x")
            f = next(f for f in anchor._anchor_read(td)["features"] if f["id"] == "F-AC-1")
            self.assertNotEqual(f.get("assertion_contract"), "x")  # refused

    def test_set_contract_no_anchor_errors(self):
        with tempfile.TemporaryDirectory() as td:
            anchor.cmd_set_contract(td, feature="F-AC-1", text="x")  # must not crash

    def test_set_contract_by_locator(self):
        with tempfile.TemporaryDirectory() as td:
            _make_track(td)
            anchor.cmd_freeze(td)
            loc = "tests/test_users.py::test_TC_1_1_returns_paginated_list"
            anchor.cmd_set_contract(td, locator=loc, text="status==200")
            f = next(f for f in anchor._anchor_read(td)["features"] if f["id"] == "F-AC-1")
            self.assertEqual(f["assertion_contract"], "status==200")


if __name__ == "__main__":
    main()
