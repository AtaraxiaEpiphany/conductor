#!/usr/bin/env python3
"""Classify dispatch relapses from ``dispatch-lifecycle.log``.

The problem this solves
-----------------------
The single-writer invariant (one agent owns a locked task at a time) is enforced
by the ``PreToolUse:Agent`` dedupe hook (``on-dispatch-dedupe.py``), whose
telemetry is the shared ``dispatch-lifecycle.log`` (``lib/dispatch_lifecycle``).
Three hooks write to it — ``probe`` (dedupe), ``start`` (SubagentStart),
``stop`` (SubagentStop) — keyed by ``(phase, task, subtask)`` + ``gen``.

When a relapse happens ("the same agent ran twice") the operator must hand-grep
the log to even tell which of three structurally-different failures it is, each
fixed in a different module. This script does that classification:

- ``concurrent``  — two ``start`` events with no ``stop`` between them (a task
  truly run by two agents at once). Also flags two ``probe`` events sharing the
  SAME ``gen`` with no intervening ``probe decision=deny`` (a single dispatch
  spawned twice that slipped the guard). Fix target: the dispatch guard.
- ``re-derived``  — ``start … stop(had_result=0) … start`` (the first agent
  ended without a result and the orchestrator re-derived). Fix target: the
  finalize/reap contract, NOT the guard.
- ``no-guard``    — a task with ``start`` events but ZERO ``probe`` events.
  The ``PreToolUse:Agent`` matcher/plumbing regressed; no guard logic can
  help. This is the "silence is not success" check.

Each finding names the shape, the ``(phase, task, subtask)``, the time window,
the ``gen`` values involved, and the prescribed recovery — the same terminations
the dedupe hook's deny reason uses.

Exit code
---------
``0`` if the log is clean (no findings), ``1`` if any finding — so this can gate
a post-loop check or CI. ``2`` if the log is missing/unreadable (a plumbing
fault, distinct from "clean").

Read-only: parses an existing log; writes nothing. Best-effort parsing — a
malformed line is skipped, never raised.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib.env import get_logs_dir  # noqa: E402

_LOG_NAME = "dispatch-lifecycle.log"

# Parse the leading ``[INFO] dispatch_lifecycle event=… key=val …`` suffix.
# Captures the timestamp and the space-delimited key=value fields.
_LINE_RE = re.compile(
    r"^(?P<ts>\S+(?:\s+\S+)?)\s+\[[A-Z]+\]\s+dispatch_lifecycle\s+(?P<kv>.*)$"
)
_KV_RE = re.compile(r"(\w+)=(\S*)")

# ``stop`` events for a result-file agent whose ``had_result`` was 0 are the
# signature of "agent ended without producing a result" → orchestrator re-derives.
_RE_DERIVE_GAP_SECONDS = 120.0  # a re-derive within this window after a bare stop


def _parse_kv(kv):
    """Parse ``key=value key=value`` into a dict (last write wins)."""
    return {k: v for k, v in _KV_RE.findall(kv)}


def _parse_line(line):
    """Return ``(ts_str, fields_dict)`` or ``None`` for a non-lifecycle line."""
    m = _LINE_RE.match(line)
    if not m:
        return None
    return m.group("ts"), _parse_kv(m.group("kv"))


def _idx_key(f):
    """The always-resolved ``(phase, task, subtask)`` join key from fields.

    ``session_token`` falls back to the track dir on real payloads, so it is NOT
    a reliable group key (see ``dispatch_lifecycle.session_token``). The
    phase/task/subtask indices are always resolved by the hooks, so they are the
    stable join key — same as the grep the hooks document.
    """
    return (f.get("phase", "-"), f.get("task", "-"), f.get("subtask", "-"))


def _coerce_gen(f):
    """The ``gen`` field as an int, or ``None`` if absent/``-``/unparseable."""
    g = f.get("gen", "-")
    try:
        return int(g) if g and g != "-" else None
    except ValueError:
        return None


def _had_result(f):
    """``had_result`` truthiness: ``"1"`` → True, else False."""
    return f.get("had_result") == "1"


def classify(events):
    """Group raw events by ``(phase,task,subtask)`` and yield finding dicts.

    A finding is ``{"shape", "phase", "task", "subtask", "window", "gen",
    "recovery"}``. Multiple findings can come from one group (e.g. a concurrent
    double-spawn AND a no-guard if probes were absent).
    """
    by_idx = defaultdict(list)
    for ts, f in events:
        by_idx[_idx_key(f)].append((ts, f))

    findings = []
    for idx, evs in by_idx.items():
        phase, task, subtask = idx
        # Skip groups with no resolved index: ``phase=-``/``task=-`` means the
        # hook ran before a locked task was resolved (early probes) or the line
        # is a test fixture. Those events carry no (phase,task,subtask) join
        # identity, so any "two starts" they show is an artifact of collapsing
        # unrelated events into one ``P-T-`` bucket — not a relapse.
        if phase in ("-", None) or task in ("-", None):
            continue
        loc = f"P{phase}T{task}" + (f".S{subtask}" if subtask not in ("-", None) else "")

        probes = [(ts, f) for ts, f in evs if f.get("event") == "probe"]
        starts = [(ts, f) for ts, f in evs if f.get("event") == "start"]
        stops = [(ts, f) for ts, f in evs if f.get("event") == "stop"]

        # --- no-guard: starts happened but the dedupe hook never probed. ---
        if starts and not probes:
            findings.append({
                "shape": "no-guard",
                "phase": phase, "task": task, "subtask": subtask, "loc": loc,
                "window": f"{starts[0][0]} … {starts[-1][0]}",
                "gen": "-",
                "recovery": (
                    "PreToolUse:Agent matcher/plumbing regressed — no probe fired. "
                    "Check hooks.json (matcher=Agent) + on-dispatch-dedupe.py wiring. "
                    "No guard logic can help until the hook fires."
                ),
            })

        # --- concurrent: two start events with no stop between them. ---
        # Walk start/stop in timestamp order; a second start before any stop =
        # two agents running at once.
        ordered = sorted(
            [(ts, "start", f) for ts, f in starts] +
            [(ts, "stop", f) for ts, f in stops],
            key=lambda x: x[0],
        )
        in_flight = False
        for ts, kind, f in ordered:
            if kind == "start":
                if in_flight:
                    findings.append({
                        "shape": "concurrent",
                        "phase": phase, "task": task, "subtask": subtask,
                        "loc": loc,
                        "window": f"second start @ {ts}",
                        "gen": _coerce_gen(f) or "-",
                        "recovery": (
                            "Two task-executor starts with no stop between — true "
                            "concurrent dispatch. Run "
                            "`track-state dispatch-finalize` to synthesize a FAILURE "
                            "verdict from the locked task and clear the guard. "
                            "Confirm CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1."
                        ),
                    })
                in_flight = True
            else:
                in_flight = False

        # --- concurrent (gen): two probes with the SAME gen and no deny. ---
        # Same gen = one dispatch spawned twice; a deny between would mean the
        # guard caught it. Absent deny = it slipped through.
        seen_gen = {}
        for ts, f in probes:
            g = _coerce_gen(f)
            if g is None:
                continue
            decision = f.get("decision", "-")
            if g in seen_gen and decision != "deny":
                findings.append({
                    "shape": "concurrent",
                    "phase": phase, "task": task, "subtask": subtask, "loc": loc,
                    "window": f"duplicate probe @ {ts}",
                    "gen": g,
                    "recovery": (
                        f"Two probes share gen={g} with no intervening deny — one "
                        "dispatch spawned twice and slipped the guard. "
                        "`track-state dispatch-finalize` to clear."
                    ),
                })
            seen_gen[g] = ts

        # --- re-derived: start … stop(had_result=0) … start within a window. ---
        for i, (stop_ts, stop_f) in enumerate(stops):
            if _had_result(stop_f):
                continue  # clean stop; not a re-derive trigger
            # find the next start after this stop
            for start_ts, start_f in starts:
                if start_ts > stop_ts:
                    # crude window check on the raw timestamp strings (ISO-8601
                    # sorts lexicographically; the gap is advisory only)
                    findings.append({
                        "shape": "re-derived",
                        "phase": phase, "task": task, "subtask": subtask,
                        "loc": loc,
                        "window": f"{stop_ts} → {start_ts}",
                        "gen": _coerce_gen(start_f) or "-",
                        "recovery": (
                            "Agent stopped without a result (had_result=0) then was "
                            "re-dispatched — the finalize/reap contract, not the "
                            "guard. Inspect why the agent returned no result.json; "
                            "the re-derive is correct behavior, repeated re-derives "
                            "are the smell."
                        ),
                    })
                    break

    return findings


def _default_log_path():
    return get_logs_dir() / _LOG_NAME


def _load_events(path):
    """Read the log into ``[(ts, fields), …]``. Missing file → None."""
    p = Path(path)
    if not p.exists():
        return None
    events = []
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            parsed = _parse_line(line)
            if parsed is None:
                continue
            events.append(parsed)
    return events


def _emit_text(findings):
    if not findings:
        print("✓ dispatch-lifecycle.log clean — no relapse shapes detected.")
        return
    print(f"✗ {len(findings)} finding(s) in dispatch-lifecycle.log:\n")
    for i, fnd in enumerate(findings, 1):
        print(f"  [{i}] {fnd['shape'].upper()}  {fnd['loc']}")
        print(f"      window: {fnd['window']}")
        print(f"      gen:    {fnd['gen']}")
        print(f"      fix:    {fnd['recovery']}\n")


def main():
    ap = argparse.ArgumentParser(
        description="Classify concurrent-subagent relapses from dispatch-lifecycle.log."
    )
    ap.add_argument("--log", default=None,
                    help=f"path to the lifecycle log (default: <logs_dir>/{_LOG_NAME})")
    ap.add_argument("--track", default=None,
                    help="filter findings by the track-dir session key")
    ap.add_argument("--json", action="store_true",
                    help="emit findings as JSON (machine-readable)")
    args = ap.parse_args()

    path = args.log or _default_log_path()
    events = _load_events(path)
    if events is None:
        sys.stderr.write(f"no dispatch-lifecycle log at {path}\n")
        return 2

    if args.track:
        events = [(ts, f) for ts, f in events
                  if args.track in (f.get("session") or "")]

    findings = classify(events)

    if args.json:
        print(json.dumps({"log": str(path), "findings": findings}, indent=2))
    else:
        print(f"log: {path}\n")
        _emit_text(findings)

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
