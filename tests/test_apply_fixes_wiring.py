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
writes no result.json), so its agent-roster row carries ``recovery:
"stdout-block"`` (SubagentStop forces a recovery turn on a missing close tag).
These tests lock: the bounded-agent contract + result
block + firewall, and the 3-way hook lockstep + recovery-group membership.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
APPLY_FIXES = (AGENTS / "apply-fixes.md").read_text(encoding="utf-8")
HOOKS = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")


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
    """The 3-way lockstep: agents/*.md ↔ agent-roster row ↔ hook derivation,
    plus the recovery-group membership (apply-fixes is stdout-block)."""

    def test_apply_fixes_rostered_with_fence(self):
        # The roster row is load-bearing: an unrostered agent gets no
        # floor/reminder (the `if not reminder` early-return).
        sys.path.insert(0, str(ROOT / "scripts"))
        from track_state import agent_roster as ar
        reminder = ar.reminder_for("apply-fixes")
        self.assertIsNotNone(reminder)
        self.assertIn("---FIX RESULT---", reminder)

    def test_apply_fixes_is_stdout_block(self):
        # apply-fixes emits a RESULT block (no result.json) → its roster row
        # carries recovery: "stdout-block", so SubagentStop forces a recovery
        # turn if it stops without the close tag. The merged matcherless stop
        # entry is SYNC — the block decision actually lands.
        sys.path.insert(0, str(ROOT / "scripts"))
        from track_state import agent_roster as ar
        self.assertEqual(ar.recovery_kind_for("apply-fixes"), "stdout-block")
        self.assertNotIn("apply-fixes", ar.result_file_agents())
        self.assertIn("apply-fixes", ar.stdout_block_agents())

    def test_subagent_matchers_are_matcherless(self):
        # D5: both subagent matchers dropped their name alternations — the
        # roster gates, so apply-fixes reaches the hooks with the built-ins.
        data = json.loads(HOOKS)
        for event in ("SubagentStart", "SubagentStop"):
            for entry in data["hooks"][event]:
                self.assertNotIn("matcher", entry)

    def test_stdout_block_agent_recovery_instruction_registered(self):
        # The recovery contract pairs kind with instruction (the validator's
        # two-homes guard); an agent in the stdout-block set without an
        # instruction would block with an empty reason at recovery
        # time. apply-fixes must have an instruction.
        sys.path.insert(0, str(ROOT / "scripts"))
        from track_state import agent_roster as ar
        self.assertIn(
            "IMMEDIATELY print the ---FIX RESULT--- block",
            ar.recovery_instruction_for("apply-fixes"))

    def test_not_a_result_file_agent(self):
        # apply-fixes writes NO result.json (it is not a plan task) — it must
        # not be admitted to the roster's result-file recovery set. Only
        # task-executor + explorer are result-file agents.
        sys.path.insert(0, str(ROOT / "scripts"))
        from track_state import agent_roster as ar
        self.assertEqual(set(ar.result_file_agents()),
                         {"task-executor", "explorer"})


if __name__ == "__main__":
    unittest.main()
