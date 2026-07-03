r"""Tests for filter-subagent-output payload extraction.

Regression: ``main()`` read ``tool_response`` and did ``response.get("result")``
for the dict branch. The real Agent PostToolUse payload has **no** ``result``
key — text lives in ``content[].text``::

    {"agentId": "...", "agentType": "...", "content": [{"text": "..."}]}

So ``response.get("result")`` was always None and the hook fell through to
``json.dumps(tool_response)``, feeding the *entire* payload (agentId, agentType,
JSON-escaped text) into the result-block / failure / recovery regex scans.
The block-extraction regex happened to still find the block *inside* the
JSON-encoded string by accident, but failure/recovery detection ran over
garbled input and the collapsed ``updatedToolOutput`` carried noise.

Fix: ``_extract_agent_text`` reads ``content[].text`` (falls back to ``result``
or a JSON dump). These tests pin the real payload shape (confirmed against
``.claude-trace``) and prove end-to-end that the block survives intact.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

_scripts = Path(__file__).resolve().parent.parent / "scripts"
# Production gets scripts/ on sys.path automatically (script dir = sys.path[0]);
# replicate that so the module's `from lib.hook_io import ...` resolves.
sys.path.insert(0, str(_scripts))

_spec = importlib.util.spec_from_file_location(
    "filter_subagent_output", _scripts / "filter-subagent-output.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_extract_agent_text = _mod._extract_agent_text

_HOOK = _scripts / "filter-subagent-output.py"


def _agent_payload(text, agent_type="task-executor"):
    """Build a realistic Agent PostToolUse hook input (confirmed payload shape)."""
    return {
        "tool_name": "Agent",
        "cwd": str(Path(__file__).resolve().parent),
        "tool_input": {"subagent_type": agent_type},
        "tool_response": {
            "agentId": "adcbd618a98aa0685",
            "agentType": agent_type.capitalize(),
            "content": [{"text": text}],
        },
    }


class ExtractAgentTextTests(TestCase):
    def test_real_payload_extracts_content_text(self):
        text = "Done.\n---TASK RESULT---\nSTATUS: SUCCESS\n---END RESULT---"
        out = _extract_agent_text(_agent_payload(text)["tool_response"])
        self.assertEqual(out, text)

    def test_multiple_content_blocks_joined(self):
        payload = {"content": [{"text": "part 1"}, {"text": "part 2"}, {"text": "part 3"}]}
        self.assertEqual(_extract_agent_text(payload), "part 1\npart 2\npart 3")

    def test_blocks_without_text_fall_through(self):
        # content present but no text fields → must not return empty silently
        payload = {"agentType": "X", "content": [{"type": "image"}, {}]}
        out = _extract_agent_text(payload)
        self.assertIn("agentType", out)  # JSON-dump fallback

    def test_legacy_result_key_still_supported(self):
        payload = {"result": "legacy result string"}
        self.assertEqual(_extract_agent_text(payload), "legacy result string")

    def test_legacy_result_dict_is_jsonified(self):
        payload = {"result": {"a": 1}}
        self.assertEqual(json.loads(_extract_agent_text(payload)), {"a": 1})

    def test_bare_string_passthrough(self):
        self.assertEqual(_extract_agent_text("plain string"), "plain string")

    def test_none_returns_empty(self):
        self.assertEqual(_extract_agent_text(None), "")

    # --- The regression guard: the OLD code returned the whole JSON dump ---
    def test_no_result_key_does_not_dump_noise_into_text(self):
        """Old behavior: response.get('result') was None → json.dumps(payload).
        New behavior returns the content text. Confirm we get the TEXT, not a
        dump containing agentId/agentType."""
        text = "STATUS: SUCCESS"
        out = _extract_agent_text(_agent_payload(text)["tool_response"])
        self.assertEqual(out, text)
        self.assertNotIn("agentId", out)
        self.assertNotIn("agentType", out)


class MainEndToEndTests(TestCase):
    """Run the hook as a subprocess (real stdin→JSON→stdout contract)."""

    def _run(self, hook_input: dict) -> dict:
        proc = subprocess.run(
            [sys.executable, str(_HOOK)],
            input=json.dumps(hook_input),
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_block_in_real_payload_survives_to_updated_output(self):
        text = "All done.\n---TASK RESULT---\nSTATUS: SUCCESS\nCOMMIT_SHA: abc1234\n---END RESULT---"
        result = self._run(_agent_payload(text))
        updated = result["hookSpecificOutput"]["updatedToolOutput"]
        # updatedToolOutput is now a schema-valid Agent result OBJECT (echoed
        # tool_response + swapped content), not the bare extracted string — a
        # bare string is rejected by Claude Code's PostToolUse validation.
        self.assertIsInstance(updated, dict)
        self.assertEqual(updated["agentId"], "adcbd618a98aa0685")  # preserved, not stripped
        block = updated["content"][0]["text"]
        # The trimmed content is exactly the extracted block — no agentId noise.
        self.assertIn("---TASK RESULT---", block)
        self.assertIn("STATUS: SUCCESS", block)
        self.assertIn("abc1234", block)
        self.assertNotIn("agentId", block)
        self.assertNotIn("agentType", block)

    def test_failure_status_read_from_result_block_not_prose(self):
        """Failure status travels in the deterministic result block (preserved in
        updatedToolOutput), not mined from agent prose — matching on-subagent-stop's
        policy of dropping prose failure-detection as a false-positive source."""
        text = ("Something broke.\nTraceback (most recent call last):\n  File x\n"
                "---TASK RESULT---\nSTATUS: FAILURE\nSUMMARY: tests failed\n"
                "---END RESULT---")
        result = self._run(_agent_payload(text))
        updated = result["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIn("STATUS: FAILURE", updated["content"][0]["text"])

    def test_prose_failure_alone_does_not_trigger_failure_advisory(self):
        """A Traceback in prose with NO result block must NOT yield a 'subagent
        reported failure' advisory — that prose-mining path was removed for
        consistency with on-subagent-stop. The no-result warning is returned
        instead (which mentions dispatch-finalize, not 'reported failure')."""
        text = "Something broke.\nTraceback (most recent call last):\n  File x"
        result = self._run(_agent_payload(text))
        ctx = result["hookSpecificOutput"].get("additionalContext") or ""
        self.assertNotIn("subagent reported failure", ctx.lower())

    def test_updated_output_is_schema_valid_object_not_bare_string(self):
        """Regression: updatedToolOutput was emitted as a bare string, which
        Claude Code's PostToolUse schema validation REJECTS (Zod invalid_type
        'expected object, received string', confirmed 1:1 against live fires in
        session debug logs) — the replacement was discarded and the verbose
        original reached the model. It must now be an Agent result object: a
        dict whose ``content`` is a list of {type:text, text:str} blocks, with
        the runtime's agentId/status preserved (echoed from tool_response)."""
        result = self._run(_agent_payload(
            "ok\n---TASK RESULT---\nSTATUS: SUCCESS\n---END RESULT---"))
        updated = result["hookSpecificOutput"]["updatedToolOutput"]
        self.assertIsInstance(updated, dict,
                              "updatedToolOutput must be an object, not a bare string")
        self.assertIsInstance(updated["content"], list)
        block = updated["content"][0]
        self.assertEqual(block["type"], "text")
        self.assertIsInstance(block["text"], str)
        self.assertEqual(updated["agentId"], "adcbd618a98aa0685")  # runtime field preserved


if __name__ == "__main__":
    main()
