"""Tests for the task-artifact checkpoint advisory (findings/artifact edge).

``artifact_advisory`` is the report-only join the phase checker sees as
``ARTIFACT_ADVISORY=``: orphan producers (no uses edge anywhere — the
dead-code case issue #1 of the extensibility review named) and unattested
consumers (completed with no ``artifacts_used`` attestation). Pinned here:
the helper's cases, its determinism (pure — same inputs, same string),
fail-open (any internal error → None, never a crash), the envelope wiring
(present only when there is something to say), and the phase-checker body
naming the envelope line (the wiring tripwire class of
test_track_findings_wiring).
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.track_state.dispatch import _build_phase_checker, artifact_advisory

ROOT = Path(__file__).resolve().parent.parent
PHASE_CHECKER = (ROOT / "agents" / "phase-checker.md").read_text(encoding="utf-8")


def _plan_tasks(produces=None, uses=None):
    """Two-phase plan_tasks fixture: P1.T1 produces, P2.T1 uses (either may
    be None to exercise the orphan/dangling arms)."""
    produces = produces if produces is not None else []
    uses = uses if uses is not None else []
    p1 = " ".join(f"<!-- produces: {p} -->" for p in produces) if produces else ""
    u2 = " ".join(f"<!-- uses: {u} -->" for u in uses) if uses else ""
    return [{
        "name": "Phase 1", "tasks": [
            {"name": f"producer {p1}".strip(),
             "produces_refs": list(produces), "uses_refs": []},
            {"name": "[Manual] verify 1", "produces_refs": [], "uses_refs": []},
        ]}, {
        "name": "Phase 2", "tasks": [
            {"name": f"consumer {u2}".strip(),
             "produces_refs": [], "uses_refs": list(uses)},
            {"name": "[Manual] verify 2", "produces_refs": [], "uses_refs": []},
        ]},
    ]


def _state(consumer_status="pending", producer_status="completed"):
    return {"phases": [
        {"tasks": [{"status": producer_status}, {"status": "completed"}]},
        {"tasks": [{"status": consumer_status}, {"status": "pending"}]},
    ]}


def _harvest(produced=(), used=()):
    return {"artifacts_produced": [{"path": p, "role": "", "source": "P1T1"}
                                   for p in produced],
            "artifacts_used": [{"path": u, "source": "P2T1"} for u in used]}


class ArtifactAdvisoryTests(unittest.TestCase):
    def test_clean_couplet_no_advisory(self):
        # Produced (plan + ledger) with a uses edge and an attestation → None.
        self.assertIsNone(artifact_advisory(
            _state(consumer_status="completed"),
            _plan_tasks(produces=["reports/b.md"], uses=["reports/b.md"]),
            _harvest(produced=["reports/b.md"], used=["reports/b.md"])))

    def test_orphan_no_uses_edge(self):
        s = artifact_advisory(
            _state(), _plan_tasks(produces=["reports/orphan.md"]), _harvest())
        self.assertIsNotNone(s)
        self.assertIn("orphan: reports/orphan.md", s)
        self.assertIn("no uses edge", s)
        self.assertIn("P1.T1", s)  # names the producer

    def test_orphan_from_ledger_only(self):
        # A runtime --artifacts declaration with no plan edge and no uses edge
        # is still dead code — the ledger half alone triggers the advisory.
        s = artifact_advisory(_state(), _plan_tasks(), _harvest(
            produced=["reports/ledger-only.md"]))
        self.assertIsNotNone(s)
        self.assertIn("orphan: reports/ledger-only.md", s)
        self.assertIn("ledger P1T1", s)

    def test_unattested_completed_consumer(self):
        # Consumer completed but never attested reading → the should-read vs
        # did-read diff.
        s = artifact_advisory(
            _state(consumer_status="completed"),
            _plan_tasks(produces=["reports/b.md"], uses=["reports/b.md"]),
            _harvest(produced=["reports/b.md"], used=[]))
        self.assertIsNotNone(s)
        self.assertIn("unattested: P2.T1", s)
        self.assertIn("reports/b.md", s)

    def test_pending_consumer_not_flagged(self):
        # In-flight consumer owes no attestation yet.
        self.assertIsNone(artifact_advisory(
            _state(consumer_status="in_progress"),
            _plan_tasks(produces=["reports/b.md"], uses=["reports/b.md"]),
            _harvest(produced=["reports/b.md"], used=[])))

    def test_attestation_satisfies_even_without_plan_edge(self):
        # A plan produces + a runtime attestation, no plan uses edge: the
        # orphan fires (no consumer edge) but no unattested (nobody declared
        # uses). Exactly one note.
        s = artifact_advisory(
            _state(), _plan_tasks(produces=["reports/b.md"]),
            _harvest(produced=["reports/b.md"], used=["reports/b.md"]))
        self.assertEqual(s.count("orphan:"), 1)
        self.assertNotIn("unattested", s)

    def test_deterministic_same_inputs_same_string(self):
        # Pure: byte-identical across calls (dict/set iteration order must not
        # leak through — sorted iterations).
        args = (_state(consumer_status="completed"),
                _plan_tasks(produces=["a.md", "b.md"], uses=["a.md"]),
                _harvest(produced=["a.md"]))
        self.assertEqual(artifact_advisory(*args), artifact_advisory(*args))

    def test_exception_returns_none(self):
        # Fail-open: a malformed input shape (tasks as strings) must yield
        # None, never a raise — the advisory can never break the checker.
        self.assertIsNone(artifact_advisory(None, ["not", "tasks"], None))
        self.assertIsNone(
            artifact_advisory({"phases": "bogus"}, [], {}))


class AdvisoryEnvelopeWiringTests(unittest.TestCase):
    """_build_phase_checker emits ARTIFACT_ADVISORY only when the join has
    something to surface; a track with no plan/handoffs stays silent."""

    def _track(self, plan_body=None, handoff_blocks=()):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        if plan_body is not None:
            Path(d, "plan.md").write_text(plan_body)
        if handoff_blocks:
            h = Path(d, ".conductor", "handoff")
            h.mkdir(parents=True, exist_ok=True)
            for stem, block in handoff_blocks:
                Path(h, f"{stem}.md").write_text(f"# {stem}\n\n{block}")
        return d

    _ORPHAN_PLAN = (
        "## Phase 1: P\n"
        "- [ ] baseline <!-- AC-1 --> <!-- produces: reports/orphan.md -->\n"
        "- [ ] [Manual] verify 1\n")
    _ORPHAN_HANDOFF = ("## Task Artifacts | ts\n\n### Produced\n"
                       "- reports/orphan.md\n")

    def test_advisory_present_for_orphan(self):
        d = self._track(self._ORPHAN_PLAN, [("P1T1", self._ORPHAN_HANDOFF)])
        state = {"phases": [{"tasks": [{"status": "completed"},
                                       {"status": "completed"}]}]}
        body = _build_phase_checker(d, state, 1, {})
        self.assertIn("ARTIFACT_ADVISORY=", body)
        self.assertIn("orphan: reports/orphan.md", body)
        # The advisory rides AFTER the verify lines, inside the prompt body.
        self.assertLess(body.index("L1_VERIFY_STATUS="),
                        body.index("ARTIFACT_ADVISORY="))

    def test_advisory_absent_when_clean(self):
        d = self._track()  # no plan, no handoffs — nothing to say
        body = _build_phase_checker(d, {"track_id": "t"}, 1, {})
        self.assertNotIn("ARTIFACT_ADVISORY=", body)


class PhaseCheckerWiringTests(unittest.TestCase):
    def test_phase_checker_names_the_advisory_line(self):
        # The envelope line is useless unless the reader body names it: pin
        # that phase-checker.md documents ARTIFACT_ADVISORY (and its
        # report-only contract) so a trim cannot orphan the emitter.
        self.assertIn("ARTIFACT_ADVISORY", PHASE_CHECKER)
        self.assertIn("report-only", PHASE_CHECKER)
        self.assertIn("orphan:", PHASE_CHECKER)
        self.assertIn("unattested:", PHASE_CHECKER)


if __name__ == "__main__":
    unittest.main()
