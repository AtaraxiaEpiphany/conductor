r"""Tests for on-subagent-start.py — the SubagentStart result-format reminder.

The matcher is matcherless; the agent-roster registry gates who gets a
reminder. Two drift guards: (1) every agents/*.md is rostered with a reminder,
so a newly-added agent can't silently start with no result-format hint; (2) an
unrostered agent gets no context (fast no-op).
"""
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))
sys.path.insert(0, str(_scripts.parent))

_spec = importlib.util.spec_from_file_location(
    "on_subagent_start", _scripts / "on-subagent-start.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

from track_state import agent_roster as _ar  # noqa: E402

_REMINDERS = {name: _ar.reminder_for(name) for name in _ar.merged_agent_names()}

_HOOK = _scripts / "on-subagent-start.py"
_HOOKS_JSON = Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"


def _subagent_start_agents():
    """Agent types the SubagentStart hook scaffolds = the merged roster names.

    The matcher is matcherless (fires for every subagent); the roster decides
    who gets context, so the roster IS the coverage set.
    """
    return _ar.merged_agent_names()


def _run(agent_type: str, cwd: str = None) -> dict:
    payload = {"agent_type": agent_type}
    if cwd:
        payload["cwd"] = cwd
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True,
    )
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


class SubagentStartReminderTests(TestCase):
    def test_every_rostered_agent_has_a_reminder(self):
        """Drift guard: every roster row must yield a reminder (a row with a
        missing/malformed fence silently starts with no result-format hint)."""
        missing = [n for n in _subagent_start_agents() if n not in _REMINDERS]
        self.assertFalse(missing, f"rostered agents without a reminder: {missing}")

    def test_each_reminder_names_a_result_block(self):
        for agent, reminder in _REMINDERS.items():
            self.assertIn("Result format:", reminder)
            self.assertIn("---", reminder, f"{agent} reminder has no delimiter")

    def test_known_agent_gets_its_reminder(self):
        out = _run("task-executor")
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("TASK RESULT", ctx)

    def test_refuter_gets_its_reminder(self):
        """The shared refuter agent (dispatched by new-track / implement / parallel)
        must receive its REFUTATION RESULT reminder — filter-subagent-output depends
        on the delimiter reaching the agent's emitted output."""
        out = _run("refuter")
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("REFUTATION RESULT", ctx)
        self.assertIn("Validate every tool call", ctx)  # floor prepended

    def test_unknown_agent_gets_no_context(self):
        out = _run("mystery-agent")
        self.assertNotIn("hookSpecificOutput", out)


# --- Safety-floor injection ---------------------------------------------------
# on-subagent-start.py prepends runtime/subagent-firewall.md (a curated universal
# safety floor) ahead of each agent's result-format reminder. These tests are the
# load-bearing guard: if the injection silently breaks, the per-agent safety floor
# vanishes from 10 agents with no other signal — so the suite must fail loudly.

_FLOOR = _scripts.parent / "runtime" / "subagent-firewall.md"

# Anchors every matched subagent must receive via the injected floor.
_FLOOR_ANCHORS = [
    "Validate every tool call",  # halt-on-failure discipline
    "V11",                        # no state mutation (stay in your lane)
    "VIOLATION",                  # recovery protocol
    "track-state.json",           # the orchestrator-owned boundary
]

# Rules the floor must NOT restate — they contradict task-executor's documented
# workflow: V5 forbids bundling test+impl, yet task-executor bundles them in Step 8;
# V9 forbids skipping git notes, yet task-executor is explicitly told it does NOT
# write them (orchestrator-owned). Guards against someone "enriching" the floor by
# copying V5/V9 verbatim out of core-contract.md (the curation is load-bearing).
_FLOOR_FORBIDDEN = [
    "Bundle test + implementation",  # V5 phrasing (core-contract.md)
    "Skip git notes",                # V9 phrasing
]


class SafetyFloorInjectionTests(TestCase):
    def test_floor_fragment_exists_and_carries_universal_anchors(self):
        """The injected floor file exists and holds the universal safety anchors."""
        self.assertTrue(_FLOOR.exists(), f"missing floor file: {_FLOOR}")
        text = _FLOOR.read_text(encoding="utf-8")
        for anchor in _FLOOR_ANCHORS:
            self.assertIn(anchor, text, f"floor missing universal anchor: {anchor!r}")

    def test_floor_excludes_rules_that_contradict_task_executor(self):
        """The floor must not restate V5/V9 — doing so would instruct task-executor
        to violate its own documented Step-8 workflow."""
        text = _FLOOR.read_text(encoding="utf-8")
        for forbidden in _FLOOR_FORBIDDEN:
            self.assertNotIn(
                forbidden, text,
                f"floor restates a contradicted rule ({forbidden!r}); "
                f"see task-executor.md Step 8 + core-contract.md V5/V9",
            )

    def test_every_rostered_agent_receives_the_safety_floor(self):
        """Load-bearing: each rostered agent's injected context carries
        the floor. Silent breakage here would strip safety from every rostered
        agent."""
        matched = list(_subagent_start_agents())
        self.assertTrue(matched, "no rostered agents found")
        for agent in matched:
            ctx = _run(agent).get("hookSpecificOutput", {}).get("additionalContext", "")
            self.assertTrue(ctx, f"{agent} got no injected context")
            for anchor in _FLOOR_ANCHORS:
                self.assertIn(
                    anchor, ctx,
                    f"{agent} injected context missing floor anchor {anchor!r}",
                )

    def test_floor_prepended_before_result_reminder(self):
        """A known agent receives the floor AND its result delimiter (filter-subagent-
        output depends on the delimiter reaching the subagent's output), floor first."""
        ctx = _run("task-executor").get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("TASK RESULT", ctx)
        self.assertLess(
            ctx.index("Validate every tool call"), ctx.index("TASK RESULT"),
            "floor must precede the result-format reminder",
        )

    def test_load_safety_floor_degrades_gracefully_on_missing_file(self):
        """A missing/unreadable floor must warn on stderr and return '' — never crash,
        and never silently drop the result reminder (the hook falls back to reminder-
        only on '')."""
        captured = io.StringIO()
        _mod.FLOOR_FILE = _scripts / "nonexistent-firewall.md"
        try:
            with contextlib.redirect_stderr(captured):
                floor = _mod._load_safety_floor()
        finally:
            _mod.FLOOR_FILE = _FLOOR
        self.assertEqual(floor, "")
        self.assertIn("WARNING", captured.getvalue())


class SubagentMatcherCompletenessTests(TestCase):
    def test_subagentstart_matcher_is_matcherless(self):
        """D5: the SubagentStart matcher carries no name alternation — a
        project-overlay agent must be able to reach the hook. The roster (not
        the matcher) gates who gets context."""
        data = json.loads(_HOOKS_JSON.read_text())
        for entry in data["hooks"]["SubagentStart"]:
            self.assertNotIn("matcher", entry,
                             "SubagentStart matcher must stay matcherless "
                             "(the roster gates)")

    def test_every_subagent_is_rostered(self):
        """Completeness guard: every agents/*.md must be in the baseline
        roster, so no subagent is ever dispatched without the safety floor +
        result-format reminder. Prevents the wiki-differ/wiki-researcher gap
        (closed in the follow-up commit) from recurring for a future agent."""
        rostered = set(_subagent_start_agents())
        agents_md = {p.stem for p in (_scripts.parent / "agents").glob("*.md")}
        unguarded = agents_md - rostered
        self.assertFalse(
            unguarded,
            f"agents/*.md absent from agent-roster.json (no safety floor): "
            f"{sorted(unguarded)}",
        )

    def test_every_agent_has_a_reminder_row(self):
        """The roster's reminder coverage is keyed by agent name — a new
        agents/*.md with no row means filter-subagent-output gets no result
        delimiter from the dispatch, and a stale row means a deleted agent
        still claims one. Keys must track the agents/ dir exactly (campaign
        2.6)."""
        agents_md = {p.stem for p in (_scripts.parent / "agents").glob("*.md")}
        self.assertEqual(
            set(_REMINDERS), agents_md,
            f"agent-roster rows out of sync with agents/: "
            f"missing={sorted(agents_md - set(_REMINDERS))} "
            f"stale={sorted(set(_REMINDERS) - agents_md)}",
        )


# --- Retry-context injection --------------------------------------------------
# on-subagent-start.py appends the locked task's most recent `### Attempt ❌`
# record to task-executor's context — the deterministic counterpart to the
# agent's own (skippable) Layer 3.R load. These tests are the load-bearing guard
# that a retry agent always receives the prior failure reason + suggested step.

def _flat_state():
    """One phase, one in_progress task at P1T1 (retry_count irrelevant to resolve)."""
    return {
        "current_phase_index": 1,
        "current_task_index": 1,
        "phases": [{"tasks": [{"name": "demo task", "status": "in_progress"}]}],
    }


def _subtask_state():
    """P1T1 with subtask 2 locked in_progress."""
    return {
        "current_phase_index": 1,
        "current_task_index": 1,
        "current_subtask_index": 2,
        "phases": [{"tasks": [{"name": "demo task", "subtasks": [
            {"name": "sub one", "status": "completed"},
            {"name": "sub two", "status": "in_progress"},
        ]}]}],
    }


@contextlib.contextmanager
def _track(state, handoff_body, *, phase=1, task=1):
    """Build an isolated track under a tmp dir and yield its cwd.

    Creates ``conductor/tracks/demo/track-state.json`` (so locked_task.resolve
    finds it) and, when handoff_body is not None, the matching handoff file.
    """
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

**What Was Done**: wrote foo.py, left tests red
**Failure Reason**: test_TC_1_1 timed out at 30s
**Suggested Next Step**: raise timeout to 120s, re-run

## Exploration Notes

### Summary
exploration map here
"""

_FRESH_HANDOFF = """# Handoff: demo

## Exploration Notes

### Summary
exploration map here
"""

_SUCCESS_HANDOFF = """# Handoff: demo

## Execution Record

### Attempt 1/3 | 2026-06-30T00:00:00Z ❌

**Failure Reason**: first try failed

### Attempt 2/3 | 2026-06-30T00:01:00Z ✅

**Commit**: abc123
"""

_SUBTASK_HANDOFF = """# Handoff: demo

## Subtask 1: sub one

### Attempt 1/3 | 2026-06-30T00:00:00Z ✅

## Subtask 2: sub two

### Attempt 1/3 | 2026-06-30T00:00:00Z ❌

**Failure Reason**: subtask two blew up
**Suggested Next Step**: fix the parser first
"""


def _ctx(agent_type, state, handoff_body):
    with _track(state, handoff_body) as cwd:
        return _run(agent_type, cwd=cwd).get("hookSpecificOutput", {}).get("additionalContext", "")


class RetryContextInjectionTests(TestCase):
    def test_retry_agent_receives_prior_failure_record(self):
        ctx = _ctx("task-executor", _flat_state(), _FAILURE_HANDOFF)
        self.assertIn("[Conductor Retry]", ctx)
        self.assertIn("Failure Reason", ctx)
        self.assertIn("test_TC_1_1 timed out at 30s", ctx)
        self.assertIn("Suggested Next Step", ctx)
        self.assertIn("raise timeout to 120s", ctx)

    def test_fresh_task_gets_no_retry_context(self):
        """No prior Attempt records → no injection, but floor+reminder survive."""
        ctx = _ctx("task-executor", _flat_state(), _FRESH_HANDOFF)
        self.assertNotIn("[Conductor Retry]", ctx)
        self.assertIn("TASK RESULT", ctx)

    def test_no_handoff_file_gets_no_retry_context(self):
        ctx = _ctx("task-executor", _flat_state(), None)
        self.assertNotIn("[Conductor Retry]", ctx)

    def test_non_retry_agent_gets_no_retry_context(self):
        """A retry handoff must not leak into a non-retry agent's context."""
        ctx = _ctx("code-reviewer", _flat_state(), _FAILURE_HANDOFF)
        self.assertNotIn("[Conductor Retry]", ctx)

    def test_trailing_success_suppresses_injection(self):
        """Latest Attempt ✅ → task completed, not a retry → no injection."""
        ctx = _ctx("task-executor", _flat_state(), _SUCCESS_HANDOFF)
        self.assertNotIn("[Conductor Retry]", ctx)

    def test_subtask_scoped_retry_context(self):
        """The locked subtask's failure record is read, not a sibling's."""
        ctx = _ctx("task-executor", _subtask_state(), _SUBTASK_HANDOFF)
        self.assertIn("[Conductor Retry]", ctx)
        self.assertIn("subtask two blew up", ctx)
        # subtask 1's history must NOT bleed in
        self.assertNotIn("first try failed", ctx)

    def test_retry_context_follows_reminder(self):
        ctx = _ctx("task-executor", _flat_state(), _FAILURE_HANDOFF)
        self.assertLess(ctx.index("TASK RESULT"), ctx.index("[Conductor Retry]"))

    def test_floor_still_precedes_reminder_with_retry_present(self):
        ctx = _ctx("task-executor", _flat_state(), _FAILURE_HANDOFF)
        self.assertLess(ctx.index("Validate every tool call"), ctx.index("TASK RESULT"))


if __name__ == "__main__":
    main()
