"""Tests for the optional ``<!-- deps: P{n}.T{n} -->`` substrate.

This mirrors the ac_refs/tc_refs precedent: deps are parsed, validated, and
aggregated — but deliberately NOT persisted into track-state.json
(to_plan_structure drops them), so the F1 state model, cursor, and dispatch
loop are untouched. Deps are inert metadata for a future scheduler; their
issues surface as warnings, never blocking init.

Covers: extraction, dangling refs, self-deps, cycles (2-node + transitive),
empty/unparsed annotations, subtask exemption, cross-phase refs, aggregation,
and the state-isolation guarantee.
"""
import os
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.plan_parse import (
    parse_plan,
    validate_deps,
    collect_deps,
    to_plan_structure,
)


def _parse(plan_body: str) -> dict:
    """Write plan_body (phases only) to a temp plan.md and parse it."""
    plan = "# Implementation Plan: t\n\n" + plan_body
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(plan)
        path = f.name
    try:
        return parse_plan(Path(path))
    finally:
        os.unlink(path)


def _dep_warnings(parsed: dict) -> list:
    return [w for w in parsed["warnings"] if "dependency annotation issue" in w]


# A phase with two independent implementation tasks + the mandatory [Manual].
_PHASE = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 -->
- [ ] Task: b <!-- AC-2, TC-2.1 -->
- [ ] [Manual] Task: verify Phase 1
"""


class DepsExtractionTests(TestCase):
    def test_deps_extracted_into_task_dict(self):
        parsed = _parse(_PHASE.replace(
            "<!-- AC-2, TC-2.1 -->", "<!-- AC-2, TC-2.1 --> <!-- deps: P1.T1 -->"))
        t = parsed["phases"][0]["tasks"][1]
        self.assertEqual(t["deps_refs"], ["P1.T1"])
        self.assertTrue(t["deps_has_comment"])
        self.assertEqual(t["deps_failures"], [])

    def test_no_deps_means_clean_and_empty_refs(self):
        parsed = _parse(_PHASE)
        for t in parsed["phases"][0]["tasks"]:
            self.assertEqual(t.get("deps_refs"), [])
            self.assertFalse(t.get("deps_has_comment"))
        self.assertEqual(_dep_warnings(parsed), [])

    def test_multiple_refs_deduped_and_normalized(self):
        # P01.T01 normalizes to P1.T1; duplicates collapse.
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 -->
- [ ] Task: b <!-- AC-2, TC-2.1 --> <!-- deps: P01.T01, P1.T1, P1.T1 -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertEqual(parsed["phases"][0]["tasks"][1]["deps_refs"], ["P1.T1"])

    def test_no_space_after_colon_still_parsed(self):
        # "deps:P1.T1" (no space) must parse — the keyword strip is colon-aware.
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 -->
- [ ] Task: b <!-- AC-2, TC-2.1 --> <!-- deps:P1.T1 -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertEqual(parsed["phases"][0]["tasks"][1]["deps_refs"], ["P1.T1"])
        self.assertEqual(_dep_warnings(parsed), [])

    def test_subtask_deps_are_ignored(self):
        # Deps target top-level tasks only; a deps comment on a subtask is
        # stripped from the name and never parsed or warned about.
        body = """## Phase 1: Build
- [ ] Task: parent <!-- AC-1, TC-1.1 -->
  - [ ] Subtask: child <!-- deps: P1.T1 -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        sub = parsed["phases"][0]["tasks"][0]["subtasks"][0]
        self.assertNotIn("deps_refs", sub)  # subtasks are plain strings
        self.assertEqual(_dep_warnings(parsed), [])

    def test_cross_phase_dependency_is_valid(self):
        body = """## Phase 1: Build
- [ ] Task: model <!-- AC-1, TC-1.1 -->
- [ ] [Manual] Task: verify Phase 1

## Phase 2: Extend
- [ ] Task: api <!-- AC-2, TC-2.1 --> <!-- deps: P1.T1 -->
- [ ] [Manual] Task: verify Phase 2
"""
        parsed = _parse(body)
        self.assertEqual(_dep_warnings(parsed), [])
        self.assertEqual(parsed["phases"][1]["tasks"][0]["deps_refs"], ["P1.T1"])


class DepsValidationTests(TestCase):
    def test_dangling_ref_warned(self):
        body = _PHASE.replace(
            "<!-- AC-2, TC-2.1 -->", "<!-- AC-2, TC-2.1 --> <!-- deps: P9.T9 -->")
        parsed = _parse(body)
        self.assertEqual(_dep_warnings(parsed), [
            "P1.T2: dependency annotation issue (dangling) — "
            "deps target P9.T9 does not exist",
        ])

    def test_self_dependency_warned(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- deps: P1.T1 -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertEqual(_dep_warnings(parsed), [
            "P1.T1: dependency annotation issue (self) — "
            "task depends on itself (P1.T1)",
        ])

    def test_two_node_cycle_warned_with_path(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- deps: P1.T2 -->
- [ ] Task: b <!-- AC-2, TC-2.1 --> <!-- deps: P1.T1 -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        cyc = _dep_warnings(parsed)
        self.assertEqual(len(cyc), 1)
        self.assertIn("(cycle)", cyc[0])
        self.assertIn("P1.T1", cyc[0])
        self.assertIn("P1.T2", cyc[0])

    def test_transitive_cycle_warned(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- deps: P1.T2 -->
- [ ] Task: b <!-- AC-2, TC-2.1 --> <!-- deps: P1.T3 -->
- [ ] Task: c <!-- AC-3, TC-3.1 --> <!-- deps: P1.T1 -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        cyc = _dep_warnings(parsed)
        self.assertEqual(len(cyc), 1)
        self.assertIn("(cycle)", cyc[0])

    def test_empty_deps_comment_warned(self):
        body = _PHASE.replace(
            "<!-- AC-2, TC-2.1 -->", "<!-- AC-2, TC-2.1 --> <!-- deps: -->")
        parsed = _parse(body)
        warns = _dep_warnings(parsed)
        self.assertTrue(any("(empty)" in w for w in warns), warns)

    def test_unparsed_token_warned(self):
        # 'T2' is not a valid P{n}.T{n} ref → unparsed, and the comment yields
        # no valid ref → empty. Both are informative; assert the unparsed one.
        body = _PHASE.replace(
            "<!-- AC-2, TC-2.1 -->", "<!-- AC-2, TC-2.1 --> <!-- deps: T2 -->")
        parsed = _parse(body)
        warns = _dep_warnings(parsed)
        self.assertTrue(any("(unparsed)" in w and "'T2'" in w for w in warns), warns)

    def test_issues_do_not_block_init(self):
        # Deps issues are warnings, never errors — a cycle cannot break serial
        # execution under F1, so init must still succeed.
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- deps: P1.T2 -->
- [ ] Task: b <!-- AC-2, TC-2.1 --> <!-- deps: P1.T1 -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertEqual(parsed["errors"], [])
        self.assertTrue(_dep_warnings(parsed))


class DepsStateIsolationTests(TestCase):
    """The load-bearing guarantee: deps never reach track-state.json."""

    def test_validate_deps_returns_issue_dicts(self):
        parsed = _parse("""## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- deps: P9.T9 -->
- [ ] [Manual] Task: verify Phase 1
""")
        issues = validate_deps(parsed)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["kind"], "dangling")
        self.assertEqual(issues[0]["at"], "P1.T1")
        self.assertIn("P9.T9", issues[0]["detail"])

    def test_to_plan_structure_omits_deps(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 -->
- [ ] Task: b <!-- AC-2, TC-2.1 --> <!-- deps: P1.T1 -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        struct = to_plan_structure(parsed)
        task0 = struct["phases"][0]["tasks"][0]
        task1 = struct["phases"][0]["tasks"][1]
        # Only name (and subtasks when present) reach the state model.
        self.assertEqual(set(task0.keys()), {"name"})
        self.assertEqual(set(task1.keys()), {"name"})
        self.assertNotIn("deps_refs", task1)

    def test_collect_deps_aggregates_edges(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 -->
- [ ] Task: b <!-- AC-2, TC-2.1 --> <!-- deps: P1.T1, P1.T1 -->
- [ ] Task: c <!-- AC-3, TC-3.1 --> <!-- deps: P1.T1, P1.T2 -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertEqual(collect_deps(parsed), [
            ((1, 2), (1, 1)),
            ((1, 3), (1, 1)),
            ((1, 3), (1, 2)),
        ])


if __name__ == "__main__":
    main()
