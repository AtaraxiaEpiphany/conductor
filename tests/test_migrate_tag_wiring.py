"""Wiring tests for the `[Migrate]` task-type tag.

`[Migrate]` (added for framework/version migrations like springboot2→3) is a
code-changing task where an existing test suite is the safety net and TDD
red-green is the wrong model — the suite STARTS red (the bump broke it), success
is making it green again. These tests pin the tag's plumbing across the gating
chain:

- ``extract_tags`` recognizes ``[Migrate]`` (the one regex every consumer reads).
- ``_tag_exempt_from_tdd`` / ``_tag_exempt_from_coverage`` honor it (F2/F3
  exemption propagates CLI-wide from these two chokepoints).
- ``_classify_task`` routes it to ``executor`` (runs task-executor — NOT
  manual/explore, which would skip the code work), so it stays wave-eligible.
- The phase-checker's migration-phase branch keys on a phase where every
  non-Manual task is [Migrate]; the agent doc carries that branch.
- The plan-format contract (the source spec-planner reads) documents the tag.
"""
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.helpers import (
    extract_tags,
    _tag_exempt_from_coverage,
    _tag_exempt_from_tdd,
)
from scripts.track_state.dispatch import _classify_task
from scripts.track_state import task_profiles

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "runtime" / "contracts" / "plan-format-contract.md"
PHASE_CHECKER = (ROOT / "agents" / "phase-checker.md").read_text(encoding="utf-8")
TASK_EXECUTOR = (ROOT / "agents" / "task-executor.md").read_text(encoding="utf-8")


class TagExtractionTests(TestCase):
    def test_extract_migrate(self):
        self.assertEqual(
            extract_tags("- [ ] [Migrate] fix javax->jakarta"),
            ["Migrate"],
        )

    def test_extract_migrate_with_comment(self):
        # AC/TC HTML comment must not false-positive or swallow the tag.
        self.assertEqual(
            extract_tags("[Migrate] rename packages <!-- AC-1, TC-1.1 -->"),
            ["Migrate"],
        )

    def test_extract_migrate_preserves_others(self):
        # A task may carry Migrate alongside another tag; both are returned.
        self.assertEqual(
            extract_tags("[Chore] [Migrate] bump spring-boot"),
            ["Chore", "Migrate"],
        )


class ExemptionChokepointTests(TestCase):
    """helpers.py:385/391 are the single F2/F3 exemption sets CLI-wide."""

    def test_exempt_from_tdd(self):
        self.assertTrue(_tag_exempt_from_tdd(["Migrate"]))
        self.assertTrue(_tag_exempt_from_tdd(["Migrate", "Chore"]))

    def test_exempt_from_coverage(self):
        self.assertTrue(_tag_exempt_from_coverage(["Migrate"]))

    def test_default_tag_not_exempt(self):
        # Untagged implementation work stays fully gated.
        self.assertFalse(_tag_exempt_from_tdd([]))
        self.assertFalse(_tag_exempt_from_coverage([]))


class RoutingTests(TestCase):
    """[Migrate] routes to executor (runs task-executor), NOT manual/explore.

    This is load-bearing: routing to explore/manual would skip the code work the
    migration requires. _classify_task is the single routing source of truth.
    """

    def test_routes_to_executor(self):
        self.assertEqual(_classify_task(["Migrate"]), "executor")

    def test_routes_to_executor_alongside_chore(self):
        self.assertEqual(_classify_task(["Migrate", "Chore"]), "executor")


class ContractDocTests(TestCase):
    def setUp(self):
        self.assertTrue(CONTRACT.exists(), "plan-format-contract.md must exist")
        self.text = CONTRACT.read_text(encoding="utf-8")

    def test_contract_documents_migrate_tag(self):
        self.assertIn("[Migrate]", self.text)
        # The distinguishing semantics — suite is the safety net, not a TDD target.
        self.assertIn("suite", self.text.lower())

    def test_contract_migrate_row_says_tdd_no(self):
        # The [Migrate] row sits in the tag table; its TDD column is NO.
        # Find the row line and assert it carries **NO** (TDD not required).
        migrate_line = next(
            (ln for ln in self.text.splitlines() if "[Migrate]" in ln),
            "",
        )
        self.assertIn("[Migrate]", migrate_line, "[Migrate] row missing from tag table")
        self.assertIn("**NO**", migrate_line, "[Migrate] row must mark TDD as NO")


class AgentDocTests(TestCase):
    def test_phase_checker_has_migration_branch(self):
        # The load-bearing phase-gate change: an all-migration phase ending red
        # is reported FAILED with the failing-test list, not fix-and-retried.
        self.assertIn("migration-phase branch", PHASE_CHECKER.lower().replace("Migration-phase", "migration-phase"))
        self.assertIn("[Migrate]", PHASE_CHECKER)
        self.assertIn("safety net", PHASE_CHECKER)

    def test_task_executor_has_migrate_workflow(self):
        # The per-tag executor workflow no longer lives inline in task-executor
        # (the §4.0 tag-table + §4.M restatement were the drift liability). It
        # lives in the registry's `workflow` field, injected into task-executor
        # at dispatch. Assert the registry carries the [Migrate] semantics...
        wf = task_profiles.workflow_for("Migrate")
        self.assertTrue(wf, "[Migrate] must carry a `workflow` in the registry")
        # ...with the load-bearing suite-as-safety-net / inverted-TDD semantics.
        low = wf.lower()
        self.assertIn("red", low)            # suite starts red
        self.assertIn("green", low)          # success = green
        self.assertIn("step 3", low)         # no Step 3 (Red)
        self.assertIn("step 6", low)         # no Step 6 (coverage gate)
        # And task-executor's §4.0/§4.M now branch on the injected profile /
        # workflow rather than restating the tag table inline — it points at the
        # injected registry block as the workflow source.
        self.assertIn("[Migrate]", TASK_EXECUTOR)
        self.assertIn("[Conductor Registry]", TASK_EXECUTOR)


class RegistryParityTests(TestCase):
    """The registry (task-type-profiles.json) is now the source of truth for
    exemption + routing. These pin that the registry-driven lookups reproduce
    the pre-registry hardcoded behavior exactly — the refactor must be
    behavior-preserving, and these guard against a future registry edit silently
    changing [Migrate]'s semantics."""

    def test_registry_reproduces_exemption_sets(self):
        # Pre-registry: coverage-exempt = {Docs,Config,Chore,Manual,Migrate};
        # tdd-exempt = {Explore,Docs,Config,Chore,Manual,Migrate}. Explore is
        # tdd-exempt but NOT coverage-exempt — the one asymmetry that must hold.
        for cov_exempt in ("Docs", "Config", "Chore", "Manual", "Migrate"):
            self.assertTrue(task_profiles.is_coverage_exempt([cov_exempt]),
                            f"[{cov_exempt}] should be coverage-exempt")
        self.assertFalse(task_profiles.is_coverage_exempt(["Explore"]),
                         "[Explore] must NOT be coverage-exempt")
        for tdd_exempt in ("Explore", "Docs", "Config", "Chore", "Manual", "Migrate"):
            self.assertTrue(task_profiles.is_tdd_exempt([tdd_exempt]),
                            f"[{tdd_exempt}] should be tdd-exempt")

    def test_registry_reproduces_routing(self):
        self.assertEqual(task_profiles.route_for(["Manual"]), "manual")
        self.assertEqual(task_profiles.route_for(["Explore"]), "explore")
        self.assertEqual(task_profiles.route_for(["Migrate"]), "executor")
        self.assertEqual(task_profiles.route_for([]), "executor")

    def test_migrate_in_registry(self):
        # [Migrate] is a registered tag (not silently dropped).
        self.assertIn("Migrate", task_profiles.TAG_VOCAB())


if __name__ == "__main__":
    main()
