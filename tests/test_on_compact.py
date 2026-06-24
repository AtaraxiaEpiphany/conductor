r"""Tests for on-compact.py — the PreCompact hook that injects compression
priority instructions so the active dispatch loop survives context compression.
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

_HOOK = Path(__file__).resolve().parent.parent / "scripts" / "on-compact.py"


def _run():
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps({}),  # PreCompact input is unused by this hook
        capture_output=True, text=True,
    )
    return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else {})


class OnCompactTests(TestCase):
    def test_always_emits_compression_priority(self):
        rc, out = _run()
        self.assertEqual(rc, 0)
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("COMPRESSION PRIORITY", ctx)

    def test_keeps_active_dispatch_loop_marks_discard_setup(self):
        rc, out = _run()
        ctx = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("[KEEP]", ctx)
        self.assertIn("[DISCARD]", ctx)


if __name__ == "__main__":
    main()
