"""Tests for ``spec-anchors``: the read-only structural-anchor check on spec.md.

Guards the weak-model failure where ``spec.md`` is written as free-form
narrative (often in another language) with no ``## Acceptance Criteria``
section or ``## Test Scenarios`` table — a structurally anchor-less spec that
``spec-integrity`` silently blesses as ``N/A``. ``cmd_spec_anchors`` asserts the
English machine-anchor *tokens* are present; it is language-agnostic about the
prose. The CJK-prose-with-anchors fixture below is the core regression this
protects against.
"""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.spec_integrity import cmd_spec_anchors


def _track(spec):
    """Build a temp track dir and write ``spec`` (or None for no spec.md)."""
    d = tempfile.mkdtemp()
    if spec is not None:
        Path(d, "spec.md").write_text(spec)
    return d


def _run(track_dir):
    """Invoke ``cmd_spec_anchors`` capturing the single JSON line it prints."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        cmd_spec_anchors(track_dir)
        return json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = old


# Chinese-prose spec WITH the English machine anchors — the canonical pass case.
# Prose is CJK; headings + AC-N / TC-N.M tokens are ASCII. This is what the
# check exists to bless.
_SPEC_CN_WITH_ANCHORS = """\
# Specification: 订单处理
## Overview
处理用户提交的订单并返回订单号。
## Requirements
### Functional Requirements
- FR-1: 当用户提交订单时，系统应当返回订单号。
### Non-Functional Requirements
- NFR-1: 系统应当在 200ms 内响应。
## Acceptance Criteria
- AC-1: 提交合法订单后返回订单号。
- AC-2: 提交非法订单时返回错误信息。
## Test Scenarios

| ID     | AC Ref | Scenario   | Expected Outcome |
| ------ | ------ | ---------- | ---------------- |
| TC-1.1 | AC-1   | 合法订单   | 返回订单号       |
| TC-2.1 | AC-2   | 非法订单   | 返回错误         |
"""

# Weak-model failure: Chinese narrative with NO anchors at all — no AC section,
# no TC table. This is the defect the check exists to catch.
_SPEC_CN_NO_ANCHORS = """\
# Specification: 订单处理
## Overview
这个功能就是处理订单。当用户提交时返回订单号。错误时返回错误。
"""

# ACs present but no Test Scenarios table — the second anchor-missing branch.
_SPEC_AC_NO_TC_TABLE = """\
# Specification: x
## Acceptance Criteria
- AC-1: 合法订单返回订单号。
"""


class SpecAnchorsTests(TestCase):
    def test_cjk_prose_with_english_anchors_passes(self):
        r = _run(_track(_SPEC_CN_WITH_ANCHORS))
        self.assertTrue(r["ok"])
        self.assertEqual(r["ac_count"], 2)
        self.assertEqual(r["tc_count"], 2)
        self.assertEqual(r["errors"], [])

    def test_missing_spec_md_is_ok_false_with_named_error(self):
        d = _track(None)
        r = _run(d)
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["errors"]), 1)
        self.assertIn("not found", r["errors"][0])

    def test_no_ac_section_is_ok_false(self):
        r = _run(_track(_SPEC_CN_NO_ANCHORS))
        self.assertFalse(r["ok"])
        self.assertEqual(r["ac_count"], 0)
        self.assertEqual(len(r["errors"]), 1)
        # Error names the missing section and the English-anchor rule.
        self.assertIn("Acceptance Criteria", r["errors"][0])
        self.assertIn("English", r["errors"][0])

    def test_acs_present_but_no_tc_table_is_ok_false(self):
        r = _run(_track(_SPEC_AC_NO_TC_TABLE))
        self.assertFalse(r["ok"])
        self.assertEqual(r["ac_count"], 1)
        self.assertEqual(r["tc_count"], 0)
        self.assertEqual(len(r["errors"]), 1)
        self.assertIn("Test Scenarios", r["errors"][0])

    def test_always_exits_zero_via_out(self):
        # cmd_spec_anchors surfaces failure via ok:false in JSON, never a
        # non-zero process exit (mirrors init-from-plan --check). Verified by
        # _run not raising and returning a parsed dict on every branch.
        for spec in (None, _SPEC_CN_NO_ANCHORS, _SPEC_AC_NO_TC_TABLE,
                     _SPEC_CN_WITH_ANCHORS):
            self.assertIsInstance(_run(_track(spec)), dict)


if __name__ == "__main__":
    main()
