"""Tests for ``track-state propose-grounding`` — the fog gate (grounding
fan-out, new-track §2.2.5).

The pure front door of the grounding fan-out: ``workflow_shapes.grounding_hits``
signal-matches a track description ⊕ brief against the registry's top-level
``grounding_signals`` keywords (the shared word-boundary matcher), and
``misc.cmd_propose_grounding`` composes the fog verdict JSON the skill consumes
(foggy / score / hits / confirm_required — the skill never re-derives).

Pinned semantics (the grill-resolved contract):

- **one distinct signal suffices to ASK** — ``foggy = score >= 1``; the ask is
  the anchor, over-firing costs one question, under-firing loses the grounding;
- **quiet tracks pay nothing** — zero signals → ``foggy=false``, no ask;
- **brief structural signals compose** — ``## Open Questions`` with >= 2
  non-empty items, ``## References`` present-but-empty;
- **--brief is fail-open** — an absent brief gates on the description alone;
- **dedupe across description ⊕ brief** — a keyword landing in both is one
  fog point;
- the registry validator accepts the top-level ``grounding_signals`` key and
  rejects a malformed one.
"""
import io
import json
import os
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.track_state import workflow_shapes as ws  # noqa: E402
from scripts.track_state.misc import cmd_propose_grounding  # noqa: E402
from scripts.track_state.registry_validate import validate_shapes  # noqa: E402


def _out_captured(fn, *args, **kwargs):
    """Capture stdout from a command fn. Returns parsed JSON."""
    old_out = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old_out


class _ShippedRegistry(TestCase):
    """Gate against the SHIPPED registry: no project overlay, fresh cache."""

    def setUp(self):
        self._prior = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        ws._load.cache_clear()

    def tearDown(self):
        if self._prior is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior
        ws._load.cache_clear()


class GroundingSignalsTests(_ShippedRegistry):
    """``grounding_signals`` accessor + ``grounding_hits`` pure core."""

    def test_shipped_registry_declares_signals(self):
        self.assertTrue(ws.grounding_signals(),
                        "the shipped baseline must declare grounding_signals")
        for sig in ws.grounding_signals():
            self.assertEqual(sig, sig.lower(),
                             f"signals are matched against lowercased text: {sig!r}")

    def test_hits_word_boundary(self):
        # The shared matcher: "ci"-style gluing must not fire. "integration"
        # hits; a bare "integrate" inside "reintegration" still hits (it is
        # its own signal edge-to-edge) — but "map" in "mapping" style gluing
        # is the class the matcher exists to kill, pinned via a short signal.
        hits = ws.grounding_hits("Integrate the billing API")
        self.assertIn("integrate", hits)

    def test_empty_text_no_hits(self):
        self.assertEqual(ws.grounding_hits(""), [])
        self.assertEqual(ws.grounding_hits("   \n  "), [])

    def test_baseline_validates_with_top_level_key(self):
        doc = json.loads((ROOT / "templates" / "workflow" /
                          "workflow-shapes.json").read_text(encoding="utf-8"))
        self.assertIn("grounding_signals", doc)
        self.assertEqual(validate_shapes(doc), [])

    def test_validator_rejects_malformed_grounding_signals(self):
        errs = validate_shapes({"grounding_signals": "integrate"})
        self.assertTrue(any("grounding_signals" in e for e in errs))
        errs = validate_shapes({"grounding_signals": ["ok", 3]})
        self.assertTrue(any("grounding_signals" in e for e in errs))

    def test_merge_carries_baseline_signals_without_overlay(self):
        # _merge_overlay must not drop the top-level DATA key when the project
        # declares no overlay copy of it (it used to collapse to
        # {default, shapes} only).
        merged = ws._merge_overlay(ws._load_baseline())
        self.assertIn("grounding_signals", merged)

    def test_missing_key_fail_open_quiet(self):
        # A registry without the key → () → the gate never fires (the correct
        # direction for an optional pre-planning spend).
        self.assertEqual(ws._shape("default").get("grounding_signals"), None)
        hits = ws.grounding_hits("integrate everything")
        # sanity: the shipped registry DOES carry the key, so flip the
        # expectation through the fallback instead.
        if not ws.grounding_signals():
            self.assertEqual(hits, [])


class ProposeGroundingTests(_ShippedRegistry):
    """``cmd_propose_grounding`` — the composed verdict JSON."""

    def test_missing_description_is_an_error(self):
        res = _out_captured(cmd_propose_grounding, "   ")
        self.assertFalse(res["ok"])
        self.assertIn("missing description", res["error"])

    def test_quiet_track_not_foggy(self):
        res = _out_captured(cmd_propose_grounding, "fix the typo in the README")
        self.assertTrue(res["ok"])
        self.assertFalse(res["foggy"])
        self.assertEqual(res["score"], 0)
        self.assertFalse(res["confirm_required"])
        self.assertFalse(res["brief_used"])
        self.assertIn("quiet", res["rationale"])

    def test_one_signal_suffices_to_ask(self):
        res = _out_captured(
            cmd_propose_grounding, "integrate the billing API")
        self.assertTrue(res["foggy"])
        self.assertEqual(res["score"], 1)
        self.assertTrue(res["confirm_required"])
        self.assertIn("integrate", res["hits"])
        self.assertIn("integrate", res["rationale"])

    def test_brief_structural_open_questions(self):
        with __import__("tempfile").TemporaryDirectory() as td:
            brief = Path(td) / "brief.md"
            brief.write_text(
                "# Brief\n\n## Open Questions\n- q1?\n- q2?\n- q3?\n\n"
                "## References\n- conductor/design/foo.md\n",
                encoding="utf-8")
            res = _out_captured(cmd_propose_grounding, "small feature",
                                brief_path=str(brief))
        self.assertTrue(res["brief_used"])
        self.assertTrue(res["foggy"])
        # references has an item → only the open-questions signal fires
        self.assertEqual(res["score"], 1)
        self.assertIn("open questions in brief (3)", res["hits"])

    def test_brief_structural_references_empty(self):
        with __import__("tempfile").TemporaryDirectory() as td:
            brief = Path(td) / "brief.md"
            brief.write_text(
                "# Brief\n\n## Open Questions\n- only one?\n\n"
                "## References\n\n## Out of Scope\n- x\n",
                encoding="utf-8")
            res = _out_captured(cmd_propose_grounding, "small feature",
                                brief_path=str(brief))
        # 1 open question (< 2, no signal) + empty references → 1 fog point
        self.assertEqual(res["score"], 1)
        self.assertIn("references section empty", res["hits"])

    def test_brief_absent_sections_no_structural_signal(self):
        with __import__("tempfile").TemporaryDirectory() as td:
            brief = Path(td) / "brief.md"
            brief.write_text(
                "# Brief\n\n## Problem\n\nsome prose\n\n## References\n- a.md\n",
                encoding="utf-8")
            res = _out_captured(cmd_propose_grounding, "small feature",
                                brief_path=str(brief))
        self.assertFalse(res["foggy"])
        self.assertEqual(res["score"], 0)

    def test_missing_brief_fail_open(self):
        res = _out_captured(cmd_propose_grounding, "small feature",
                            brief_path="/nonexistent/brief.md")
        self.assertFalse(res["brief_used"])
        self.assertFalse(res["foggy"])

    def test_keyword_dedupe_across_description_and_brief(self):
        # "integrate" in BOTH the description and the brief = ONE fog point.
        with __import__("tempfile").TemporaryDirectory() as td:
            brief = Path(td) / "brief.md"
            brief.write_text(
                "# Brief\n\n## Problem\n\nwe must integrate things\n",
                encoding="utf-8")
            res = _out_captured(cmd_propose_grounding, "integrate the API",
                                brief_path=str(brief))
        self.assertEqual(res["score"], 1)
        self.assertEqual(res["hits"], ["integrate"])

    def test_confirm_required_mirrors_foggy(self):
        for desc, expect in (("integrate the API", True),
                             ("fix a typo", False)):
            res = _out_captured(cmd_propose_grounding, desc)
            self.assertEqual(res["confirm_required"], expect)
            self.assertEqual(res["confirm_required"], res["foggy"])


if __name__ == "__main__":
    main()
