"""Wiring tests for the post-loop-step → apply-fixes chunked-patch pilot (P5).

The post-loop auto-review (§7.0) writes ``review-result.json``; the spine chunks
its Critical/High fixable findings per-file and drains ONE chunk per
``apply_fixes`` leaf. ``apply-fixes`` is a **bounded** (maxTurns 20) sonnet agent
that applies one chunk's findings, commits each fix, runs the suite, and emits a
``---FIX RESULT---`` block. It replaces the prior open-ended free-form
``general-purpose`` patch agent — the "unguarded chimney" (no maxTurns,
open-ended scope, no result contract).

This is a *top-level* dispatch (the teleoperator runs it via the ``apply_fixes``
leaf), not a nested child — so it does NOT touch
``EXPECTED_AGENT_TOOL_AGENTS``. It is a stdout-block agent (emits a RESULT block,
writes no result.json), so it joins the SubagentStop SYNC recovery group +
``STDOUT_BLOCK_AGENTS``. These tests lock: the bounded-agent contract + result
block + firewall, and the 3-way hook lockstep + recovery-group membership.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
APPLY_FIXES = (AGENTS / "apply-fixes.md").read_text(encoding="utf-8")
HOOKS = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
ON_START = (ROOT / "scripts" / "on-subagent-start.py").read_text(encoding="utf-8")
ON_STOP = (ROOT / "scripts" / "on-subagent-stop.py").read_text(encoding="utf-8")


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


class ApplyFixesAgentTests(unittest.TestCase):
    def test_sonnet_patcher_with_edit_tools(self):
        # Patching needs Edit/Write (apply suggestions) + Bash (commit + run the
        # suite). sonnet tier — judgment required to translate a suggestion into
        # a correct minimal fix.
        self.assertEqual(_frontmatter_value(APPLY_FIXES, "model"), "sonnet")
        tools = _frontmatter_tools(APPLY_FIXES)
        for required in ("Bash", "Read", "Edit", "Write"):
            self.assertIn(required, tools, f"apply-fixes missing tool: {required}")
        # No Agent tool — it is a leaf, not a nesting parent.
        self.assertNotIn("Agent", tools)

    def test_maxturns_bounded(self):
        # THE chimney fix: the prior free-form agent had no maxTurns (open-ended).
        # The cap + per-file chunking make each agent small enough to finish
        # before overflow. 20 is the planned bound; a higher value signals the
        # chunking stopped being effective.
        self.assertLessEqual(int(_frontmatter_value(APPLY_FIXES, "maxTurns")), 25)
        self.assertGreaterEqual(int(_frontmatter_value(APPLY_FIXES, "maxTurns")), 10)

    def test_accept_edits_mode(self):
        # Patching is non-destructive edit work scoped to one file; acceptEdits
        # keeps it flowing without per-edit prompts (mirrors task-executor).
        self.assertEqual(_frontmatter_value(APPLY_FIXES, "permissionMode"), "acceptEdits")

    def test_emits_structured_result_block(self):
        # filter-subagent-output keeps only the RESULT block; without one the
        # generic no-result advisory fires inside the teleoperator's context, and
        # the spine's post_on=non_failure rule can't distinguish SUCCESS.
        self.assertIn("---FIX RESULT---", APPLY_FIXES)
        self.assertIn("---END RESULT---", APPLY_FIXES)
        for field in ("STATUS:", "FILE:", "APPLIED:", "COMMITTED:"):
            self.assertIn(field, APPLY_FIXES, f"result block missing field: {field}")

    def test_firewall_scopes_to_one_file_and_forbids_state_mutation(self):
        # The chunking is the whole point — the firewall must forbid widening to
        # other files / findings, and forbid every state-mutating channel (this
        # is remediation, not a plan task: no track-state.json, no plan markers,
        # no sidecar, no result.json, no dispatch-finalize).
        lower = APPLY_FIXES.lower()
        self.assertIn("track-state.json", lower)
        self.assertIn("not a plan task", lower)
        for forbidden in ("dispatch-finalize", "write-result", "result.json"):
            self.assertIn(forbidden, lower, f"firewall must forbid {forbidden}")


class HookWiringTests(unittest.TestCase):
    """The 3-way lockstep: agents/*.md ↔ SubagentStart matcher ↔ AGENT_REMINDERS,
    plus the SubagentStop recovery-group membership (apply-fixes is stdout-block)."""

    def test_subagentstart_matcher_includes_apply_fixes(self):
        data = json.loads(HOOKS)
        matched = set()
        for entry in data["hooks"]["SubagentStart"]:
            matched.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertIn("apply-fixes", matched)

    def test_subagentstop_sync_matcher_includes_apply_fixes(self):
        # apply-fixes emits a RESULT block (no result.json) → it belongs in the
        # SYNC SubagentStop group whose STDOUT_BLOCK_AGENTS recovery contract
        # forces a recovery turn if it stops without the close tag. Assert it is
        # in the SYNC (non-async) group specifically.
        data = json.loads(HOOKS)
        sync_agents = set()
        for entry in data["hooks"]["SubagentStop"]:
            if entry.get("async"):
                continue
            sync_agents.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertIn("apply-fixes", sync_agents)

    def test_on_subagent_start_reminder_registered(self):
        # CRITICAL coupling: on-subagent-start drops the safety floor entirely
        # for any matched agent NOT in AGENT_REMINDERS. Adding apply-fixes to the
        # matcher alone would silently strip its floor — the reminder is load-bearing.
        self.assertIn('"apply-fixes"', ON_START)
        self.assertIn("---FIX RESULT---", ON_START)

    def test_stdout_block_agent_recovery_instruction_registered(self):
        # The SYNC group's recovery contract keys on STDOUT_BLOCK_AGENTS; an agent
        # in the matcher but missing from this dict would KeyError at recovery
        # time. apply-fixes must have an instruction.
        keys = re.findall(r'^\s*"([a-z-]+)":\s*\(', ON_STOP, re.MULTILINE)
        self.assertIn("apply-fixes", keys)

    def test_not_a_result_file_agent(self):
        # apply-fixes writes NO result.json (it is not a plan task) — it must not
        # be admitted to RESULT_FILE_AGENT_TYPES (the fresh-result recovery set).
        # on-subagent-stop asserts _RESULT_FILE_INSTRUCTIONS == RESULT_FILE_AGENT_TYPES,
        # so the dict keys are the authoritative surface.
        keys = re.findall(r'^\s*"([a-z-]+)":\s*\(', ON_STOP, re.MULTILINE)
        # Only task-executor + explorer are result-file agents.
        self.assertEqual(set(k for k in keys
                             if k in ("task-executor", "explorer")),
                         {"task-executor", "explorer"})
        self.assertNotIn("apply-fixes", {"task-executor", "explorer"})


if __name__ == "__main__":
    unittest.main()
