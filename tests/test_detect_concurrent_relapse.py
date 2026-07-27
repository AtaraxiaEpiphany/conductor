"""Three-shape classifier for ``detect-concurrent-relapse``.

The dispatch-lifecycle log records ``probe``/``start``/``stop`` events keyed by
``(phase, task, subtask)`` + ``gen``. When a subagent re-runs, three structurally
different failures look identical in the UI ("agent ran twice") but fix in
different modules. ``scripts/detect-concurrent-relapse.classify`` separates them:

* ``concurrent`` — two starts, no stop between (true double-spawn); OR two probes
  with the SAME gen and no intervening deny (one dispatch spawned twice that
  slipped the guard).
* ``re-derived`` — start … stop(had_result=0) … start (agent ended without a
  result; orchestrator re-derived).
* ``no-guard``  — starts present, zero probes (the PreToolUse:Agent matcher
  regressed; no guard logic can help).

These tests feed synthetic log lines through the real parser + classifier and
assert each shape is labelled, plus that a clean fixture finds nothing.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

# Load the script as a module by file path (it isn't a package).
_spec = importlib.util.spec_from_file_location(
    "detect_concurrent_relapse", _SCRIPTS / "detect-concurrent-relapse.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_line = _mod._parse_line
classify = _mod.classify


def _evts(*lines):
    """Parse a sequence of raw log lines into ``[(ts, fields), …]``."""
    out = []
    for ln in lines:
        p = parse_line(ln)
        # the parser yields (ts, {fields}); lines without a kv body (just ts)
        # are returned as a 2-tuple too — keep only the dict-bearing ones.
        out.append(p)
    return out


_TS = "2026-07-27T10:00:{:02d}.000000+00:00"


def L(ts_sec, event, **fields):
    """Build a lifecycle line with a per-event second for ordering."""
    base = {
        "phase": "1", "task": "1", "subtask": "-",
        "session": "-track-", "agent": "task-executor",
        "marker": "-", "in_flight": "-", "decision": "-",
        "head": "-", "had_result": "-", "gen": "-",
    }
    base.update(fields)
    kv = " ".join(f"{k}={v}" for k, v in base.items())
    return f"{_TS.format(ts_sec)} [INFO] dispatch_lifecycle event={event} {kv}"


class ConcurrentShapeTests(TestCase):
    def test_two_starts_no_stop_is_concurrent(self):
        evs = _evts(
            L(1, "start"),       # first spawn
            L(2, "start"),       # second spawn — no stop between
        )
        findings = classify(evs)
        shapes = {f["shape"] for f in findings}
        self.assertIn("concurrent", shapes,
                      "two starts with no stop between must be classified concurrent")

    def test_two_probes_same_gen_no_deny_is_concurrent(self):
        # A single dispatch spawned twice, slipping the guard (no deny between).
        evs = _evts(
            L(1, "probe", gen="1", decision="allow"),
            L(2, "probe", gen="1", decision="allow"),  # SAME gen, no deny
        )
        findings = classify(evs)
        shapes = {f["shape"] for f in findings}
        self.assertIn("concurrent", shapes,
                      "two probes sharing a gen with no deny must be concurrent")

    def test_two_probes_same_gen_with_deny_is_NOT_concurrent(self):
        # The guard caught it (deny between) → not a relapse.
        evs = _evts(
            L(1, "probe", gen="1", decision="allow"),
            L(2, "probe", gen="1", decision="deny"),  # guard fired
        )
        findings = [f for f in classify(evs) if f["shape"] == "concurrent"]
        self.assertEqual(findings, [],
                         "a deny between same-gen probes means the guard worked")


class ReDerivedShapeTests(TestCase):
    def test_start_barestop_start_is_rederived(self):
        evs = _evts(
            L(1, "start"),
            L(2, "stop", had_result="0"),   # ended without a result
            L(3, "start"),                  # orchestrator re-derived
        )
        findings = classify(evs)
        shapes = {f["shape"] for f in findings}
        self.assertIn("re-derived", shapes,
                      "start→stop(had_result=0)→start must be re-derived")

    def test_start_cleanstop_start_is_NOT_rederived(self):
        # A stop WITH a result is a clean completion, not a re-derive trigger.
        evs = _evts(
            L(1, "start"),
            L(2, "stop", had_result="1"),
            L(3, "start"),
        )
        findings = [f for f in classify(evs) if f["shape"] == "re-derived"]
        self.assertEqual(findings, [],
                         "a clean stop (had_result=1) is not a re-derive trigger")


class NoGuardShapeTests(TestCase):
    def test_starts_without_any_probe_is_no_guard(self):
        evs = _evts(
            L(1, "start"),
            L(2, "stop", had_result="1"),
            # NO probe events at all → matcher regressed
        )
        findings = classify(evs)
        shapes = {f["shape"] for f in findings}
        self.assertIn("no-guard", shapes,
                      "starts with zero probes must be no-guard")

    def test_starts_with_probe_is_NOT_no_guard(self):
        evs = _evts(
            L(0, "probe", gen="1", decision="allow"),
            L(1, "start"),
            L(2, "stop", had_result="1"),
        )
        findings = [f for f in classify(evs) if f["shape"] == "no-guard"]
        self.assertEqual(findings, [],
                         "a probe present means the guard fired")


class CleanFixtureTests(TestCase):
    def test_clean_dispatch_classifies_to_nothing(self):
        # The happy path: probe → start → stop(had_result=1). No finding.
        evs = _evts(
            L(0, "probe", gen="1", decision="allow"),
            L(1, "start"),
            L(2, "stop", had_result="1"),
        )
        self.assertEqual(classify(evs), [],
                         "a clean single dispatch must produce no findings")

    def test_unresolved_index_events_are_skipped(self):
        # phase=-/task=- events (early probes / test fixtures) must NOT form a
        # relapse finding — they have no (phase,task,subtask) join identity.
        evs = _evts(
            L(1, "start", phase="-", task="-"),
            L(2, "start", phase="-", task="-"),
        )
        self.assertEqual(classify(evs), [],
                         "unresolved-index events must not be classified")


class CLITests(TestCase):
    """Exit-code contract: 0 clean, 1 on finding, 2 on missing log."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_log(self, *lines):
        p = Path(self.tmp.name) / "dispatch-lifecycle.log"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_clean_log_exits_0(self):
        p = self._write_log(
            L(0, "probe", gen="1", decision="allow"),
            L(1, "start"),
            L(2, "stop", had_result="1"),
        )
        import subprocess
        rc = subprocess.run(
            [sys.executable, str(_SCRIPTS / "detect-concurrent-relapse.py"),
             "--log", str(p)],
            capture_output=True, text=True,
            env={"PYTHONPATH": str(_SCRIPTS), "PATH": "/usr/bin:/bin"},
        ).returncode
        self.assertEqual(rc, 0, "clean log must exit 0")

    def test_relapse_log_exits_1(self):
        p = self._write_log(L(1, "start"), L(2, "start"))
        import subprocess
        rc = subprocess.run(
            [sys.executable, str(_SCRIPTS / "detect-concurrent-relapse.py"),
             "--log", str(p)],
            capture_output=True, text=True,
            env={"PYTHONPATH": str(_SCRIPTS), "PATH": "/usr/bin:/bin"},
        ).returncode
        self.assertEqual(rc, 1, "a finding must exit 1")

    def test_missing_log_exits_2(self):
        import subprocess
        rc = subprocess.run(
            [sys.executable, str(_SCRIPTS / "detect-concurrent-relapse.py"),
             "--log", str(Path(self.tmp.name) / "nope.log")],
            capture_output=True, text=True,
            env={"PYTHONPATH": str(_SCRIPTS), "PATH": "/usr/bin:/bin"},
        ).returncode
        self.assertEqual(rc, 2, "missing log must exit 2 (distinct from clean)")


if __name__ == "__main__":
    main()
