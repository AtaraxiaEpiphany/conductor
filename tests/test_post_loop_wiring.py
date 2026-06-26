"""Wiring test for templates/post-loop.md §7.5 Comprehension Digest.

The digest is the loop-engineering countermeasure to comprehension debt
("the faster the loop ships code you didn't write, the bigger the gap between
what exists and what you understand — unless you read what the loop made").
These asserts pin that the section exists and carries its load-bearing tokens,
so it can't be silently dropped in a future edit. Plain Path.read_text() idiom.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST_LOOP = (ROOT / "templates" / "post-loop.md").read_text(encoding="utf-8")


class PostLoopWiring(unittest.TestCase):
    def test_comprehension_digest_section_present(self):
        self.assertIn("## 7.5 COMPREHENSION DIGEST", POST_LOOP)

    def test_digest_sits_between_review_and_archive(self):
        """§7.5 must land after §7 AUTO-REVIEW and before §8 CLEANUP & ARCHIVE."""
        i_review = POST_LOOP.find("## 7.0 AUTO-REVIEW")
        i_digest = POST_LOOP.find("## 7.5 COMPREHENSION DIGEST")
        i_archive = POST_LOOP.find("## 8.0 CLEANUP & ARCHIVE")
        self.assertGreater(i_review, -1)
        self.assertGreater(i_digest, i_review)
        self.assertGreater(i_archive, i_digest)

    def test_load_bearing_tokens_present(self):
        for token in ("Read this first", "compounds", "comprehension debt"):
            with self.subTest(token=token):
                self.assertIn(token, POST_LOOP)

    def test_digest_adds_no_dispatch(self):
        """The digest reuses in-context data; it must not dispatch a new agent."""
        digest = POST_LOOP.split("## 7.5 COMPREHENSION DIGEST", 1)[1].split("## 8.0", 1)[0]
        self.assertEqual(digest.count("Dispatch"), 0)
        self.assertIn("No new dispatch", digest)


if __name__ == "__main__":
    unittest.main()
