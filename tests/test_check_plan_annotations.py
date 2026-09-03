r"""Tests for check-plan-annotations.py — the PreToolUse guard that blocks
plan.md writes whose top-level implementation task lines lack a well-formed
``<!-- AC-n, TC-n.n -->`` annotation.

plan_parse._extract_refs never raises: a missing/malformed comment silently
records empty ac_refs/tc_refs and the task loses all traceability (a data-loss
defect). These tests pin the comment-ref aggregation (across ALL comments on the
line, mirroring _extract_refs), the tag/subtask exemptions, the structure-only
task detection (no ``Task:`` keyword required, matching plan_parse._TASK_LINE),
the marker-char regression guard, and the deny path. Pure functions are exercised
directly via importlib (the script filename is hyphenated, so it can't be a
normal import).
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "check_plan_annotations", _scripts / "check-plan-annotations.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_HOOK = _scripts / "check-plan-annotations.py"


class CommentRefsTests(TestCase):
    """``_comment_refs`` aggregates AC/TC across every ``<!-- -->`` on the line
    (mirrors plan_parse._extract_refs) — refs split across two comments pass."""

    def _refs(self, line):
        return _mod._comment_refs(line)

    def test_ac_and_tc_together(self):
        self.assertEqual(self._refs("- [ ] Task: x <!-- AC-1, TC-1.1 -->"),
                         (True, True))

    def test_no_comment(self):
        self.assertEqual(self._refs("- [ ] Task: x"), (False, False))

    def test_ac_only(self):
        self.assertEqual(self._refs("- [ ] Task: x <!-- AC-1 -->"), (True, False))

    def test_tc_only(self):
        self.assertEqual(self._refs("- [ ] Task: x <!-- TC-1.1 -->"), (False, True))

    def test_refs_split_across_two_comments(self):
        # _extract_refs aggregates across comments → this valid line must pass.
        self.assertEqual(
            self._refs("- [ ] Task: x <!-- AC-1 --> <!-- TC-1.1 -->"),
            (True, True))

    def test_malformed_refs_ignored(self):
        # Missing hyphens → not valid AC-\d+ / TC-\d+\.\d+ → silently empty.
        self.assertEqual(self._refs("- [ ] Task: x <!-- AC1, TC1.1 -->"),
                         (False, False))

    def test_lowercase_refs_ignored(self):
        # No IGNORECASE (matches the parser) → lowercase fails.
        self.assertEqual(self._refs("- [ ] Task: x <!-- ac-1, tc-1.1 -->"),
                         (False, False))

    def test_multi_digit_refs(self):
        self.assertEqual(self._refs("- [ ] Task: x <!-- AC-10, TC-2.10 -->"),
                         (True, True))

    def test_prose_ref_outside_comment_ignored(self):
        # A stray AC-1 / TC-1.1 in the description does NOT satisfy the rule.
        self.assertEqual(
            self._refs("- [ ] Task: covers AC-1 and TC-1.1 somehow"),
            (False, False))


class ScanTests(TestCase):
    def test_clean_task_not_flagged(self):
        self.assertEqual(
            _mod._scan("- [ ] Task: x <!-- AC-1, TC-1.1 -->\n"), [])

    def test_missing_annotation_flagged_with_lineno(self):
        text = "# Plan\n## Phase 1: Build\n- [ ] Task: no comment\n"
        hits = _mod._scan(text)
        self.assertEqual(len(hits), 1)
        lineno, raw, fix = hits[0]
        self.assertEqual(lineno, 3)
        self.assertIn("add a <!-- AC-n, TC-n.n -->", fix)

    def test_ac_only_flagged_names_missing_tc(self):
        hits = _mod._scan("- [ ] Task: x <!-- AC-1 -->\n")
        self.assertEqual(len(hits), 1)
        self.assertIn("TC-n.n", hits[0][2])

    def test_tc_only_flagged_names_missing_ac(self):
        hits = _mod._scan("- [ ] Task: x <!-- TC-1.1 -->\n")
        self.assertEqual(len(hits), 1)
        self.assertIn("AC-n", hits[0][2])

    def test_malformed_flagged(self):
        self.assertEqual(len(_mod._scan("- [ ] Task: x <!-- AC1, TC1.1 -->\n")), 1)

    def test_multi_digit_clean_not_flagged(self):
        self.assertEqual(
            _mod._scan("- [ ] Task: x <!-- AC-10, TC-2.10 -->\n"), [])

    def test_two_comments_clean_not_flagged(self):
        self.assertEqual(
            _mod._scan("- [ ] Task: x <!-- AC-1 --> <!-- TC-1.1 -->\n"), [])

    def test_max_hits_caps_output(self):
        text = "".join(f"- [ ] Task: t{i}\n" for i in range(20))
        self.assertEqual(len(_mod._scan(text)), 8)

    def test_checkbox_bullet_without_keyword_still_flagged(self):
        # plan_parse._TASK_LINE recognizes tasks by checkbox structure alone (no
        # "Task:" keyword required) — the hook must match that, else
        # "- [ ] implement login" silently loses traceability (false negative).
        self.assertEqual(len(_mod._scan("- [ ] implement login\n")), 1)


class ExemptionTests(TestCase):
    """Tagged tasks (non-implementation) and indented subtasks carry no
    annotation and must NOT be flagged."""

    def test_manual_tag_exempt(self):
        self.assertEqual(_mod._scan("- [ ] [Manual] Task: verify P1\n"), [])

    def test_explore_tag_exempt(self):
        self.assertEqual(_mod._scan("- [ ] [Explore] Task: map module\n"), [])

    def test_config_tag_exempt(self):
        self.assertEqual(_mod._scan("- [ ] [Config] Task: knobs\n"), [])

    def test_docs_tag_exempt(self):
        self.assertEqual(_mod._scan("- [ ] [Docs] Task: write readme\n"), [])

    def test_chore_tag_exempt(self):
        self.assertEqual(_mod._scan("- [ ] [Chore] Task: bump deps\n"), [])

    def test_refactor_tag_not_exempt(self):
        # [Refactor] is real implementation work (tdd_exempt=False — it owes a
        # working test), so it must NOT be exempt from §6 the way the other
        # dispatch tags are. An earlier revision exempted the whole registry
        # vocab and silently dropped refactor traceability.
        self.assertEqual(len(_mod._scan("- [ ] [Refactor] extract validation\n")), 1)
        # With a well-formed annotation it passes (not false-flagged).
        self.assertEqual(
            _mod._scan("- [ ] [Refactor] extract validation <!-- AC-1, TC-1.1 -->\n"),
            [])

    def test_indented_subtask_exempt(self):
        # 2-space indent = subtask (inherits AC from parent) → not a top-level task.
        self.assertEqual(_mod._scan("  - [ ] Subtask: nested\n"), [])


class NonTaskLineTests(TestCase):
    """Non-task lines (headings, plain bullets, blanks) are never flagged."""

    def test_phase_heading_not_flagged(self):
        self.assertEqual(_mod._scan("## Phase 1: Build\n"), [])

    def test_title_not_flagged(self):
        self.assertEqual(_mod._scan("# Implementation Plan: Demo\n"), [])

    def test_plain_bullet_not_flagged(self):
        self.assertEqual(_mod._scan("- A plain markdown bullet\n"), [])

    def test_blank_line_not_flagged(self):
        self.assertEqual(_mod._scan("\n"), [])


class MarkerRegressionTests(TestCase):
    """Every plan.md marker char inside a ``[ ]`` is a real checkbox on a
    completed/deferred/skipped task that STILL carries its annotation — it must
    NOT be false-flagged. (Mirrors the checkbox hook's marker regression suite.)
    """

    def test_all_valid_markers_with_annotation_not_flagged(self):
        for marker in (" ", "x", "~", "!", ">", "#", "-", "d"):
            with self.subTest(marker=marker):
                self.assertEqual(
                    _mod._scan(f"- [{marker}] Task: done <!-- AC-1, TC-1.1 -->\n"),
                    [],
                    f"'[{marker}]' with an annotation was false-flagged")


class TextToScanTests(TestCase):
    def test_write_yields_content(self):
        chunks = list(_mod._text_to_scan({"content": "- [ ] Task: x"}))
        self.assertEqual(chunks, [("content", "- [ ] Task: x")])

    def test_edit_yields_new_string(self):
        chunks = list(_mod._text_to_scan({"new_string": "- [ ] Task: x"}))
        self.assertEqual(chunks, [("new_string", "- [ ] Task: x")])

    def test_multiedit_yields_each_edit(self):
        chunks = list(_mod._text_to_scan(
            {"edits": [{"new_string": "- [ ] Task: a"},
                       {"new_string": "- [ ] Task: b <!-- AC-1, TC-1.1 -->"}]}))
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0][0], "edits[0].new_string")


class DenyPathTests(TestCase):
    def _run(self, tool_input):
        proc = subprocess.run(
            [sys.executable, str(_HOOK)],
            input=json.dumps({"tool_input": tool_input}),
            capture_output=True, text=True,
        )
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc.returncode, out

    def test_plan_md_with_missing_annotation_is_denied(self):
        rc, out = self._run({"file_path": "/t/plan.md",
                             "content": "- [ ] Task: no comment"})
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        reason = spec.get("permissionDecisionReason", "")
        self.assertIn("<!-- AC-n, TC-n.n -->", reason)
        self.assertIn("§6", reason)

    def test_plan_md_clean_is_allowed(self):
        rc, out = self._run({"file_path": "/t/plan.md",
                             "content": "- [ ] Task: ok <!-- AC-1, TC-1.1 -->"})
        self.assertNotIn("hookSpecificOutput", out)

    def test_plan_md_tagged_task_without_comment_allowed(self):
        rc, out = self._run({"file_path": "/t/plan.md",
                             "content": "- [ ] [Manual] Task: verify"})
        self.assertNotIn("hookSpecificOutput", out)

    def test_non_plan_file_is_ignored(self):
        # An untagged task line in a README must NOT be blocked.
        rc, out = self._run({"file_path": "/t/README.md",
                             "content": "- [ ] Task: no comment"})
        self.assertNotIn("hookSpecificOutput", out)


class ArtifactEdgeTests(TestCase):
    """``_scan_artifact_edges`` (rule 9): malformed produces/uses comments
    (present, zero path tokens) are deny hits; dangling/orphan edges are
    advisory lines — never deny hits (deliver + surface)."""

    def test_clean_couplet_no_hits(self):
        deny, advisory = _mod._scan_artifact_edges(
            "- [ ] a <!-- AC-1 --> <!-- produces: reports/x.md -->\n"
            "- [ ] b <!-- AC-2 --> <!-- uses: reports/x.md -->\n")
        self.assertEqual(deny, [])
        self.assertEqual(advisory, [])

    def test_empty_produces_comment_is_deny_hit(self):
        deny, advisory = _mod._scan_artifact_edges(
            "- [ ] a <!-- AC-1 --> <!-- produces: -->\n")
        self.assertEqual(len(deny), 1)
        self.assertEqual(deny[0][0], 1)
        self.assertEqual(advisory, [])

    def test_empty_uses_comment_is_deny_hit(self):
        deny, _ = _mod._scan_artifact_edges(
            "- [ ] a <!-- AC-1 --> <!-- uses: -->\n")
        self.assertEqual(len(deny), 1)

    def test_orphan_produces_is_advisory_not_deny(self):
        deny, advisory = _mod._scan_artifact_edges(
            "- [ ] a <!-- AC-1 --> <!-- produces: reports/orphan.md -->\n")
        self.assertEqual(deny, [])
        self.assertEqual(len(advisory), 1)
        self.assertIn("produces reports/orphan.md", advisory[0])
        self.assertIn("no task declares", advisory[0])

    def test_dangling_uses_is_advisory_not_deny(self):
        deny, advisory = _mod._scan_artifact_edges(
            "- [ ] b <!-- AC-2 --> <!-- uses: reports/ghost.md -->\n")
        self.assertEqual(deny, [])
        self.assertEqual(len(advisory), 1)
        self.assertIn("uses reports/ghost.md", advisory[0])

    def test_lineno_carried_in_advisory(self):
        deny, advisory = _mod._scan_artifact_edges(
            "# Plan\n## Phase 1\n- [ ] a <!-- AC-1 --> "
            "<!-- produces: reports/x.md -->\n")
        self.assertEqual(deny, [])
        self.assertTrue(advisory[0].startswith("  line 3:"))


class ArtifactEdgeHookPathTests(TestCase):
    """End-to-end through the hook process: malformed edges deny; advisory
    edges ALLOW with [Conductor] context attached (the write lands)."""

    def _run(self, tool_input):
        proc = subprocess.run(
            [sys.executable, str(_HOOK)],
            input=json.dumps({"tool_input": tool_input}),
            capture_output=True, text=True,
        )
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc.returncode, out

    def test_malformed_produces_denied(self):
        rc, out = self._run({"file_path": "/t/plan.md",
                             "content": "- [ ] Task: a <!-- AC-1, TC-1.1 --> "
                                        "<!-- produces: -->"})
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        self.assertIn("rule 9", spec.get("permissionDecisionReason", ""))
        ctx = spec.get("additionalContext", "")
        self.assertIn("malformed task-artifact edge", ctx)

    def test_orphan_edge_allowed_with_context(self):
        # Deliver + surface, never deny: the orphan write LANDS, with an
        # advisory naming the dead-edge candidate.
        rc, out = self._run({"file_path": "/t/plan.md",
                             "content": "- [ ] Task: a <!-- AC-1, TC-1.1 --> "
                                        "<!-- produces: reports/orphan.md -->"})
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "allow")
        ctx = spec.get("additionalContext", "")
        self.assertIn("[Conductor]", ctx)
        self.assertIn("reports/orphan.md", ctx)

    def test_clean_couplet_no_hook_specific_output(self):
        rc, out = self._run({"file_path": "/t/plan.md",
                             "content": "- [ ] Task: a <!-- AC-1, TC-1.1 --> "
                                        "<!-- produces: reports/x.md -->\n"
                                        "- [ ] Task: b <!-- AC-2, TC-2.1 --> "
                                        "<!-- uses: reports/x.md -->"})
        self.assertNotIn("hookSpecificOutput", out)

    def test_edit_fragment_skips_edge_scan(self):
        # An Edit's new_string is a fragment — the edge advisory needs the
        # whole plan, so a fragmentary orphan declares nothing (documented
        # residual; parse-time validate_uses covers the full plan).
        rc, out = self._run({"file_path": "/t/plan.md",
                             "new_string": "- [ ] Task: a <!-- AC-1, TC-1.1 --> "
                                           "<!-- produces: reports/orphan.md -->"})
        self.assertNotIn("hookSpecificOutput", out)


if __name__ == "__main__":
    main()
