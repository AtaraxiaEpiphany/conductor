"""Wiring tests for the task-executor → test-digester nested-subagent pilot.

``task-executor`` runs long TDD cycles (Steps 3-8); the dominant context consumer
across those turns is verbose test/coverage stdout. Step 3 (Red) and Step 6
(Coverage) delegate run-and-digest to ``test-digester`` (haiku, read-only), which
returns a compact ``---TEST DIGEST RESULT---`` block and keeps the noisy output in
its own sub-context. This is the second deliberate nesting exception after
doc-linter → log-checker, admitted via ``EXPECTED_AGENT_TOOL_AGENTS`` in
``test_log_checker_wiring.py``.

Coverage parsing is deterministic and shared: ``test-digester`` pipes captured
output through ``scripts/coverage-pct.py`` → ``lib.coverage.parse_coverage_percent``,
the same parser ``on-batch-complete.py``'s F3 probe uses. These tests lock the
agent wiring, the firewall fence, and the parser contract so the two call sites
can't drift.
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
SCRIPTS = ROOT / "scripts"
TASK_EXECUTOR = (AGENTS / "task-executor.md").read_text(encoding="utf-8")
TEST_DIGESTER = (AGENTS / "test-digester.md").read_text(encoding="utf-8")
HOOKS = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
ON_START = (ROOT / "scripts" / "on-subagent-start.py").read_text(encoding="utf-8")


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


class TestDigesterAgentTests(unittest.TestCase):
    def test_haiku_leaf_no_agent_tool(self):
        # The child is a tiered-down haiku leaf: it must NOT itself hold the Agent
        # tool (depth capped at 2 — no sub-sub-agent) and must stay read-only.
        self.assertEqual(_frontmatter_value(TEST_DIGESTER, "model"), "haiku")
        tools = _frontmatter_tools(TEST_DIGESTER)
        self.assertNotIn("Agent", tools, "test-digester must not nest (depth ≤ 2)")
        for forbidden in ("Edit", "Write"):
            self.assertNotIn(forbidden, tools, f"test-digester is read-only: no {forbidden}")
        # Bash (run the command) + Read (resolve dev-commands) are required.
        for required in ("Bash", "Read"):
            self.assertIn(required, tools, f"test-digester missing tool: {required}")

    def test_maxturns_bounded(self):
        # A digester that runs one command and parses it needs few turns; a high
        # cap would signal scope creep (retrying/fixing, which is the parent's job).
        self.assertLessEqual(int(_frontmatter_value(TEST_DIGESTER, "maxTurns")), 15)

    def test_emits_structured_result_block(self):
        # filter-subagent-output keeps only the RESULT block; without one the
        # generic no-result advisory fires inside task-executor's context.
        self.assertIn("---TEST DIGEST RESULT---", TEST_DIGESTER)
        self.assertIn("---END RESULT---", TEST_DIGESTER)
        for field in ("STATUS:", "COVERAGE_PCT:", "FAILING_TESTS:", "OUTPUT_TAIL:"):
            self.assertIn(field, TEST_DIGESTER, f"result block missing field: {field}")

    def test_uses_deterministic_coverage_parser(self):
        # Coverage % must come from the shared parser (coverage-pct.py), not be
        # eyeball-typed from the report — that is the whole point of a digester,
        # and an honest N/A (not fabrication) must be the documented miss path.
        self.assertIn("coverage-pct.py", TEST_DIGESTER)
        self.assertIn("N/A", TEST_DIGESTER)

    def test_firewall_is_read_only_no_fix(self):
        # The child's expanded reach (Bash) is the pilot's risk surface; the
        # firewall must forbid edits and "fixing" the failure (the parent's job).
        lower = TEST_DIGESTER.lower()
        self.assertIn("read-only", lower)
        for forbidden in ("retry", "fix"):
            self.assertIn(forbidden, lower, f"firewall must address {forbidden}")


class TaskExecutorNestingTests(unittest.TestCase):
    def test_task_executor_has_agent_tool(self):
        # The nesting capability: task-executor gains Agent for the §4.5 dispatch.
        tools = _frontmatter_tools(TASK_EXECUTOR)
        self.assertIn("Agent", tools)

    def test_section_4_5_dispatches_test_digester(self):
        # §4.5 must delegate to test-digester via the canonical dispatch form, for
        # both PURPOSE=red (Step 3) and PURPOSE=coverage (Step 6).
        self.assertIn("4.5", TASK_EXECUTOR)
        self.assertIn("Dispatch `test-digester`", TASK_EXECUTOR)
        self.assertIn("PURPOSE=red", TASK_EXECUTOR)
        self.assertIn("PURPOSE=coverage", TASK_EXECUTOR)

    def test_firewall_fences_agent_tool_to_test_digester(self):
        # The Agent tool must be fenced into §4.5 test-digester dispatches only —
        # the exception can't widen to arbitrary nesting.
        self.assertIn("test-digester", TASK_EXECUTOR)
        self.assertIn("only", TASK_EXECUTOR.lower())

    def test_maxturns_trimmed(self):
        # The digester absorbs the verbose test output that justified 70 turns;
        # the cap must stay well below that so the parent doesn't buffer output
        # it no longer holds. Bumped from 48→64 deliberately: the PreToolUse
        # tripwire (on-pre-tool-tripwire.py) now code-enforces shutdown at ~38
        # rounds, so extra turns are happy-path headroom, not buffering risk.
        # Guard against an unjustified drift back toward the old 70.
        self.assertLess(int(_frontmatter_value(TASK_EXECUTOR, "maxTurns")), 70)


class HookWiringTests(unittest.TestCase):
    """The 3-way lockstep: agents/*.md ↔ SubagentStart matcher ↔ AGENT_REMINDERS.
    test_on_subagent_start enforces this at the suite level; these assertions pin
    test-digester's specific entries."""

    def test_subagentstart_matcher_includes_test_digester(self):
        data = json.loads(HOOKS)
        matched = set()
        for entry in data["hooks"]["SubagentStart"]:
            matched.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertIn("test-digester", matched)

    def test_subagentstop_matcher_includes_test_digester(self):
        # test-digester is a leaf analysis child (emits a RESULT block, writes no
        # result.json) — like log-checker it belongs in a SubagentStop matcher
        # (async / no-recovery-contract), NOT the result-file recovery set.
        data = json.loads(HOOKS)
        stop_agents = set()
        for entry in data["hooks"]["SubagentStop"]:
            stop_agents.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertIn("test-digester", stop_agents)

    def test_on_subagent_start_reminder_registered(self):
        # CRITICAL coupling: on-subagent-start drops the safety floor entirely for
        # any matched agent NOT in AGENT_REMINDERS. Adding test-digester to the
        # matcher alone would silently strip its floor — the reminder registration
        # is load-bearing, not cosmetic.
        self.assertIn('"test-digester"', ON_START)
        self.assertIn("---TEST DIGEST RESULT---", ON_START)


class CoverageParserTests(unittest.TestCase):
    """The shared per-language parser (lib.coverage.parse_coverage_percent) — the
    deterministic contract both test-digester (via coverage-pct.py) and the F3
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
