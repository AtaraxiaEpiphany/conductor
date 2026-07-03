r"""Tests for check-plan-checkboxes.py — the PreToolUse guard that blocks
plan.md writes whose task/subtask bullets lack the ``[ ]`` checkbox marker.

spec-planner occasionally emits ``- Subtask: x`` without the bracket; plan_parse
silently drops the line (a data-loss defect). These tests pin the detection
regex, the one-line suggestion, the multi-tool text extraction, and the deny
path. Pure functions are exercised directly via importlib (the script filename
is hyphenated, so it can't be a normal import).
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
    "check_plan_checkboxes", _scripts / "check-plan-checkboxes.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_HOOK = _scripts / "check-plan-checkboxes.py"


class MissingCheckboxRegexTests(TestCase):
    def _hits(self, text):
        return bool(_mod._MISSING_CHECKBOX.search(text))

    def test_bracketless_subtask_flagged(self):
        self.assertTrue(self._hits("- Subtask: do the thing"))

    def test_indented_bracketless_subtask_flagged(self):
        self.assertTrue(self._hits("  - subtask: nested"))

    def test_tag_without_checkbox_flagged(self):
        # [Explore] is a dispatch tag, NOT a checkbox — still missing the marker.
        self.assertTrue(self._hits("- [Explore] Task: map the module"))

    def test_valid_checkbox_not_flagged(self):
        self.assertFalse(self._hits("- [ ] Subtask: do the thing"))

    def test_all_valid_markers_not_flagged(self):
        """Every plan.md marker char inside a ``[ ]`` is a real checkbox and must
        NOT be flagged. Regression: ``[x]`` / ``[d]`` were false-positived because
        the lookahead missed the opening bracket and the tag-branch then matched
        them as a dispatch tag."""
        for marker in (" ", "x", "~", "!", ">", "#", "-", "d"):
            with self.subTest(marker=marker):
                self.assertFalse(
                    self._hits(f"- [{marker}] Task: example"),
                    f"'[{marker}]' was false-flagged as a missing checkbox")

    def test_non_task_bullet_not_flagged(self):
        self.assertFalse(self._hits("- A plain markdown bullet"))


class SuggestTests(TestCase):
    def test_inserts_empty_checkbox_after_bullet(self):
        self.assertEqual(_mod._suggest("- Subtask: x"), "- [ ] Subtask: x")

    def test_preserves_indent(self):
        self.assertEqual(_mod._suggest("  - subtask: y"), "  - [ ] subtask: y")


class ScanTests(TestCase):
    def test_scan_finds_missing_and_reports_line(self):
        text = "# Plan\n- [ ] Task: ok\n- Subtask: missing\n"
        hits = _mod._scan(text)
        self.assertEqual(len(hits), 1)
        lineno, raw, suggested = hits[0]
        self.assertEqual(lineno, 3)
        self.assertIn("[ ]", suggested)

    def test_scan_clean_text_returns_nothing(self):
        self.assertEqual(_mod._scan("- [ ] Task: ok\n- [ ] Subtask: ok\n"), [])

    def test_scan_annotated_bullet_without_keyword_is_flagged(self):
        # Keyword-independent net: an HTML-comment-annotated bullet missing its
        # checkbox is flagged even without the Task:/Subtask: keyword.
        hits = _mod._scan("- implement login <!-- AC-1 -->\n")
        self.assertEqual(len(hits), 1)
        lineno, raw, suggested = hits[0]
        self.assertEqual(lineno, 1)
        self.assertIn("[ ]", suggested)

    def test_scan_well_formed_annotated_bullet_not_flagged(self):
        # ``- [ ] implement login <!-- AC-1 -->`` is valid (keyword optional).
        self.assertEqual(_mod._scan("- [ ] implement login <!-- AC-1 -->\n"), [])

    def test_scan_plain_prose_bullet_without_annotation_not_flagged(self):
        self.assertEqual(_mod._scan("- a plain markdown bullet\n"), [])


class MalformedBracketRegexTests(TestCase):
    """``- [] x`` (empty) / ``- [  ] x`` (whitespace) / ``- [xy] x`` (wrong
    width) sit in the gap between _TASK_LINE (one valid char) and _BAD_MARKER_LINE
    (one char) — zero/2+ chars match neither, so plan_parse would silently drop
    them. The hook's malformed-bracket guard must catch all three."""

    def _malformed(self, line):
        mb = _mod._BRACKET_TOKEN.match(line)
        if not mb:
            return False
        bracket = mb.group(2)
        return (not _mod._VALID_CHECKBOX.match(bracket)
                and not _mod._KNOWN_BRACKET_TOKEN.match(bracket))

    def test_empty_bracket_flagged(self):
        self.assertTrue(self._malformed("- [] Task: foo"))

    def test_whitespace_bracket_flagged(self):
        self.assertTrue(self._malformed("- [  ] Task: foo"))

    def test_two_char_bracket_flagged(self):
        self.assertTrue(self._malformed("- [xy] Task: foo"))

    def test_valid_checkbox_not_malformed(self):
        # Regression: a real checkbox must never be flagged as malformed.
        for marker in (" ", "x", "~", "!", ">", "#", "-", "d"):
            with self.subTest(marker=marker):
                self.assertFalse(self._malformed(f"- [{marker}] Task: foo"))

    def test_known_tag_not_malformed(self):
        # [Manual]/[Explore]/[N/A] are legitimate non-checkbox first tokens —
        # they route to the missing-checkbox path, not malformed. Guards the
        # _KNOWN_BRACKET_TOKEN allow-list against a malformed false positive.
        for tag in ("[Manual]", "[Explore]", "[Docs]", "[Config]", "[Chore]",
                    "[N/A]", "[verified]"):
            with self.subTest(tag=tag):
                self.assertFalse(self._malformed(f"- {tag} Task: foo"))


class SuggestMalformedTests(TestCase):
    def test_empty_bracket_replaced(self):
        self.assertEqual(_mod._suggest_malformed("- [] Task: foo"),
                         "- [ ] Task: foo")

    def test_wrong_width_replaced(self):
        self.assertEqual(_mod._suggest_malformed("- [xy] Task: foo"),
                         "- [ ] Task: foo")

    def test_preserves_indent(self):
        self.assertEqual(_mod._suggest_malformed("\t- [] subtask"),
                         "\t- [ ] subtask")

    def test_only_first_bracket_replaced(self):
        # A <!-- ... --> comment may contain its own brackets; only the checkbox
        # token (first bracket) should be rewritten.
        self.assertEqual(_mod._suggest_malformed("- [] Task <!-- keep [x] -->"),
                         "- [ ] Task <!-- keep [x] -->")


class MalformedScanTests(TestCase):
    def test_scan_finds_empty_bracket_and_suggests_fix(self):
        text = "## Phase 1: P\n- [] Task: foo\n- [ ] [Manual] Task: v\n"
        hits = _mod._scan(text)
        self.assertEqual(len(hits), 1)
        lineno, raw, suggested = hits[0]
        self.assertEqual(lineno, 2)
        self.assertEqual(suggested, "- [ ] Task: foo")

    def test_scan_flags_both_missing_and_malformed(self):
        # Both defect classes in one plan, each flagged once with its own fix.
        text = ("- [] Task: malformed\n"
                "- Subtask: missing\n"
                "- [ ] Task: ok\n")
        hits = _mod._scan(text)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0][2], "- [ ] Task: malformed")
        self.assertEqual(hits[1][2], "- [ ] Subtask: missing")

    def test_scan_valid_plan_returns_nothing(self):
        # No false positives on a clean plan (valid checkboxes + tagged Manual).
        text = ("- [ ] Task: ok <!-- AC-1, TC-1.1 -->\n"
                "  - [ ] Subtask: x\n"
                "- [ ] [Manual] Task: verify\n")
        self.assertEqual(_mod._scan(text), [])


class TextToScanTests(TestCase):
    def test_write_yields_content(self):
        chunks = list(_mod._text_to_scan({"content": "- Subtask: x"}))
        self.assertEqual(chunks, [("content", "- Subtask: x")])

    def test_edit_yields_new_string(self):
        chunks = list(_mod._text_to_scan({"new_string": "- Subtask: x"}))
        self.assertEqual(chunks, [("new_string", "- Subtask: x")])

    def test_multiedit_yields_each_edit(self):
        chunks = list(_mod._text_to_scan(
            {"edits": [{"new_string": "- Subtask: a"}, {"new_string": "- [ ] ok"}]}))
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

    def test_plan_md_with_missing_checkbox_is_denied(self):
        rc, out = self._run({"file_path": "/t/plan.md", "content": "- Subtask: x"})
        spec = out.get("hookSpecificOutput", {})
        self.assertEqual(spec.get("permissionDecision"), "deny")
        self.assertIn("[ ]", spec.get("permissionDecisionReason", ""))

    def test_plan_md_clean_is_allowed(self):
        rc, out = self._run({"file_path": "/t/plan.md", "content": "- [ ] Task: ok"})
        self.assertNotIn("hookSpecificOutput", out)

    def test_non_plan_file_is_ignored(self):
        # A bracket-less task line in a README must NOT be blocked.
        rc, out = self._run({"file_path": "/t/README.md", "content": "- Subtask: x"})
        self.assertNotIn("hookSpecificOutput", out)


if __name__ == "__main__":
    main()
