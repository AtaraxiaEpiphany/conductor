"""Tests for ``cmd_review_attest`` (Track B4) — the review-grounding attestation
write path.

The write mechanism for the review-grounded verification channel: after
``conductor:spec-reviewer`` (``REVIEW_MODE: attest``) attests a deliverable's
artifact against its AC, the orchestrator writes each verdict here. The
attestation lands in ``evidence.review_attestations[AC-N]``, which
``spec_integrity._attested_acs`` reads as the Rate-3 verification signal for
review-grounded ACs (advisory — not gated, mirroring test-grounded tc_coverage).

This closes the B2↔B4 loop end-to-end: an attestation written here shows up in
``compute_ac_integrity``'s ``ac_verification_rate`` + the per-AC ``ac_evidence``
``status: attested``.
"""
import io
import json
import shutil
import sys
import tempfile
import importlib.util
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import load, save
from scripts.track_state.dispatch import cmd_review_attest
from scripts.track_state.spec_integrity import (
    _attested_acs, compute_ac_integrity)
from scripts.track_state import cli

from tests.test_step import _make_state

# pre-command-check.py is a standalone script (hyphenated, not an importable
# module) — load it via importlib, mirroring tests/test_registry_doc.py.
_scripts = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "pre_command_check", _scripts / "pre-command-check.py")
_pcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pcc)


def _run(fn, *args, **kwargs):
    """Capture a command's stdout JSON (the one ``out(...)`` it emits)."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


def _track(tasks=None):
    """A temp track dir with a two-task phase (Task A=T1, Task B=T2)."""
    d = tempfile.mkdtemp()
    save(d, _make_state())
    return d


class ReviewAttestWriteTests(TestCase):
    def setUp(self):
        self.d = _track()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_attest_writes_review_attestations_to_evidence(self):
        o = _run(cmd_review_attest, self.d, 1, 1, "AC-1", "pass",
                 anchor="docs/api.md", attested_by="spec-reviewer")
        self.assertTrue(o["ok"])
        tgt = load(self.d)["phases"][0]["tasks"][0]
        self.assertEqual(tgt["evidence"]["review_attestations"]["AC-1"], {
            "anchor": "docs/api.md", "attested_by": "spec-reviewer",
            "verdict": "pass"})

    def test_invalid_ac_id_rejected(self):
        o = _run(cmd_review_attest, self.d, 1, 1, "AC", "pass")
        self.assertFalse(o.get("ok"))
        self.assertIn("AC id", o["error"])
        # The bad AC never reaches evidence.
        self.assertNotIn("evidence", load(self.d)["phases"][0]["tasks"][0])

    def test_invalid_verdict_rejected(self):
        o = _run(cmd_review_attest, self.d, 1, 1, "AC-1", "maybe")
        self.assertFalse(o.get("ok"))
        self.assertIn("verdict", o["error"])

    def test_invalid_index_rejected(self):
        o = _run(cmd_review_attest, self.d, 99, 1, "AC-1", "pass")
        self.assertFalse(o.get("ok"))
        self.assertIn("no task", o["error"])

    def test_re_attest_overwrites_idempotent(self):
        # The latest review wins — re-attesting AC-1 overwrites the prior verdict.
        _run(cmd_review_attest, self.d, 1, 1, "AC-1", "pass")
        _run(cmd_review_attest, self.d, 1, 1, "AC-1", "fail", anchor="docs/x.md")
        att = load(self.d)["phases"][0]["tasks"][0]["evidence"]["review_attestations"]
        self.assertEqual(att["AC-1"]["verdict"], "fail")
        self.assertEqual(att["AC-1"]["anchor"], "docs/x.md")


class AttestedAcsReadTests(TestCase):
    """``_attested_acs`` (the B2 reader) consumes what ``review-attest`` writes."""

    def test_pass_verdict_attests_fail_does_not(self):
        d = _track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # Task A (completed) attests AC-1 pass + AC-2 fail; Task B pending is ignored.
        _run(cmd_review_attest, d, 1, 1, "AC-1", "pass")
        _run(cmd_review_attest, d, 1, 1, "AC-2", "fail")
        # mark Task A completed so _attested_acs considers it
        state = load(d)
        state["phases"][0]["tasks"][0]["status"] = "completed"
        save(d, state)
        self.assertEqual(_attested_acs(load(d)), {"AC-1"})

    def test_pending_task_attestations_ignored(self):
        d = _track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        _run(cmd_review_attest, d, 1, 1, "AC-1", "pass")  # Task A stays pending
        self.assertEqual(_attested_acs(load(d)), set())


class IntegrityEndToEndTests(TestCase):
    """B2↔B4 loop: an attestation written here reaches compute_ac_integrity."""

    _SPEC_REVIEW = (
        "# Specification: Design Doc\n"
        "## Acceptance Criteria\n"
        "- AC-1: API design documented\n"
        "- AC-2: runbook delivered\n"
        "## Artifact Anchors\n"
        "| AC Ref | Artifact | Location |\n| ------ | -------- | -------- |\n"
        "| AC-1 | API design doc | docs/api.md |\n"
        "| AC-2 | runbook | docs/run.md |\n")
    _PLAN_REVIEW = (
        "# Implementation Plan\n## Phase 1: Author\n"
        "- [ ] Task A <!-- AC-1 -->\n- [ ] Task B <!-- AC-2 -->\n")

    def _review_track(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        Path(d, "spec.md").write_text(self._SPEC_REVIEW)
        Path(d, "plan.md").write_text(self._PLAN_REVIEW)
        save(d, _make_state(workflow_shape="deliverable"))
        return d

    def test_attestation_raises_verification_rate_and_evidence_status(self):
        d = self._review_track()
        # Before: no attestations → verification 0%, AC-1 unattested.
        r0 = compute_ac_integrity(d)
        self.assertEqual(r0["ac_verification_rate"], 0.0)
        by_ac = {e["ac"]: e for e in r0["ac_evidence"]}
        self.assertEqual(by_ac["AC-1"]["status"], "unattested")
        # Mark Task A completed + attest AC-1.
        state = load(d)
        state["phases"][0]["tasks"][0]["status"] = "completed"
        save(d, state)
        _run(cmd_review_attest, d, 1, 1, "AC-1", "pass", anchor="API design doc")
        r1 = compute_ac_integrity(d)
        self.assertEqual(r1["ac_verification_rate"], 50.0)  # AC-1 attested, AC-2 not
        by_ac = {e["ac"]: e for e in r1["ac_evidence"]}
        self.assertEqual(by_ac["AC-1"]["status"], "attested")
        self.assertEqual(by_ac["AC-2"]["status"], "unattested")


class CliWiringTests(TestCase):
    def _invoke(self, argv):
        old_argv, old_out = sys.argv, sys.stdout
        sys.argv, sys.stdout = ["track-state"] + argv, io.StringIO()
        try:
            cli.main()
            return json.loads(sys.stdout.getvalue())
        finally:
            sys.argv, sys.stdout = old_argv, old_out

    def test_review_attest_resolves_via_cli(self):
        d = _track()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        o = self._invoke(["review-attest", d, "--phase", "1", "--task", "1",
                          "--ac", "AC-1", "--verdict", "pass",
                          "--anchor", "docs/a.md", "--attested-by", "spec-reviewer"])
        self.assertTrue(o["ok"])
        tgt = load(d)["phases"][0]["tasks"][0]
        self.assertEqual(tgt["evidence"]["review_attestations"]["AC-1"]["verdict"], "pass")

    def test_review_attest_in_help_group_and_sanctioned(self):
        grouped = {c for _name, cmds in cli._COMMAND_GROUPS for c in cmds}
        self.assertIn("review-attest", cli.COMMAND_HELP)
        self.assertIn("review-attest", grouped)
        # The drift lint requires the sanctioned set to agree.
        self.assertIn("review-attest", _pcc._SANCTIONED_TS_SUBCOMMANDS)


if __name__ == "__main__":
    main()
