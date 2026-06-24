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
the repo root stays on the path too.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
