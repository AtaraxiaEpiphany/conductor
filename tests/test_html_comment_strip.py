"""Regression: HTML-comment stripping used ``re.sub(r'<!--.*?-->', ...)`` without
``re.DOTALL``, so a multi-line ``<!-- ... -->`` annotation was NOT stripped. The
tag-like or marker text inside it then leaked into the cleaned name — a
false-positive ``[Config]`` tag in ``extract_tags``, or a residual ``[N/A]``
marker surviving ``_clean_name``.
"""
import sys
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from track_state.helpers import extract_tags  # noqa: E402
from track_state.plan_parse import _clean_name  # noqa: E402


class HtmlCommentStripTests(TestCase):
    def test_extract_tags_strips_multiline_comment(self):
        # [Config] sits inside a multi-line comment — must NOT be extracted.
        name = "Do work <!-- multi\nline note [Config] --> done"
        self.assertEqual([], extract_tags(name))

    def test_extract_tags_single_line_comment_still_stripped(self):
        name = "[Docs] Write thing <!-- AC-1, TC-1.1 -->"
        self.assertEqual(["Docs"], extract_tags(name))

    def test_clean_name_strips_multiline_comment(self):
        rest = "Task name <!-- note line one\nnote line two [N/A] -->"
        cleaned = _clean_name(rest)
        self.assertNotIn("<!--", cleaned)
        self.assertNotIn("-->", cleaned)
        self.assertNotIn("[N/A]", cleaned)
        self.assertIn("Task name", cleaned)

    def test_clean_name_single_line_comment_still_stripped(self):
        self.assertEqual("Task name", _clean_name("Task name <!-- AC-1 -->"))


if __name__ == "__main__":
    main()
