"""Tests for ``check-contract-registry-sync`` — the contract drift gate.

The plan-format contract must carry NO hand-maintained tag enumeration table:
the vocabulary lives in the resolved registry (``task-type-profiles`` — baseline
⊕ overlay) and is rendered by ``track-state registry-doc``. A table in the
contract is a second home for the vocab and the first to drift (a project overlay
adds a tag and the contract silently contradicts it). These tests pin the gate
the way ``test_index_maps`` pins ``check-index-maps``: end-to-end via subprocess,
with a file-swap sandbox that restores the real contract after each drift case
(``get_plugin_root`` is ``__file__``-based, so the script always reads the real
contract — the sandbox swaps its *content*, not its path).
"""
import os
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "check-contract-registry-sync.py"
_CONTRACT = _REPO / "runtime" / "contracts" / "plan-format-contract.md"


def _run():
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_ROOT", None)  # hermetic: force __file__-based root
    return subprocess.run([sys.executable, str(_SCRIPT)],
                          capture_output=True, text=True, env=env)


class _ContractSandbox:
    """Swap the contract's content for the duration of a test, then restore."""

    def __init__(self):
        self._saved = None

    def __enter__(self):
        self._saved = _CONTRACT.read_text(encoding="utf-8")
        return self

    def __exit__(self, *exc):
        _CONTRACT.write_text(self._saved, encoding="utf-8")

    def append(self, text):
        _CONTRACT.write_text(
            _CONTRACT.read_text(encoding="utf-8") + "\n" + text,
            encoding="utf-8",
        )


class HappyPathTests(TestCase):
    def test_clean_contract_passes(self):
        # The committed contract carries no hand-maintained tag/mode table — the
        # whole point of the collapse this gate polices.
        r = _run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK:", r.stdout)


class TagTableDriftTests(TestCase):
    def test_tag_table_is_caught(self):
        # A restored tag enumeration table (the drift this gate exists for) —
        # every row whose first cell is a known tag literal trips the gate.
        with _ContractSandbox() as m:
            m.append(
                "\n| Tag | Meaning |\n|---|---|\n"
                "| `[Explore]` | investigation |\n"
                "| `[Refactor]` | extract |\n"
            )
            r = _run()
        # sys.exit(msg) writes the HALT message to stderr. The finding message
        # names the kind + literal; the HALT banner is the same across detectors.
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("enumerated as a table row", r.stderr)
        self.assertIn("[Explore]", r.stderr)
        self.assertIn("[Refactor]", r.stderr)


class ProseExemptionTests(TestCase):
    """Grammar/invariant text that MENTIONS a tag is NOT a table → keep it.

    The contract legitimately carries a Rule keyed on `[Manual]` and grammar
    examples (`- [ ] [Refactor] …`). Those are prose/grammar, not a vocab
    enumeration — the gate must not trip.
    """

    def test_prose_tag_mention_is_not_caught(self):
        with _ContractSandbox() as m:
            m.append(
                "\nTag the manual-verification task with `[Manual]` so the "
                "orchestrator auto-defers it. A grammar example: "
                "`- [ ] [Refactor] extract the module`.\n"
            )
            r = _run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    main()
