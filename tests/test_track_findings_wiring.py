"""Wiring tests for the track-findings *read* side (#track-findings).

``compile_track_findings`` (covered by ``test_track_findings.py``) produces
``{TRACK_DIR}/.conductor/track-findings.md`` at every PASSED checkpoint. That
file is useless unless two agents actually read it: ``explorer`` (§3.2, before
code exploration) and ``task-executor`` (Layer 0(c), before Layer 1). These are
prose invariants in agent bodies — a future trim campaign can delete the
read-side sentence while the compiler still faithfully writes a file nothing
consumes. That silently dead loop is exactly what this test prevents.

Mirrors the doc-probe read-side pin in ``test_doc_probe_wiring.py``: we assert
the path token and the cross-phase framing appear in BOTH reader bodies, and
that the absence path is documented (so greenfield/no-explorer is a designed
skip, not an unhandled error).
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
EXPLORER = (AGENTS / "explorer.md").read_text(encoding="utf-8")
TASK_EXECUTOR = (AGENTS / "task-executor.md").read_text(encoding="utf-8")

# The single token both readers must reference. If the compiled filename ever
# changes (handoff.py::_track_findings_path), this constant — and both agent
# bodies — must move together; this test is the tripwire.
FINDINGS_DOC = "track-findings.md"
# The envelope line both readers are retargeted to (design: findings/artifact
# edge) — the pointer rides the envelope, the bodies name it, so a body edit
# that reverts to path-recall is caught here.
FINDINGS_LINE = "FINDINGS_FILE="


class ExplorerReadsTrackFindingsTests(unittest.TestCase):
    def test_section_3_2_references_compiled_doc(self):
        # §3.2 is the explorer's cross-phase bridge. Without a reference to the
        # compiled path, the explorer re-explores from scratch every phase and
        # the durable findings a prior phase recorded never reach it.
        self.assertIn(FINDINGS_DOC, EXPLORER)

    def test_section_3_2_names_envelope_pointer(self):
        # The read trigger is the envelope line, not path recall — the weakest
        # contract class the findings edge retired.
        self.assertIn(FINDINGS_LINE, EXPLORER)

    def test_cross_phase_framing_present(self):
        # The reader must know WHY it is reading this file (prior art from an
        # earlier phase of THIS track), so it verifies rather than inherits —
        # the load-bearing instruction, not just the filename.
        self.assertIn("cross-phase", EXPLORER.lower())

    def test_absence_path_documented(self):
        # First phase / no explorer yet → file absent → skip silently. This must
        # be explicit so a missing file is treated as expected (not a
        # missing-corpus signal that triggers spurious graduation).
        self.assertIn("absent", EXPLORER.lower())

    def test_stub_case_documented(self):
        # The compiler writes a `_No durable findings recorded yet._` stub when
        # a checkpoint compiled an empty harvest (see _render_track_findings).
        # That file is PRESENT — so the absence-path bullet above doesn't cover
        # it. The body must name the stub and say "treat as absent," else a
        # reader misreads an empty harvest as "explored and found nothing" and
        # skips exploration it should do.
        self.assertIn("No durable findings", EXPLORER)


class TaskExecutorReadsTrackFindingsTests(unittest.TestCase):
    def test_layer_0c_references_compiled_doc(self):
        # Layer 0(c) is the task-executor's read point (renumbered to 0(c) when
        # track-findings took the slot; doc-probe fan-out moved to 0(d)). If this
        # reference disappears, the task-executor implements blind to prior
        # phases' durable findings.
        self.assertIn(FINDINGS_DOC, TASK_EXECUTOR)

    def test_layer_0c_names_envelope_pointer(self):
        # Mirror of the explorer pin: the read trigger is the envelope line,
        # not path recall.
        self.assertIn(FINDINGS_LINE, TASK_EXECUTOR)

    def test_layer_label_present(self):
        # Pin the layer label so a renumbering is caught here, not silently in
        # a later doc-probe test (test_doc_probe_wiring already pins 0(d) for
        # the fan-out; this pins 0(c) for track-findings).
        self.assertIn("0(c)", TASK_EXECUTOR)

    def test_verify_before_rely_present(self):
        # The compile is a point-in-time snapshot; code may have moved on. The
        # reader MUST be told to verify against current code before relying on a
        # finding — otherwise stale findings silently guide implementation.
        self.assertIn("verify", TASK_EXECUTOR.lower())

    def test_absence_path_documented(self):
        # Same safe-failure contract as the explorer: absent file → skip
        # silently, not an error.
        self.assertIn("absent", TASK_EXECUTOR.lower())

    def test_stub_case_documented(self):
        # Mirror of the explorer stub test: the compiler writes a PRESENT stub
        # on an empty harvest, which the absence-path bullet does not cover.
        # The body must name the stub and say "treat as absent," else the
        # task-executor misreads "no durable findings yet" as "explored and
        # empty" and skips Layer 1 work it should do.
        self.assertIn("No durable findings", TASK_EXECUTOR)

    def test_reports_artifacts_via_flags(self):
        # Write side of the ledger (design: findings/artifact edge): §6.1
        # must document the repeatable --artifacts/--artifacts-used flags —
        # without them in the body, executors never declare produced files
        # and the whole delivery chain (handoff roll → catalog → task-context
        # join → checkpoint advisory) has no producers.
        self.assertIn("--artifacts", TASK_EXECUTOR)
        self.assertIn("--artifacts-used", TASK_EXECUTOR)


if __name__ == "__main__":
    unittest.main()
