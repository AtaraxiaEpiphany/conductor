"""Pytest configuration: align test import paths with production.

Production runs with ``scripts/`` on ``sys.path`` — it is ``sys.path[0]`` for
hooks (invoked as ``python3 …/scripts/<hook>.py``) and is inserted explicitly
by the ``scripts/track-state`` launcher. The ``track_state`` package therefore
shares its sibling ``lib`` package via top-level imports
(``from lib.atomic_io import …``).

Tests historically imported via the ``scripts.track_state`` namespace (repo
root on sys.path), which left top-level ``import lib`` unresolvable — a
test/production path divergence that walled the two utility stacks off from
each other and blocked any ``lib``↔``track_state`` sharing (the "dual stack"
debt).

This conftest puts ``scripts/`` on sys.path so both styles resolve and
``track_state`` can consume the ``lib`` substrate exactly as it does in
production. Existing ``from scripts.track_state…`` imports keep working, since
the repo root stays on the path too (``python3 -m pytest`` inserts the cwd).

It lives in ``tests/`` rather than at the repo root so the shim is scoped to
the test tree it serves — every test is under ``tests/``, so pytest loads this
conftest for all of them, and the plugin root stays free of test-only config.
"""
import os
import sys
import tempfile
from pathlib import Path

# tests/ → repo root → scripts/
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Pin the runtime data dir for the WHOLE suite. Tests that spawn hook scripts
# as subprocesses inherit the ambient env (cwd = the plugin repo, which has no
# conductor/tracks/, with CLAUDE_PROJECT_DIR unset), so every lifecycle/log
# write in them silently fell through the tier ladder to the shared
# <plugin>/.data fallback — the plugin repo's .data/logs accumulated ~3k test
# events (the "logs in the wrong place" symptom). CLAUDE_PLUGIN_DATA is tier 1
# of ``lib.env.resolve_data_dir`` and wins over every cwd heuristic, so one
# session-scoped pin here covers in-process writers AND
# ``subprocess.run(env=dict(os.environ))`` spawners alike. Tests that exercise
# the ladder's other tiers set or delete the var in their own env — ``setdefault``
# never overrides them. The dir outlives the run (a NamedTemporaryDirectory would
# delete it under still-open handles); the OS temp cleaner reaps it.
if "CLAUDE_PLUGIN_DATA" not in os.environ:
    os.environ["CLAUDE_PLUGIN_DATA"] = tempfile.mkdtemp(prefix="conductor-tests-")
