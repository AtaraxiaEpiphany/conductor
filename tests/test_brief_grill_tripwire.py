"""Tests for ``scripts/on-brief-grill-tripwire.py`` — the brief grill guard.

Feeds the guard a synthetic hook payload on stdin and asserts the
permissionDecision. Covers: deny a brief.md write during an incomplete grill
(marker committed:false, counter below floor); allow once the counter reaches
the floor; allow when the marker is finalized (committed:true) or absent;
allow non-brief writes; the AskUserQuestion matcher increments the counter.
Isolates CLAUDE_PLUGIN_DATA so sibling tests' counters don't interfere.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state import brief as br

_REPO = Path(__file__).resolve().parent.parent
_GUARD = _REPO / "scripts" / "on-brief-grill-tripwire.py"

# Must match the hook's floor — keep in sync with scripts/on-brief-grill-tripwire.py.
_MIN_FLOOR = 6


def _probe(project_dir, tool, file_path=None, cwd=None, questions=None):
    """Feed the guard a synthetic hook payload. ``questions`` (int) fabricates a
    tool_input.questions list of that length — the frontier-round payload the
    hook counts; None sends no tool_input (a degraded payload that must still
    count as 1 question, never 0)."""
    payload = {"tool_name": tool, "cwd": cwd or str(project_dir)}
    if file_path is not None:
        payload["tool_input"] = {"file_path": file_path}
    if questions is not None:
        payload.setdefault("tool_input", {})["questions"] = [
            {"question": f"q{i + 1}"} for i in range(questions)
        ]
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    env["CLAUDE_PLUGIN_DATA"] = str(project_dir / ".data")
    env["CLAUDE_PLUGIN_ROOT"] = str(project_dir)
    r = subprocess.run(
        [sys.executable, str(_GUARD)], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


def _set_marker(track_dir, committed):
    cdir = track_dir / ".conductor"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "brief-progress.json").write_text(json.dumps({
        "track_id": track_dir.name, "committed": committed,
    }))


def _set_grill_complete(track_dir, committed=False):
    """Write a marker carrying the explicit grill-complete signal — the real
    invariant that lets a well-done <MIN grill satisfy the gate."""
    cdir = track_dir / ".conductor"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "brief-progress.json").write_text(json.dumps({
        "track_id": track_dir.name,
        "committed": committed,
        "grill_complete": True,
    }))


def _make_registry(project_dir):
    """Create conductor/tracks.md so the hook's active-brief marker resolver can
    locate the track from a project-root cwd (mirrors the real conductor layout)."""
    (project_dir / "conductor").mkdir(parents=True, exist_ok=True)
    (project_dir / "conductor" / "tracks.md").write_text("# tracks\n")


def _track(project_dir, track_id="foo_20260728"):
    return project_dir / "conductor" / "tracks" / track_id


class BriefGrillTripwireTests(TestCase):
    def test_deny_brief_write_during_incomplete_grill(self):
        """A brief.md write while the marker is committed:false and the counter
        is below the floor must be DENIED — the §3 grill is incomplete and a
        brief written from guesses pollutes downstream planning."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            dec = _probe(Path(d), "Write", str(td / "brief.md"))
            self.assertEqual(dec, "deny")

    def test_allow_brief_write_after_grill_floor_reached(self):
        """Once MIN_GRILL_QUESTIONS questions are recorded, the brief.md write
        is allowed — the grill is sufficiently complete. The probes carry no
        payload (degraded input), so each counts 1: the unbatched floor path."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            for _ in range(_MIN_FLOOR):
                _probe(Path(d), "AskUserQuestion", cwd=str(td))
            dec = _probe(Path(d), "Write", str(td / "brief.md"))
            self.assertIsNone(dec)  # allow

    def test_askuserquestion_increments_counter(self):
        """The AskUserQuestion matcher must add the call's question count to
        the per-track counter — without it the floor can never be reached and
        every brief write is blocked forever. Probes carry explicit 1-question
        payloads; the sum crosses the floor exactly at the 6th question."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            # One short of the floor → still denied.
            for _ in range(_MIN_FLOOR - 1):
                _probe(Path(d), "AskUserQuestion", cwd=str(td), questions=1)
            self.assertEqual(_probe(Path(d), "Write", str(td / "brief.md")), "deny")
            # One more question reaches the floor → allowed.
            _probe(Path(d), "AskUserQuestion", cwd=str(td), questions=1)
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))

    def test_allow_brief_write_when_marker_finalized(self):
        """A committed:true marker means the brief is durable (post-§5 finalize)
        — a re-edit must NOT be blocked even if the counter is low."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=True)
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))

    def test_allow_brief_write_when_no_marker(self):
        """No marker (pre-init, or a human editing an old brief outside the
        skill) → allow. The guard only constrains an active grill."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            td.mkdir(parents=True, exist_ok=True)
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))

    def test_allow_non_brief_write_during_grill(self):
        """Only brief.md is gated — other writes pass through even mid-grill."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            self.assertIsNone(_probe(Path(d), "Write", str(td / "notes.md")))

    def test_corrupt_marker_fail_opens_to_allow(self):
        """A corrupt marker must NOT crash or block — fail-open (allow), mirroring
        the other conductor guards: a misbehaving guard is worse than none."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            cdir = td / ".conductor"
            cdir.mkdir(parents=True, exist_ok=True)
            (cdir / "brief-progress.json").write_text("{ not json")
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))


class FrontierRoundCountingTests(TestCase):
    """D1 semantics: the counter counts QUESTIONS, not calls. A frontier round
    (grill-discipline §3) batches up to 4 mutually-independent decisions into
    one AskUserQuestion call; the hook reads ``tool_input.questions`` and bumps
    by its length. The floor keeps its meaning both ways: a batched grill
    reaches it in fewer round-trips, and batching can never LOWER it."""

    def test_batched_round_counts_its_questions(self):
        """2 frontier calls × 4 questions = 8 ≥ 6 → allow in 2 round-trips —
        the whole point of counting questions: a well-batched grill isn't
        penalized with extra round-trips to satisfy a call-count floor."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            for _ in range(2):
                _probe(Path(d), "AskUserQuestion", cwd=str(td), questions=4)
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))

    def test_two_single_questions_still_below_floor(self):
        """2 calls × 1 question = 2 < 6 → deny. Batching does not lower the
        floor: a shortcut grill stays blocked whether or not it batches."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            for _ in range(2):
                _probe(Path(d), "AskUserQuestion", cwd=str(td), questions=1)
            self.assertEqual(_probe(Path(d), "Write", str(td / "brief.md")), "deny")

    def test_mixed_batch_sizes_sum_across_calls(self):
        """1 call × 4 + 1 call × 2 = 6 → allow. The counter sums across calls
        of different batch sizes (frontiers aren't uniformly full)."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            _probe(Path(d), "AskUserQuestion", cwd=str(td), questions=4)
            self.assertEqual(_probe(Path(d), "Write", str(td / "brief.md")), "deny")  # 4
            _probe(Path(d), "AskUserQuestion", cwd=str(td), questions=2)
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))  # 6

    def test_empty_question_list_counts_one(self):
        """An empty ``questions[]`` is a malformed payload — it must count 1,
        not 0, or omitting the list would bypass the floor entirely."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            for _ in range(_MIN_FLOOR - 1):
                _probe(Path(d), "AskUserQuestion", cwd=str(td), questions=0)
            self.assertEqual(_probe(Path(d), "Write", str(td / "brief.md")), "deny")  # 5


class BriefGrillCompleteSignalTests(TestCase):
    """The decoupled gate: ``grill_complete`` on the marker is the REAL invariant
    (shared understanding reached), independent of the AskUserQuestion count. A
    grill done well in <MIN turns — because decisions were pre-resolved by reading
    docs / carried by $ARGUMENTS — must satisfy the gate via the explicit signal."""

    def test_grill_complete_allows_write_below_floor(self):
        """The headline fix: with grill_complete:true and ZERO questions recorded,
        the brief write is ALLOWED. The signal is the real invariant; the count
        floor is only the backstop."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_grill_complete(td, committed=False)
            # No AskUserQuestion turns at all.
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))

    def test_grill_complete_with_corrupt_counter_still_allows(self):
        """grill_complete gates on marker state, not the counter sidecar — a
        missing/zero counter is irrelevant once the signal is set."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_grill_complete(td, committed=False)
            # A couple of questions recorded, still well below floor.
            for _ in range(2):
                _probe(Path(d), "AskUserQuestion", cwd=str(td))
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))

    def test_no_signal_below_floor_still_denied(self):
        """Without the grill-complete signal, the count floor remains in force —
        a model can't skip the grill by just writing. (The deny reason should now
        mention the grill-done escape, but the decision is still deny.)"""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            for _ in range(2):
                _probe(Path(d), "AskUserQuestion", cwd=str(td))
            self.assertEqual(_probe(Path(d), "Write", str(td / "brief.md")), "deny")

    def test_deny_reason_prescribes_grill_done_signal(self):
        """The deny reason must hand the orchestrator the grill-done escape hatch
        (``track-state brief-grill-done``), not just 'finish the grill' — so a
        legitimately-complete grill is actionable, not a dead end."""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_marker(td, committed=False)
            payload = {"tool_name": "Write", "cwd": str(Path(d)),
                       "tool_input": {"file_path": str(td / "brief.md")}}
            env = dict(os.environ)
            env["CLAUDE_PROJECT_DIR"] = str(Path(d))
            env["CLAUDE_PLUGIN_DATA"] = str(Path(d) / ".data")
            r = subprocess.run([sys.executable, str(_GUARD)],
                               input=json.dumps(payload),
                               capture_output=True, text=True, env=env)
            reason = (json.loads(r.stdout)
                      .get("hookSpecificOutput", {})
                      .get("permissionDecisionReason", ""))
            self.assertIn("brief-grill-done", reason)


class BriefCounterKeyFallbackTests(TestCase):
    """The counter-not-written bug: when the orchestrator's cwd is the PROJECT
    ROOT (not the track dir), ``_derive_track_id_from_cwd`` returned None and the
    bump was silently skipped → the counter never landed → the Write gate read 0
    and said '0 of 6'. The fix resolves the active in-progress brief marker from
    the registry so a project-root cwd still bumps the right counter."""

    def test_project_root_cwd_still_records_counter(self):
        """AskUserQuestion fired from the PROJECT ROOT (cwd = project dir, not the
        track dir) must still land a counter under the track_id the Write gate
        reads — counting its questions, batched or not. Without the registry
        fallback, this was the '0 of 6' bug."""
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            _make_registry(project)  # conductor/tracks.md present
            td = _track(project)
            _set_marker(td, committed=False)
            # Fire two batched AskUserQuestion rounds from the PROJECT ROOT —
            # the bug scenario, now with a frontier payload.
            for _ in range(2):
                _probe(project, "AskUserQuestion", cwd=str(project), questions=3)
            # Now the brief Write from the project root must be ALLOWED — the
            # counter landed under the track_id (resolved via the registry) and
            # the Write gate reads it back. Pre-fix this asserted 'deny' (0 of 6).
            self.assertIsNone(_probe(project, "Write", str(td / "brief.md"),
                                     cwd=str(project)))

    def test_grill_done_signal_still_works_without_registry(self):
        """The grill-complete path is marker-only: it must NOT depend on the
        registry being present. (A repo where tracks.md hasn't been created yet
        but a brief marker exists should still honor grill_complete.)"""
        with tempfile.TemporaryDirectory() as d:
            td = _track(Path(d))
            _set_grill_complete(td, committed=False)
            # No conductor/tracks.md.
            self.assertIsNone(_probe(Path(d), "Write", str(td / "brief.md")))


class BriefCounterLifecycleTests(TestCase):
    """Counter hygiene: the reused-track_id grill bypass. Before the shared
    ``lib/brief_counters`` module, counters never cleared (finalize didn't
    know about them) and never reaped — a stale high count pre-satisfied a
    later brief's grill floor. Pins: finalize clears; stale entries reaped on
    bump; legacy plain-int entries treated as absent."""

    def _counter_file(self, project):
        return project / ".data" / "brief-grill-counters.json"

    def test_stale_high_count_does_not_satisfy_floor(self):
        """THE bypass regression: a stale (TTL-expired) high count for a reused
        track_id must NOT satisfy the grill floor — the write stays denied."""
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            td = _track(project)
            _set_marker(td, committed=False)
            stale_ts = time.time() - 86400 - 1  # past BRIEF_COUNTER_TTL
            self._counter_file(project).parent.mkdir(parents=True, exist_ok=True)
            self._counter_file(project).write_text(json.dumps({
                td.name: {"count": 99, "ts": stale_ts},
            }))
            self.assertEqual(_probe(project, "Write", str(td / "brief.md")), "deny")

    def test_legacy_int_entry_treated_as_absent(self):
        """A pre-schema plain-int entry has no ts — it reads as 0 (not a
        satisfiable floor) and is reaped on the next write."""
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            td = _track(project)
            _set_marker(td, committed=False)
            self._counter_file(project).parent.mkdir(parents=True, exist_ok=True)
            self._counter_file(project).write_text(json.dumps({td.name: 99}))
            self.assertEqual(_probe(project, "Write", str(td / "brief.md")), "deny")

    def test_fresh_high_count_still_satisfies_floor(self):
        """Guard against over-tightening: a fresh (in-TTL) count at the floor
        still satisfies the gate — the reap must not eat live counters."""
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            td = _track(project)
            _set_marker(td, committed=False)
            for _ in range(_MIN_FLOOR):
                _probe(project, "AskUserQuestion", cwd=str(td))
            self.assertIsNone(_probe(project, "Write", str(td / "brief.md")))

    def test_finalize_clears_counter(self):
        """brief-finalize clears the track's counter so a later brief for the
        same track_id starts at a fresh grill budget (the primary hygiene —
        the TTL reap is defense-in-depth for runs that never finalize)."""
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            td = _track(project)
            _set_marker(td, committed=False)
            for _ in range(_MIN_FLOOR):
                _probe(project, "AskUserQuestion", cwd=str(td))
            self.assertTrue(self._counter_file(project).exists())
            # Finalize in-process, with the data dir pointed at the temp
            # project (get_data_dir reads env at call time).
            os.environ["CLAUDE_PLUGIN_DATA"] = str(project / ".data")
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    br.cmd_brief_finalize(str(td))
                result = json.loads(buf.getvalue())
            finally:
                os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            self.assertTrue(result["ok"])
            data = json.loads(self._counter_file(project).read_text())
            self.assertNotIn(td.name, data)

    def test_bump_reaps_stale_entries(self):
        """A bump rewrites the file without TTL-expired or legacy entries —
        the sidecar can't grow unbounded or carry stale pre-satisfaction."""
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            stale = _track(project, "stale_20260721")
            legacy = _track(project, "legacy_20260721")
            self._counter_file(project).parent.mkdir(parents=True, exist_ok=True)
            self._counter_file(project).write_text(json.dumps({
                stale.name: {"count": 42, "ts": time.time() - 86400 - 1},
                legacy.name: 7,
            }))
            # Bump from a THIRD track's cwd — the bump reaps stale/legacy
            # entries and records only the bumper.
            other = _track(project, "other_20260721")
            _probe(project, "AskUserQuestion", cwd=str(other))
            data = json.loads(self._counter_file(project).read_text())
            self.assertNotIn(legacy.name, data)
            self.assertNotIn(stale.name, data)
            self.assertEqual(data[other.name]["count"], 1)


if __name__ == "__main__":
    main()
