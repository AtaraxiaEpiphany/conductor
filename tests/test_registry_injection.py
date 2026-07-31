"""Wiring tests for the SubagentStart registry-vocab injection.

The deterministic layer (CLI, plan parser, dispatch router, F2/F3 gates) is
data-driven via the task-type and verify-mode registries (baseline ⊕ project
overlay). This file pins the bridge that data-drives the *agent-prose* layer too:
the SubagentStart hook injects a ``[Conductor Registry]`` block into
spec-planner, task-executor, and phase-checker, so a project overlay's tags/modes
flow end-to-end to the agents with zero plugin edits (mirrors how phase-checker
already reads the registry directly).

These are the load-bearing guards that:
- spec-planner sees the full resolved TAG_VOCAB + MODE_VOCAB (emit any, refuse none).
- task-executor sees this task's leading-tag profile (+ its `workflow` when the
  tag is Migrate) and the resolved exemption sets.
- the registry block is ordered between the reminder and any retry block.
- fail-open: a malformed/missing registry never breaks the floor/reminder.
- the headline end-to-end proof: a synthetic project overlay adding a tag/mode
  appears in the injected vocab.
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_HOOK = _scripts / "on-subagent-start.py"
_CLI = _scripts / "track-state"


def _run_cli(tag=None):
    """Run ``track-state registry-doc [--tag <Name>]`` → (rc, stdout)."""
    cmd = [sys.executable, "-B", str(_CLI), "registry-doc"]
    if tag is not None:
        cmd += ["--tag", tag]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout

from track_state import task_profiles as tp  # noqa: E402
from track_state import verify_mode_profiles as vmp  # noqa: E402


def _run(agent_type: str, cwd: str = None, env=None) -> dict:
    payload = {"agent_type": agent_type}
    if cwd:
        payload["cwd"] = cwd
    import os
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, "-B", str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, env=full_env,
    )
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def _flat_state(name="[Migrate] bump spring-boot", task_type="migrate"):
    """One phase, one in_progress task whose task_type mirrors the leading tag."""
    return {
        "current_phase_index": 1,
        "current_task_index": 1,
        "phases": [{"tasks": [{"name": name, "status": "in_progress",
                               "task_type": task_type}]}],
    }


@contextlib.contextmanager
def _track(state, handoff_body=None, *, phase=1, task=1):
    with tempfile.TemporaryDirectory() as d:
        track_dir = Path(d) / "conductor" / "tracks" / "demo"
        (track_dir / ".conductor" / "handoff").mkdir(parents=True)
        (track_dir / "track-state.json").write_text(json.dumps(state))
        if handoff_body is not None:
            (track_dir / ".conductor" / "handoff" / f"P{phase}T{task}.md").write_text(handoff_body)
        yield d


_FAILURE_HANDOFF = """# Handoff: demo

## Execution Record

### Attempt 1/3 | 2026-06-30T00:00:00Z ❌

**What Was Done**: wrote foo.py
**Failure Reason**: test_TC_1_1 timed out at 30s
**Suggested Next Step**: raise timeout to 120s
"""


class PlannerInjectionTests(TestCase):
    def test_planner_receives_registry_lead(self):
        ctx = _run("spec-planner").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("[Conductor Registry]", ctx)

    def test_planner_sees_every_tag(self):
        ctx = _run("spec-planner").get("hookSpecificOutput", {}).get("additionalContext", "")
        for tag in tp.TAG_VOCAB():
            self.assertIn(f"[{tag}]", ctx, f"planner injection missing tag {tag!r}")

    def test_planner_sees_when_to_use_hints(self):
        ctx = _run("spec-planner").get("hookSpecificOutput", {}).get("additionalContext", "")
        # The injected block carries each tag's when-to-use hint, data-driven.
        self.assertIn(tp.when_to_use_for("Migrate"), ctx)

    def test_planner_sees_every_mode(self):
        ctx = _run("spec-planner").get("hookSpecificOutput", {}).get("additionalContext", "")
        for mode in vmp.MODE_VOCAB():
            self.assertIn(mode, ctx, f"planner injection missing verify-mode {mode!r}")


class PhaseCheckerInjectionTests(TestCase):
    def test_phase_checker_receives_modes(self):
        ctx = _run("phase-checker").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("[Conductor Registry]", ctx)
        for mode in vmp.MODE_VOCAB():
            self.assertIn(mode, ctx)


class ExecutorInjectionTests(TestCase):
    def test_executor_with_migrate_task_gets_workflow(self):
        with _track(_flat_state()) as cwd:
            ctx = _run("task-executor", cwd=cwd).get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("[Conductor Registry]", ctx)
        self.assertIn("RESOLVED PROFILE for this task's leading tag [Migrate]", ctx)
        self.assertIn("tdd_exempt: True", ctx)
        self.assertIn("coverage_exempt: True", ctx)
        # The [Migrate] workflow is large + conditional → read-on-demand, not
        # inlined. The block carries the POINTER (the fetch command naming the
        # tag), and the prose itself must NOT be injected.
        self.assertIn("workflow: present", ctx)
        self.assertIn("track-state registry-doc --tag Migrate", ctx)
        wf = tp.workflow_for("Migrate")
        # The workflow prose is NOT inlined into the dispatch — it is fetched.
        self.assertNotIn(wf.split(".")[0], ctx)

    def test_executor_with_refactor_task_gets_refactor_flag(self):
        # The [Refactor] tag's refactor: true surfaces in the injected block so
        # §3.6c can fire on SUCCESS without a [Refactor] name marker or env.
        # Mirrors how [Migrate]'s workflow pointer surfaces.
        with _track(_flat_state(name="[Refactor] extract the helper",
                                task_type="refactor")) as cwd:
            ctx = _run("task-executor", cwd=cwd).get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("[Conductor Registry]", ctx)
        self.assertIn("RESOLVED PROFILE for this task's leading tag [Refactor]", ctx)
        # [Refactor] is NOT TDD/coverage-exempt — it still owes a working test.
        self.assertIn("tdd_exempt: False", ctx)
        self.assertIn("coverage_exempt: False", ctx)
        # The load-bearing flag: refactor: true tells §3.6c to dispatch refactorer.
        self.assertIn("refactor: true", ctx)

    def test_executor_default_task_gets_refactor_false(self):
        # A default (untagged) task resolves refactor: false — no refactorer.
        with _track(_flat_state(name="[Config] tweak timeout",
                                task_type="config")) as cwd:
            ctx = _run("task-executor", cwd=cwd).get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("refactor: false", ctx)

    def test_executor_default_task_gets_no_profile(self):
        # An untagged (default) task resolves no leading tag → only the
        # exemption-set summary is injected (no RESOLVED PROFILE line).
        state = _flat_state(name="plain impl task", task_type="default")
        with _track(state) as cwd:
            ctx = _run("task-executor", cwd=cwd).get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("[Conductor Registry]", ctx)
        self.assertIn("RESOLVED EXEMPTION SETS", ctx)
        self.assertNotIn("RESOLVED PROFILE", ctx)

    def test_executor_no_locked_task_still_sees_exemption_sets(self):
        ctx = _run("task-executor").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("[Conductor Registry]", ctx)
        self.assertIn("RESOLVED EXEMPTION SETS", ctx)
        # The exemption sets are the data-driven replacement for the old hardcoded
        # "Exempted: [Docs], [Config], [Chore], [Migrate]" enumeration.
        for cov in ("Docs", "Config", "Chore", "Manual", "Migrate"):
            self.assertIn(f"[{cov}]", ctx)
        self.assertIn("[Explore]", ctx)


class OrderingTests(TestCase):
    """floor < reminder < registry < retry (the contract the assembly preserves)."""

    def test_registry_follows_reminder(self):
        ctx = _run("spec-planner").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertLess(ctx.index("SPEC PLAN RESULT"), ctx.index("[Conductor Registry]"))

    def test_registry_precedes_retry(self):
        with _track(_flat_state(), _FAILURE_HANDOFF) as cwd:
            ctx = _run("task-executor", cwd=cwd).get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertLess(ctx.index("[Conductor Registry]"), ctx.index("[Conductor Retry]"))

    def test_floor_still_leads_with_registry_present(self):
        ctx = _run("spec-planner").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertLess(ctx.index("Validate every tool call"), ctx.index("SPEC PLAN RESULT"))


class FailOpenTests(TestCase):
    """A registry block must never break the floor/reminder primary contract."""

    def test_non_registry_agent_gets_no_registry_block(self):
        ctx = _run("code-reviewer").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertNotIn("[Conductor Registry]", ctx)
        # ...but the floor + reminder still arrive.
        self.assertIn("Validate every tool call", ctx)
        self.assertIn("REVIEW RESULT", ctx)

    def test_executor_registry_survives_alongside_retry(self):
        # With both a Migrate task and a failure handoff, registry + retry both
        # inject and the floor still leads — none of the three blocks dropped.
        with _track(_flat_state(), _FAILURE_HANDOFF) as cwd:
            ctx = _run("task-executor", cwd=cwd).get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("Validate every tool call", ctx)
        self.assertIn("[Conductor Registry]", ctx)
        self.assertIn("[Conductor Retry]", ctx)


class OverlayEndToEndTests(TestCase):
    """The headline proof: a project overlay's tag/mode flows to the agents."""

    def test_overlay_tag_appears_in_planner_injection(self):
        # A project drops conductor/workflow/task-type-profiles.json adding a
        # project-specific tag. It must surface in spec-planner's injected vocab.
        overlay = {
            "tags": {
                "K8sRollout": {
                    "route": "manual", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Project-specific k8s rollout tag.",
                }
            }
        }
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "conductor" / "workflow").mkdir(parents=True)
            (proj / "conductor" / "workflow" / "task-type-profiles.json").write_text(
                json.dumps(overlay)
            )
            ctx = _run("spec-planner", env={"CLAUDE_PROJECT_DIR": str(proj)}).get(
                "hookSpecificOutput", {}
            ).get("additionalContext", "")
        self.assertIn("[K8sRollout]", ctx, "project-overlay tag did not reach spec-planner")
        self.assertIn("Project-specific k8s rollout tag.", ctx)

    def test_overlay_tag_appears_in_executor_exemption_set(self):
        # Same overlay; the executor's injected exemption set must list it too.
        overlay = {
            "tags": {
                "Lint": {
                    "route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Project-specific lint tag.",
                }
            }
        }
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "conductor" / "workflow").mkdir(parents=True)
            (proj / "conductor" / "workflow" / "task-type-profiles.json").write_text(
                json.dumps(overlay)
            )
            ctx = _run("task-executor", env={"CLAUDE_PROJECT_DIR": str(proj)}).get(
                "hookSpecificOutput", {}
            ).get("additionalContext", "")
        self.assertIn("[Lint]", ctx, "project-overlay tag did not reach task-executor")

    def test_overlay_workflow_flows_to_executor(self):
        # A project tag WITH a bespoke workflow must surface as an on-demand
        # POINTER in the executor when that tag is the locked task's leading tag
        # — the [Migrate] generalization. The pointer names the overlay tag;
        # the prose itself is fetched (NOT inlined).
        overlay = {
            "tags": {
                "CustomProc": {
                    "route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Project-specific procedure.",
                    "workflow": "PROJECT CUSTOM WORKFLOW: do the bespoke dance.",
                }
            }
        }
        state = {
            "current_phase_index": 1, "current_task_index": 1,
            "phases": [{"tasks": [{"name": "[CustomProc] run it",
                                   "status": "in_progress", "task_type": "customproc"}]}],
        }
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "conductor" / "workflow").mkdir(parents=True)
            (proj / "conductor" / "workflow" / "task-type-profiles.json").write_text(
                json.dumps(overlay)
            )
            track_dir = proj / "conductor" / "tracks" / "demo"
            (track_dir / ".conductor" / "handoff").mkdir(parents=True)
            (track_dir / "track-state.json").write_text(json.dumps(state))
            ctx = _run("task-executor", cwd=str(proj),
                       env={"CLAUDE_PROJECT_DIR": str(proj)}).get(
                "hookSpecificOutput", {}
            ).get("additionalContext", "")
        self.assertIn("[CustomProc]", ctx)
        # The POINTER names the overlay tag (registry-doc --tag CustomProc); the
        # bespoke prose is NOT inlined — the executor fetches it on demand.
        self.assertIn("workflow: present", ctx)
        self.assertIn("track-state registry-doc --tag CustomProc", ctx)
        self.assertNotIn("PROJECT CUSTOM WORKFLOW: do the bespoke dance.", ctx)


class RegistryDocOnDemandTests(TestCase):
    """The on-demand payload the executor pointer points at is actually fetchable.

    The pointer injected into task-executor is only load-bearing if
    ``registry-doc --tag <Tag>`` really emits that tag's workflow prose. This is
    the bridge between tier B (pointer injected) and tier A (prose fetched).
    """

    def test_registry_doc_tag_filter_renders_workflow(self):
        # The --tag Migrate filter must emit the workflow prose verbatim — this
        # is the payload the executor fetches on demand instead of having it
        # always injected into every dispatch.
        rc, out = _run_cli(tag="Migrate")
        self.assertEqual(rc, 0, f"registry-doc --tag Migrate failed:\n{out}")
        self.assertIn("# Task Type `Migrate`", out)
        wf = tp.workflow_for("Migrate")
        # The full workflow prose (every sentence, not just the first) renders.
        for sentence in wf.split("."):
            stripped = sentence.strip()
            if stripped:
                self.assertIn(stripped, out)

    def test_registry_doc_tag_filter_unknown_fail_open(self):
        # An unknown tag must fail open (surface + exit 0), never raise —
        # mirroring the renderer's posture and the validator's hard-error split.
        rc, out = _run_cli(tag="Bogus")
        self.assertEqual(rc, 0)
        self.assertIn("UNKNOWN", out)


if __name__ == "__main__":
    main()
