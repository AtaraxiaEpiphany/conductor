"""Wiring tests for the SubagentStart registry-vocab injection.

The deterministic layer (CLI, plan parser, dispatch router, F2/F3 gates) is
data-driven via the task-type registry (baseline ⊕ project overlay). This file
pins the bridge that data-drives the *agent-prose* layer too: the SubagentStart
hook injects a ``[Conductor Registry]`` block into task-executor, spec-reviewer,
and refuter, so a project overlay's tags flow end-to-end to those agents with
zero plugin edits. spec-planner is deliberately NOT injected — it fetches the
full catalog on demand via ``track-state registry-doc`` (a tier-B join, pinned
in test_registry_doc.py); only the small/per-task resolved bits stay injected.

These are the load-bearing guards that:
- spec-planner is NOT in _REGISTRY_AGENTS (it fetches the vocab via registry-doc;
  the floor + reminder still arrive, but no registry block).
- task-executor sees this task's leading-tag profile (and its on-demand
  `workflow` pointer when the tag carries one) and the resolved exemption sets.
- the registry block is ordered between the reminder and any retry block.
- fail-open: a malformed/missing registry never breaks the floor/reminder.
- the headline end-to-end proof: a synthetic project overlay adding a tag
  appears in the registry-doc render AND the injected reviewer/executor blocks.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_HOOK = _scripts / "on-subagent-start.py"
_CLI = _scripts / "track-state"


def _run_cli(tag=None, env=None):
    """Run ``track-state registry-doc [--tag <Name>]`` → (rc, stdout)."""
    cmd = [sys.executable, "-B", str(_CLI), "registry-doc"]
    if tag is not None:
        cmd += ["--tag", tag]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout

from track_state import task_profiles as tp  # noqa: E402


def _run(agent_type: str, cwd: str = None, env=None) -> dict:
    payload = {"agent_type": agent_type}
    if cwd:
        payload["cwd"] = cwd
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        [sys.executable, "-B", str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, env=full_env,
    )
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def _flat_state(name="[Config] tweak timeout", task_type="config"):
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


class PlannerOnDemandTests(TestCase):
    """spec-planner fetches the full tag catalog on demand via
    ``track-state registry-doc`` (§3.1) — it is NOT in ``_REGISTRY_AGENTS``, so
    the SubagentStart hook injects no ``[Conductor Registry]`` block for it. The
    full catalog is a tier-B join (large + not per-task), so only the
    small/per-task resolved bits stay hook-injected (task-executor's leading-tag
    profile). These tests pin the deliberate removal: spec-planner gets the
    floor + reminder only, and the vocab reaches it via the CLI it runs itself
    (the every-tag / when-to-use / signals properties of that render are pinned
    in test_registry_doc.py)."""

    def test_planner_not_in_registry_agents(self):
        import importlib.util
        hspec = importlib.util.spec_from_file_location(
            "oss_planner", _scripts / "on-subagent-start.py")
        hook = importlib.util.module_from_spec(hspec)
        hspec.loader.exec_module(hook)
        self.assertNotIn("spec-planner", hook._REGISTRY_AGENTS)

    def test_planner_receives_no_registry_block(self):
        ctx = _run("spec-planner").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertNotIn("[Conductor Registry]", ctx)
        # ...but the floor + result-format reminder still arrive.
        self.assertIn("Validate every tool call", ctx)
        self.assertIn("SPEC PLAN RESULT", ctx)

    def test_registry_for_planner_builder_removed(self):
        # _registry_for_planner is dead code now — the on-demand CLI replaced it.
        # Pin the removal so a stale builder can't creep back.
        txt = (_scripts / "on-subagent-start.py").read_text()
        self.assertNotIn("_registry_for_planner", txt)


class ReviewerInjectionTests(TestCase):
    """spec-reviewer + refuter AUDIT tag membership, so they receive the
    resolved vocab WITH the review flags (over_tag_risk / tdd_exempt /
    coverage_exempt) per row — letting their prose defer to the flag names
    instead of restating which tags carry them (a restated set is the first
    thing to drift; the producer side of the adversarial pair must read the
    same ground truth as the verifier)."""

    _REVIEWERS = ("spec-reviewer", "refuter")

    def test_reviewers_receive_registry_block(self):
        for agent in self._REVIEWERS:
            ctx = _run(agent).get("hookSpecificOutput", {}).get("additionalContext", "")
            self.assertIn("[Conductor Registry]", ctx, f"{agent} missing the block")
            # The reviewer-framed lead names the audit role (distinct from the
            # planner/executor leads).
            self.assertIn("audit membership", ctx, f"{agent} missing reviewer lead")

    def test_reviewers_see_every_tag(self):
        # Reviewers audit the full resolved membership, so the whole vocab flows.
        for agent in self._REVIEWERS:
            ctx = _run(agent).get("hookSpecificOutput", {}).get("additionalContext", "")
            for tag in tp.TAG_VOCAB():
                self.assertIn(f"[{tag}]", ctx, f"{agent} missing tag {tag!r}")

    def test_reviewers_see_the_review_flags_per_row(self):
        # The block surfaces the flag tokens the reviewers' prose names — the
        # data the lint's flag-coverage assertion guarantees. Pinned on the
        # baseline tags that carry each flag so a dropped emission is caught
        # (over-tag from Docs/Config/Chore; tdd-exempt + coverage-exempt from
        # the exempt tags).
        for agent in self._REVIEWERS:
            ctx = _run(agent).get("hookSpecificOutput", {}).get("additionalContext", "")
            for token in ("over-tag", "tdd-exempt", "coverage-exempt"):
                self.assertIn(token, ctx, f"{agent} missing flag token {token!r}")

    def test_reviewer_block_flags_map_is_honest(self):
        # Every {flag: token} the declaration claims is actually emitted by the
        # rendered reviewer block (the explicit map, not name.replace). This is
        # the reverse direction of the lint's flag-coverage assertion — the
        # declaration cannot drift from what the renderers emit.
        import importlib.util
        hspec = importlib.util.spec_from_file_location(
            "oss_reviewer", _scripts / "on-subagent-start.py")
        hook = importlib.util.module_from_spec(hspec)
        hspec.loader.exec_module(hook)
        block = hook._registry_for_reviewer()
        missing = {name: tok for name, tok in hook.reviewer_block_flags().items()
                   if tok not in block}
        self.assertFalse(missing, f"declared flags not emitted: {missing}")

    def test_overlay_tag_flags_reach_the_reviewer(self):
        # Headline end-to-end: a project overlay adds a tag carrying over_tag_risk.
        # It must surface in spec-reviewer's injected block WITH its flag — so the
        # reviewer's prose (which defers to the flag names) reads the SAME
        # resolved ground truth the dispatch layer does, with zero plugin edits.
        # The overlay chokepoint (_load, baseline ⊕ project) is exercised for
        # tags; the reviewer is overlay-aware end-to-end.
        overlay_tag = {"tags": {"AcmeRollout": {
            "route": "manual", "tdd_exempt": True, "coverage_exempt": True,
            "over_tag_risk": True, "when_to_use": "Project rollout tag.",
        }}}
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "conductor" / "workflow").mkdir(parents=True)
            (proj / "conductor" / "workflow" / "task-type-profiles.json").write_text(
                json.dumps(overlay_tag))
            ctx = _run("spec-reviewer", env={"CLAUDE_PROJECT_DIR": str(proj)}).get(
                "hookSpecificOutput", {}
            ).get("additionalContext", "")
        self.assertIn("[AcmeRollout]", ctx, "overlay tag did not reach spec-reviewer")
        self.assertIn("over-tag", ctx, "overlay tag's over_tag_risk flag did not surface")


class ExecutorInjectionTests(TestCase):
    def test_executor_with_refactor_task_gets_refactor_flag(self):
        # The [Refactor] tag's refactor: true surfaces in the injected block so
        # §3.6c can fire on SUCCESS without a [Refactor] name marker or env.
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
        # "Exempted: [Docs], [Config], [Chore]" enumeration.
        for cov in ("Docs", "Config", "Chore", "Manual"):
            self.assertIn(f"[{cov}]", ctx)
        self.assertIn("[Explore]", ctx)


class OrderingTests(TestCase):
    """floor < reminder < registry < retry (the contract the assembly preserves)."""

    def test_registry_follows_reminder(self):
        ctx = _run("spec-reviewer").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertLess(ctx.index("REVIEW RESULT"), ctx.index("[Conductor Registry]"))

    def test_registry_precedes_retry(self):
        with _track(_flat_state(), _FAILURE_HANDOFF) as cwd:
            ctx = _run("task-executor", cwd=cwd).get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertLess(ctx.index("[Conductor Registry]"), ctx.index("[Conductor Retry]"))

    def test_floor_still_leads_with_registry_present(self):
        ctx = _run("spec-reviewer").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("[Conductor Registry]", ctx)  # registry present for this agent
        self.assertLess(ctx.index("Validate every tool call"), ctx.index("REVIEW RESULT"))


class FailOpenTests(TestCase):
    """A registry block must never break the floor/reminder primary contract."""

    def test_non_registry_agent_gets_no_registry_block(self):
        ctx = _run("code-reviewer").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertNotIn("[Conductor Registry]", ctx)
        # ...but the floor + reminder still arrive.
        self.assertIn("Validate every tool call", ctx)
        self.assertIn("REVIEW RESULT", ctx)

    def test_executor_registry_survives_alongside_retry(self):
        # With both a task and a failure handoff, registry + retry both inject
        # and the floor still leads — none of the three blocks dropped.
        with _track(_flat_state(), _FAILURE_HANDOFF) as cwd:
            ctx = _run("task-executor", cwd=cwd).get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("Validate every tool call", ctx)
        self.assertIn("[Conductor Registry]", ctx)
        self.assertIn("[Conductor Retry]", ctx)


class OverlayEndToEndTests(TestCase):
    """The headline proof: a project overlay's tag flows to the agents."""

    def test_overlay_tag_reaches_planner_via_registry_doc(self):
        # spec-planner fetches the vocab on demand via registry-doc (no longer
        # injected). A project overlay tag must appear in that render — with its
        # signals — so the planner can match + emit it. The on-demand replacement
        # for the old injected block.
        overlay = {
            "tags": {
                "K8sRollout": {
                    "route": "manual", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Project-specific k8s rollout tag.",
                    "signals": ["k8s", "kubectl", "helm", "rollout"],
                }
            }
        }
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "conductor" / "workflow").mkdir(parents=True)
            (proj / "conductor" / "workflow" / "task-type-profiles.json").write_text(
                json.dumps(overlay)
            )
            env = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
            rc, out = _run_cli(env=env)
        self.assertEqual(rc, 0, f"registry-doc with overlay failed:\n{out}")
        self.assertIn("[K8sRollout]", out, "overlay tag did not reach registry-doc")
        self.assertIn("Project-specific k8s rollout tag.", out)
        # The overlay tag's signals reach the on-demand render too.
        self.assertIn("kubectl", out)

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
        # POINTER in the executor when that tag is the locked task's leading tag.
        # The pointer names the overlay tag; the prose itself is fetched (NOT
        # inlined) — no baseline tag carries a `workflow`, so this overlay path
        # is the only one that exercises the on-demand pointer.
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
        # No baseline tag carries a `workflow` (the field is a project-overlay
        # escape hatch), so exercise the on-demand fetch via an overlay tag:
        # ``registry-doc --tag CustomProc`` must emit that tag's workflow prose
        # verbatim — the payload the executor fetches on demand instead of
        # having it always injected into every dispatch.
        overlay = {
            "tags": {
                "CustomProc": {
                    "route": "executor", "tdd_exempt": True, "coverage_exempt": True,
                    "when_to_use": "Project-specific procedure.",
                    "workflow": "PROJECT CUSTOM WORKFLOW: do the bespoke dance.",
                }
            }
        }
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "conductor" / "workflow").mkdir(parents=True)
            (proj / "conductor" / "workflow" / "task-type-profiles.json").write_text(
                json.dumps(overlay)
            )
            env = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
            rc, out = _run_cli(tag="CustomProc", env=env)
        self.assertEqual(rc, 0, f"registry-doc --tag CustomProc failed:\n{out}")
        self.assertIn("# Task Type `CustomProc`", out)
        # The full workflow prose renders verbatim (not just the pointer).
        self.assertIn("PROJECT CUSTOM WORKFLOW: do the bespoke dance.", out)

    def test_registry_doc_tag_filter_unknown_fail_open(self):
        # An unknown tag must fail open (surface + exit 0), never raise —
        # mirroring the renderer's posture and the validator's hard-error split.
        rc, out = _run_cli(tag="Bogus")
        self.assertEqual(rc, 0)
        self.assertIn("UNKNOWN", out)


if __name__ == "__main__":
    main()
