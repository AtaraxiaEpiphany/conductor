"""Structural tests for the phase-checker verifier fan-out (G2).

The phase checkpoint is reshaped from one monolithic agent into a
**fan-out-and-synthesize**:

- ``conductor:ac-tracer`` (read-only) — the AC-evidence-trace tier. Runs
  ``track-state spec-integrity`` once, parses, returns the per-AC grounding
  verdict.
- ``conductor:test-runner`` (read-only) — the L1 verify-only tier. Resolves the
  test command and runs it ONCE (no fix, no retry).
- ``conductor:phase-checker`` — the **synthesizer**. Consumes the fleet's two
  verdicts, owns the L1 fix-and-retry (only when test-runner reports failure),
  runs L2 (browser MCP) + L4 (human) + the checkpoint commit.

The orchestrator (``implement`` §3.2, reused by ``parallel`` §4.2) fans out
ac-tracer + test-runner in ONE message, parses their result blocks, then
dispatches phase-checker with the verdicts. Both verifiers stay Agent-free (no
nest-dispatch); the §8.0 block gains ``L1_VERIFY:`` / ``L2:`` fields.

These pin the topology, the read-only firewall on both verifiers, the
synthesizer contract, and the dispatcher/hook wiring so the fan-out can't be
silently reverted.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"


def _frontmatter_tools(agent_text: str) -> str:
    in_fm = False
    for line in agent_text.splitlines():
        if line.strip() == "---":
            in_fm = not in_fm
            continue
        if in_fm and line.startswith("tools:"):
            return line.split("tools:", 1)[1].strip()
    return ""


class AcTracerContractTests(TestCase):
    def setUp(self):
        self.agent = (AGENTS / "ac-tracer.md").read_text(encoding="utf-8")

    def test_agent_exists(self):
        self.assertTrue((AGENTS / "ac-tracer.md").exists())

    def test_result_block_delimiter(self):
        self.assertIn("---AC TRACE RESULT---", self.agent)

    def test_runs_spec_integrity(self):
        # The substrate is the spec-integrity CLI (the binding runtime gate that
        # used to live inline in phase-checker Step 3.6).
        self.assertIn("track-state spec-integrity", self.agent)

    def test_is_read_only(self):
        # Pure CLI + parse — must NOT gain Edit/Write/Agent. Pinned so a future
        # edit doesn't quietly let it fix the authoring defect it detects.
        tools = _frontmatter_tools(self.agent)
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Write", tools)
        self.assertNotIn("Agent", tools)

    def test_emits_verdict_not_checkpoint_decision(self):
        # It returns a verdict (passed/warn/skipped/FAILED); phase-checker acts.
        self.assertIn("VERDICT: passed", self.agent)
        self.assertIn("VERDICT: FAILED", self.agent)

    def test_does_not_act_on_strict_env(self):
        # Strictness escalation (CONDUCTOR_AC_VERIFY_STRICT) is the synthesizer's
        # call — ac-tracer reports the warn regardless.
        self.assertIn("CONDUCTOR_AC_VERIFY_STRICT", self.agent)
        self.assertIn("synthesizer's call", self.agent)


class TestRunnerContractTests(TestCase):
    def setUp(self):
        self.agent = (AGENTS / "test-runner.md").read_text(encoding="utf-8")

    def test_agent_exists(self):
        self.assertTrue((AGENTS / "test-runner.md").exists())

    def test_result_block_delimiter(self):
        self.assertIn("---L1 VERIFY RESULT---", self.agent)

    def test_is_read_only(self):
        # Runs the test command once — must NOT fix/edit/write (phase-checker
        # owns fix-and-retry).
        tools = _frontmatter_tools(self.agent)
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Write", tools)
        self.assertNotIn("Agent", tools)

    def test_runs_once_no_fix(self):
        # The defining seam vs phase-checker: verify-only, single run, no retry.
        lower = self.agent.lower()
        self.assertIn("once", lower)
        self.assertIn("do not retry", lower)
        self.assertIn("no fix", lower)

    def test_failed_suite_is_not_error(self):
        # A failing suite is a real L1 result (STATUS: failed), not an agent
        # error. Pinned so the synthesizer gets a verdict it can act on.
        self.assertIn("STATUS: passed|failed|error", self.agent)


class PhaseCheckerSynthesizerTests(TestCase):
    """phase-checker is now the synthesizer — it consumes the fleet's verdicts
    rather than running the verify tiers inline."""

    def setUp(self):
        self.agent = (AGENTS / "phase-checker.md").read_text(encoding="utf-8")

    def test_consumes_fleet_verdicts_in_assignment(self):
        self.assertIn("`AC_TRACE_VERDICT`", self.agent)
        self.assertIn("`L1_VERIFY_STATUS`", self.agent)
        self.assertIn("`L1_VERIFY_COMMAND`", self.agent)

    def test_names_both_verifiers(self):
        self.assertIn("conductor:ac-tracer", self.agent)
        self.assertIn("conductor:test-runner", self.agent)

    def test_result_block_has_l1_verify_and_l2_fields(self):
        # §8.0 extended so the synthesizer reports the merged fleet state.
        self.assertIn("L1_VERIFY:", self.agent)
        self.assertIn("L2:", self.agent)

    def test_owns_fix_and_retry_on_failure(self):
        # The L1-fix tier (write/fix tests, re-run up to 2x) stays here — it
        # fires only when test-runner reports failure.
        self.assertIn("fix-and-retry", self.agent)

    def test_consumes_ac_trace_rather_than_running_it(self):
        # The Step 3.6 addendum now consumes ac-tracer's verdict; it must NOT
        # re-run spec-integrity itself (that would duplicate the fleet's work).
        self.assertIn("do NOT re-run the CLI", self.agent)


class DispatcherWiringTests(TestCase):
    def setUp(self):
        self.implement = (ROOT / "skills" / "implement" / "SKILL.md").read_text(encoding="utf-8")
        self.parallel = (ROOT / "skills" / "parallel" / "SKILL.md").read_text(encoding="utf-8")
        self.hooks = (ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        self.start = (ROOT / "scripts" / "on-subagent-start.py").read_text(encoding="utf-8")
        self.stop = (ROOT / "scripts" / "on-subagent-stop.py").read_text(encoding="utf-8")
        self.template = (ROOT / "templates" / "phase-checkpoint.md").read_text(encoding="utf-8")

    def test_implement_fans_out_both_verifiers_in_one_message(self):
        # §3.2 must fan out ac-tracer + test-runner in parallel, THEN dispatch
        # phase-checker with the verdicts.
        self.assertIn("conductor:ac-tracer", self.implement)
        self.assertIn("conductor:test-runner", self.implement)
        self.assertIn("ONE message", self.implement)
        self.assertIn("fan-out-and-synthesize", self.implement)
        # And pass the verdicts through to the synthesizer.
        self.assertIn("AC_TRACE_VERDICT", self.implement)
        self.assertIn("L1_VERIFY_STATUS", self.implement)

    def test_parallel_references_the_fanout(self):
        self.assertIn("ac-tracer", self.parallel)
        self.assertIn("test-runner", self.parallel)

    def test_hooks_matchers_include_both_verifiers(self):
        self.assertIn("ac-tracer", self.hooks)
        self.assertIn("test-runner", self.hooks)

    def test_start_reminders_include_both_verifiers(self):
        self.assertIn('"ac-tracer"', self.start)
        self.assertIn('"test-runner"', self.start)

    def test_stop_stdout_block_registry_includes_both_verifiers(self):
        # Both are STDOUT_BLOCK agents (the skill needs their verdict; a missing
        # block earns a recovery turn).
        self.assertIn('"ac-tracer"', self.stop)
        self.assertIn('"test-runner"', self.stop)

    def test_template_documents_the_fanout_realization(self):
        # The project-side protocol notes that Steps 3 + 3.6 run as fanned-out
        # subagents before the synthesizer.
        self.assertIn("test-runner", self.template)
        self.assertIn("ac-tracer", self.template)
        self.assertIn("synthesizer", self.template)


if __name__ == "__main__":
    main()
