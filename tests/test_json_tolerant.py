"""Tests for lib.json_utils.parse_tolerant_json + extract_result_block.

parse_tolerant_json repairs the four common weak-model JSON degradations
(code fences, surrounding prose, trailing commas, smart quotes) so agent
``---RESULT---`` blocks parse even from models that emit near-miss JSON.
"""
import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from lib.json_utils import parse_tolerant_json, extract_result_block


class ParseTolerantJsonTests(TestCase):
    def test_clean_json_passes_through_unchanged(self):
        self.assertEqual(parse_tolerant_json('{"a": 1}'), {"a": 1})

    def test_strips_code_fences(self):
        self.assertEqual(
            parse_tolerant_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_strips_plain_triple_backticks(self):
        self.assertEqual(
            parse_tolerant_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_strips_trailing_commas_in_object(self):
        self.assertEqual(
            parse_tolerant_json('{"a": 1, "b": 2,}'), {"a": 1, "b": 2})

    def test_strips_trailing_commas_in_array(self):
        self.assertEqual(
            parse_tolerant_json('[1, 2, 3,]'), [1, 2, 3])

    def test_strips_trailing_commas_nested(self):
        self.assertEqual(
            parse_tolerant_json('{"a": [1, 2,], "b": {"c": 3,},}'),
            {"a": [1, 2], "b": {"c": 3}})

    def test_normalizes_smart_quotes(self):
        # “ ” « » → " (single-quote forms left alone — invalid JSON delimiters).
        self.assertEqual(
            parse_tolerant_json('{"msg": “hello”}'), {"msg": "hello"})
        self.assertEqual(
            parse_tolerant_json('{"a": «x»}'), {"a": "x"})

    def test_extracts_first_balanced_block_from_surrounding_prose(self):
        text = 'Here is the analysis:\n{"project_type": "api", "x": 1}\nDone.'
        self.assertEqual(
            parse_tolerant_json(text), {"project_type": "api", "x": 1})

    def test_extracts_array_from_surrounding_prose(self):
        text = 'Result:\n[1, 2, 3]\n(end)'
        self.assertEqual(parse_tolerant_json(text), [1, 2, 3])

    def test_braces_inside_string_values_dont_fool_extractor(self):
        text = '{"note": "see {this}", "n": 2}'
        self.assertEqual(
            parse_tolerant_json(text), {"note": "see {this}", "n": 2})

    def test_realistic_project_analyzer_block(self):
        text = (
            "---ANALYSIS RESULT---\n"
            "```json\n"
            "{\n"
            '  "project_type": "web_app",\n'
            '  "languages": [\n'
            '    {"name": "TypeScript", "percentage": 70,},\n'
            '    {"name": "Python", "percentage": 30,}\n'
            "  ],\n"
            "}\n"
            "```\n"
            "---END ANALYSIS RESULT---"
        )
        out = parse_tolerant_json(text)
        self.assertIsNotNone(out)
        self.assertEqual(out["project_type"], "web_app")
        self.assertEqual(len(out["languages"]), 2)

    def test_returns_none_on_truly_invalid(self):
        self.assertIsNone(parse_tolerant_json("not json at all"))
        self.assertIsNone(parse_tolerant_json("{unbalanced:"))

    def test_returns_none_on_empty_and_non_string(self):
        self.assertIsNone(parse_tolerant_json(""))
        self.assertIsNone(parse_tolerant_json("   "))
        self.assertIsNone(parse_tolerant_json(None))
        self.assertIsNone(parse_tolerant_json(123))


class ExtractResultBlockTests(TestCase):
    def test_extracts_block_between_delimiters(self):
        text = "junk\n---ANALYSIS RESULT---\n{\"a\": 1}\n---END ANALYSIS RESULT---\nmore"
        self.assertEqual(
            extract_result_block(text, "ANALYSIS RESULT").strip(), '{"a": 1}')

    def test_tolerates_extra_whitespace_in_end_marker(self):
        text = "---REVIEW RESULT---\nx\n---END  REVIEW RESULT---"
        self.assertEqual(extract_result_block(text, "REVIEW RESULT").strip(), "x")

    def test_case_insensitive(self):
        text = "---analysis result---\ny\n---end analysis result---"
        self.assertEqual(extract_result_block(text, "ANALYSIS RESULT").strip(), "y")

    def test_returns_none_when_start_missing(self):
        self.assertIsNone(extract_result_block("no markers", "ANALYSIS RESULT"))

    def test_returns_none_when_end_missing(self):
        self.assertIsNone(
            extract_result_block("---ANALYSIS RESULT---\nbody", "ANALYSIS RESULT"))

    def test_empty_marker_returns_none(self):
        self.assertIsNone(extract_result_block("text", ""))


if __name__ == "__main__":
    main()
