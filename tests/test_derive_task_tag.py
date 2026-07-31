"""Tests for ``task_profiles.derive_task_tag`` — advisory free-text → tag.

The inverse of ``derive_task_type`` (which reads a tag already on a name string):
``derive_task_tag`` classifies a task DESCRIPTION that has no tag yet, by
signal-matching each registered tag's ``signals``/``when_to_use`` surface. This
is the registry-driven engine behind "dynamic plan generation" — task-tag
choices mechanically derivable from the registry instead of hand-remembered by
spec-planner.

The load-bearing invariants under test:

- **Safe-failure-mode bias**: ``None`` (= default TDD) is the correct outcome for
  the majority of tasks and for ANY ambiguity. A wrongly-untagged ``[Config]``
  costs one extra Red cycle; a wrongly-TAGGED feature task silently skips TDD +
  the coverage gate (F2/F3 exempt). Defaulting to ``None`` biases toward
  correctness — over-tagging is the danger, not under-tagging.
- **fail-open**: never raises into a caller; garbage/empty/None → ``None``.
- **Registry-driven, overlay-aware**: a project overlay tag with a ``signals``
  field is selectable with zero plugin edits.
- **Advisory-only**: ``init-from-plan`` still hard-validates the final tag, so
  this only *proposes*.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

from track_state import task_profiles as tp  # noqa: E402


class DeriveTaskTagBasics(TestCase):
    """Canonical descriptions map to the expected leading tag, or None."""

    def test_migrate_description(self):
        self.assertEqual(
            tp.derive_task_tag("bump spring-boot to 3.0 and fix the javax->jakarta rename"),
            "Migrate",
        )

    def test_docs_description(self):
        self.assertEqual(tp.derive_task_tag("update the README installation section"), "Docs")
        # "design doc" (singular doc) must hit the Docs signals, not fall through to None.
        self.assertEqual(tp.derive_task_tag("write the design doc for the payments module"), "Docs")

    def test_explore_description(self):
        self.assertEqual(tp.derive_task_tag("map the authentication module architecture"), "Explore")
        self.assertEqual(tp.derive_task_tag("spike: investigate the latency regression"), "Explore")

    def test_config_description(self):
        self.assertEqual(tp.derive_task_tag("configure the feature flag in config.yaml"), "Config")

    def test_chore_description(self):
        self.assertEqual(tp.derive_task_tag("bump the dependency versions in package.json"), "Chore")
        self.assertEqual(tp.derive_task_tag("update the eslint and prettier lint config"), "Chore")

    def test_manual_description(self):
        self.assertEqual(tp.derive_task_tag("cross-browser check the new checkout page"), "Manual")

    def test_feature_work_is_none(self):
        """Business-logic work has no exemption tag → default TDD."""
        self.assertIsNone(tp.derive_task_tag("add a DB connection pool to the app"))

    def test_refactor_is_none(self):
        """A plain refactor without a migration safety-net signal → default TDD.

        ``[Migrate]`` requires the "existing suite as safety net" signal; a
        readability refactor has none, so it stays untagged (NOT Migrate). This
        ALSO pins that ``[Refactor]`` (now a real tag with ``refactor: true``)
        is NOT auto-proposed: it deliberately carries no ``signals``, so a
        description containing "refactor" stays untagged rather than silently
        opting into the tactical refactorer.
        """
        self.assertIsNone(tp.derive_task_tag("refactor the user service for readability"))
        self.assertIsNone(tp.derive_task_tag("extract the duplication and simplify"))
        self.assertIsNone(tp.derive_task_tag("clean up and deduplicate the helpers"))


class DeriveTaskTagOverTaggingGuard(TestCase):
    """The headline invariant: feature work that *incidentally* touches a config
    / docs / chore file is NOT mis-tagged as an exemption. Over-tagging silently
    skips TDD + the coverage gate — this guard is the difference between a safe
    default and a silent correctness hole."""

    def test_feature_that_reads_config_is_not_config(self):
        self.assertIsNone(tp.derive_task_tag("add user login feature that reads db config"))

    def test_feature_with_feature_flags_is_not_config(self):
        # "implement the payments service that uses feature flags" — the verb
        # "implement" + "service" mark this as feature work; the feature-flag
        # mention is incidental, not a Config task.
        self.assertIsNone(tp.derive_task_tag("implement the payments service that uses feature flags"))

    def test_build_component_with_config_is_none(self):
        self.assertIsNone(tp.derive_task_tag("build the auth component and write its config"))


class DeriveTaskTagFailOpen(TestCase):
    """derive_task_tag NEVER raises into a caller — None on any bad input."""

    def test_empty_string(self):
        self.assertIsNone(tp.derive_task_tag(""))

    def test_whitespace_only(self):
        self.assertIsNone(tp.derive_task_tag("   \t\n  "))

    def test_none_input(self):
        self.assertIsNone(tp.derive_task_tag(None))

    def test_garbage_returns_something_or_none_but_never_raises(self):
        # Non-alphanumeric gibberish must not crash; the result is irrelevant as
        # long as the call returns (None is the safe default on no signal hits).
        result = tp.derive_task_tag("!@#$%^&*()_+-=[]{}|;':\",./<>?")
        self.assertTrue(result is None or isinstance(result, str))

    def test_non_string_input_does_not_raise(self):
        # A caller passing the wrong type must not get an unhandled exception.
        for bad in (123, [], {}, object()):
            self.assertIsNone(tp.derive_task_tag(bad))


class DeriveTaskTagOverlay(TestCase):
    """The dynamic-generation proof: a project overlay tag with a ``signals``
    field becomes selectable with zero plugin edits — the same overlay-mechanism
    win as ``workflow``."""

    def setUp(self):
        # Snapshot env + cwd so every test restores them (mirrors test_task_type_field).
        self._prior_proj = os.environ.get("CLAUDE_PROJECT_DIR")
        self._cwd = os.getcwd()

    def tearDown(self):
        if self._prior_proj is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = self._prior_proj
        os.chdir(self._cwd)
        tp._load.cache_clear()

    def _mk_project(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(proj, ignore_errors=True))
        Path(proj, "conductor", "tracks").mkdir(parents=True)
        Path(proj, "conductor", "workflow").mkdir(parents=True)
        return proj

    def _write_overlay(self, proj, data):
        Path(proj, "conductor", "workflow", "task-type-profiles.json").write_text(
            json.dumps(data), encoding="utf-8",
        )

    def test_overlay_tag_with_signals_is_selectable(self):
        proj = self._mk_project()
        os.environ["CLAUDE_PROJECT_DIR"] = proj
        self._write_overlay(proj, {"tags": {"K8sRollout": {
            "route": "executor", "tdd_exempt": True, "coverage_exempt": True,
            "when_to_use": "Deploy/release to a Kubernetes cluster via kubectl/helm.",
            "signals": ["k8s", "kubernetes", "kubectl", "helm", "rollout",
                        "deploy to cluster", "manifest", "canary"],
        }}})
        tp._load.cache_clear()

        self.assertIn("K8sRollout", tp.TAG_VOCAB())
        # The overlay tag is BOTH registered AND selectable from free text.
        self.assertEqual(
            tp.derive_task_tag("roll out the new k8s manifest via helm"), "K8sRollout",
        )
        self.assertEqual(
            tp.derive_task_tag("canary deploy to the kubernetes cluster"), "K8sRollout",
        )


if __name__ == "__main__":
    main()
