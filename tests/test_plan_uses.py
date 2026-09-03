"""Tests for the optional ``<!-- produces:/uses: -->`` task-artifact edges.

Findings/artifact edge (plan-format-contract.md rule 9): the plan-side
declaration half of the task-artifact ledger. Mirrors the deps substrate:
edges are parsed and validated but deliberately NOT persisted into
track-state.json (to_plan_structure drops them). Issues surface as warnings,
never blocking init — the enforcement bar is deliver + surface, never deny on
an unconsumed artifact (only a malformed comment is denied, by the hook).

Covers: extraction (comma-separated, stripped, deduped), empty-comment
warnings, orphan produces / dangling uses warnings, subtask exemption,
warnings-never-errors, and the state-isolation guarantee.
"""
import os
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.plan_parse import (
    parse_plan,
    validate_uses,
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


def _edge_warnings(parsed: dict) -> list:
    return [w for w in parsed["warnings"] if "task-artifact edge issue" in w]


# A producer/consumer couplet across two phases + the mandatory [Manual]s.
_COUPLET = """## Phase 1: Build
- [ ] Task: baseline <!-- AC-1, TC-1.1 --> <!-- produces: reports/baseline.md -->
- [ ] [Manual] Task: verify Phase 1

## Phase 2: Verify
- [ ] Task: regression <!-- AC-2, TC-2.1 --> <!-- uses: reports/baseline.md -->
- [ ] [Manual] Task: verify Phase 2
"""


class UsesExtractionTests(TestCase):
    def test_edges_extracted_into_task_dicts(self):
        parsed = _parse(_COUPLET)
        self.assertEqual(parsed["phases"][0]["tasks"][0]["produces_refs"],
                         ["reports/baseline.md"])
        self.assertTrue(parsed["phases"][0]["tasks"][0]["produces_has_comment"])
        self.assertEqual(parsed["phases"][1]["tasks"][0]["uses_refs"],
                         ["reports/baseline.md"])
        self.assertTrue(parsed["phases"][1]["tasks"][0]["uses_has_comment"])
        self.assertEqual(_edge_warnings(parsed), [])

    def test_no_edges_means_clean_and_empty(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        t = parsed["phases"][0]["tasks"][0]
        self.assertEqual(t["produces_refs"], [])
        self.assertFalse(t["produces_has_comment"])
        self.assertEqual(t["uses_refs"], [])
        self.assertFalse(t["uses_has_comment"])
        self.assertEqual(_edge_warnings(parsed), [])

    def test_multiple_paths_comma_separated_stripped_deduped(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- produces: reports/x.md, docs/y.md , reports/x.md -->
- [ ] Task: b <!-- AC-2, TC-2.1 --> <!-- uses: reports/x.md, docs/y.md -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertEqual(parsed["phases"][0]["tasks"][0]["produces_refs"],
                         ["reports/x.md", "docs/y.md"])
        self.assertEqual(_edge_warnings(parsed), [])

    def test_keyword_scoped_no_cross_trigger(self):
        # A stray "produces" inside an AC comment or a deps comment does not
        # make that comment an artifact comment.
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1; this task produces value --> <!-- deps: -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertFalse(parsed["phases"][0]["tasks"][0]["produces_has_comment"])
        self.assertEqual(_edge_warnings(parsed), [])

    def test_case_insensitive_keyword(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- Produces: reports/x.md -->
- [ ] Task: b <!-- AC-2, TC-2.1 --> <!-- USES: reports/x.md -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertEqual(parsed["phases"][0]["tasks"][0]["produces_refs"],
                         ["reports/x.md"])
        self.assertEqual(parsed["phases"][0]["tasks"][1]["uses_refs"],
                         ["reports/x.md"])
        self.assertEqual(_edge_warnings(parsed), [])

    def test_subtask_edges_are_ignored(self):
        # Subtasks are plain strings in the parse — an edge comment on a
        # subtask line is stripped from the name and never parsed.
        body = """## Phase 1: Build
- [ ] Task: parent <!-- AC-1, TC-1.1 -->
  - [ ] Subtask: child <!-- produces: reports/x.md -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        sub = parsed["phases"][0]["tasks"][0]["subtasks"][0]
        self.assertNotIn("produces_refs", sub)
        self.assertEqual(_edge_warnings(parsed), [])


class UsesValidationTests(TestCase):
    def test_orphan_produces_warned(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- produces: reports/orphan.md -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertEqual(_edge_warnings(parsed), [
            "P1.T1: task-artifact edge issue (orphan) — "
            "produces reports/orphan.md but no task declares "
            "uses: reports/orphan.md",
        ])

    def test_dangling_uses_warned(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- uses: reports/ghost.md -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertEqual(_edge_warnings(parsed), [
            "P1.T1: task-artifact edge issue (dangling) — "
            "uses reports/ghost.md but no task declares "
            "produces: reports/ghost.md",
        ])

    def test_empty_produces_comment_warned(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- produces: -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        warns = _edge_warnings(parsed)
        self.assertTrue(any("(produces_empty)" in w for w in warns), warns)

    def test_empty_uses_comment_warned(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- uses: -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        warns = _edge_warnings(parsed)
        self.assertTrue(any("(uses_empty)" in w for w in warns), warns)

    def test_forward_ref_allowed_produces_precedes(self):
        # The couplet's point: a Phase-1 produces with its uses in Phase 5 is
        # the DESIGNED shape — cross-phase edges carry no orphan warning.
        body = """## Phase 1: Build
- [ ] Task: baseline <!-- AC-1, TC-1.1 --> <!-- produces: reports/b.md -->
- [ ] [Manual] Task: verify Phase 1

## Phase 5: Verify
- [ ] Task: check <!-- AC-2, TC-2.1 --> <!-- uses: reports/b.md -->
- [ ] [Manual] Task: verify Phase 5
"""
        parsed = _parse(body)
        self.assertEqual(_edge_warnings(parsed), [])

    def test_issues_do_not_block_init(self):
        body = """## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- produces: reports/x.md -->
- [ ] [Manual] Task: verify Phase 1
"""
        parsed = _parse(body)
        self.assertEqual(parsed["errors"], [])
        self.assertTrue(_edge_warnings(parsed))

    def test_validate_uses_returns_issue_dicts(self):
        parsed = _parse("""## Phase 1: Build
- [ ] Task: a <!-- AC-1, TC-1.1 --> <!-- produces: reports/x.md -->
- [ ] [Manual] Task: verify Phase 1
""")
        issues = validate_uses(parsed)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["kind"], "orphan")
        self.assertEqual(issues[0]["at"], "P1.T1")
        self.assertIn("reports/x.md", issues[0]["detail"])


class UsesStateIsolationTests(TestCase):
    """The load-bearing guarantee: edges never reach track-state.json."""

    def test_to_plan_structure_omits_edges(self):
        parsed = _parse(_COUPLET)
        struct = to_plan_structure(parsed)
        for t in struct["phases"][0]["tasks"] + struct["phases"][1]["tasks"]:
            self.assertEqual(set(t.keys()), {"name"})


if __name__ == "__main__":
    main()
