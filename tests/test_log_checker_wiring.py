"""Wiring tests for the Phase 3 nested-subagent pilot: doc-linter → log-checker.

doc-linter is read-only (``Read, Grep, Glob`` — no ``Bash``), yet its §4.5 *Log
Consistency* check must inspect git history to attribute ``DOC_UPDATE`` log
entries to track-bearing commits. Rather than widen doc-linter's firewall with
``Bash``, the git-needing step is delegated to a tightly-scoped child agent,
``log-checker`` (``Bash, Read, Grep, Glob``). This is the conductor fleet's first
nested subagent — the deliberate, sole exception to "subagents don't have the
Agent tool" (pinned in test_doc_sync_split_wiring.py for corpus-writer /
wiki-synthesizer, which stay Agent-free because their verify is skill-sequenced).

These tests lock the pilot's wiring so it can't silently regress AND so the
exception can't silently proliferate: doc-linter must remain the ONLY agent with
the ``Agent`` tool, and its body must constrain that tool to a single §4.5
``log-checker`` dispatch.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
DOC_LINTER = (AGENTS / "doc-linter.md").read_text(encoding="utf-8")
LOG_CHECKER = (AGENTS / "log-checker.md").read_text(encoding="utf-8")
HOOKS = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
ON_START = (ROOT / "scripts" / "on-subagent-start.py").read_text(encoding="utf-8")


def _frontmatter_tools(agent_text: str) -> str:
    """Extract the ``tools:`` value from an agent's YAML frontmatter."""
    in_fm = False
    for line in agent_text.splitlines():
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm and line.startswith("tools:"):
            return line.split("tools:", 1)[1].strip()
    return ""


def _section_four_checks(text):
    """Set of §4.x check ids defined (### headings), e.g. '4.5'."""
    return {m.group(1) for m in re.finditer(r"^### (4\.\d+)\s", text, re.MULTILINE)}


class LogCheckerAgentTests(unittest.TestCase):
    def test_agent_exists_with_bash_capable_tools(self):
        # log-checker is the Bash-capable child that closes doc-linter's §4.5 git
        # gap. It needs Bash (git inspection) + the read-only trio.
        tools = _frontmatter_tools(LOG_CHECKER)
        for required in ("Bash", "Read", "Grep", "Glob"):
            self.assertIn(required, tools, f"log-checker missing tool: {required}")

    def test_emits_structured_result_block(self):
        # filter-subagent-output keeps only the RESULT block; without one the
        # generic no-result advisory fires INSIDE doc-linter's context. log-checker
        # must emit a delimited block.
        self.assertIn("---LOG CHECK RESULT---", LOG_CHECKER)
        self.assertIn("---END RESULT---", LOG_CHECKER)
        self.assertIn("MISMATCHES:", LOG_CHECKER)

    def test_firewall_is_read_only_git(self):
        # The child's expanded reach (Bash) is the pilot's risk surface, so the
        # firewall must enumerate the allowed read-only git commands and forbid
        # every mutating one. A child that could commit/notes-add/reset would
        # defeat the point of keeping the parent read-only.
        self.assertIn("read-only", LOG_CHECKER.lower())
        # Allowed inspection commands named:
        for allowed in ("git log", "git notes show", "git diff"):
            self.assertIn(allowed, LOG_CHECKER, f"firewall must permit {allowed}")
        # Mutating commands explicitly forbidden:
        for forbidden in ("commit", "reset", "checkout", "rebase"):
            self.assertIn(forbidden, LOG_CHECKER, f"firewall must forbid {forbidden}")

    def test_uses_git_notes_attribution(self):
        # The attribution mechanism is conductor git notes (conductor.track_id),
        # the same source scripts/git-notes-query.py reads. Pin it so the method
        # stays grounded in the real attribution, not a re-derived heuristic.
        self.assertIn("conductor.track_id", LOG_CHECKER)
        self.assertIn("git notes", LOG_CHECKER)

    def test_scoped_to_handed_entries_only(self):
        # The child must not widen into a fresh audit — it checks exactly the
        # DOC_UPDATE entries doc-linter handed it.
        self.assertIn("ENTRIES", LOG_CHECKER)


class DocLinterNestingTests(unittest.TestCase):
    def test_doc_linter_has_agent_tool(self):
        # The nesting capability: doc-linter gains Agent (and ONLY Agent — Bash
        # stays out so the parent firewall holds; the git step is delegated).
        tools = _frontmatter_tools(DOC_LINTER)
        self.assertIn("Agent", tools)
        self.assertNotIn("Bash", tools,
                         "doc-linter must stay non-Bash; the git step is delegated")

    def test_doc_linter_is_the_only_agent_with_agent_tool(self):
        # Anti-proliferation guard: doc-linter is the deliberate, sole exception
        # to "subagents don't nest." If a second agent grows the Agent tool, that
        # is a design decision this test forces into the open rather than letting
        # nesting spread silently across the fleet.
        nested = []
        for path in sorted(AGENTS.glob("*.md")):
            if "Agent" in _frontmatter_tools(path.read_text(encoding="utf-8")):
                nested.append(path.name)
        self.assertEqual(
            ["doc-linter.md"], nested,
            f"unexpected agents with the Agent tool (doc-linter should be sole): {nested}",
        )

    def test_section_4_5_heading_preserved(self):
        # test_doc_linter_wiring.py requires the §4.x heading set to match the
        # canonical map exactly (4.5 → LOG_ISSUES). The delegation must keep the
        # §4.5 heading — it is NOT moved into the child.
        self.assertIn("4.5", _section_four_checks(DOC_LINTER))

    def test_section_4_5_dispatches_log_checker(self):
        # §4.5's git-needing step must delegate to log-checker via the canonical
        # dispatch form (Dispatch <agent>, prompt: + fenced block).
        self.assertIn("log-checker", DOC_LINTER)
        self.assertIn("Dispatch `log-checker`", DOC_LINTER)

    def test_section_7_constrains_agent_to_log_checker(self):
        # The Agent tool must be fenced into a single §4.5 log-checker dispatch
        # in the firewall, so the exception can't widen to arbitrary nesting.
        self.assertIn("Agent", DOC_LINTER)
        self.assertIn("log-checker", DOC_LINTER)


class HookWiringTests(unittest.TestCase):
    """The 3-way lockstep: agents/*.md ↔ SubagentStart matcher ↔ AGENT_REMINDERS.
    test_on_subagent_start.test_every_matched_agent_has_a_reminder and
    .test_every_subagent_is_in_the_subagentstart_matcher enforce this at the
    suite level; these assertions pin log-checker's specific entries."""

    def test_subagentstart_matcher_includes_log_checker(self):
        data = json.loads(HOOKS)
        matched = set()
        for entry in data["hooks"]["SubagentStart"]:
            matched.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertIn("log-checker", matched)

    def test_subagentstop_async_matcher_includes_log_checker(self):
        # log-checker is a leaf analysis child (emits a RESULT block, writes no
        # result.json). It belongs in the async / no-recovery-contract group with
        # doc-linter, skip-analyst, refuter — NOT the result-file or stdout-block
        # recovery groups.
        data = json.loads(HOOKS)
        stop_agents = set()
        for entry in data["hooks"]["SubagentStop"]:
            stop_agents.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertIn("log-checker", stop_agents)

    def test_on_subagent_start_reminder_registered(self):
        # CRITICAL coupling: on-subagent-start.py drops the safety floor entirely
        # for any matched agent NOT in AGENT_REMINDERS (the `if not reminder`
        # early-return). Adding log-checker to the matcher alone would silently
        # strip its floor — the reminder registration is load-bearing, not
        # cosmetic.
        self.assertIn('"log-checker"', ON_START)
        self.assertIn("---LOG CHECK RESULT---", ON_START)


if __name__ == "__main__":
    unittest.main()
