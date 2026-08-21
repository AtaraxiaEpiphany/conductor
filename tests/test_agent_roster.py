"""Tests for the agent-roster registry — roster-as-data Phase A (D1/D2).

The third registry owns the dispatch SCAFFOLD per agent (result fence,
registry injection, retry context, single-writer guard, stop-hook recovery).
Phase A lands it alongside the six hardcoded literal sets it replaces — so the
load-bearing tests here are the **equivalence pins**: for every one of the 23
seeded agents, the roster-derived value must be byte-identical to the live
hook literal (the plugin-generality C1 guard — the pins make Phase B's
hook-side switch a provable no-op, and after Phase B they keep the roster
honest as the only home).

Pinned semantics:

- **verbatim fences** — ``reminder_for(n) == AGENT_REMINDERS[n]`` for all 23
  (the lead + fence composition reconstructs the literal exactly);
- **derived sets** — single_writers / registry_agents / retry_agents /
  result_file_agents / stdout_block_agents equal the six hook-side homes;
- **recovery instructions** — byte-identical to ``_RESULT_FILE_INSTRUCTIONS``
  and ``STDOUT_BLOCK_AGENTS``;
- **merge ladder** — overlay rows added, project wins, malformed overlay
  falls back to baseline alone, missing baseline falls back to the EMPTY
  roster (``no scaffold``), never a crash;
- **validator** — class enum, fence non-empty, unknown fields, the
  recovery/recovery_instruction pairing (two-homes guard).
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.track_state import agent_roster as ar  # noqa: E402
from scripts.track_state.registry_validate import (  # noqa: E402
    AGENT_CLASSES, RECOVERY_KINDS, validate_agent_roster, validate_merged_roster,
)


def _load_hook_module(name: str, rel: str):
    """Import a dash-named hook script by path (the established test pattern —
    hook scripts are not importable by module name)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The six literal homes (still live in Phase A; the pins prove the roster
# reconstructs them byte-identically. Phase B deletes them into these reads.)
_start = _load_hook_module("aru_start_hook", "scripts/on-subagent-start.py")
_stop = _load_hook_module("aru_stop_hook", "scripts/on-subagent-stop.py")
_dedupe = _load_hook_module("aru_dedupe_hook", "scripts/on-dispatch-dedupe.py")
from lib.recovery import RESULT_FILE_AGENT_TYPES  # noqa: E402


class _ShippedRoster(TestCase):
    """Run against the SHIPPED baseline: no project overlay, fresh cache."""

    def setUp(self):
        self._prior = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        ar._load.cache_clear()

    def tearDown(self):
        if self._prior is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior
        ar._load.cache_clear()


class EquivalencePins(_ShippedRoster):
    """The roster reconstructs the six literal homes byte-identically."""

    def test_every_hook_agent_is_rostered_and_vice_versa(self):
        self.assertEqual(set(ar.merged_agent_names()),
                         set(_start.AGENT_REMINDERS))
        self.assertEqual(len(ar.merged_agent_names()), 23)

    def test_reminders_byte_identical(self):
        for name, literal in _start.AGENT_REMINDERS.items():
            self.assertEqual(ar.reminder_for(name), literal,
                             f"fence for {name} must seed verbatim")

    def test_single_writers_match_write_agents(self):
        self.assertEqual(set(ar.single_writers()), set(_dedupe._WRITE_AGENTS))
        self.assertEqual(ar.single_writers(), ("task-executor", "explorer"))

    def test_registry_agents_match(self):
        self.assertEqual(set(ar.registry_agents()), _start._REGISTRY_AGENTS)

    def test_retry_agents_match(self):
        self.assertEqual(set(ar.retry_agents()), _start._RETRY_AGENTS)

    def test_result_file_agents_match_all_three_homes(self):
        # lib.recovery's set, the stop hook's instruction keys, and the roster
        # must agree (the import-time assert in on-subagent-stop held the first
        # pair; the roster now carries all three).
        self.assertEqual(set(ar.result_file_agents()),
                         set(RESULT_FILE_AGENT_TYPES))
        self.assertEqual(set(ar.result_file_agents()),
                         set(_stop._RESULT_FILE_INSTRUCTIONS))

    def test_result_file_instructions_byte_identical(self):
        for name, instr in _stop._RESULT_FILE_INSTRUCTIONS.items():
            self.assertEqual(ar.recovery_instruction_for(name), instr)

    def test_stdout_block_agents_match(self):
        self.assertEqual(set(ar.stdout_block_agents()),
                         set(_stop.STDOUT_BLOCK_AGENTS))

    def test_stdout_block_instructions_byte_identical(self):
        for name, instr in _stop.STDOUT_BLOCK_AGENTS.items():
            self.assertEqual(ar.recovery_instruction_for(name), instr)

    def test_no_agent_has_both_recovery_kinds(self):
        # The two instruction dicts must stay disjoint (one completion signal
        # per agent — a row in both would race the two SubagentStop branches).
        self.assertFalse(set(_stop._RESULT_FILE_INSTRUCTIONS)
                         & set(_stop.STDOUT_BLOCK_AGENTS))
        rf, sb = set(ar.result_file_agents()), set(ar.stdout_block_agents())
        self.assertFalse(rf & sb)

    def test_every_row_class_in_enum(self):
        for name in ar.merged_agent_names():
            self.assertIn(ar.class_for(name), AGENT_CLASSES)

    def test_shipped_baseline_and_merged_validate_clean(self):
        doc = json.loads(
            (ROOT / "templates" / "workflow" / "agent-roster.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(validate_agent_roster(doc), [])
        self.assertEqual(validate_merged_roster(ar._load()), [])


class UnrosteredFailOpen(_ShippedRoster):
    """An agent absent from the roster runs untouched — today's behavior."""

    def test_unrostered_accessors_degrade(self):
        self.assertIsNone(ar.row_for("general-purpose"))
        self.assertIsNone(ar.reminder_for("general-purpose"))
        self.assertEqual(ar.class_for("general-purpose"), "")
        self.assertFalse(ar.is_single_writer("general-purpose"))
        self.assertEqual(ar.recovery_kind_for("general-purpose"), "none")
        self.assertEqual(ar.recovery_instruction_for("general-purpose"), "")
        self.assertNotIn("general-purpose", ar.single_writers())
        self.assertNotIn("general-purpose", ar.result_file_agents())
        self.assertNotIn("general-purpose", ar.stdout_block_agents())


class MergeLadder(TestCase):
    """Overlay semantics: project rows added / project wins / fail-open."""

    def setUp(self):
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        (self.proj / "conductor" / "workflow").mkdir(parents=True)
        ar._load.cache_clear()

    def tearDown(self):
        if self._prior_proj is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        else:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        self._tmp.cleanup()
        ar._load.cache_clear()

    def _write_overlay(self, doc):
        (self.proj / "conductor" / "workflow" / "agent-roster.json").write_text(
            json.dumps(doc), encoding="utf-8")

    def test_overlay_row_added(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.proj)
        self._write_overlay({"agents": {
            "proj-writer": {"class": "executor", "fence": "---X RESULT--- ... ---END RESULT---"},
        }})
        ar._load.cache_clear()
        self.assertIn("proj-writer", ar.merged_agent_names())
        self.assertEqual(ar.class_for("proj-writer"), "executor")
        self.assertTrue(ar.is_single_writer("proj-writer"))
        self.assertIn("proj-writer", ar.single_writers())
        # the whole baseline rides along unchanged
        self.assertIn("task-executor", ar.merged_agent_names())

    def test_overlay_row_wins_conflict(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.proj)
        self._write_overlay({"agents": {
            # demote a baseline verifier to plain advisory (row-level replace)
            "test-runner": {"class": "advisory",
                            "fence": "---X RESULT--- ... ---END RESULT---"},
        }})
        ar._load.cache_clear()
        self.assertEqual(ar.class_for("test-runner"), "advisory")
        self.assertFalse(ar.is_single_writer("test-runner"))
        self.assertNotIn("test-runner", ar.result_file_agents())
        self.assertNotIn("test-runner", ar.stdout_block_agents())
        self.assertEqual(ar.recovery_instruction_for("test-runner"), "")

    def test_malformed_overlay_falls_back_to_baseline(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.proj)
        (self.proj / "conductor" / "workflow" / "agent-roster.json").write_text(
            "{not json", encoding="utf-8")
        ar._load.cache_clear()
        err = io.StringIO()
        with redirect_stderr(err):
            names = ar.merged_agent_names()
        self.assertEqual(set(names), set(_start.AGENT_REMINDERS))
        self.assertIn("WARNING", err.getvalue())

    def test_non_object_overlay_falls_back_to_baseline(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.proj)
        self._write_overlay(["not", "an", "object"])
        ar._load.cache_clear()
        err = io.StringIO()
        with redirect_stderr(err):
            names = ar.merged_agent_names()
        self.assertEqual(set(names), set(_start.AGENT_REMINDERS))
        self.assertIn("WARNING", err.getvalue())

    def test_missing_baseline_falls_back_to_empty_roster(self):
        # The floor: no registry at all → the EMPTY roster ("no scaffold"),
        # never a crash — the pre-registry unknown-name behavior for everyone.
        self.assertEqual(ar._merge_overlay({"agents": {}}), {"agents": {}})

    def test_missing_baseline_file_uses_fallback(self):
        real = ar._plugin_registry_path
        ar._plugin_registry_path = lambda: ROOT / "nope" / "agent-roster.json"
        try:
            ar._load.cache_clear()
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(ar.merged_agent_names(), ())
                self.assertIsNone(ar.reminder_for("task-executor"))
        finally:
            ar._plugin_registry_path = real
            ar._load.cache_clear()
        self.assertIn("WARNING", err.getvalue())

    def test_baseline_without_agents_key_uses_fallback(self):
        real = ar._plugin_registry_path
        bad = Path(tempfile.mkdtemp()) / "bad.json"
        bad.write_text(json.dumps({"nope": 1}), encoding="utf-8")
        ar._plugin_registry_path = lambda: bad
        try:
            ar._load.cache_clear()
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(ar.merged_agent_names(), ())
        finally:
            ar._plugin_registry_path = real
            ar._load.cache_clear()


class ValidatorTests(TestCase):
    """Strict-write validation of roster fragments + resolved results."""

    GOOD = {"class": "executor", "fence": "---X RESULT--- ... ---END RESULT---",
            "recovery": "stdout-block", "recovery_instruction": "print the block"}

    def test_good_row_clean(self):
        self.assertEqual(validate_agent_roster({"agents": {"a": self.GOOD}}), [])
        self.assertEqual(
            validate_merged_roster({"agents": {"a": self.GOOD}}), [])

    def test_unknown_class_rejected(self):
        row = {**self.GOOD, "class": "wizard"}
        errs = validate_agent_roster({"agents": {"a": row}})
        self.assertTrue(any("class" in e for e in errs))

    def test_missing_class_rejected(self):
        row = {k: v for k, v in self.GOOD.items() if k != "class"}
        errs = validate_agent_roster({"agents": {"a": row}})
        self.assertTrue(any("'class' is required" in e for e in errs))

    def test_missing_or_empty_fence_rejected(self):
        for bad in (None, "", 7):
            errs = validate_agent_roster(
                {"agents": {"a": {**self.GOOD, "fence": bad}}})
            self.assertTrue(any("fence" in e for e in errs), bad)

    def test_unknown_field_rejected(self):
        row = {**self.GOOD, "fenc": "typo"}
        errs = validate_agent_roster({"agents": {"a": row}})
        self.assertTrue(any("unknown field 'fenc'" in e for e in errs))

    def test_bool_fields_must_be_bool(self):
        row = {**self.GOOD, "retry": "yes"}
        errs = validate_agent_roster({"agents": {"a": row}})
        self.assertTrue(any("retry must be a boolean" in e for e in errs))

    def test_recovery_requires_instruction(self):
        row = {k: v for k, v in self.GOOD.items()
               if k != "recovery_instruction"}
        errs = validate_agent_roster({"agents": {"a": row}})
        self.assertTrue(any("requires a non-empty 'recovery_instruction'" in e
                            for e in errs))

    def test_orphan_instruction_rejected(self):
        row = {k: v for k, v in self.GOOD.items() if k != "recovery"}
        errs = validate_agent_roster({"agents": {"a": row}})
        self.assertTrue(any("instruction without a recovery kind" in e
                            for e in errs))

    def test_unknown_recovery_kind_rejected(self):
        row = {**self.GOOD, "recovery": "carrier-pigeon"}
        errs = validate_agent_roster({"agents": {"a": row}})
        self.assertTrue(any("recovery=" in e for e in errs))

    def test_none_recovery_needs_no_instruction(self):
        row = {"class": "advisory", "fence": "---X RESULT--- ... ---END RESULT---"}
        self.assertEqual(validate_agent_roster({"agents": {"a": row}}), [])

    def test_unknown_top_key_rejected(self):
        errs = validate_agent_roster({"agents": {}, "agentz": {}})
        self.assertTrue(any("unknown top-level key" in e for e in errs))

    def test_non_dict_doc_rejected(self):
        self.assertEqual(validate_agent_roster(["nope"]),
                         ["agent roster top-level must be an object"])

    def test_non_object_row_rejected(self):
        errs = validate_agent_roster({"agents": {"a": "nope"}})
        self.assertTrue(any("must be an object" in e for e in errs))

    def test_merged_requires_agents_object(self):
        errs = validate_merged_roster({"_comment": "fragment"})
        self.assertTrue(any("must declare an 'agents' object" in e
                            for e in errs))

    def test_merged_empty_roster_is_valid(self):
        # The empty roster IS the fail-open floor — valid, never an error.
        self.assertEqual(validate_merged_roster({"agents": {}}), [])

    def test_recovery_kinds_closed(self):
        self.assertEqual(RECOVERY_KINDS,
                         ("result-file", "stdout-block", "none"))


if __name__ == "__main__":
    main()
