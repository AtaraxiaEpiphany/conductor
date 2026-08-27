"""Probe registry — the tier-B dynamic-context axis (the fourth registry).

:mod:`task_profiles`, :mod:`workflow_shapes`, and :mod:`agent_roster` say what
a task MEANS, how nodes SEQUENCE, and what SCAFFOLD a dispatch RECEIVES. This
module says how an agent fetches **live project state** on demand: a *probe*
is a named, registered, read-only snapshot command (``track-state probe
<name>``) that answers a context question the static files cannot — the
exemplar ``test-state`` returns the latest test-run verdicts from the
on-test-run hook's ledger.

Why a registry and not hardcoded hooks: the context-model's tier B wants
context fetched through the CLI (deterministic extraction, drift-free from the
parsers), and "which probes exist" is project-varying data the same way agent
scaffolds are. A project adds a probe (say, a CI-status shim) with one overlay
row — zero plugin edits.

Resolves as **plugin baseline ⊕ project overlay** (``conductor/workflow/
probes.json``), exactly mirroring :mod:`agent_roster` (row-level replace,
fail-open to baseline on any overlay error, ``@lru_cache`` + ``cache_clear``).
The read contract: probes are **named** (registered, lintable), **read-only**
(side-effect-free — documented, enforced only by review), and **cheap**
(builtin parsers read one log; command probes run with a hard 10s timeout and
a bounded stdout). Ad-hoc context scraping stays out — that is the tmux-capture
lesson: dynamic context is fine, undisciplined context is not.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from .agent_roster import _plugin_root, _project_root

# The builtin probes this module implements (kind: "builtin"). Anything else
# with kind builtin is a lint error — a row naming an unimplemented builtin is
# a dead name, not a probe.
_BUILTINS = frozenset({"test-state"})

# Fail-open floor: no probes at all (the pre-registry behavior — every probe
# name unknown).
_FALLBACK: dict = {"probes": {}}

# Bounds for the builtin test-state snapshot and command probes: the snapshot
# must stay glanceable (recent N runs, not the whole ledger) and a command
# probe's captured stdout bounded (a chatty probe must not flood the caller).
_RECENT_CAP = 20
_CMD_STDOUT_CAP = 8192
_CMD_TIMEOUT_S = 10

# on-test-run.py log line: ``{iso} [INFO] {ts} test_command="..." result=X``.
# The verdict token is always the trailing ``result=`` field; the command is
# the quoted test_command value (may contain escaped quotes — keep it raw).
_TC_RE = re.compile(r'test_command="(?P<cmd>.*)" result=(?P<result>\w+)\s*$')


def _plugin_registry_path() -> Path:
    return _plugin_root() / "templates" / "workflow" / "probes.json"


def _project_override_path() -> Path | None:
    root = _project_root()
    if root is None:
        return None
    return root / "conductor" / "workflow" / "probes.json"


def _load_baseline() -> dict:
    """Load the plugin baseline probe registry, fail-open to :data:`_FALLBACK`."""
    cand = _plugin_registry_path()
    try:
        if cand.exists():
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data.get("probes"), dict):
                return data
            reason = "has invalid shape (missing 'probes')"
        else:
            reason = "is missing"
    except (OSError, json.JSONDecodeError) as exc:
        reason = f"is unreadable ({exc})"
    print(
        f"WARNING: probes registry at {cand} {reason}; "
        f"using the empty fallback registry.",
        file=sys.stderr,
    )
    return _FALLBACK


@lru_cache(maxsize=1)
def _load() -> dict:
    """Load + cache the resolved registry (baseline ⊕ project overlay).

    Baseline fail-open to the empty floor; overlay fail-open to baseline alone
    — a malformed project probes.json must never take the builtins down.
    """
    merged = dict(_load_baseline().get("probes", {}))
    overlay_path = _project_override_path()
    if overlay_path is not None and overlay_path.exists():
        try:
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            rows = overlay.get("probes") if isinstance(overlay, dict) else None
            if isinstance(rows, dict):
                merged.update(rows)
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"WARNING: project probes overlay at {overlay_path} "
                f"unreadable ({exc}); using plugin baseline alone.",
                file=sys.stderr,
            )
    return {"probes": merged}


def probe_names() -> tuple[str, ...]:
    """The closed vocabulary of registered probe names, in registry order."""
    return tuple(_load().get("probes", {}).keys())


def row_for(name: str) -> dict | None:
    """The resolved row for one probe, or ``None`` when unregistered/malformed."""
    row = _load().get("probes", {}).get(name)
    return row if isinstance(row, dict) else None


# --- the builtins ---------------------------------------------------------------


def _builtin_test_state():
    """Latest test-run verdicts from the on-test-run hook's ledger.

    Parses ``<logs>/on-test-run.log`` (one line per test command the hook
    observed: ``{iso} [INFO] {ts} test_command="…" result=passed|failed|
    interrupted``) into a glanceable snapshot: the last run, the most recent
    ``_RECENT_CAP`` runs, and a verdict tally. Read-only; the ledger itself
    is hook-owned.
    """
    from lib.env import get_logs_dir  # lazy: hook-path env resolution

    log_path = get_logs_dir() / "on-test-run.log"
    if not log_path.exists():
        return {"ok": False, "name": "test-state",
                "reason": "no test runs recorded"}
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"ok": False, "name": "test-state",
                "reason": f"log unreadable ({exc})"}

    runs = []
    for line in lines:
        m = _TC_RE.search(line)
        if m:
            runs.append({"command": m.group("cmd"),
                         "result": m.group("result")})
    if not runs:
        return {"ok": False, "name": "test-state",
                "reason": "no test runs recorded"}

    summary = {"passed": 0, "failed": 0, "interrupted": 0}
    for r in runs:
        if r["result"] in summary:
            summary[r["result"]] += 1
    return {"ok": True, "name": "test-state",
            "last": runs[-1],
            "recent": runs[-_RECENT_CAP:],
            "summary": summary}


_BUILTIN_FNS = {"test-state": _builtin_test_state}


# --- the runner -----------------------------------------------------------------


def run_probe(name: str) -> dict:
    """Run one probe by name. Always returns a dict; ``ok`` carries the verdict.

    ``builtin`` rows dispatch to the in-module parser; ``command`` rows run the
    row's argv (``shlex``-split, no shell) with a hard timeout and a bounded
    stdout — the cheap + side-effect-free contract is documented in the design
    note and reviewed, not enforced beyond these bounds. Unknown name →
    ``{ok: False, reason: ...}`` naming the registered vocabulary (fail-open,
    never an exception).
    """
    row = row_for(name)
    if row is None:
        return {"ok": False, "name": name,
                "reason": f"unknown probe (registered: {', '.join(probe_names()) or 'none'})"}

    kind = row.get("kind")
    if kind == "builtin":
        fn = _BUILTIN_FNS.get(name)
        if fn is None:
            return {"ok": False, "name": name,
                    "reason": f"builtin probe {name!r} is not implemented"}
        return fn()

    if kind == "command":
        cmd = row.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return {"ok": False, "name": name,
                    "reason": "command probe has no command"}
        try:
            proc = subprocess.run(
                shlex.split(cmd), capture_output=True, text=True,
                timeout=_CMD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return {"ok": False, "name": name,
                    "reason": f"command exceeded {_CMD_TIMEOUT_S}s timeout"}
        except (OSError, ValueError) as exc:
            return {"ok": False, "name": name,
                    "reason": f"command failed to start ({exc})"}
        return {"ok": proc.returncode == 0, "name": name,
                "exit": proc.returncode,
                "stdout": proc.stdout[-_CMD_STDOUT_CAP:]}

    return {"ok": False, "name": name,
            "reason": f"unsupported probe kind {kind!r}"}
