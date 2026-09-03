"""Tests for write-result's flag-per-field input mode (the ③ change).

Pins the contract that lets agents stop hand-writing result JSON:
- typed --flags assemble the result (success, failure, nested failure_detail);
- integer fields are coerced and a bad value is rejected at write time (the
  type-validation the raw-JSON path lacked);
- repeatable --deviation builds spec_deviation_detail[];
- repeatable --artifacts/--artifacts-used build the task-artifact ledger
  (produced declarations + read attestations; findings/artifact edge);
- status is the one required field and must be SUCCESS|FAILURE;
- --data and stdin remain as backward-compatible raw-JSON inputs.

cmd_write_result takes an injectable ``args`` list so these tests never touch
sys.argv; stdin-mode tests monkeypatch sys.stdin instead.
"""
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

from scripts.track_state.result import cmd_write_result


def _capture(fn, *args, **kwargs):
    """Run fn capturing stdout/stderr; return (parsed_stdout_or_None, stderr, exit_code)."""
    old_o, old_e = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    code = 0
    out_val = None
    try:
        fn(*args, **kwargs)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    raw_o, raw_e = sys.stdout.getvalue(), sys.stderr.getvalue()
    sys.stdout, sys.stderr = old_o, old_e
    try:
        out_val = json.loads(raw_o) if raw_o.strip() else None
    except json.JSONDecodeError:
        out_val = None
    return out_val, raw_e, code


class WriteResultFlagTests(TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        (Path(self.dir) / ".conductor").mkdir()

    def _result_path(self):
        return Path(self.dir) / ".conductor" / "result.json"

    def _read(self):
        return json.loads(self._result_path().read_text())

    # ── field-mode assembly ────────────────────────────────────────────

    def test_success_flags_assemble_and_coerce_types(self):
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--status", "success", "--commit-sha", "abc1234",
             "--summary", "did the thing", "--files-changed", "a.ts,b.ts",
             "--tc-coverage", "TC-1.1", "--coverage-pct", "94",
             "--coverage-tool", "pytest", "--phase", "1", "--task", "2",
             "--task-name", "Build X", "--attempt", "1", "--max-retries", "3"],
        )
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        r = self._read()
        self.assertEqual(r["status"], "SUCCESS")          # upper-cased
        self.assertEqual(r["commit_sha"], "abc1234")
        self.assertEqual(r["coverage_pct"], 94)           # coerced to int, not "94"
        self.assertIsInstance(r["coverage_pct"], int)
        self.assertEqual(r["phase"], 1)
        self.assertEqual(r["task"], 2)
        self.assertEqual(r["max_retries"], 3)

    def test_failure_flags_populate_nested_failure_detail(self):
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--status", "failure", "--summary", "blocked",
             "--failure-done", "wrote tests", "--failure-reason", "import error",
             "--failure-suggested", "fix path", "--phase", "1", "--task", "1"],
        )
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        r = self._read()
        self.assertEqual(r["status"], "FAILURE")
        self.assertEqual(r["failure_detail"], {
            "what_was_done": "wrote tests",
            "failure_reason": "import error",
            "suggested_next_step": "fix path",
        })

    def test_repeatable_deviation_builds_array(self):
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--status", "success", "--summary", "partial",
             "--deviation", '{"ac_id":"AC-2","reason":"partial","suggested_revision":"split"}',
             "--deviation", '{"ac_id":"AC-3","reason":"none","suggested_revision":"n/a"}',
             "--phase", "1", "--task", "1"],
        )
        self.assertEqual(code, 0)
        r = self._read()
        self.assertEqual(len(r["spec_deviation_detail"]), 2)
        self.assertEqual(r["spec_deviation_detail"][0]["ac_id"], "AC-2")
        self.assertEqual(r["spec_deviation_detail"][1]["ac_id"], "AC-3")

    def test_subtask_omit_when_absent(self):
        _capture(cmd_write_result, self.dir,
                 ["--status", "success", "--summary", "x", "--phase", "1", "--task", "1"])
        self.assertNotIn("subtask", self._read())

    # ── task-artifact ledger flags (findings/artifact edge) ───────────

    def test_repeatable_artifacts_both_forms_and_messy_json(self):
        # --artifacts is repeatable in both --flag val and --flag=val forms;
        # the JSON may carry spaces and single-quotes inside the role.
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--status", "success", "--summary", "x", "--phase", "1", "--task", "1",
             "--artifacts", '{"path": "reports/baseline.md", "role": "baseline metrics, 112 points"}',
             "--artifacts={\"path\":\"maps/table.json\"}",
             "--artifacts", '{"path":"docs/notes.md","role":"decoder\'s field guide"}'],
        )
        self.assertEqual(code, 0)
        r = self._read()
        self.assertEqual(r["artifacts"], [
            {"path": "reports/baseline.md", "role": "baseline metrics, 112 points"},
            {"path": "maps/table.json", "role": ""},
            {"path": "docs/notes.md", "role": "decoder's field guide"},
        ])

    def test_artifacts_normalizes_leading_dot_slash(self):
        _capture(cmd_write_result, self.dir,
                 ["--status", "success", "--summary", "x", "--phase", "1", "--task", "1",
                  "--artifacts", '{"path":"  ./reports/baseline.md  "}'])
        r = self._read()
        self.assertEqual(r["artifacts"][0]["path"], "reports/baseline.md")

    def test_repeatable_artifacts_used(self):
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--status", "success", "--summary", "x", "--phase", "1", "--task", "1",
             "--artifacts-used", "reports/baseline.md",
             "--artifacts-used", "./maps/table.json"],
        )
        self.assertEqual(code, 0)
        r = self._read()
        self.assertEqual(r["artifacts_used"],
                         ["reports/baseline.md", "maps/table.json"])

    def test_bad_artifacts_json_rejected(self):
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--status", "success", "--summary", "x", "--phase", "1", "--task", "1",
             "--artifacts", "not-json"],
        )
        self.assertNotEqual(code, 0)
        self.assertIn("--artifacts", out["error"])
        self.assertFalse(self._result_path().exists())

    def test_artifacts_without_string_path_rejected(self):
        # {"path": 3}, a JSON array, and an empty path are all rejected with
        # the same message: --artifacts needs a string 'path'.
        for bad in ('{"path": 3}', '[{"path": "a.md"}]', '{"path": "   "}'):
            out, _err, code = _capture(
                cmd_write_result, self.dir,
                ["--status", "success", "--summary", "x", "--phase", "1", "--task", "1",
                 "--artifacts", bad],
            )
            self.assertNotEqual(code, 0, f"must reject {bad}")
            self.assertIn("path", out["error"])

    # ── validation ─────────────────────────────────────────────────────

    def test_non_integer_coverage_pct_rejected_at_write(self):
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--status", "success", "--coverage-pct", "94%", "--phase", "1", "--task", "1"],
        )
        self.assertNotEqual(code, 0)
        self.assertIsNotNone(out)
        self.assertIn("coverage-pct", out["error"])
        self.assertIn("int", out["error"])
        self.assertFalse(self._result_path().exists())   # never written

    def test_bad_deviation_json_rejected(self):
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--status", "success", "--summary", "x", "--phase", "1", "--task", "1",
             "--deviation", "not-json"],
        )
        self.assertNotEqual(code, 0)
        self.assertIn("deviation", out["error"])

    def test_status_required(self):
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--commit-sha", "x", "--phase", "1", "--task", "1"],
        )
        self.assertNotEqual(code, 0)
        self.assertIn("status", out["error"])

    def test_invalid_status_rejected(self):
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--status", "bogus", "--phase", "1", "--task", "1"],
        )
        self.assertNotEqual(code, 0)
        self.assertIn("status", out["error"])
        self.assertFalse(self._result_path().exists())

    # ── backward compatibility (--data + stdin) ────────────────────────

    def test_data_raw_json_still_works(self):
        out, _err, code = _capture(
            cmd_write_result, self.dir,
            ["--data", '{"status":"SUCCESS","summary":"raw"}'],
        )
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        self.assertEqual(self._read()["summary"], "raw")

    def test_stdin_raw_json_still_works(self):
        old_stdin = sys.stdin
        sys.stdin = io.StringIO('{"status":"SUCCESS","summary":"piped"}')
        try:
            out, _err, code = _capture(cmd_write_result, self.dir, [])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        self.assertEqual(self._read()["summary"], "piped")


if __name__ == "__main__":
    main()
