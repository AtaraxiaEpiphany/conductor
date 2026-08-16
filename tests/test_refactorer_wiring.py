"""Wiring tests for the orchestrator-dispatched refactorer agent (tactical tier).

The plugin has TWO refactor tiers, by design:
- **Mechanical** — task-executor's inline Step 5 (default-on lint-fix on the
  diff), runs inside the executor's small-window context (pinned by
  test_refactor_contract_wiring.py / test_workflow_doc_wiring.py).
- **Tactical** — THIS agent. Deeper target-bearing refactor (extract duplication
  the task introduced, reduce complexity in new code) dispatched by
  conductor:implement at the opt-in [Refactor] seam (§3.6c), in its OWN window so
  it does not tax the executor's 38-round tripwire. It mirrors apply-fixes
  (bounded mutating patcher, no Agent tool, own maxTurns, own commits, compact
  ---REFACTOR RESULT--- block) and the §3.6b [Review] seam (opt-in name marker +
  env, dispatch after a successful finalize, parse result block, announce, proceed).

This is a *top-level* dispatch (the orchestrator runs it via the refactorer leaf),
not a nested child — so it does NOT touch EXPECTED_AGENT_TOOL_AGENTS. It is a
stdout-block agent (emits a RESULT block, writes no result.json), so it joins the
SubagentStop SYNC recovery group + STDOUT_BLOCK_AGENTS. These tests lock: the
bounded-agent contract + result block + firewall, the 3-way hook lockstep +
recovery-group membership, and the §3.6c seam.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
REFACTORER = (AGENTS / "refactorer.md").read_text(encoding="utf-8")
HOOKS = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
ON_START = (ROOT / "scripts" / "on-subagent-start.py").read_text(encoding="utf-8")
ON_STOP = (ROOT / "scripts" / "on-subagent-stop.py").read_text(encoding="utf-8")
SKILL = (ROOT / "skills" / "implement" / "SKILL.md").read_text(encoding="utf-8")
RECOVERY = (ROOT / "scripts" / "lib" / "recovery.py").read_text(encoding="utf-8")


def _frontmatter_value(agent_text: str, key: str) -> str:
    in_fm = False
    for line in agent_text.splitlines():
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm and line.startswith(key + ":"):
            return line.split(key + ":", 1)[1].strip()
    return ""


class RefactorerAgentTests(unittest.TestCase):
    def test_sonnet_patcher_with_edit_tools(self):
        # Tactical refactor needs Edit/Write (apply the refactor) + Bash (commit +
        # run the suite) + Grep/Glob (locate duplication in the diff). sonnet tier
        # — judgment required to state a target and apply a correct minimal refactor.
        self.assertEqual(_frontmatter_value(REFACTORER, "model"), "sonnet")
        tools = _frontmatter_value(REFACTORER, "tools")
        for required in ("Bash", "Read", "Edit", "Write", "Grep", "Glob"):
            self.assertIn(required, tools, f"refactorer missing tool: {required}")
        # No Agent tool — it is a leaf, not a nesting parent.
        self.assertNotIn("Agent", tools)

    def test_maxturns_bounded(self):
        # Mirrors apply-fixes: the cap keeps the agent small enough to finish
        # before overflow. 20 is the planned bound; a higher value signals the
        # diff-scoping stopped being effective.
        self.assertGreaterEqual(int(_frontmatter_value(REFACTORER, "maxTurns")), 10)
        self.assertLessEqual(int(_frontmatter_value(REFACTORER, "maxTurns")), 25)

    def test_accept_edits_mode(self):
        # Refactor is non-destructive edit work scoped to the task's diff;
        # acceptEdits keeps it flowing without per-edit prompts (mirrors apply-fixes).
        self.assertEqual(_frontmatter_value(REFACTORER, "permissionMode"), "acceptEdits")

    def test_emits_structured_result_block(self):
        # filter-subagent-output keeps only the RESULT block; without one the
        # generic no-result advisory fires in the orchestrator's context, and
        # §3.6c's STATUS-based announce can't distinguish SUCCESS from FAILURE.
        self.assertIn("---REFACTOR RESULT---", REFACTORER)
        self.assertIn("---END RESULT---", REFACTORER)
        for field in ("STATUS:", "COMMITTED:", "REFACTORED:", "SKIPPED:"):
            self.assertIn(field, REFACTORER, f"result block missing field: {field}")

    def test_firewall_scopes_to_revision_range_and_forbids_state_mutation(self):
        # Refactor is diff-scoped to REVISION_RANGE and must forbid every
        # state-mutating channel (this is improvement, not a plan task: no
        # track-state.json, no plan markers, no sidecar, no result.json,
        # no dispatch-finalize).
        lower = REFACTORER.lower()
        self.assertIn("track-state.json", lower)
        self.assertIn("not a plan task", lower)
        for forbidden in ("dispatch-finalize", "write-result", "result.json"):
            self.assertIn(forbidden, lower, f"firewall must forbid {forbidden}")

    def test_behavior_preserving_mandate(self):
        # The defining invariant: a public-API/behavior change is NOT refactor
        # (it is Step 7's lane) and must be skipped, not applied.
        self.assertIn("behavior-preserving", REFACTORER.lower())


class HookWiringTests(unittest.TestCase):
    """The 3-way lockstep: agents/*.md ↔ SubagentStart matcher ↔ AGENT_REMINDERS,
    plus the SubagentStop SYNC recovery-group membership (refactorer is stdout-block)."""

    def test_subagentstart_matcher_includes_refactorer(self):
        data = json.loads(HOOKS)
        matched = set()
        for entry in data["hooks"]["SubagentStart"]:
            matched.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertIn("refactorer", matched)

    def test_subagentstop_sync_matcher_includes_refactorer(self):
        # refactorer emits a RESULT block (no result.json) → SYNC group, whose
        # STDOUT_BLOCK_AGENTS recovery contract forces a recovery turn if it stops
        # without the close tag. Assert it is in the SYNC (non-async) group.
        data = json.loads(HOOKS)
        sync_agents = set()
        for entry in data["hooks"]["SubagentStop"]:
            if entry.get("async"):
                continue
            sync_agents.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertIn("refactorer", sync_agents)

    def test_subagentstop_async_matcher_excludes_refactorer(self):
        # It is a stdout-block agent with a recovery contract → SYNC, NOT async.
        data = json.loads(HOOKS)
        async_agents = set()
        for entry in data["hooks"]["SubagentStop"]:
            if not entry.get("async"):
                continue
            async_agents.update(a.strip() for a in entry["matcher"].split("|"))
        self.assertNotIn("refactorer", async_agents)

    def test_on_subagent_start_reminder_registered(self):
        # CRITICAL coupling: on-subagent-start drops the safety floor entirely
        # for any matched agent NOT in AGENT_REMINDERS. Adding refactorer to the
        # matcher alone would silently strip its floor — the reminder is load-bearing.
        self.assertIn('"refactorer"', ON_START)
        self.assertIn("---REFACTOR RESULT---", ON_START)

    def test_stdout_block_agent_recovery_instruction_registered(self):
        # The SYNC group's recovery contract keys on STDOUT_BLOCK_AGENTS; an agent
        # in the matcher but missing from this dict would KeyError at recovery time.
        keys = re.findall(r'^\s*"([a-z-]+)":\s*\(', ON_STOP, re.MULTILINE)
        self.assertIn("refactorer", keys)

    def test_not_a_result_file_agent(self):
        # refactorer writes NO result.json (it is not a plan task) — it must not
        # be admitted to RESULT_FILE_AGENT_TYPES (the fresh-result recovery set),
        # whose authorship is lib/recovery.py and must stay exactly {task-executor,
        # explorer} (the on-subagent-stop assert guards _RESULT_FILE_INSTRUCTIONS
        # parity with this set).
        m = re.search(r"RESULT_FILE_AGENT_TYPES\s*=\s*frozenset\(\{([^}]*)\}\)", RECOVERY)
        self.assertIsNotNone(m, "RESULT_FILE_AGENT_TYPES definition not found")
        members = {s.strip().strip('"').strip("'") for s in m.group(1).split(",") if s.strip()}
        self.assertEqual(members, {"task-executor", "explorer"})


class RefactorSeamTests(unittest.TestCase):
    """§3.6c — the opt-in [Refactor] seam in conductor:implement, mirroring §3.6b."""

    def test_section_heading_present(self):
        self.assertIn("### 3.6c", SKILL)
        self.assertIn("Tactical Refactor", SKILL)

    def test_opt_in_marker_documented(self):
        # [Refactor] name marker + the env global opt-in.
        self.assertIn("[Refactor]", SKILL)
        self.assertIn("CONDUCTOR_TASK_REFACTOR", SKILL)

    def test_name_marker_not_a_tag(self):
        # Mirrors §3.6b's [Review] wording: a name marker does NOT enter the
        # [Docs]/[Config]/… exemption logic, so a refactorable task still owes
        # TDD (F2) + coverage (F3).
        self.assertIn("name marker, not a tag", SKILL)

    def test_dispatch_form(self):
        # Dispatched as conductor:refactorer with TRACK_DIR + REVISION_RANGE.
        self.assertIn("conductor:refactorer", SKILL)
        self.assertIn("REVISION_RANGE", SKILL)

    def test_dispatch_range_binds_code_sha(self):
        # REVISION_RANGE must bind code_sha (the agent's code commit), NOT sha
        # (the conductor chore commit, whose diff is state files). Binding sha
        # made [Refactor] a no-op (REFACTORED: NONE); code_sha is the task's
        # actual code — the same bound the refactor-scope commit gate enforces.
        # Rail A paste-verbatim (design D3): the binding lives in the CODE
        # builder (`_build_refactorer_prompt`, emitted on the finalize
        # envelope's `refactor.prompt`); the skill pastes it verbatim.
        from scripts.track_state import dispatch
        prompt = dispatch._build_refactorer_prompt("/td", "abc1234")
        self.assertIn("REVISION_RANGE=abc1234~1..abc1234", prompt)
        self.assertIn("pasting that `prompt` field verbatim", SKILL)

    def test_non_blocking(self):
        # The task already succeeded; the refactor seam is non-blocking.
        self.assertIn("non-blocking", SKILL.lower())

    def test_success_routes_to_37(self):
        # STATUS: SUCCESS announce → §3.7 (proceed; the refactor is just debt improvement).
        self.assertIn("STATUS: SUCCESS", SKILL)

    def test_failure_routes_to_37(self):
        # STATUS: FAILURE is non-blocking → announce → §3.7 (do not block the task).
        self.assertIn("STATUS: FAILURE", SKILL)

    def test_success_routing_3_6b_before_3_6c(self):
        # §3.6 SUCCESS routes review before refactor: self_review (§3.6b) →
        # refactor (§3.6c) → §3.7 — asserted on the ordered phrasing so a
        # reorder can't pass by containing both tokens somewhere.
        self.assertIn("`self_review` (§3.6b) → `refactor` (§3.6c)", SKILL)


if __name__ == "__main__":
    unittest.main()
