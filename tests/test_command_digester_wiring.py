"""Wiring tests for the nested-subagent pilots and their shared child,
``command-digester``.

A Conductor subagent is normally a leaf (no ``Agent`` tool) — bulk output is
already kept out of the orchestrator's context by ``filter-subagent-output``.
Nesting is a deliberate, fenced exception, each parent delegating bounded
command work to one tightly-scoped child whose own context absorbs the noise.
The roster merge (design Phase C) collapsed the two former children —
``test-digester`` and ``log-checker`` — into ONE agent keyed on ``PURPOSE``:

- **task-executor → command-digester (``PURPOSE=red`` / ``PURPOSE=coverage``)** —
  task-executor runs long TDD cycles (Steps 3-8); the dominant context consumer
  is verbose test/coverage stdout. Step 3 (Red) and Step 6 (Coverage) delegate
  run-and-digest, receiving a compact ``---TEST DIGEST RESULT---`` block.
- **doc-linter → command-digester (``PURPOSE: log-verify``)** — doc-linter is
  read-only (``Read, Grep, Glob`` — no ``Bash``), yet its §4.5 *Log Consistency*
  check must inspect git history. Rather than widen doc-linter's firewall with
  ``Bash``, the git-needing step is delegated, receiving a
  ``---LOG CHECK RESULT---`` block.

Both parents are gated by ``EXPECTED_AGENT_TOOL_AGENTS`` below — the
anti-proliferation allowlist — so a third Agent-having agent is a design
decision forced into the open rather than silent spread. Each parent body must
constrain the ``Agent`` tool to its one documented child dispatch.

Coverage parsing is deterministic and shared: the ``red``/``coverage`` purposes
pipe captured output through ``scripts/coverage-pct.py`` →
``lib.coverage.parse_coverage_percent``, the same parser the F3 probe in
``on-batch-complete.py`` uses. These tests lock the agent wiring, the firewall
fence, and the parser contract so the call sites can't drift.
"""
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
SCRIPTS = ROOT / "scripts"
TASK_EXECUTOR = (AGENTS / "task-executor.md").read_text(encoding="utf-8")
DOC_LINTER = (AGENTS / "doc-linter.md").read_text(encoding="utf-8")
COMMAND_DIGESTER = (AGENTS / "command-digester.md").read_text(encoding="utf-8")
HOOKS = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")

# Anti-proliferation allowlist: the only agents that may hold the ``Agent`` tool
# (i.e. dispatch a nested child). Each entry is a fenced, single-purpose exception
# to "subagents are leaves" — adding a third is a design decision that should edit
# this set explicitly (with a nesting rationale: capability gap or bulk-output
# isolation, a haiku-tier child, depth ≤ 2, and a firewall fence pinning the
# Agent tool to one dispatch) rather than spread silently. The child itself is
# NOT in this set — command-digester must stay a leaf (depth ≤ 2).
EXPECTED_AGENT_TOOL_AGENTS = {"doc-linter.md", "task-executor.md"}


def _load_coverage_module():
    # Load scripts/lib/coverage.py from its file path so the test does not depend
    # on scripts/ being on sys.path (it isn't under pytest). The module is pure
    # stdlib (re/pathlib/typing), so standalone exec is safe.
    spec = importlib.util.spec_from_file_location(
        "_conductor_coverage", SCRIPTS / "lib" / "coverage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cov = _load_coverage_module()
parse_coverage_percent = _cov.parse_coverage_percent
detect_project_type = _cov.detect_project_type


def _frontmatter_value(agent_text: str, key: str) -> str:
    in_fm = False
    for line in agent_text.splitlines():
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm and line.startswith(key + ":"):
            return line.split(key + ":", 1)[1].strip()
    return ""


def _frontmatter_tools(agent_text: str) -> str:
    return _frontmatter_value(agent_text, "tools")


def _section_four_checks(text):
    """Set of §4.x check ids defined (### headings), e.g. '4.5'."""
    return {m.group(1) for m in re.finditer(r"^### (4\.\d+)\s", text, re.MULTILINE)}


class CommandDigesterAgentTests(unittest.TestCase):
    def test_haiku_leaf_no_agent_tool(self):
        # The child is a tiered-down haiku leaf: it must NOT itself hold the Agent
        # tool (depth capped at 2 — no sub-sub-agent) and must stay read-only.
        self.assertEqual(_frontmatter_value(COMMAND_DIGESTER, "model"), "haiku")
        tools = _frontmatter_tools(COMMAND_DIGESTER)
        self.assertNotIn("Agent", tools,
                         "command-digester must not nest (depth ≤ 2)")
        for forbidden in ("Edit", "Write"):
            self.assertNotIn(forbidden, tools,
                             f"command-digester is read-only: no {forbidden}")
        # Bash (run the workload) + Read (resolve dev-commands) are required —
        # the merged profile serves both the test purposes and log-verify.
        for required in ("Bash", "Read", "Grep", "Glob"):
            self.assertIn(required, tools,
                          f"command-digester missing tool: {required}")

    def test_maxturns_bounded(self):
        # A digester that runs one workload and parses it needs few turns; a high
        # cap would signal scope creep (retrying/fixing, which is the parent's job).
        self.assertLessEqual(int(_frontmatter_value(COMMAND_DIGESTER, "maxTurns")), 15)

    def test_dispatches_on_purpose_all_three_documented(self):
        # The ONE-AGENT merge is keyed on PURPOSE: the body must document all
        # three purposes and dispatch on that key, not on the parent's identity.
        for purpose in ("red", "coverage", "log-verify"):
            self.assertIn(purpose, COMMAND_DIGESTER,
                          f"PURPOSE={purpose} not documented in the merged agent")
        self.assertIn("Dispatch on `PURPOSE`", COMMAND_DIGESTER)

    def test_emits_both_structured_result_blocks(self):
        # filter-subagent-output keeps only a RESULT block; without one the
        # generic no-result advisory fires inside the parent's context. Each
        # purpose family keeps its own block format (parents parse the fields).
        self.assertIn("---TEST DIGEST RESULT---", COMMAND_DIGESTER)
        self.assertIn("---LOG CHECK RESULT---", COMMAND_DIGESTER)
        self.assertIn("---END RESULT---", COMMAND_DIGESTER)
        for field in ("STATUS:", "COVERAGE_PCT:", "FAILING_TESTS:", "OUTPUT_TAIL:"):
            self.assertIn(field, COMMAND_DIGESTER,
                          f"test-digest block missing field: {field}")
        self.assertIn("MISMATCHES:", COMMAND_DIGESTER)

    def test_uses_deterministic_coverage_parser(self):
        # Coverage % must come from the shared parser (coverage-pct.py), not be
        # eyeball-typed from the report — that is the whole point of a digester,
        # and an honest N/A (not fabrication) must be the documented miss path.
        self.assertIn("coverage-pct.py", COMMAND_DIGESTER)
        self.assertIn("N/A", COMMAND_DIGESTER)

    def test_firewall_is_read_only_no_fix(self):
        # The child's expanded reach (Bash) is the pilot's risk surface; the
        # firewall must forbid edits and "fixing" the failure (the parent's job),
        # and pin log-verify to read-only git inspection.
        lower = COMMAND_DIGESTER.lower()
        self.assertIn("read-only", lower)
        for forbidden in ("retry", "fix"):
            self.assertIn(forbidden, lower, f"firewall must address {forbidden}")
        for allowed in ("git log", "git notes show", "git diff"):
            self.assertIn(allowed, COMMAND_DIGESTER,
                          f"firewall must permit {allowed}")
        for forbidden in ("commit", "reset", "checkout", "rebase"):
            self.assertIn(forbidden, COMMAND_DIGESTER,
                          f"firewall must forbid {forbidden}")

    def test_uses_git_notes_attribution(self):
        # The log-verify attribution mechanism is conductor git notes
        # (conductor.track_id), the same source scripts/git-notes-query.py reads.
        # Pin it so the method stays grounded in the real attribution, not a
        # re-derived heuristic.
        self.assertIn("conductor.track_id", COMMAND_DIGESTER)
        self.assertIn("git notes", COMMAND_DIGESTER)

    def test_scoped_to_handed_assignment_only(self):
        # The child must not widen into a fresh audit — it checks exactly the
        # entries/assignment the parent handed it.
        self.assertIn("ENTRIES", COMMAND_DIGESTER)


class TaskExecutorNestingTests(unittest.TestCase):
    def test_task_executor_has_agent_tool(self):
        # The nesting capability: task-executor gains Agent for the §4.5 dispatch.
        tools = _frontmatter_tools(TASK_EXECUTOR)
        self.assertIn("Agent", tools)

    def test_section_4_5_dispatches_command_digester(self):
        # §4.5 must delegate to command-digester via the canonical dispatch form,
        # for both PURPOSE=red (Step 3) and PURPOSE=coverage (Step 6).
        self.assertIn("4.5", TASK_EXECUTOR)
        self.assertIn("Dispatch `command-digester`", TASK_EXECUTOR)
        self.assertIn("PURPOSE=red", TASK_EXECUTOR)
        self.assertIn("PURPOSE=coverage", TASK_EXECUTOR)

    def test_firewall_fences_agent_tool_to_command_digester(self):
        # The Agent tool must be fenced into §4.5 command-digester dispatches only
        # — the exception can't widen to arbitrary nesting.
        self.assertIn("command-digester", TASK_EXECUTOR)
        self.assertIn("only", TASK_EXECUTOR.lower())

    def test_maxturns_trimmed(self):
        # The digester absorbs the verbose test output that justified 70 turns;
        # the cap must stay well below that so the parent doesn't buffer output
        # it no longer holds. Bumped from 48→64 deliberately: the PreToolUse
        # tripwire (on-pre-tool-tripwire.py) now code-enforces shutdown at ~38
        # rounds, so extra turns are happy-path headroom, not buffering risk.
        # Guard against an unjustified drift back toward the old 70.
        self.assertLess(int(_frontmatter_value(TASK_EXECUTOR, "maxTurns")), 70)


class DocLinterNestingTests(unittest.TestCase):
    def test_doc_linter_has_agent_tool(self):
        # The nesting capability: doc-linter gains Agent (and ONLY Agent — Bash
        # stays out so the parent firewall holds; the git step is delegated).
        tools = _frontmatter_tools(DOC_LINTER)
        self.assertIn("Agent", tools)
        self.assertNotIn("Bash", tools,
                         "doc-linter must stay non-Bash; the git step is delegated")

    def test_only_allowlisted_agents_have_agent_tool(self):
        # Anti-proliferation guard: the Agent tool (nesting capability) is an
        # opt-in allowlist, not a default. A new Agent-having agent is a design
        # decision this test forces into the open — add the file to
        # EXPECTED_AGENT_TOOL_AGENTS (module-level, with a nesting rationale)
        # rather than letting nesting spread silently across the fleet.
        nested = set()
        for path in sorted(AGENTS.glob("*.md")):
            if "Agent" in _frontmatter_tools(path.read_text(encoding="utf-8")):
                nested.add(path.name)
        self.assertEqual(
            EXPECTED_AGENT_TOOL_AGENTS, nested,
            f"unexpected agents with the Agent tool "
            f"(allowlist = {sorted(EXPECTED_AGENT_TOOL_AGENTS)}): {sorted(nested)}",
        )

    def test_section_4_5_heading_preserved(self):
        # test_doc_linter_wiring.py requires the §4.x heading set to match the
        # canonical map exactly (4.5 → LOG_ISSUES). The delegation must keep the
        # §4.5 heading — it is NOT moved into the child.
        self.assertIn("4.5", _section_four_checks(DOC_LINTER))

    def test_section_4_5_dispatches_command_digester_log_verify(self):
        # §4.5's git-needing step must delegate to command-digester via the
        # canonical dispatch form, and the merged agent dispatches on PURPOSE —
        # the prompt MUST carry PURPOSE: log-verify (a prompt without it leaves
        # the child without a workload selector).
        self.assertIn("Dispatch `command-digester`", DOC_LINTER)
        self.assertIn("PURPOSE: log-verify", DOC_LINTER)

    def test_section_7_constrains_agent_to_command_digester(self):
        # The Agent tool must be fenced into a single §4.5 command-digester
        # dispatch in the firewall, so the exception can't widen to arbitrary
        # nesting.
        self.assertIn("Agent", DOC_LINTER)
        self.assertIn("command-digester", DOC_LINTER)


class HookWiringTests(unittest.TestCase):
    """The 3-way lockstep: agents/*.md ↔ agent-roster row ↔ hook derivation.
    test_on_subagent_start enforces this at the suite level; these assertions
    pin command-digester's specific entries."""

    def test_command_digester_rostered_with_purpose_keyed_fence(self):
        # The roster row is load-bearing, not cosmetic: an unrostered agent
        # gets no floor/reminder (the `if not reminder` early-return). The
        # fence is PURPOSE-keyed — it must name BOTH result block formats.
        sys.path.insert(0, str(ROOT / "scripts"))
        from track_state import agent_roster as ar
        reminder = ar.reminder_for("command-digester")
        self.assertIsNotNone(reminder)
        self.assertIn("---TEST DIGEST RESULT---", reminder)
        self.assertIn("---LOG CHECK RESULT---", reminder)

    def test_command_digester_has_no_recovery_contract(self):
        # command-digester is a leaf analysis child (emits a RESULT block,
        # writes no result.json). It belongs to the no-recovery-contract rows —
        # NOT the result-file or stdout-block recovery sets.
        sys.path.insert(0, str(ROOT / "scripts"))
        from track_state import agent_roster as ar
        self.assertEqual(ar.recovery_kind_for("command-digester"), "none")
        self.assertNotIn("command-digester", ar.result_file_agents())
        self.assertNotIn("command-digester", ar.stdout_block_agents())

    def test_subagent_matchers_are_matcherless(self):
        # Both subagent matchers dropped their name alternations (the roster
        # gates) — command-digester reaches the hooks with the built-ins.
        data = json.loads(HOOKS)
        for event in ("SubagentStart", "SubagentStop"):
            for entry in data["hooks"][event]:
                self.assertNotIn("matcher", entry)

    def test_merged_agent_names_gone_from_roster(self):
        # The merge must not leave the dead names in the roster — a stale row
        # claims a scaffold for an agent file that is gone.
        sys.path.insert(0, str(ROOT / "scripts"))
        from track_state import agent_roster as ar
        names = set(ar.merged_agent_names())
        self.assertNotIn("test-digester", names)
        self.assertNotIn("log-checker", names)


class CoverageParserTests(unittest.TestCase):
    """The shared per-language parser (lib.coverage.parse_coverage_percent) — the
    deterministic contract both command-digester (via coverage-pct.py) and the F3
    server-side probe depend on. Pinned so a heuristic tweak can't silently drift
    between the two call sites."""

    def test_python_total_line(self):
        out = "Name    Stmts   Miss\nmodule.py   10    1\nTOTAL    10    1    90.00%\n"
        self.assertEqual(parse_coverage_percent(out, "python"), 90.0)

    def test_go_coverage_line(self):
        out = "ok  pkg 0.1s  coverage: 87.5% of statements\n"
        self.assertEqual(parse_coverage_percent(out, "go"), 87.5)

    def test_node_all_files_line(self):
        # Jest "All files" row — preserved behavior takes the first number on the
        # matching line.
        out = "File     % Stmts\nfoo.js    100\nAll files | 94 | 80 |\n"
        self.assertEqual(parse_coverage_percent(out, "node"), 94.0)

    def test_unknown_project_type_returns_none(self):
        self.assertIsNone(parse_coverage_percent("TOTAL 100%", "rust"))

    def test_empty_or_unparseable_returns_none(self):
        self.assertIsNone(parse_coverage_percent("", "python"))
        self.assertIsNone(parse_coverage_percent("no numbers here at all", "python"))

    def test_detect_project_type_none_when_no_markers(self):
        # In a dir with no project markers, detect returns None (the digester then
        # requires --lang explicitly). Use the plugin's own dir (no setup.py /
        # package.json / go.mod / pyproject.toml at repo root).
        self.assertIsNone(detect_project_type(ROOT))


if __name__ == "__main__":
    unittest.main()
