r"""Behavioral tests for on-subagent-stop.py — the recovery guard.

Pins the post-collapse recovery model: a fresh result.json is the single
completion signal for result-file agents (task-executor, explorer); stdout-block
agents (phase-checker, code-reviewer) are gated on their close tag. Critically, a written
FAILURE result.json is a VALID signal and must NOT trigger a recovery block
(failures flow through the orchestrator's retry/skip path) — this is the
behavioral change from the old prose-based detect_failure, which force-blocked
on any "status: FAILURE" / "Traceback" text.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
_HOOK = _scripts / "on-subagent-stop.py"


def _write_result(track_dir: Path, body: str):
    p = track_dir / ".conductor" / "result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


class RecoveryGuardTests(TestCase):
    def _run(self, agent_type: str, cwd: str, last_message: str = "",
             session_id: str = "test-sess", env: dict = None) -> tuple:
        hook_input = {
            "agent_type": agent_type,
            "session_id": session_id,
            "cwd": cwd,
            "last_assistant_message": last_message,
        }
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        proc = subprocess.run(
            [sys.executable, str(_HOOK)],
            input=json.dumps(hook_input),
            capture_output=True, text=True, env=run_env,
        )
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc.returncode, out

    # --- result-file agents: gated on fresh result.json ---

    def test_task_executor_with_fresh_result_allows(self):
        with tempfile.TemporaryDirectory() as d:
            _write_result(Path(d), '{"status":"SUCCESS"}')
            rc, out = self._run("task-executor", d)
            self.assertEqual(rc, 0)
            self.assertNotIn("decision", out)

    def test_task_executor_without_result_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run("task-executor", d)
            self.assertEqual(rc, 2)
            self.assertEqual(out["decision"], "block")
            self.assertIn("write-result", out["reason"])

    def test_explorer_without_result_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run("explorer", d)
            self.assertEqual(rc, 2)
            self.assertIn("write-result", out["reason"])

    # --- THE design change: a written FAILURE result is a valid signal ---

    def test_task_executor_failure_result_does_not_block(self):
        """A FAILURE result.json means the agent reported correctly — the
        orchestrator's retry/skip path handles it. Must NOT force a recovery
        turn (old prose detect_failure blocked on 'status: FAILURE')."""
        with tempfile.TemporaryDirectory() as d:
            _write_result(Path(d), '{"status":"FAILURE","summary":"boom"}')
            rc, out = self._run("task-executor", d)
            self.assertEqual(rc, 0)
            self.assertNotIn("decision", out)

    def test_stale_result_json_still_blocks(self):
        """A result.json older than the freshness window is treated as absent —
        the agent didn't write one THIS turn."""
        with tempfile.TemporaryDirectory() as d:
            _write_result(Path(d), '{"status":"SUCCESS"}')
            ts = time.time() - 600  # 10 min old
            os_ts = (ts, ts)
            import os
            os.utime(Path(d) / ".conductor" / "result.json", os_ts)
            rc, out = self._run("task-executor", d)
            self.assertEqual(rc, 2)

    # --- stdout-block agent: phase-checker gated on close tag ---

    def test_phase_checker_with_close_tag_allows(self):
        with tempfile.TemporaryDirectory() as d:
            msg = "work...\n---CHECKPOINT RESULT---\nSTATUS: PASSED\n---END RESULT---"
            rc, out = self._run("phase-checker", d, last_message=msg)
            self.assertEqual(rc, 0)

    def test_phase_checker_without_close_tag_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run("phase-checker", d, last_message="stopped early")
            self.assertEqual(rc, 2)
            self.assertIn("CHECKPOINT RESULT", out["reason"])

    def test_phase_checker_block_after_long_preamble(self):
        """Regression: close tag at END of a >2KB turn must still be found."""
        with tempfile.TemporaryDirectory() as d:
            preamble = "Checking phase readiness. " * 150  # >2KB
            msg = preamble + "\n---CHECKPOINT RESULT---\nSTATUS: PASSED\n---END RESULT---"
            rc, _ = self._run("phase-checker", d, last_message=msg)
            self.assertEqual(rc, 0)

    # --- stdout-block agent: code-reviewer gated on close tag (like phase-checker) ---

    def test_code_reviewer_with_close_tag_allows(self):
        with tempfile.TemporaryDirectory() as d:
            msg = "Review complete.\n---REVIEW RESULT---\nSTATUS: APPROVE\n---END REVIEW RESULT---"
            rc, out = self._run("code-reviewer", d, last_message=msg)
            self.assertEqual(rc, 0)
            self.assertNotIn("decision", out)

    def test_code_reviewer_without_close_tag_blocks(self):
        """code-reviewer now runs in the SYNC SubagentStop entry and is gated on
        its ---END REVIEW RESULT--- close tag (it was previously async /
        advisory-only, so a review that exhausted turns before emitting its block
        was silently lost). A missing close tag earns one recovery turn."""
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run("code-reviewer", d, last_message="stopped mid-review")
            self.assertEqual(rc, 2)
            self.assertEqual(out["decision"], "block")
            self.assertIn("REVIEW RESULT", out["reason"])

    # --- stdout-block agents: corpus-writer + spec-planner gated on close tag ---
    # (doc-syncer was split into corpus-writer [Phase 1] + wiki-synthesizer
    # [Phase 2]; both are STDOUT_BLOCK agents sharing the ---DOC SYNC RESULT---
    # delimiter. corpus-writer is tested here as the representative.)

    def test_corpus_writer_without_close_tag_blocks(self):
        """corpus-writer (Phase 1 of the doc-sync split) runs STDOUT_BLOCK_AGENTS
        — a stop without its ---END RESULT--- close tag earns a recovery turn."""
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run("corpus-writer", d, last_message="stopped mid-sync")
            self.assertEqual(rc, 2)
            self.assertEqual(out["decision"], "block")
            self.assertIn("DOC SYNC RESULT", out["reason"])

    def test_corpus_writer_with_close_tag_allows(self):
        with tempfile.TemporaryDirectory() as d:
            msg = "Sync done.\n---DOC SYNC RESULT---\nPHASE: 1\nSTATUS: COMPLETED\n---END RESULT---"
            rc, out = self._run("corpus-writer", d, last_message=msg)
            self.assertEqual(rc, 0)
            self.assertNotIn("decision", out)

    def test_spec_planner_without_close_tag_blocks(self):
        """spec-planner runs STDOUT_BLOCK_AGENTS — its ---SPEC PLAN RESULT---
        block is the completion signal the parent parses for STATUS (the plan
        structure itself is derived from plan.md by init-from-plan, not from
        this block), so a crash before emitting ---END SPEC PLAN RESULT--- must
        earn a recovery turn rather than silently losing the write confirmation."""
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run("spec-planner", d, last_message="stopped mid-plan")
            self.assertEqual(rc, 2)
            self.assertEqual(out["decision"], "block")
            self.assertIn("SPEC PLAN RESULT", out["reason"])

    def test_spec_planner_with_close_tag_allows(self):
        with tempfile.TemporaryDirectory() as d:
            msg = "Plan written.\n---SPEC PLAN RESULT---\nSTATUS: SUCCESS\n---END SPEC PLAN RESULT---"
            rc, out = self._run("spec-planner", d, last_message=msg)
            self.assertEqual(rc, 0)
            self.assertNotIn("decision", out)

    def test_spec_reviewer_without_close_tag_blocks(self):
        """spec-reviewer runs STDOUT_BLOCK_AGENTS — its ---REVIEW RESULT--- block is
        the only signal the parent parses for STATUS (APPROVED/CANCELLED/FAILURE).
        Previously it fell into the no-recovery-contract branch, so a stop without
        the block was silently lost and the parent fell back to reading the files
        directly. A missing close tag earns one recovery turn."""
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run("spec-reviewer", d, last_message="stopped mid-review")
            self.assertEqual(rc, 2)
            self.assertEqual(out["decision"], "block")
            self.assertIn("REVIEW RESULT", out["reason"])

    def test_spec_reviewer_with_close_tag_allows(self):
        with tempfile.TemporaryDirectory() as d:
            msg = "Review done.\n---REVIEW RESULT---\nSTATUS: APPROVED\n---END REVIEW RESULT---"
            rc, out = self._run("spec-reviewer", d, last_message=msg)
            self.assertEqual(rc, 0)
            self.assertNotIn("decision", out)

    def test_spec_reviewer_recovery_instruction_is_noninteractive(self):
        """Regression: spec-reviewer was an interactive agent (AskUserQuestion
        review loop, wrote review-result.json, emitted STATUS: CANCELLED). Run as
        a fire-and-forget subagent it could never complete its human loop → it
        stopped without a result block every time ("always returns non-standard
        result"). It is now read-only/non-interactive: the recovery instruction
        must NOT tell it to write review-result.json (it's read-only) or emit
        CANCELLED (no interactive loop to cancel), and the agent file must declare
        read-only tools with NO AskUserQuestion."""
        # The recovery instruction lives in the agent-roster registry (its
        # stdout-block row for spec-reviewer).
        sys.path.insert(0, str(_scripts.parent))
        from track_state import agent_roster as ar
        instr = ar.recovery_instruction_for("spec-reviewer")
        # Stale interactive artifacts must be gone from the instruction.
        self.assertNotIn("review-result.json", instr,
                         "spec-reviewer is read-only — must not write review-result.json")
        self.assertNotIn("CANCELLED", instr,
                         "non-interactive agent has no loop to cancel — no CANCELLED status")
        # New non-interactive contract is present.
        self.assertIn("CHANGES_REQUESTED", instr)
        self.assertIn("AskUserQuestion", instr)  # mentioned as a prohibition
        # Agent file: read-only tools, no AskUserQuestion in frontmatter tools.
        agent = (_scripts.parent / "agents" / "spec-reviewer.md").read_text()
        fm_tools = agent.split("tools:", 1)[1].splitlines()[0]
        self.assertNotIn("AskUserQuestion", fm_tools)
        self.assertNotIn("Edit", fm_tools)
        self.assertNotIn("Write", fm_tools)

    # --- Session-scoped bounding: STDOUT-block recovery must not loop forever ---

    def test_stdout_block_recovery_is_bounded_after_max_turns(self):
        """Regression: STDOUT-block agents (spec-reviewer etc.) run with NO locked
        task cursor, so their recovery was UNBOUNDED — every no-block stop forced
        another recovery turn with no escape, burning the whole maxTurns budget
        before exhausting with no block (the "always returns non-standard result"
        failure mode). The counter is now session-scoped
        (lib.recovery.increment_session_recovery): after MAX_RECOVERY_TURNS
        forced turns, the hook stops blocking and lets the stop land so the agent
        dies honestly instead of looping. Isolate CLAUDE_PLUGIN_DATA + use a
        unique session_id so sibling tests' counters don't interfere."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pcc_recovery", _scripts / "lib" / "recovery.py")
        rec = importlib.util.module_from_spec(spec)
        # Need scripts/ on the path so ``from .env import get_data_dir`` resolves.
        sys.path.insert(0, str(_scripts))
        spec.loader.exec_module(rec)
        sys.path.pop(0)
        with tempfile.TemporaryDirectory() as data_root, \
                tempfile.TemporaryDirectory() as d:
            env = {"CLAUDE_PLUGIN_DATA": data_root}
            sid = "bound-sess-unique"
            # First MAX_RECOVERY_TURNS no-block stops must block (force recovery).
            for _ in range(rec.MAX_RECOVERY_TURNS):
                rc, out = self._run("spec-reviewer", d, last_message="no block",
                                    session_id=sid, env=env)
                self.assertEqual(rc, 2, "should still force recovery within budget")
                self.assertEqual(out["decision"], "block")
            # The next no-block stop exceeds the budget → the hook must ALLOW the
            # stop (rc 0, no decision) instead of looping forever.
            rc, out = self._run("spec-reviewer", d, last_message="still no block",
                                session_id=sid, env=env)
            self.assertEqual(rc, 0)
            self.assertNotIn("decision", out)

    def test_stdout_block_recovery_clears_on_success(self):
        """A dispatch that eventually emits its block has its counter cleared, so
        a later dispatch reusing the token (rare) starts at a fresh budget — not
        already at the cap from the prior stuck dispatch."""
        with tempfile.TemporaryDirectory() as data_root, \
                tempfile.TemporaryDirectory() as d:
            env = {"CLAUDE_PLUGIN_DATA": data_root}
            sid = "clear-sess-unique"
            # Burn one recovery turn, then succeed (emit the block).
            self._run("spec-reviewer", d, last_message="no block",
                      session_id=sid, env=env)
            msg = "done\n---REVIEW RESULT---\nSTATUS: APPROVED\n---END REVIEW RESULT---"
            rc, _ = self._run("spec-reviewer", d, last_message=msg,
                              session_id=sid, env=env)
            self.assertEqual(rc, 0)
            # Counter cleared → a fresh no-block stop is back to forcing recovery.
            rc, out = self._run("spec-reviewer", d, last_message="no block",
                                session_id=sid, env=env)
            self.assertEqual(rc, 2)
            self.assertEqual(out["decision"], "block")

    # --- async agents: still no recovery contract ---

    def test_async_agent_without_result_allows(self):
        """skip-analyst remains async in hooks.json with no recovery contract —
        a missing result must NOT block (and async blocks are a no-op anyway)."""
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run("skip-analyst", d)
            self.assertEqual(rc, 0)
            self.assertNotIn("decision", out)

    # --- Fix B: stop telemetry carries the dispatched-for task when lock is gone ---

    def test_stop_event_carries_inflight_task_when_lock_gone(self):
        """When the lock is released before SubagentStop (common — finalize moves
        the cursor on), the stop telemetry must still carry (phase, task) from
        the persistent inflight marker, so the dispatch is joinable to its task
        in dispatch-lifecycle.log. Without this the line renders phase=- task=-."""
        import os
        with tempfile.TemporaryDirectory() as data_root, \
                tempfile.TemporaryDirectory() as track_cwd:
            # An inflight marker for P2.T3 (the dispatch was for THIS task).
            cdir = Path(track_cwd) / ".conductor"
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / ".dispatch-inflight-2-3.json").write_text(json.dumps({
                "phase": 2, "task": 3, "subtask": None,
                "start_sha": "abc", "written_at": "2026-07-22T00:00:00+00:00",
                "gen": 1,
            }))
            # No track-state.json → no lock resolved → fallback path engaged.
            env = dict(os.environ, CLAUDE_PLUGIN_DATA=data_root)
            hook_input = {
                "agent_type": "skip-analyst",  # async → no block; pure telemetry
                "session_id": "fixb-sess",
                "cwd": track_cwd,
                "last_assistant_message": "",
            }
            proc = subprocess.run(
                [sys.executable, str(_HOOK)],
                input=json.dumps(hook_input),
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(proc.returncode, 0)
            log = Path(data_root) / "logs" / "dispatch-lifecycle.log"
            self.assertTrue(log.exists(), "lifecycle log should have been written")
            last_stop = [ln for ln in log.read_text().splitlines()
                         if "event=stop" in ln and "agent=skip-analyst" in ln][-1]
            self.assertIn("phase=2", last_stop)
            self.assertIn("task=3", last_stop)


if __name__ == "__main__":
    main()
