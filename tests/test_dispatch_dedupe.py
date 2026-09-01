"""Tests for the PreToolUse:Agent dispatch-dedupe hook (on-dispatch-dedupe.py).

The hook enforces the single-writer invariant for a locked task: if a
task-executor/explorer dispatch is already in flight (marker present, HEAD still
the Start commit, no result.json), a second ``Agent`` dispatch for that same
task is ``permissionDecision: "deny"`` before it spawns. Otherwise allow.

Property-level (pin the invariant, not the implementation): the hook reads the
same marker + HEAD/result predicate the spine uses, so these tests drive that
predicate directly and assert allow/deny — they do not assert on internals.
Fail-open is asserted too: corrupt state must allow, never raise.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_HOOK = _scripts / "on-dispatch-dedupe.py"

# Import the lib directly for marker setup (lightweight, no track_state import).
from lib import dispatch_inflight as inflight  # noqa: E402
from lib import dispatch_lifecycle as lifecycle  # noqa: E402


# --- shared fixtures ----------------------------------------------------------
def _git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    return d


def _commit(d, msg, body="# plan\n"):
    # Commit a conductor-managed file so HEAD advances. `body` lets a second
    # commit in the same repo change content (else "nothing added").
    path = os.path.join(d, ".conductor", "plan.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
    subprocess.run(["git", "add", "--", ".conductor/plan.md"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=d, check=True)


def _short_head(d):
    return subprocess.run(
        ["git", "rev-parse", "--short=7", "HEAD"], cwd=d,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _write_locked_track(d, tid="demo_20260716", phase=1, task=1, subtask=None):
    """Write a track-state.json with the cursor on an in_progress task."""
    track_dir = os.path.join(d, "conductor", "tracks", tid)
    os.makedirs(track_dir, exist_ok=True)
    state = {
        "track_id": tid,
        "current_phase_index": phase,
        "current_task_index": task,
        "current_subtask_index": subtask,
        "phases": [{"name": "P1", "tasks": [
            {"name": "T1", "status": "in_progress", "commit_sha": None}]}],
    }
    with open(os.path.join(track_dir, "track-state.json"), "w") as f:
        json.dump(state, f)
    return track_dir


def _stamp_marker(track_dir, phase, task, subtask, start_sha):
    inflight.write(track_dir, phase, task, subtask, start_sha, "2026-07-16T00:00:00+00:00")


def _run_hook(cwd, subagent_type="task-executor"):
    payload = {
        "tool_name": "Agent", "cwd": cwd,
        "tool_input": {"subagent_type": subagent_type, "prompt": "x"},
    }
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


# --- hook tests ---------------------------------------------------------------
class DispatchDedupeHookTests(TestCase):
    def setUp(self):
        self.repo = _git_repo()
        _commit(self.repo, "chore(conductor): Start task 'T1' [P1.T1]")
        self.start_sha = _short_head(self.repo)
        self.track_dir = _write_locked_track(self.repo)

    def test_non_agent_tool_is_allowed(self):
        rc, out = _run_hook(self.repo)
        # Override payload to a Bash tool — must allow unconditionally.
        payload = {"tool_name": "Bash", "cwd": self.repo,
                   "tool_input": {"command": "ls"}}
        proc = subprocess.run(
            [sys.executable, str(_HOOK)],
            input=json.dumps(payload), capture_output=True, text=True,
        )
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(decision, "allow")

    def test_non_write_agent_is_allowed(self):
        # phase-checker is read-only → not single-writer-critical → allow.
        rc, out = _run_hook(self.repo, subagent_type="phase-checker")
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(decision, "allow")

    def test_no_marker_is_allowed(self):
        # Fresh state — no prior dispatch recorded → allow.
        rc, out = _run_hook(self.repo)
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(decision, "allow")

    def test_inflight_dispatch_is_denied(self):
        # Marker present, HEAD still the Start commit, no result.json → in flight.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        rc, out = _run_hook(self.repo)
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        # Reason must name the task and prescribe the TERMINATING recovery
        # (`dispatch-finalize`), NOT `step`. In this exact state `step`
        # re-emits `dispatch` and would loop the model back here, so the
        # directive must be the finalize command. We assert the prescribed
        # action (the `Run \`...<cmd>...\`` clause), not mere substring
        # presence — the reason legitimately *warns against* `step` too.
        reason = spec.get("permissionDecisionReason", "")
        self.assertIn("P1T1", reason)
        self.assertIn("dispatch-finalize", reason)
        self.assertIn('run `track-state dispatch-finalize', reason)
        # And it must explicitly warn off the looping path.
        self.assertIn("Do NOT re-run", reason)
        # The reason must first offer the WAIT branch (the agent may still be
        # running — foreground or auto-backgrounded) before prescribing
        # finalize: finalizing a live agent burns a retry for work about to
        # land.
        self.assertIn("WAIT for it", reason)

    def test_spawn_stamp_lifecycle_allows_first_denies_second(self):
        # End-to-end regression for the 2026-09-01 dispatch-deadlock incident.
        # The marker is stamped at SPAWN (lib.dispatch_inflight.stamp, called
        # by on-subagent-start), not at prepare — so the full lifecycle is:
        # (1) prepared, not yet spawned → NO marker → first dispatch ALLOWED
        #     (the incident: a prepare-time stamp denied this very spawn);
        # (2) spawned → marker present, HEAD == start_sha, no result.json →
        #     a SECOND dispatch DENIED (the guard's actual purpose);
        # (3) finalize clears the marker → next dispatch ALLOWED again.
        # (2) uses the production stamp path (real gen bump + live HEAD), not
        # a fabricated marker — pinning that stamp() writes exactly the values
        # this hook's predicate consumes.
        # Step 1: fresh dispatch — allow.
        rc, out = _run_hook(self.repo)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow",
            "a prepared-but-not-spawned dispatch must be allowed (no marker)")
        self.assertIsNone(inflight.read(self.track_dir, 1, 1, None))
        # Step 2: the spawn fires → marker stamped → a second dispatch denied.
        stamped = inflight.stamp(self.track_dir, 1, 1, None)
        self.assertIsNotNone(stamped, "spawn stamp must write the marker")
        self.assertEqual(stamped["start_sha"], self.start_sha,
                         "stamp must record the live HEAD (the Start commit)")
        rc, out = _run_hook(self.repo)
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny",
                         "a second spawn while the first runs must be denied")
        self.assertIn("P1T1", spec.get("permissionDecisionReason", ""))
        # Step 3: finalize's marker clear → the guard releases → allow.
        inflight.clear(self.track_dir, 1, 1, None)
        rc, out = _run_hook(self.repo)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow",
            "after finalize clears the marker the next dispatch is allowed")

    def test_denies_explorer_too(self):
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        rc, out = _run_hook(self.repo, subagent_type="explorer")
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_denies_namespaced_dispatch_form(self):
        # `conductor:task-executor` (installed-plugin dispatch) must hit the
        # single-writer gate — before agent_roster.canonical_name the lookup
        # missed and an in-flight marker could not deny the duplicate.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        rc, out = _run_hook(self.repo, subagent_type="conductor:task-executor")
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_allows_namespaced_unrostered_agent(self):
        # Fail-open preserved: a namespaced but unrostered agent is not
        # single-writer-critical → allow (same as the bare unrostered form).
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        rc, out = _run_hook(self.repo, subagent_type="conductor:mystery-agent")
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_allows_after_head_advances_and_clears_marker(self):
        # Marker present but HEAD moved past the Start commit → not in flight.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        _commit(self.repo, "feat: real work landed", body="# plan v2\n")  # advance HEAD
        rc, out = _run_hook(self.repo)
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "allow")
        # Stale marker must be cleared.
        self.assertFalse(inflight.read(self.track_dir, 1, 1, None))

    def test_allows_when_result_json_present(self):
        # HEAD still the Start commit, but a result.json landed → the dispatch
        # returned a verdict → not in flight → allow.
        _stamp_marker(self.track_dir, 1, 1, None, self.start_sha)
        result_path = os.path.join(self.track_dir, ".conductor", "result.json")
        os.makedirs(os.path.dirname(result_path), exist_ok=True)
        with open(result_path, "w") as f:
            json.dump({"status": "success"}, f)
        rc, out = _run_hook(self.repo)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_corrupt_marker_is_allowed_failopen(self):
        # Bad-JSON marker → tolerant reader returns None → allow, no raise.
        marker = inflight.marker_path(self.track_dir, 1, 1, None)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{ not valid json")
        rc, out = _run_hook(self.repo)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_no_locked_task_is_allowed(self):
        # A repo with no in_progress cursor → resolve() returns None → allow.
        repo = _git_repo()
        _commit(repo, "init")
        rc, out = _run_hook(repo)
        self.assertEqual(rc, 0)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "allow")

    def test_probe_emits_nonempty_session_via_transcript_path(self):
        """The probe must carry a non-empty session even when PreToolUse
        delivers ``session_id`` empty. The transcript_path stem is the
        fallback. This pins the join-key fix: a probe with ``session=`` makes
        a captured relapse impossible to disambiguate from start/stop lines.
        """
        tmp_data = tempfile.mkdtemp()
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=tmp_data)
        sid = "deadbeef-0000-1111-2222-333333333333"
        payload = {
            "tool_name": "Agent", "cwd": self.repo,
            # session_id deliberately absent (the real PreToolUse case); the
            # transcript_path stem must become the join key.
            "transcript_path": f"/home/u/.claude/projects/proj/{sid}.jsonl",
            "tool_input": {"subagent_type": "task-executor", "prompt": "x"},
        }
        proc = subprocess.run(
            [sys.executable, str(_HOOK)],
            input=json.dumps(payload), capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        log_path = Path(tmp_data) / "logs" / "dispatch-lifecycle.log"
        self.assertTrue(log_path.exists(), "lifecycle log not written")
        probe_lines = [
            ln for ln in log_path.read_text().splitlines()
            if "event=probe" in ln and f"session={sid}" in ln
        ]
        self.assertTrue(
            probe_lines,
            f"no probe line with session={sid}; got:\n"
            f"{log_path.read_text()}",
        )


# --- session_token unit tests -------------------------------------------------
class DispatchLifecycleSessionTokenTests(TestCase):
    """``lifecycle.session_token`` is the shared join key for probe/start/stop.

    PreToolUse sometimes delivers ``session_id`` empty; the transcript_path
    stem is the deterministic fallback so all three events agree on session.
    """
    def test_prefers_explicit_session_id(self):
        self.assertEqual(
            lifecycle.session_token(
                {"session_id": "abc",
                 "transcript_path": "/p/x/def.jsonl"}),
            "abc")

    def test_falls_back_to_transcript_stem_when_session_id_empty(self):
        self.assertEqual(
            lifecycle.session_token(
                {"session_id": "",
                 "transcript_path": "/home/u/.claude/projects/proj/UUID-1.jsonl"}),
            "UUID-1")

    def test_falls_back_to_transcript_stem_when_session_id_absent(self):
        self.assertEqual(
            lifecycle.session_token(
                {"transcript_path": "/anywhere/SID.jsonl"}),
            "SID")

    def test_returns_dash_when_neither_present(self):
        self.assertEqual(lifecycle.session_token({}), "-")
        self.assertEqual(lifecycle.session_token(None), "-")

    def test_returns_dash_when_transcript_path_has_no_stem(self):
        self.assertEqual(lifecycle.session_token({"transcript_path": "/"}), "-")

    def test_never_raises_on_garbage(self):
        # Non-dict input or weird values must not raise.
        self.assertEqual(lifecycle.session_token("not a dict"), "-")
        self.assertEqual(lifecycle.session_token(
            {"transcript_path": 12345}), "-")


# --- marker-lib unit tests ----------------------------------------------------
class DispatchInflightLibTests(TestCase):
    def test_write_read_roundtrip(self):
        d = tempfile.mkdtemp()
        inflight.write(d, 1, 2, 3, "abc1234", "2026-07-16T00:00:00+00:00")
        m = inflight.read(d, 1, 2, 3)
        self.assertIsNotNone(m)
        self.assertEqual(m["phase"], 1)
        self.assertEqual(m["task"], 2)
        self.assertEqual(m["subtask"], 3)
        self.assertEqual(m["start_sha"], "abc1234")

    def test_read_missing_returns_none(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(inflight.read(d, 1, 1, None))

    def test_read_corrupt_returns_none(self):
        d = tempfile.mkdtemp()
        p = inflight.marker_path(d, 1, 1, None)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("garbage")
        self.assertIsNone(inflight.read(d, 1, 1, None))

    def test_clear_removes_marker(self):
        d = tempfile.mkdtemp()
        inflight.write(d, 1, 1, None, "abc1234", "t")
        self.assertIsNotNone(inflight.read(d, 1, 1, None))
        inflight.clear(d, 1, 1, None)
        self.assertIsNone(inflight.read(d, 1, 1, None))

    def test_clear_missing_is_noop(self):
        d = tempfile.mkdtemp()
        inflight.clear(d, 1, 1, None)  # must not raise

    def test_clear_all_removes_every_marker(self):
        d = tempfile.mkdtemp()
        inflight.write(d, 1, 1, None, "aaaaaaa", "t")
        inflight.write(d, 2, 3, 4, "bbbbbbb", "t")
        inflight.clear_all(d)
        self.assertIsNone(inflight.read(d, 1, 1, None))
        self.assertIsNone(inflight.read(d, 2, 3, 4))

    def test_subtask_none_omits_suffix(self):
        d = tempfile.mkdtemp()
        p = inflight.marker_path(d, 1, 1, None)
        self.assertNotIn("-None", p.name)
        self.assertEqual(p.name, ".dispatch-inflight-1-1.json")


# --- gen marker tests --------------------------------------------------------
class InflightGenTests(TestCase):
    """The inflight marker carries a dispatch ``gen`` (monotonic per
    phase/task/subtask), bumped by the spine on every write. Two probes with the
    SAME gen = a single dispatch spawned twice (the concurrent relapse); a higher
    gen on the second = the spine re-dispatched (fresh prepare)."""

    def test_first_write_stamps_gen_1(self):
        d = tempfile.mkdtemp()
        inflight.write(d, 1, 1, None, "abc1234", "2026-07-17T00:00:00+00:00")
        self.assertEqual(inflight.read_gen(d, 1, 1, None), 1)
        marker = inflight.read(d, 1, 1, None)
        self.assertEqual(marker["gen"], 1)

    def test_read_gen_zero_when_no_marker(self):
        d = tempfile.mkdtemp()
        self.assertEqual(inflight.read_gen(d, 1, 1, None), 0)

    def test_read_gen_defaults_missing_field_to_1(self):
        """A marker written before gen existed has no gen key → read_gen treats
        it as gen 1 (so the spine's next bump lands on 2, not on garbage)."""
        import json as _json
        from pathlib import Path
        d = tempfile.mkdtemp()
        p = inflight.marker_path(d, 1, 1, None)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps({"phase": 1, "task": 1, "start_sha": "x"}))
        self.assertEqual(inflight.read_gen(d, 1, 1, None), 1)

    def test_read_gen_tolerates_corrupt_gen(self):
        import json as _json
        d = tempfile.mkdtemp()
        p = inflight.marker_path(d, 1, 1, None)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps({"phase": 1, "task": 1, "start_sha": "x",
                                  "gen": "not-a-number"}))
        self.assertEqual(inflight.read_gen(d, 1, 1, None), 1)

    def test_clear_resets_gen(self):
        d = tempfile.mkdtemp()
        inflight.write(d, 1, 1, None, "abc1234", "t", gen=3)
        self.assertEqual(inflight.read_gen(d, 1, 1, None), 3)
        inflight.clear(d, 1, 1, None)
        self.assertEqual(inflight.read_gen(d, 1, 1, None), 0)


if __name__ == "__main__":
    main()
