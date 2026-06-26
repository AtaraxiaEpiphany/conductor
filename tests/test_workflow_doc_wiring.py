"""Wiring tests for agent↔workflow-doc pointers + the task-workflow git-notes drift fix (#2).

Guards against:
- phase-checker / task-executor §4.0 re-bloating with restated steps (they must point at the templates).
- regression of the git-notes ownership drift in task-workflow.md (Step 9 must stay orchestrator-owned —
  `dispatch-finalize` writes task-commit notes, not the executing agent).
- loss of the agent-specific extensions that legitimately stay inline (L2 browser-E2E, EXECUTION_MODE,
  the add-checkpoint `ok: true` gate, tag routing, the dispatch-finalize git-notes invariant).
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
TEMPLATES = ROOT / "templates"


class TaskExecutorWorkflowPointerTests(TestCase):
    def setUp(self):
        self.agent = (AGENTS / "task-executor.md").read_text(encoding="utf-8")

    def test_points_at_task_workflow_steps_3_8(self):
        # §4.0 defers the canonical TDD cycle to the workflow doc instead of restating it.
        self.assertIn("task-workflow.md", self.agent)
        self.assertIn("Steps 3-8", self.agent)

    def test_keeps_git_notes_invariant(self):
        # The orchestrator owns git notes; the agent must not write them (anti-pattern V9 guard).
        self.assertIn("dispatch-finalize", self.agent)
        self.assertIn("do NOT write git notes", self.agent)

    def test_keeps_tag_routing_table(self):
        # Tag -> workflow dispatch is agent-specific, not in the template.
        self.assertIn("[Explore]", self.agent)
        self.assertIn("[Docs]", self.agent)
        self.assertIn("FAILURE", self.agent)


class TaskWorkflowDriftGuardTests(TestCase):
    def setUp(self):
        self.doc = (TEMPLATES / "task-workflow.md").read_text(encoding="utf-8")

    def test_git_notes_are_orchestrator_owned(self):
        # The drift: Step 9 used to tell the agent to run `git notes add`. It must now state
        # dispatch-finalize owns it and the agent does not.
        self.assertIn("dispatch-finalize", self.doc)
        self.assertIn("does **not** run `git notes add`", self.doc)

    def test_ownership_split_documented(self):
        # The orchestrator/agent step split must be explicit so a reader knows the executing
        # agent performs only Steps 3-8 (orchestrator owns 1, 2, 9, 10, 11).
        self.assertIn("Steps 3-8", self.doc)
        self.assertIn("9, 10, 11", self.doc)


class PhaseCheckerWorkflowPointerTests(TestCase):
    def setUp(self):
        self.agent = (AGENTS / "phase-checker.md").read_text(encoding="utf-8")

    def test_points_at_phase_checkpoint_doc(self):
        # §4.0 defers the base protocol to the checkpoint doc instead of restating Steps 1-10.
        self.assertIn("phase-checkpoint.md", self.agent)

    def test_keeps_l2_verification_addendum(self):
        # L2 browser-E2E is an agent extension absent from the template; it must stay inline.
        self.assertIn("Step 3.5", self.agent)
        self.assertIn("browser-automation MCP", self.agent)
        self.assertIn("L2", self.agent)

    def test_keeps_continuous_mode_addendum(self):
        self.assertIn("EXECUTION_MODE", self.agent)
        self.assertIn("continuous", self.agent)

    def test_keeps_checkpoint_ok_gate(self):
        # The add-checkpoint `ok: true` gate is a binding override of the template's invocation.
        self.assertIn("add-checkpoint", self.agent)
        self.assertIn("ok: true", self.agent)


if __name__ == "__main__":
    main()
