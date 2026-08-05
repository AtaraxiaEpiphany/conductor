"""Drift guard for the two ``_FALLBACK`` fail-open floors.

Each loader module (task_profiles, workflow_shapes) carries a hand-maintained
``_FALLBACK`` dict — a deliberately *trimmed* copy of the baseline JSON used only
as the fail-open floor when the registry file is missing or unparseable. The drift
lint (``check-contract-registry-sync.py``) polices the *contract file* for a second
vocab home, but **not** these two constants — so a JSON edit that forgets the
fallback diverges silently: a tag/shape added to the registry has no fail-open
entry, and a field dropped from a fallback row is invisible.

These tests pin the subset contract: the fallback's entity keys must be a subset
of the resolved baseline's entity keys (same key set), and each fallback entity's
*fields* must be a subset of the resolved entity's fields. Narrower fields per
fallback row are acceptable (the fallback is deliberately trimmed); a *broader*
or *foreign* key set is the divergence that must fail loudly.

Runs against baseline alone (no ``CLAUDE_PROJECT_DIR`` overlay) — the fallback is
the floor under the *baseline*, so the comparison is fallback-vs-baseline, not
fallback-vs-overlay.
"""
import os
import unittest
from contextlib import contextmanager

from scripts.track_state import (
    task_profiles as tp,
    workflow_shapes as ws,
)


@contextmanager
def _no_overlay():
    """Resolve each registry's ``_load()`` against baseline alone (no overlay).

    Clears ``CLAUDE_PROJECT_DIR`` for the duration and busts every loader's
    ``@lru_cache`` so ``_load()`` re-resolves without an overlay on entry and
    restores the caller's caches on exit.
    """
    prior = os.environ.pop("CLAUDE_PROJECT_DIR", None)
    try:
        for mod in (tp, ws):
            mod._load.cache_clear()
        yield
    finally:
        if prior is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = prior
        for mod in (tp, ws):
            mod._load.cache_clear()


def _assert_subset(testcase, label, fallback_unit, resolved_unit):
    """Every key in the fallback unit must exist in the resolved unit.

    The fallback is deliberately trimmed (fewer fields), so resolved ⊇ fallback
    is the invariant — a fallback row declaring a field the real row dropped (or
    never had) is the divergence this catches. Foreign keys are the failure.
    """
    missing = [k for k in fallback_unit if k not in resolved_unit]
    testcase.assertFalse(
        missing,
        f"{label}: fallback declares fields not in resolved baseline: {missing}",
    )


class FallbackSubsetTests(unittest.TestCase):
    """Each ``_FALLBACK`` is a strict subset of the resolved baseline registry."""

    def test_task_profiles_fallback_subset_of_baseline(self):
        with _no_overlay():
            fallback = tp._FALLBACK.get("tags", {})
            resolved = tp._load().get("tags", {})
        self.assertTrue(fallback, "task _FALLBACK has no 'tags' entities")
        # Every fallback tag must exist in the baseline (no phantom fail-open tag).
        for tag, frow in fallback.items():
            self.assertIn(tag, resolved, f"fallback tag {tag!r} not in baseline")
            _assert_subset(self, f"task {tag}", frow, resolved[tag])

    def test_workflow_shapes_fallback_subset_of_baseline(self):
        with _no_overlay():
            fallback = ws._FALLBACK.get("shapes", {})
            resolved = ws._load().get("shapes", {})
        self.assertTrue(fallback, "workflow-shape _FALLBACK has no 'shapes' entities")
        for shape, frow in fallback.items():
            self.assertIn(shape, resolved, f"fallback shape {shape!r} not in baseline")
            _assert_subset(self, f"shape {shape}", frow, resolved[shape])


if __name__ == "__main__":
    unittest.main()
