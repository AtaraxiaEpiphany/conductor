"""Tests for Track E (labeling optimization) — the fold-in riding the
grounding fan-out track (conductor/design/grounding-fanout §Menu Track E).

Extensibility-review Finding 1, methods 2–5:

- method 2 — the fog-test complexity rule in spec-planner §4.2 (unsure about
  the GROUND means [Explore], distinct from unsure-about-exemption → untagged);
- method 3 — research-first signal widening (complexity wording as shape
  `signals` data; one registry edit, the matcher already pure code);
- method 4 — the `examples` few-shot field: validator + `tag add --examples`
  generator + the registry-doc Tag Signals render;
- method 5 — labeling telemetry persisted per-track (agreements included —
  the denominator), stdout advisories unchanged.
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.track_state import quality  # noqa: E402
from scripts.track_state import workflow_shapes as ws  # noqa: E402
from scripts.track_state import task_profiles as tp  # noqa: E402
from scripts.track_state.misc import cmd_registry_doc  # noqa: E402
from scripts.track_state.quality import cmd_init_from_plan  # noqa: E402
from scripts.track_state.registry_validate import (  # noqa: E402
    validate_task_types, validate_shapes,
)
from scripts.track_state.task_profiles import tag_add, tag_summary_rows  # noqa: E402

PLANNER = ROOT / "agents" / "spec-planner.md"
PROFILES = ROOT / "templates" / "workflow" / "task-type-profiles.json"


def _out_captured(fn, *args, **kwargs):
    old_out = sys.stdout
    sys.stdout = io.StringIO()
    try:
        fn(*args, **kwargs)
        return sys.stdout.getvalue()
    finally:
        sys.stdout = old_out


class _ShippedRegistry(TestCase):
    """Resolve against the SHIPPED registries: no overlay, fresh caches."""

    def setUp(self):
        self._prior = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        ws._load.cache_clear()

    def tearDown(self):
        if self._prior is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior
        ws._load.cache_clear()


class FogTestRuleTests(TestCase):
    """Method 2 — the complexity rule is planner doctrine, pinned."""

    def test_rule_present_in_planner_42(self):
        body = PLANNER.read_text(encoding="utf-8")
        self.assertIn("The fog test (complexity rule)", body)
        # The split it exists to make: exemption-unsure → untagged;
        # ground-unsure → [Explore].
        self.assertIn("which exemption applies", body)
        self.assertIn("the ground", body)
        self.assertIn("insert an `[Explore]` task ahead of the building tasks",
                      body)
        self.assertIn("RESEARCH_NOTES", body,
                      "the rule must consume the fan-out's notes")


class ResearchFirstSignalsTests(_ShippedRegistry):
    """Method 3 — complexity wording widens research-first's signals."""

    def test_complexity_wording_ranked(self):
        for word in ("integrate", "integration", "cross-cutting",
                     "cross-module", "multi-system"):
            self.assertIn(word, ws.signals_for("research-first"),
                          f"research-first must carry {word!r}")
            hits = [c for c in ws.rank_shapes(f"we must {word} the services")
                    if c["shape"] == "research-first"]
            self.assertTrue(hits, f"{word!r} must rank research-first")

    def test_baseline_still_validates(self):
        doc = json.loads((ROOT / "templates" / "workflow" /
                          "workflow-shapes.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_shapes(doc), [])


class ExamplesFieldTests(_ShippedRegistry):
    """Method 4 — the few-shot exemplar field end to end."""

    def test_shipped_rows_carry_examples_and_validate(self):
        doc = json.loads(PROFILES.read_text(encoding="utf-8"))
        self.assertEqual(validate_task_types(doc), [])
        for tag in ("Explore", "Docs", "Config", "Chore"):
            self.assertIn("examples", doc["tags"][tag],
                          f"{tag} must seed examples")

    def test_validator_rejects_bad_examples(self):
        for bad in ([], ["ok", ""], "not-a-list", [3]):
            errs = validate_task_types(
                {"tags": {"X": {"when_to_use": "w", "examples": bad}}})
            self.assertTrue(any("examples" in e for e in errs),
                            f"{bad!r} must be rejected")

    def test_tag_add_writes_examples(self):
        with tempfile.TemporaryDirectory() as d:
            r = tag_add("K8sRollout", when_to_use="Roll out to k8s",
                        examples="roll the api deployment to staging;NOT a "
                                 "config edit — that is [Config]",
                        project_dir=d)
            self.assertTrue(r["ok"], r)
            row = json.loads(
                (Path(d) / "conductor" / "workflow" /
                 "task-type-profiles.json").read_text(encoding="utf-8")
            )["tags"]["K8sRollout"]
            self.assertEqual(row["examples"], [
                "roll the api deployment to staging",
                "NOT a config edit — that is [Config]",
            ])

    def test_registry_doc_renders_examples_into_tag_signals(self):
        text = _out_captured(cmd_registry_doc)
        self.assertIn("## Tag Signals", text)
        self.assertIn("e.g. map the auth flow across the billing and "
                      "inventory services", text)

    def test_compact_injection_rows_not_polluted(self):
        # tag_summary_rows is the COMPACT reviewer/injection renderer —
        # examples live in the registry-doc Tag Signals block only. [Explore]
        # carries examples; its compact render must stay tag-line + signals.
        rows = tag_summary_rows()
        i = next(i for i, r in enumerate(rows) if r.startswith("[Explore]"))
        # The tag line + (at most) its signals line — never example lines.
        block = rows[i:i + 2]
        self.assertTrue(block[0].startswith("[Explore] route=explore"))
        self.assertTrue(
            len(block) == 1 or block[1].lstrip().startswith("signals:"),
            block)


class TelemetryTests(TestCase):
    """Method 5 — the persisted per-track labeling instrument."""

    def _plan(self, td):
        td.mkdir(parents=True, exist_ok=True)
        (td / "plan.md").write_text(
            "# Implementation Plan: t\n\n"
            "## Phase 1: Build\n"
            "- [ ] [Explore] map the auth flow\n"
            "- [ ] bump the retry limit constant\n",
            encoding="utf-8")

    def test_init_persists_samples_with_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp) / "track"
            self._plan(td)
            res = json.loads(_out_captured(
                cmd_init_from_plan, str(td), "gf_test_20260902", "feature",
                "d", execution_mode="interactive"))
            self.assertTrue(res["ok"])
            store = json.loads((td / ".conductor" / "label-telemetry.json")
                               .read_text(encoding="utf-8"))
            self.assertEqual(store["track_id"], "gf_test_20260902")
            self.assertEqual(store["n_tasks"], 2)
            by_task = {s["task"]: s for s in store["samples"]}
            # Agreement INCLUDED (the denominator)...
            self.assertEqual(by_task["P1.T1"],
                             {"task": "P1.T1", "declared": "explore",
                              "suggested": "explore",
                              "name": "map the auth flow"})
            # ...disagreement captured (the numerator)...
            self.assertEqual(by_task["P1.T2"]["declared"], "untagged")
            self.assertEqual(by_task["P1.T2"]["suggested"], "chore")
            # ...and the stdout advisory keeps its original-case display.
            self.assertIn("P1.T2: declared untagged, signals suggest [Chore]",
                          res["tag_advisories"][0])

    def test_check_mode_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp) / "track"
            self._plan(td)
            res = json.loads(_out_captured(
                cmd_init_from_plan, str(td), "gf_test_20260902", "feature",
                "d", check=True))
            self.assertTrue(res["ok"])
            self.assertFalse((td / ".conductor" / "label-telemetry.json")
                             .exists(), "--check is read-only")

    def test_advisories_remain_stdout_telemetry(self):
        # The R1 lint contract is unchanged: print, never enforce. Agreement
        # stays silent; disagreement names both labels.
        self.assertEqual(
            quality._tag_signal_advisories(
                {"phases": [{"tasks": [
                    {"name": "Add a payment retry queue"}]}]}),
            [])
        adv = quality._tag_signal_advisories(
            {"phases": [{"tasks": [
                {"name": "Update the README installation section"}]}]})
        self.assertEqual(len(adv), 1)
        self.assertIn("signals suggest [Docs]", adv[0])


if __name__ == "__main__":
    main()
