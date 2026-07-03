r"""Tests for the ``decision`` blob emitted by ``cmd_recover`` (#1 transducer).

When recover surfaces a task that is ``failed`` + retries-exhausted +
``interactive``, it attaches a pre-computed Retry/Skip/Block ``decision`` so the
orchestrator stops judging retry-exhaustion and constructing bash — it
``AskUserQuestion → run decision.commands[choice] verbatim``. Continuous mode
gets no blob (skip-analyst owns it). The free-text task name is embedded only
inside ``shlex``-quoted ``git commit -m`` / ``--reason`` args, so a name with
quotes/backticks can't break the shell line; the whole blob is JSON-escaped by
``emit``, so the same name round-trips the transport.
"""
import io
import json
import shlex
import shutil
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.core import save
from scripts.track_state.dispatch import cmd_recover
from scripts.track_state.constants import MAX_RETRIES


def _out_captured(fn, *args, **kwargs):
    """Capture stdout (must be a single JSON object). Returns parsed dict."""
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        fn(*args, **kwargs)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _recent_iso():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _make_track_dir(task_status="failed", retry_count=MAX_RETRIES,
                    execution_mode="interactive", name="Task A"):
    """A track whose current task (P1.T1) is in the given state. No git needed —
    cmd_recover does not commit; it only reads state + emits."""
    d = tempfile.mkdtemp()
    Path(d, "plan.md").write_text(f"# Plan\n\n## Phase 1: Build\n- [ ] {name}\n")
    state = {
        "track_id": "test",
        "type": "feature",
        "status": "in_progress",
        "description": "test track",
        "current_phase_index": 1,
        "current_task_index": 1,
        "execution_mode": execution_mode,
        "updated_at": _recent_iso(),
        "phases": [{
            "name": "Phase 1",
            "status": "pending",
            "tasks": [{
                "name": name,
                "status": task_status,
                "retry_count": retry_count,
                "last_failure_summary": "boom",
            }],
        }],
    }
    save(d, state)
    return d


class DecisionPresentTests(TestCase):
    def setUp(self):
        self.d = _make_track_dir()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_decision_present_when_failed_exhausted_interactive(self):
        result = _out_captured(cmd_recover, self.d)
        self.assertEqual(result["status"], "failed")
        self.assertIn("decision", result)

    def test_decision_shape(self):
        decision = _out_captured(cmd_recover, self.d)["decision"]
        # AskUserQuestion inputs.
        self.assertIn("Task A", decision["question"])
        self.assertEqual(decision["header"], "Failed task")
        labels = [o["label"] for o in decision["options"]]
        self.assertEqual(labels, ["Retry", "Skip", "Block"])
        for opt in decision["options"]:
            self.assertIn("description", opt)
        # Three command sets, each mutation + sync-plan + commit.
        self.assertEqual(set(decision["commands"]), {"Retry", "Skip", "Block"})
        for label in ("Retry", "Skip", "Block"):
            cmds = decision["commands"][label]
            self.assertEqual(len(cmds), 3, label)
            self.assertTrue(cmds[1].startswith("track-state sync-plan"), label)
            self.assertTrue(cmds[2].startswith("git commit -m "), label)
        # Routing map.
        self.assertEqual(decision["next"],
                         {"Retry": "3.1", "Skip": "3.1", "Block": "HALT"})

    def test_retry_command_targets_the_failed_task(self):
        cmds = _out_captured(cmd_recover, self.d)["decision"]["commands"]["Retry"]
        self.assertIn('--phase 1 --task 1', cmds[0])
        self.assertTrue(cmds[0].startswith('track-state reset '), cmds[0])

    def test_skip_and_block_carry_reasons(self):
        decision = _out_captured(cmd_recover, self.d)["decision"]
        self.assertIn("Skipped: failed task not required", decision["commands"]["Skip"][0])
        self.assertIn("Blocked: failed task needs human intervention",
                      decision["commands"]["Block"][0])


class DecisionAbsentTests(TestCase):
    def test_no_decision_when_retries_not_exhausted(self):
        d = _make_track_dir(retry_count=MAX_RETRIES - 1)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertNotIn("decision", _out_captured(cmd_recover, d))

    def test_no_decision_in_continuous_mode(self):
        d = _make_track_dir(execution_mode="continuous")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # continuous failed+exhausted is skip-analyst's job, not a decision blob.
        self.assertNotIn("decision", _out_captured(cmd_recover, d))

    def test_no_decision_when_task_not_failed(self):
        d = _make_track_dir(task_status="in_progress", retry_count=0)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertNotIn("decision", _out_captured(cmd_recover, d))

    def test_pending_task_emits_no_decision(self):
        # Pins the existing test_compact_output recover fixture behavior: a
        # pending task never trips the failed-exhausted gate.
        d = _make_track_dir(task_status="pending", retry_count=0)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertNotIn("decision", _out_captured(cmd_recover, d))


class ShellSafetyTests(TestCase):
    """A free-text task name with shell metacharacters must not break the
    command lines the orchestrator runs verbatim, and must round-trip JSON."""

    NAME = 'na`me"$x'

    def setUp(self):
        self.d = _make_track_dir(name=self.NAME)
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_name_round_trips_json_transport(self):
        # cmd_recover emits via json.dumps; _out_captured parses it back. If the
        # name survived, it round-tripped the JSON transport.
        decision = _out_captured(cmd_recover, self.d)["decision"]
        self.assertIn(self.NAME, decision["question"])

    def test_commit_lines_are_shell_safe_and_keep_name(self):
        decision = _out_captured(cmd_recover, self.d)["decision"]
        for label in ("Retry", "Skip", "Block"):
            commit_line = decision["commands"][label][2]
            # shlex.split raises ValueError on unbalanced quotes — the failure
            # mode of an unquoted name baked into `git commit -m "..."`.
            parts = shlex.split(commit_line)
            self.assertEqual(parts[0], "git", label)
            msg = parts[parts.index("-m") + 1]
            self.assertIn(self.NAME, msg, label)

    def test_reason_args_are_shell_safe(self):
        decision = _out_captured(cmd_recover, self.d)["decision"]
        for label in ("Skip", "Block"):
            mut_line = decision["commands"][label][0]
            # Parses cleanly (no unbalanced quote from the quoted --reason).
            shlex.split(mut_line)


class CompactSurvivesTests(TestCase):
    """`decision` is in the recover compact allowlist — default emit keeps it."""

    def test_decision_survives_default_compact(self):
        d = _make_track_dir()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        # compact=True is the default the orchestrator sees.
        result = _out_captured(cmd_recover, d)
        self.assertIn("decision", result)


if __name__ == "__main__":
    main()
