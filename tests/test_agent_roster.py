"""Tests for the agent-roster registry — roster-as-data (Phases A+B).

The third registry owns the dispatch SCAFFOLD per agent (result fence,
registry injection, retry context, single-writer guard, stop-hook recovery).
Phase B deleted the six pre-registry literal homes into roster reads — the
roster is now the SOLE home — so the load-bearing tests here are the **golden
pins**: the derived sets must equal the values the hooks used to hardcode
(the plugin-generality C1 guard, now guarding data instead of a refactor),
plus the homes-gone source pins proving no literal set crept back.

Pinned semantics:

- **golden sets** — single_writers / registry_agents / retry_agents /
  result_file_agents / stdout_block_agents equal the six pre-registry homes'
  values (pinned inline; the equivalence tests that ran while both homes
  existed proved the Phase B switch was a no-op);
- **homes gone** — no hook source carries the literal dicts; each reads the
  roster; the SubagentStart/SubagentStop matchers are matcherless;
- **merge ladder** — overlay rows added, project wins, malformed overlay
  falls back to baseline alone, missing baseline falls back to the EMPTY
  roster (``no scaffold``), never a crash;
- **validator** — class enum, fence non-empty, unknown fields, the
  recovery/recovery_instruction pairing (two-homes guard).
"""
import io
import json
import os
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.track_state import agent_roster as ar  # noqa: E402
from scripts.track_state.registry_validate import (  # noqa: E402
    AGENT_CLASSES, RECOVERY_KINDS, validate_agent_roster, validate_merged_roster,
)

# The 23 baseline rows, in registry (insertion) order — the exact names the six
# pre-registry literal homes carried. A new agents/*.md without a row fails the
# agents/-parity pin in test_on_subagent_start; this pin catches stale rows.
BASELINE_NAMES = (
    "task-executor", "code-reviewer", "explorer", "phase-checker",
    "ac-tracer", "build-runner", "test-runner", "corpus-writer",
    "wiki-synthesizer", "doc-linter", "skip-analyst", "failure-analyst",
    "spec-planner", "spec-reviewer", "project-analyzer", "wiki-differ",
    "wiki-researcher", "refuter", "command-digester", "doc-probe",
    "apply-fixes", "refactorer", "strategy-writer",
)

# The pre-registry literal sets, pinned as values (their code homes are gone).
GOLDEN_SINGLE_WRITERS = ("task-executor", "explorer")            # _WRITE_AGENTS
GOLDEN_REGISTRY_AGENTS = {"task-executor", "spec-reviewer", "refuter"}  # _REGISTRY_AGENTS
GOLDEN_RETRY_AGENTS = {"task-executor"}                          # _RETRY_AGENTS
GOLDEN_RESULT_FILE = {"task-executor", "explorer"}               # RESULT_FILE_AGENT_TYPES
# STDOUT_BLOCK_AGENTS keys, registry order.
GOLDEN_STDOUT_BLOCK = (
    "code-reviewer", "phase-checker", "ac-tracer", "build-runner",
    "test-runner", "corpus-writer", "wiki-synthesizer", "spec-planner",
    "spec-reviewer", "apply-fixes", "refactorer",
)

_HOOK_SOURCES = {
    "scripts/on-subagent-start.py": ("AGENT_REMINDERS", "_REGISTRY_AGENTS",
                                     "_RETRY_AGENTS"),
    "scripts/on-subagent-stop.py": ("_RESULT_FILE_INSTRUCTIONS",
                                    "STDOUT_BLOCK_AGENTS"),
    "scripts/on-dispatch-dedupe.py": ("_WRITE_AGENTS",),
    "scripts/filter-subagent-output.py": ("RESULT_FILE_AGENT_TYPES",),
    "scripts/lib/recovery.py": ("RESULT_FILE_AGENT_TYPES",),
}


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


class RosterGoldenPins(_ShippedRoster):
    """The derived sets equal the pre-registry literal homes' values."""

    def test_baseline_covers_the_23_agents(self):
        self.assertEqual(ar.merged_agent_names(), BASELINE_NAMES)

    def test_single_writers(self):
        self.assertEqual(ar.single_writers(), GOLDEN_SINGLE_WRITERS)

    def test_registry_agents(self):
        self.assertEqual(set(ar.registry_agents()), GOLDEN_REGISTRY_AGENTS)

    def test_retry_agents(self):
        self.assertEqual(set(ar.retry_agents()), GOLDEN_RETRY_AGENTS)

    def test_result_file_agents(self):
        self.assertEqual(set(ar.result_file_agents()), GOLDEN_RESULT_FILE)

    def test_stdout_block_agents_registry_order(self):
        self.assertEqual(ar.stdout_block_agents(), GOLDEN_STDOUT_BLOCK)

    def test_no_agent_has_both_recovery_kinds(self):
        # One completion signal per agent — a row in both sets would race the
        # two SubagentStop branches.
        rf, sb = set(ar.result_file_agents()), set(ar.stdout_block_agents())
        self.assertFalse(rf & sb)

    def test_every_row_class_in_enum(self):
        for name in ar.merged_agent_names():
            self.assertIn(ar.class_for(name), AGENT_CLASSES)

    def test_class_census(self):
        census = {}
        for name in ar.merged_agent_names():
            census[ar.class_for(name)] = census.get(ar.class_for(name), 0) + 1
        self.assertEqual(census,
                         {"executor": 2, "verifier": 4, "reviewer": 3,
                          "advisory": 14})

    def test_every_reminder_composes_from_the_lead(self):
        for name in BASELINE_NAMES:
            reminder = ar.reminder_for(name)
            self.assertTrue(reminder.startswith(ar.REMINDER_LEAD), name)
            self.assertIn("---", reminder, f"{name} fence has no delimiter")

    def test_task_executor_fence_and_flags(self):
        self.assertEqual(ar.reminder_for("task-executor"),
                         "[Conductor] Result format: "
                         "---TASK RESULT--- ... ---END RESULT---")
        self.assertTrue(ar.is_single_writer("task-executor"))
        self.assertEqual(ar.recovery_kind_for("task-executor"), "result-file")

    def test_command_digester_fence_is_purpose_keyed(self):
        # The one two-format fence in the roster — both delimiters must ride.
        reminder = ar.reminder_for("command-digester")
        self.assertIn("---TEST DIGEST RESULT---", reminder)
        self.assertIn("---LOG CHECK RESULT---", reminder)
        self.assertEqual(ar.recovery_kind_for("command-digester"), "none")

    def test_task_executor_recovery_instruction(self):
        self.assertEqual(
            ar.recovery_instruction_for("task-executor"),
            "IMMEDIATELY call track-state write-result (Section 6.0) and "
            "print the ---TASK RESULT--- block. Report FAILURE if you cannot "
            "complete.")

    def test_spec_reviewer_recovery_instruction_noninteractive(self):
        # The read-only auditor contract (cb35bcf): no file writes, no
        # CANCELLED — the recovery turn can complete without a human loop.
        instr = ar.recovery_instruction_for("spec-reviewer")
        self.assertNotIn("review-result.json", instr)
        self.assertNotIn("CANCELLED", instr)
        self.assertIn("CHANGES_REQUESTED", instr)

    def test_shipped_baseline_and_merged_validate_clean(self):
        doc = json.loads(
            (ROOT / "templates" / "workflow" / "agent-roster.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(validate_agent_roster(doc), [])
        self.assertEqual(validate_merged_roster(ar._load()), [])


class LiteralHomesGone(TestCase):
    """Phase B's deletion pins: no literal set crept back; hooks read the roster.

    Source-text asserts (not imports) — the point is what ships in the file,
    and a hook module has CLI side effects at import.
    """

    def test_six_literal_sets_absent_from_hook_sources(self):
        for rel, literals in _HOOK_SOURCES.items():
            src = (ROOT / rel).read_text(encoding="utf-8")
            for lit in literals:
                self.assertNotIn(lit, src, f"{lit} resurrected in {rel}")

    def test_hooks_read_the_roster(self):
        for rel in ("scripts/on-subagent-start.py",
                    "scripts/on-subagent-stop.py",
                    "scripts/on-dispatch-dedupe.py",
                    "scripts/filter-subagent-output.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("agent_roster", src,
                          f"{rel} must read the agent-roster registry")

    def test_subagent_matchers_are_matcherless(self):
        # D5: matchers drop their name alternations so a project-overlay agent
        # ever reaches the scripts (matchers are static JSON — no
        # registry-aware pattern exists). The stop hook merged to ONE sync
        # entry (the old async arm's block-is-a-no-op semantics would have
        # left spec-reviewer/refuter recovery contracts inert).
        data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        for event in ("SubagentStart", "SubagentStop"):
            self.assertTrue(data["hooks"][event], f"no {event} entry")
            for entry in data["hooks"][event]:
                self.assertNotIn(
                    "matcher", entry,
                    f"{event} matcher carries a name alternation — widen it "
                    f"away and let the roster gate")
        self.assertEqual(len(data["hooks"]["SubagentStop"]), 1,
                         "SubagentStop must be ONE merged (sync) entry")
        self.assertNotIn("async", data["hooks"]["SubagentStop"][0]["hooks"][0],
                         "the merged SubagentStop entry must be sync — an "
                         "async hook's block decision is a no-op")


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
        self.assertEqual(set(names), set(BASELINE_NAMES))
        self.assertIn("WARNING", err.getvalue())

    def test_non_object_overlay_falls_back_to_baseline(self):
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.proj)
        self._write_overlay(["not", "an", "object"])
        ar._load.cache_clear()
        err = io.StringIO()
        with redirect_stderr(err):
            names = ar.merged_agent_names()
        self.assertEqual(set(names), set(BASELINE_NAMES))
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
