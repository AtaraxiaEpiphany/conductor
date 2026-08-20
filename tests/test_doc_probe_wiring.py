"""Wiring tests for the task-executor → doc-probe nested-subagent pilot (opt-in).

``task-executor`` loads N matching scoped design docs in Layer 0(b); on a small
context window that bulk read is the dominant consumer *before* implementation
starts. When opted in (task name ``[Probe]`` marker OR ``CONDUCTOR_TASK_FANOUT=1``),
Layer 0(c) replaces the direct reads with a fan-out: one ``doc-probe`` child per
matching doc (haiku, read-only), each returning a compact ``---PROBE RESULT---``
digest. The parent assembles N digests instead of reading N full docs.

This is the second task-executor nesting exception after §4.5
command-digester, and the third fleet-wide after doc-linter → command-digester
(PURPOSE: log-verify). doc-probe is a *child*
(haiku, no Agent tool) — it does NOT widen ``EXPECTED_AGENT_TOOL_AGENTS`` (still
``{doc-linter.md, task-executor.md}``). These tests lock: the child's read-only
haiku-leaf contract + result block; the parent's opt-in gate + canonical dispatch
form + firewall fence; and the 3-way hook lockstep (matcher ↔ reminder).
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
TASK_EXECUTOR = (AGENTS / "task-executor.md").read_text(encoding="utf-8")
DOC_PROBE = (AGENTS / "doc-probe.md").read_text(encoding="utf-8")
HOOKS = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
ON_START = (ROOT / "scripts" / "on-subagent-start.py").read_text(encoding="utf-8")


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


class DocProbeAgentTests(unittest.TestCase):
    def test_haiku_leaf_no_agent_tool(self):
        # The child is a tiered-down haiku leaf: it must NOT itself hold the Agent
        # tool (depth capped at 2 — no sub-sub-agent) and must stay read-only.
        # Crucially doc-probe is NOT an Agent-having agent, so it does not widen
        # EXPECTED_AGENT_TOOL_AGENTS in test_log_checker_wiring.py.
        self.assertEqual(_frontmatter_value(DOC_PROBE, "model"), "haiku")
        tools = _frontmatter_tools(DOC_PROBE)
        self.assertNotIn("Agent", tools, "doc-probe must not nest (depth ≤ 2)")
        for forbidden in ("Edit", "Write"):
            self.assertNotIn(forbidden, tools, f"doc-probe is read-only: no {forbidden}")
        # Read (the doc) + Grep/Glob (locate anchors) are required.
        for required in ("Read", "Grep", "Glob"):
            self.assertIn(required, tools, f"doc-probe missing tool: {required}")

    def test_maxturns_bounded(self):
        # A probe that reads one doc and digests it needs few turns; a high cap
        # would signal scope creep (surveying the corpus / implementing, which is
        # the parent's job).
        self.assertLessEqual(int(_frontmatter_value(DOC_PROBE, "maxTurns")), 15)

    def test_emits_structured_result_block(self):
        # filter-subagent-output keeps only the RESULT block; without one the
        # generic no-result advisory fires inside task-executor's context.
        self.assertIn("---PROBE RESULT---", DOC_PROBE)
        self.assertIn("---END RESULT---", DOC_PROBE)
        for field in ("STATUS:", "RELEVANCE:", "KEY_TYPES:", "ANCHORS:", "GOTCHAS:"):
            self.assertIn(field, DOC_PROBE, f"result block missing field: {field}")

    def test_status_includes_irrelevant(self):
        # A clean "this doc does not touch the task" is as useful as a hit — it
        # spares the parent a full read. The irrelevant path must be documented
        # (with NONE payload fields), not silently coerced to relevant.
        self.assertIn("irrelevant", DOC_PROBE.lower())

    def test_firewall_is_read_only_single_doc(self):
        # The child's only reach is Read; the firewall must forbid edits,
        # implementing, and widening beyond the one DOC_PATH (the parent dispatches
        # one probe per doc — a child that surveyed siblings would duplicate that).
        lower = DOC_PROBE.lower()
        self.assertIn("read-only", lower)
        for forbidden in ("edit", "implement", "fabricat"):
            self.assertIn(forbidden, lower, f"firewall must address {forbidden}")
        # Single-doc scope must be explicit.
        self.assertIn("one", lower)


class TaskExecutorNestingTests(unittest.TestCase):
    def test_section_3_0d_dispatches_doc_probe(self):
        # §3.0d (the opt-in doc-probe fan-out layer; track-findings is §3.0c)
        # must delegate to doc-probe via the canonical dispatch form, with
        # DOC_PATH + TASK_SCOPE in the fenced prompt.
        self.assertIn("0(d)", TASK_EXECUTOR)
        self.assertIn("Dispatch `doc-probe`", TASK_EXECUTOR)
        self.assertIn("DOC_PATH=", TASK_EXECUTOR)
        self.assertIn("TASK_SCOPE=", TASK_EXECUTOR)

    def test_opt_in_gate_documented(self):
        # The fan-out is opt-in — both triggers named so a default task never
        # silently spawns children. The gate must be the conjunction (probe marker
        # OR env), checked before fanning out.
        self.assertIn("[Probe]", TASK_EXECUTOR)
        self.assertIn("CONDUCTOR_TASK_FANOUT=1", TASK_EXECUTOR)

    def test_anti_pattern_guard_present(self):
        # Continuation is the parent's yield→stop→re-dispatch path, NEVER
        # spawn-child. The body must call this out so a future edit doesn't widen
        # doc-probe from "scoped read" into "do part of the task".
        self.assertIn("anti-pattern", TASK_EXECUTOR.lower())
        self.assertIn("continue", TASK_EXECUTOR.lower())

    def test_firewall_fences_agent_tool_to_both_children(self):
        # The Agent tool fence must name BOTH permitted children (§4.5
        # command-digester + §3.0d doc-probe) — the exception can't widen to
        # arbitrary nesting, and the doc-probe branch must be visibly gated,
        # not silent.
        for child in ("command-digester", "doc-probe"):
            self.assertIn(child, TASK_EXECUTOR,
                          f"firewall must name permitted child: {child}")


class HookWiringTests(unittest.TestCase):
    """The 3-way lockstep: agents/*.md ↔ SubagentStart matcher ↔ AGENT_REMINDERS.
    test_on_subagent_start enforces this at the suite level; these assertions pin
    doc-probe's specific entries."""

    def test_subagentstart_matcher_includes_doc_probe(self):
        data = json.loads(HOOKS)
        matched = set()
        for entry in data["hooks"]["SubagentStart"]:
            matched.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertIn("doc-probe", matched)

    def test_subagentstop_matcher_includes_doc_probe(self):
        # doc-probe is a leaf analysis child (emits a RESULT block, writes no
        # result.json) — like command-digester it belongs in a SubagentStop matcher
        # (async / no-recovery-contract), NOT the result-file or stdout-block
        # recovery sets. A forced recovery turn on a haiku read-only child would
        # waste budget, not save it.
        data = json.loads(HOOKS)
        stop_agents = set()
        for entry in data["hooks"]["SubagentStop"]:
            stop_agents.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertIn("doc-probe", stop_agents)

    def test_doc_probe_not_in_recovery_groups(self):
        # Mirror of the command-digester contract: doc-probe must NOT be admitted to
        # the recovery-contract groups (_RESULT_FILE_INSTRUCTIONS or
        # STDOUT_BLOCK_AGENTS in on-subagent-stop). It is a leaf dispatched by
        # task-executor; reliability is the parent's concern, and a forced recovery
        # turn on a haiku read-only child would waste budget. Asserted via source
        # text (the hook module has CLI side effects at import).
        import re
        ON_STOP = (ROOT / "scripts" / "on-subagent-stop.py").read_text(encoding="utf-8")
        # Captures dict keys of both _RESULT_FILE_INSTRUCTIONS and STDOUT_BLOCK_AGENTS
        # (each is `"name": (` at line start). doc-probe must not be one.
        keys = re.findall(r'^\s*"([a-z-]+)":\s*\(', ON_STOP, re.MULTILINE)
        self.assertNotIn("doc-probe", keys)

    def test_on_subagent_start_reminder_registered(self):
        # CRITICAL coupling: on-subagent-start drops the safety floor entirely for
        # any matched agent NOT in AGENT_REMINDERS. Adding doc-probe to the matcher
        # alone would silently strip its floor — the reminder registration is
        # load-bearing, not cosmetic.
        self.assertIn('"doc-probe"', ON_START)
        self.assertIn("---PROBE RESULT---", ON_START)


if __name__ == "__main__":
    unittest.main()
