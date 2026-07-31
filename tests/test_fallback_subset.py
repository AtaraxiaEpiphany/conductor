"""Drift guard for the four ``_FALLBACK`` fail-open floors.

Each loader module (task_profiles, verify_mode_profiles, workflow_shapes,
verifier_profiles) carries a hand-maintained ``_FALLBACK`` dict — a deliberately
*trimmed* copy of the baseline JSON used only as the fail-open floor when the
registry file is missing or unparseable. The drift lint
(``check-contract-registry-sync.py``) polices the *contract file* for a second
vocab home, but **not** these four constants — so a JSON edit that forgets the
fallback diverges silently: a tag/mode/shape/verifier added to the registry has
no fail-open entry, and a field dropped from a fallback row is invisible.

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
    verify_mode_profiles as vmp,
    workflow_shapes as ws,
    verifier_profiles as vp,
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
        for mod in (tp, vmp, ws, vp):
            mod._load.cache_clear()
        yield
    finally:
        if prior is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = prior
        for mod in (tp, vmp, ws, vp):
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

    def test_verify_mode_profiles_fallback_subset_of_baseline(self):
        with _no_overlay():
            fallback = vmp._FALLBACK.get("modes", {})
            resolved = vmp._load().get("modes", {})
        self.assertTrue(fallback, "verify-mode _FALLBACK has no 'modes' entities")
        for mode, frow in fallback.items():
            self.assertIn(mode, resolved, f"fallback mode {mode!r} not in baseline")
            _assert_subset(self, f"mode {mode}", frow, resolved[mode])

    def test_workflow_shapes_fallback_subset_of_baseline(self):
        with _no_overlay():
            fallback = ws._FALLBACK.get("shapes", {})
            resolved = ws._load().get("shapes", {})
        self.assertTrue(fallback, "workflow-shape _FALLBACK has no 'shapes' entities")
        for shape, frow in fallback.items():
            self.assertIn(shape, resolved, f"fallback shape {shape!r} not in baseline")
            _assert_subset(self, f"shape {shape}", frow, resolved[shape])

    def test_verifier_profiles_fallback_subset_of_baseline(self):
        # The verifier _FALLBACK is flat (top-level keys ARE the verifier names),
        # unlike the other three registries which nest under tags/modes/shapes;
        # _load() wraps it as {"verifiers": _FALLBACK}.
        with _no_overlay():
            fallback = vp._FALLBACK
            resolved = vp._load().get("verifiers", {})
        self.assertTrue(fallback, "verifier _FALLBACK is empty")
        for name, frow in fallback.items():
            self.assertIn(name, resolved, f"fallback verifier {name!r} not in baseline")
            _assert_subset(self, f"verifier {name}", frow, resolved[name])


if __name__ == "__main__":
    unittest.main()
